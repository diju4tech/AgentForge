"""
AgentForge Orchestrator
Planner → Builder → Reviewer agent loop with full production hardening.

Improvements over v1:
  #2  Atomic checkpoint writes (os.replace)
  #3  Agent timeout + exponential-backoff retry
  #4  Git workspace validation on resume
  #5  Global iteration circuit breaker
  #7  In-process metrics (counters + timers, saved to logs/metrics.yaml)
  #9  Agent I/O logging (logs/<task_id>/<ts>_<agent>.txt)
  #10 Parallel task execution (git worktrees, ThreadPoolExecutor)
  #11 Pluggable queue backend (YAML default, Redis subclass ready)
  #12 Pydantic config validation with clear startup errors
  #13 Prompt injection guard on PRD content
  #14 GitHub PAT expiry warning at startup
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

load_dotenv()


# ============================================================
# CONFIG MODELS  (#12 — Pydantic validation)
# ============================================================

class SCMConfig(BaseModel):
    type: str = "github"
    owner: str = ""
    repo: str = ""
    base_branch: str = "main"


class AgentConfig(BaseModel):
    command: str = "claude"
    timeout: int = 300          # seconds per agent call
    max_retries: int = 3        # retries on timeout/error


class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0


class BuildConfig(BaseModel):
    max_retries: int = 3                # per task before escalation
    sleep_interval: int = 2            # seconds between tasks
    max_global_iterations: int = 50    # circuit breaker
    queue_backend: str = "yaml"        # "yaml" | "redis"
    parallel_tasks: bool = False       # run independent tasks in parallel
    max_workers: int = 2               # worker threads when parallel=True
    redis: RedisConfig = RedisConfig()

    @field_validator("queue_backend")
    @classmethod
    def valid_backend(cls, v: str) -> str:
        if v not in ("yaml", "redis"):
            raise ValueError(f"queue_backend must be 'yaml' or 'redis', got '{v}'")
        return v


class ProjectMeta(BaseModel):
    name: str = "Unnamed Project"
    prd: str = "PRD.md"


class ProjectConfig(BaseModel):
    project: ProjectMeta = ProjectMeta()
    scm: SCMConfig = SCMConfig()
    agent: AgentConfig = AgentConfig()
    build: BuildConfig = BuildConfig()


def load_config(path: str = "project.yaml") -> ProjectConfig:
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    try:
        return ProjectConfig.model_validate(raw)
    except Exception as e:
        raise SystemExit(f"[Config] Invalid project.yaml:\n{e}")


# ============================================================
# METRICS  (#7)
# ============================================================

class Metrics:
    """In-process counters and timers. Saved to logs/metrics.yaml on loop end."""

    def __init__(self):
        self._counters: Dict[str, int] = {}
        self._timings: Dict[str, List[float]] = {}
        self._timers: Dict[str, float] = {}

    def inc(self, name: str, n: int = 1):
        self._counters[name] = self._counters.get(name, 0) + n

    def start(self, name: str):
        self._timers[name] = time.monotonic()

    def stop(self, name: str):
        if name in self._timers:
            elapsed = time.monotonic() - self._timers.pop(name)
            self._timings.setdefault(name, []).append(round(elapsed, 3))

    def summary(self) -> Dict:
        timing_stats = {}
        for k, vals in self._timings.items():
            timing_stats[k] = {
                "count": len(vals),
                "avg_s": round(sum(vals) / len(vals), 2),
                "max_s": round(max(vals), 2),
                "total_s": round(sum(vals), 2),
            }
        return {"counters": self._counters, "timings": timing_stats}

    def save(self, path: str = "logs/metrics.yaml"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.summary(), f, sort_keys=False)
        print(f"[Metrics] Saved to {path}")


# ============================================================
# TASK QUEUE  (#11 — pluggable backend)
# ============================================================

class TaskQueue:
    """
    YAML-backed task queue.
    Swap backend by subclassing and overriding _load() / _save().
    See RedisTaskQueue below.
    """

    def __init__(self, path: str = "tasks/task_queue.yaml"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def _load(self) -> Dict:
        if not os.path.exists(self.path):
            return {"tasks": []}
        with open(self.path, "r") as f:
            return yaml.safe_load(f) or {"tasks": []}

    def _save(self, data: Dict):
        with open(self.path, "w") as f:
            yaml.dump(data, f, sort_keys=False)

    def get_next_task(self) -> Optional[Dict]:
        for task in self._load().get("tasks", []):
            if task.get("status") == "pending":
                return task
        return None

    def get_independent_tasks(self, n: int, completed_ids: List[str]) -> List[Dict]:
        """Return up to n pending tasks whose dependencies are all completed."""
        result = []
        for task in self._load().get("tasks", []):
            if task.get("status") != "pending":
                continue
            deps = task.get("dependencies", [])
            if all(d in completed_ids for d in deps):
                result.append(task)
            if len(result) >= n:
                break
        return result

    def update_task(self, task_id: str, updates: Dict):
        data = self._load()
        for task in data.get("tasks", []):
            if task["task_id"] == task_id:
                task.update(updates)
        self._save(data)

    def add_feedback(self, task_id: str, comments: List[Dict], max_retries: int = 3):
        data = self._load()
        for task in data.get("tasks", []):
            if task["task_id"] == task_id:
                task.setdefault("comments", []).extend(comments)
                task["retry_count"] = task.get("retry_count", 0) + 1
                task["status"] = (
                    "escalated" if task["retry_count"] >= max_retries else "pending"
                )
        self._save(data)

    def mark_in_progress(self, task_id: str):
        self.update_task(task_id, {"status": "in_progress", "started_at": _now()})

    def mark_complete(self, task_id: str):
        self.update_task(task_id, {"status": "completed", "completed_at": _now()})

    def load_all(self) -> List[Dict]:
        return self._load().get("tasks", [])

    def replace_all(self, tasks: List[Dict]):
        self._save({"tasks": tasks})

    def has_escalated(self) -> bool:
        return any(t.get("status") == "escalated" for t in self.load_all())


class RedisTaskQueue(TaskQueue):
    """
    Redis-backed task queue. Drop-in replacement for TaskQueue.
    Requires: pip install redis
    Configure via project.yaml -> build.queue_backend: redis
    """

    def __init__(self, host: str = "localhost", port: int = 6379,
                 db: int = 0, key: str = "agentforge:tasks"):
        try:
            import redis as redis_lib
        except ImportError:
            raise ImportError("Redis backend requires: pip install redis")
        self.client = redis_lib.Redis(host=host, port=port, db=db, decode_responses=True)
        self.key = key

    def _load(self) -> Dict:
        raw = self.client.get(self.key)
        return yaml.safe_load(raw) if raw else {"tasks": []}

    def _save(self, data: Dict):
        self.client.set(self.key, yaml.dump(data, sort_keys=False))


def _build_queue(cfg: ProjectConfig) -> TaskQueue:
    backend = cfg.build.queue_backend
    if backend == "yaml":
        return TaskQueue()
    if backend == "redis":
        r = cfg.build.redis
        return RedisTaskQueue(host=r.host, port=r.port, db=r.db)
    raise ValueError(f"Unknown queue backend: {backend}")


# ============================================================
# CHECKPOINT  (#2 — atomic writes)
# ============================================================

class Checkpoint:
    """
    Persists orchestrator state between sessions.
    Writes are atomic (temp file + os.replace) — crash-safe on POSIX.
    """

    def __init__(self, path: str = "context/CHECKPOINT.yaml"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def load(self) -> Dict:
        if not os.path.exists(self.path):
            return self._default()
        with open(self.path, "r") as f:
            return yaml.safe_load(f) or self._default()

    def save(self, state: Dict):
        state["last_updated"] = _now()
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            yaml.dump(state, f, sort_keys=False)
        os.replace(tmp, self.path)  # atomic on POSIX

    def update(self, **kwargs):
        state = self.load()
        state.update(kwargs)
        self.save(state)

    def _default(self) -> Dict:
        return {
            "phase": "init",
            "current_task_id": None,
            "tasks_completed": [],
            "tasks_escalated": [],
            "last_action": None,
            "last_updated": None,
            "notes": "",
        }


# ============================================================
# AGENT RUNNER  (#3 timeout+retry; #9 I/O logging; #13 injection guard)
# ============================================================

_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(all\s+|previous\s+|above\s+|prior\s+)?instructions?"),
    re.compile(r"(?i)you\s+are\s+now\s+"),
    re.compile(r"(?i)new\s+(role|persona|system\s+prompt|instructions?)"),
    re.compile(r"(?i)disregard\s+(the\s+|your\s+)?(above|previous|prior|system)"),
    re.compile(r"(?i)act\s+as\s+(if\s+you\s+are|a\s+new)"),
]


def _sanitize_text(text: str) -> str:
    """Strip prompt injection patterns from user-supplied content. (#13)"""
    for pattern in _INJECTION_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class AgentRunner:
    """
    Model-agnostic agent invocation via CLI subprocess.
    Change agent.command in project.yaml to swap models without code changes.
    """

    def __init__(self, command: str, timeout: int = 300,
                 max_retries: int = 3, agents_dir: str = "agents"):
        self.command = command
        self.timeout = timeout
        self.max_retries = max_retries
        self.agents_dir = agents_dir

    def _load_prompt(self, name: str) -> str:
        path = os.path.join(self.agents_dir, f"{name}.md")
        with open(path, "r") as f:
            return f.read()

    def _log_io(self, agent: str, task_id: str, prompt: str, output: str):  # (#9)
        log_dir = os.path.join("logs", task_id)
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        with open(os.path.join(log_dir, f"{ts}_{agent}.txt"), "w") as f:
            f.write(f"=== PROMPT ===\n{prompt}\n\n=== OUTPUT ===\n{output}\n")

    def _run(self, prompt: str, task_id: str = "unknown", agent: str = "agent") -> str:
        """Run CLI agent with timeout and exponential-backoff retry. (#3)"""
        last_error: Exception = RuntimeError("No attempts made")
        for attempt in range(self.max_retries):
            try:
                result = subprocess.run(
                    [self.command],
                    input=prompt.encode(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                )
                output = result.stdout.decode()
                if result.returncode != 0:
                    raise RuntimeError(
                        f"Agent exited {result.returncode}: {result.stderr.decode()[:200]}"
                    )
                self._log_io(agent, task_id, prompt, output)
                return output
            except subprocess.TimeoutExpired as e:
                last_error = e
                print(f"[Agent] {agent} timed out (attempt {attempt + 1}/{self.max_retries})")
            except Exception as e:
                last_error = e
                print(f"[Agent] {agent} error (attempt {attempt + 1}/{self.max_retries}): {e}")

            if attempt < self.max_retries - 1:
                backoff = 2 ** attempt
                print(f"[Agent] Retrying in {backoff}s...")
                time.sleep(backoff)

        raise RuntimeError(
            f"Agent {agent} failed after {self.max_retries} attempts: {last_error}"
        )

    def run_planner(self, prd_text: str) -> str:
        safe_prd = _sanitize_text(prd_text)  # (#13)
        prompt = self._load_prompt("planner") + "\n\nPRD:\n" + safe_prd
        return self._run(prompt, task_id="planning", agent="planner")

    def run_builder(self, task: Dict) -> str:
        prompt = self._load_prompt("builder") + "\n\nTASK:\n" + yaml.dump(task)
        return self._run(prompt, task_id=task["task_id"], agent="builder")

    def run_reviewer(self, task: Dict, builder_output: str) -> Dict:
        prompt = (
            self._load_prompt("reviewer")
            + "\n\nTASK:\n" + yaml.dump(task)
            + "\n\nBUILDER OUTPUT:\n" + builder_output
        )
        raw = self._run(prompt, task_id=task["task_id"], agent="reviewer")
        return self._parse_reviewer_output(raw, task["task_id"])

    def _parse_reviewer_output(self, raw: str, task_id: str) -> Dict:
        try:
            text = raw.strip()
            if "```yaml" in text:
                text = text.split("```yaml")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            parsed = yaml.safe_load(text)
            if not isinstance(parsed, dict) or "decision" not in parsed:
                raise ValueError("Missing 'decision' field")
            return parsed
        except Exception as e:
            print(f"[Reviewer] Parse error ({e}) — defaulting to REJECTED")
            return {
                "decision": "REJECTED",
                "task_id": task_id,
                "summary": "Reviewer output could not be parsed",
                "comments": [{"category": "parse_error", "severity": "blocking",
                               "detail": raw[:500]}],
                "next_action": "retry",
            }


# ============================================================
# GIT OPS  (#10 — worktree support for parallel tasks)
# ============================================================

class GitOps:

    def __init__(self, base_branch: str = "main"):
        self.base_branch = base_branch

    def _run(self, cmd: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)

    def is_dirty(self) -> bool:
        return bool(self._run(["git", "status", "--porcelain"]).stdout.strip())

    def checkout_base(self):
        self._run(["git", "checkout", self.base_branch])
        self._run(["git", "pull", "origin", self.base_branch])

    def create_branch(self, task_id: str) -> str:
        branch = f"feature/{task_id}"
        self._run(["git", "checkout", "-b", branch])
        return branch

    def commit_and_push(self, branch: str, message: str, cwd: Optional[str] = None):
        self._run(["git", "add", "."], cwd=cwd)
        self._run(["git", "commit", "-m", message], cwd=cwd)
        self._run(["git", "push", "-u", "origin", branch], cwd=cwd)

    def reset_workspace(self, cwd: Optional[str] = None):
        self._run(["git", "reset", "--hard"], cwd=cwd)
        self._run(["git", "clean", "-fd"], cwd=cwd)

    def create_worktree(self, task_id: str) -> tuple:
        """Isolated worktree for parallel task execution. (#10)"""
        branch = f"feature/{task_id}"
        path = os.path.join("/tmp", f"agentforge-{task_id}")
        self._run(["git", "worktree", "add", path, "-b", branch])
        return path, branch

    def remove_worktree(self, path: str):
        self._run(["git", "worktree", "remove", path, "--force"])
        self._run(["git", "worktree", "prune"])


# ============================================================
# SCM API — GitHub
# ============================================================

class GitHubAPI:

    def __init__(self, owner: str, repo: str, token: str, base_branch: str = "main"):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.base_branch = base_branch
        self._base = f"https://api.github.com/repos/{owner}/{repo}"

    def _headers(self) -> Dict:
        return {"Authorization": f"token {self.token}",
                "Accept": "application/vnd.github+json"}

    def create_pr(self, branch: str, title: str, body: str) -> Dict:
        r = requests.post(
            f"{self._base}/pulls",
            json={"title": title, "head": branch,
                  "base": self.base_branch, "body": body},
            headers=self._headers(),
        )
        return r.json()

    def merge_pr(self, pr_number: int) -> Dict:
        r = requests.put(f"{self._base}/pulls/{pr_number}/merge",
                         headers=self._headers())
        return r.json()


def _build_scm_api(cfg: ProjectConfig) -> GitHubAPI:
    if cfg.scm.type == "github":
        return GitHubAPI(
            owner=cfg.scm.owner, repo=cfg.scm.repo,
            token=os.getenv("GITHUB_TOKEN", ""),
            base_branch=cfg.scm.base_branch,
        )
    raise ValueError(f"Unsupported SCM type: {cfg.scm.type}")


# ============================================================
# PAT EXPIRY CHECK  (#14)
# ============================================================

def _check_pat_expiry(token: str):
    """Warn if GitHub PAT expires within 7 days. Non-blocking."""
    if not token:
        print("[Warning] GITHUB_TOKEN is not set.")
        return
    try:
        r = requests.get("https://api.github.com/user",
                         headers={"Authorization": f"token {token}"},
                         timeout=5)
        expiry_str = r.headers.get("github-authentication-token-expiration")
        if expiry_str:
            exp = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S UTC")
            days_left = (exp - datetime.utcnow()).days
            if days_left <= 7:
                print(f"[Warning] GitHub PAT expires in {days_left} day(s) — regenerate soon.")
    except Exception:
        pass  # Non-blocking


# ============================================================
# VALIDATION GATES
# ============================================================

def tests_exist() -> bool:
    return os.path.exists("tests") and bool(os.listdir("tests"))


def run_tests() -> bool:
    print("[Gate] Running pytest...")
    result = subprocess.run(["pytest", "-q"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print(result.stdout.decode())
    if result.stderr:
        print(result.stderr.decode())
    return result.returncode == 0


def validate_docker_build() -> bool:
    print("[Gate] Running docker compose build...")
    result = subprocess.run(["docker", "compose", "build"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stderr.decode())
    return result.returncode == 0


# ============================================================
# ORCHESTRATOR
# ============================================================

class Orchestrator:

    def __init__(self, config_path: str = "project.yaml"):
        self.cfg = load_config(config_path)
        self.queue = _build_queue(self.cfg)
        self.checkpoint = Checkpoint()
        self.agent = AgentRunner(
            command=self.cfg.agent.command,
            timeout=self.cfg.agent.timeout,
            max_retries=self.cfg.agent.max_retries,
        )
        self.git = GitOps(base_branch=self.cfg.scm.base_branch)
        self.scm = _build_scm_api(self.cfg)
        self.metrics = Metrics()
        self.max_retries = self.cfg.build.max_retries
        self.sleep_interval = self.cfg.build.sleep_interval
        self.max_global_iterations = self.cfg.build.max_global_iterations
        self.parallel = self.cfg.build.parallel_tasks
        self.max_workers = self.cfg.build.max_workers

        _check_pat_expiry(os.getenv("GITHUB_TOKEN", ""))  # (#14)

    # ---- plan phase ----

    def plan_phase(self):
        if self.queue.load_all():
            print(f"[Plan] Task queue populated — skipping planning.")
            return

        print("[Plan] Running Planner agent...")
        self.checkpoint.update(phase="planning", last_action="running_planner")

        with open(self.cfg.project.prd, "r") as f:
            prd_text = f.read()

        self.metrics.start("planner")
        raw = self.agent.run_planner(prd_text)
        self.metrics.stop("planner")

        try:
            plan = yaml.safe_load(raw)
            tasks = plan.get("tasks", [])
            for task in tasks:
                task.setdefault("status", "pending")
                task.setdefault("retry_count", 0)
                task.setdefault("comments", [])
            self.queue.replace_all(tasks)
            self.metrics.inc("tasks_planned", len(tasks))
            print(f"[Plan] Generated {len(tasks)} tasks: {[t['task_id'] for t in tasks]}")
        except Exception as e:
            print(f"[Plan] Failed to parse planner output: {e}\n{raw[:500]}")
            return

        self.checkpoint.update(phase="building", last_action="plan_complete")

    # ---- git state guard on resume  (#4) ----

    def _validate_workspace(self):
        if self.git.is_dirty():
            print("[Git] Dirty workspace on resume — resetting...")
            self.git.reset_workspace()
            self.checkpoint.update(last_action="workspace_reset_on_resume")

    # ---- single task execution ----

    def _execute_task(self, task: Dict) -> bool:
        """Builder → Reviewer → gates → commit. Returns True on success."""
        task_id = task["task_id"]
        self.queue.mark_in_progress(task_id)
        self.checkpoint.update(current_task_id=task_id, last_action=f"started_{task_id}")

        worktree_path: Optional[str] = None
        if self.parallel:
            worktree_path, branch = self.git.create_worktree(task_id)
        else:
            self.git.checkout_base()
            branch = self.git.create_branch(task_id)

        try:
            self.metrics.start(f"builder_{task_id}")
            builder_output = self.agent.run_builder(task)
            self.metrics.stop(f"builder_{task_id}")
            self.metrics.inc("builder_calls")

            self.metrics.start(f"reviewer_{task_id}")
            review = self.agent.run_reviewer(task, builder_output)
            self.metrics.stop(f"reviewer_{task_id}")
            self.metrics.inc("reviewer_calls")

            decision = review.get("decision", "REJECTED")
            print(f"[Reviewer] {decision} — {review.get('summary', '')}")

            if decision == "APPROVED":
                return self._handle_approved(task, branch, worktree_path)
            else:
                self._handle_rejected(task_id, review)
                return False
        finally:
            if worktree_path and os.path.exists(worktree_path):
                self.git.remove_worktree(worktree_path)

    # ---- build loop ----

    def build_loop(self):
        self._validate_workspace()  # (#4)
        iteration = 0

        while True:
            iteration += 1
            if iteration > self.max_global_iterations:  # (#5)
                print(f"[Loop] Global iteration limit ({self.max_global_iterations}) reached.")
                self.checkpoint.update(last_action="global_iteration_limit")
                self.metrics.inc("global_limit_hit")
                break

            if self.parallel:
                self._parallel_iteration()
            else:
                task = self.queue.get_next_task()
                if not task:
                    break
                print(f"\n{'='*60}")
                print(f"[Task] {task['task_id']}: {task['task_name']}  "
                      f"(retry {task.get('retry_count', 0)}/{self.max_retries})")
                if task.get("comments"):
                    print(f"[Task] {len(task['comments'])} prior comment(s) to address")
                self._execute_task(task)

            if not any(t.get("status") == "pending"
                       for t in self.queue.load_all()):
                break

            time.sleep(self.sleep_interval)

        self._finish()

    # ---- parallel iteration  (#10) ----

    def _parallel_iteration(self):
        state = self.checkpoint.load()
        completed_ids = state.get("tasks_completed", [])
        tasks = self.queue.get_independent_tasks(self.max_workers, completed_ids)
        if not tasks:
            return

        print(f"[Parallel] Dispatching {len(tasks)} independent task(s)...")
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._execute_task, t): t for t in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"[Parallel] {task['task_id']} raised: {e}")
                    self.queue.add_feedback(
                        task["task_id"],
                        [{"category": "execution_error", "severity": "blocking",
                          "detail": str(e)}],
                        self.max_retries,
                    )

    # ---- approved path ----

    def _handle_approved(self, task: Dict, branch: str,
                          worktree_path: Optional[str]) -> bool:
        task_id = task["task_id"]
        cwd = worktree_path

        if not tests_exist():
            self.git.reset_workspace(cwd=cwd)
            self._gate_feedback(task_id, "testing", "No test files found in tests/")
            self.metrics.inc("gate_failures_no_tests")
            return False

        if not run_tests():
            self.git.reset_workspace(cwd=cwd)
            self._gate_feedback(task_id, "testing", "pytest returned non-zero exit code")
            self.metrics.inc("gate_failures_tests")
            return False

        if not validate_docker_build():
            self.git.reset_workspace(cwd=cwd)
            self._gate_feedback(task_id, "docker", "docker compose build failed")
            self.metrics.inc("gate_failures_docker")
            return False

        self.git.commit_and_push(branch, f"{task_id}: {task['task_name']}", cwd=cwd)

        pr_body = (
            f"Auto-generated PR for `{task_id}`\n\n"
            f"**Objective:** {task.get('objective', '')}\n\n"
            "**Acceptance criteria:**\n" +
            "\n".join(f"- {c}" for c in task.get("acceptance_criteria", []))
        )
        pr = self.scm.create_pr(branch=branch, title=task["task_name"], body=pr_body)
        pr_number = pr.get("number")

        if pr_number:
            self.scm.merge_pr(pr_number)
            self.queue.mark_complete(task_id)
            state = self.checkpoint.load()
            completed = state.get("tasks_completed", [])
            completed.append(task_id)
            self.checkpoint.update(tasks_completed=completed, current_task_id=None,
                                   last_action=f"completed_{task_id}")
            self.metrics.inc("tasks_completed")
            print(f"[Done] {task_id} merged as PR #{pr_number}.")
            return True
        else:
            print(f"[Error] PR creation failed: {pr.get('message', pr)}")
            self.queue.update_task(task_id, {"status": "pending"})
            self.checkpoint.update(last_action=f"pr_failed_{task_id}")
            self.metrics.inc("pr_failures")
            return False

    # ---- rejected path ----

    def _handle_rejected(self, task_id: str, review: Dict):
        comments = review.get("comments", [])
        print(f"[Reviewer] Rejected — {len(comments)} comment(s):")
        for c in comments:
            print(f"  [{c.get('category')}] {c.get('detail', '')[:120]}")
        self.git.reset_workspace()
        self.queue.add_feedback(task_id, comments, self.max_retries)
        self.metrics.inc("tasks_rejected")

        task_after = next((t for t in self.queue.load_all()
                           if t["task_id"] == task_id), {})
        if task_after.get("status") == "escalated":
            state = self.checkpoint.load()
            escalated = state.get("tasks_escalated", [])
            if task_id not in escalated:
                escalated.append(task_id)
            self.checkpoint.update(tasks_escalated=escalated,
                                   last_action=f"escalated_{task_id}")
            self.metrics.inc("tasks_escalated")
            print(f"[Escalated] {task_id} needs human intervention.")
        else:
            retry = task_after.get("retry_count", "?")
            self.checkpoint.update(last_action=f"rejected_{task_id}_retry{retry}")

    # ---- helpers ----

    def _gate_feedback(self, task_id: str, category: str, detail: str):
        self.queue.add_feedback(
            task_id,
            [{"category": category, "severity": "blocking", "detail": detail}],
            self.max_retries,
        )
        self.checkpoint.update(last_action=f"gate_failed_{category}_{task_id}")

    def _finish(self):
        escalated = [t["task_id"] for t in self.queue.load_all()
                     if t.get("status") == "escalated"]
        if escalated:
            print(f"\n[Loop] Finished with escalations — human review needed: {escalated}")
        else:
            print("\n[Loop] All tasks completed.")
        self.checkpoint.update(phase="complete", tasks_escalated=escalated,
                               last_action="loop_done")
        self.metrics.save()

    # ---- entry points ----

    def plan(self):
        state = self.checkpoint.load()
        print(f"[Init] Phase: {state['phase']} | Last: {state['last_action']}")
        self.plan_phase()

    def run(self):
        state = self.checkpoint.load()
        print(f"[Init] Phase: {state['phase']} | Last: {state['last_action']}")
        print(f"[Init] Completed: {state.get('tasks_completed', [])} | "
              f"Escalated: {state.get('tasks_escalated', [])}")
        if state["phase"] in ("init", "planning"):
            self.plan_phase()
        self.build_loop()


# ============================================================
# HELPERS
# ============================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentForge Orchestrator")
    parser.add_argument("--plan-only", action="store_true",
                        help="Run Planner only then exit")
    parser.add_argument("--config", default="project.yaml",
                        help="Path to project config (default: project.yaml)")
    args = parser.parse_args()

    orch = Orchestrator(config_path=args.config)
    if args.plan_only:
        orch.plan()
    else:
        orch.run()
