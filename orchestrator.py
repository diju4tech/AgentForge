"""
Agentic Build Framework — Orchestrator
Coordinates Planner → Builder → Reviewer agents in a task loop.
Config is loaded from project.yaml. State is persisted in context/CHECKPOINT.yaml.
"""

import os
import subprocess
import yaml
import time
import argparse
import requests
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# CONFIG
# ============================================================

def load_config(path: str = "project.yaml") -> Dict[str, Any]:
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ============================================================
# TASK QUEUE
# Abstraction over storage backend. Currently YAML-backed.
# To swap backend (Redis, SQS, etc.): subclass and implement
# the same public interface — orchestrator logic stays unchanged.
# ============================================================

class TaskQueue:

    def __init__(self, path: str = "tasks/task_queue.yaml"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    # --- internal ---

    def _load(self) -> Dict:
        if not os.path.exists(self.path):
            return {"tasks": []}
        with open(self.path, "r") as f:
            return yaml.safe_load(f) or {"tasks": []}

    def _save(self, data: Dict):
        with open(self.path, "w") as f:
            yaml.dump(data, f, sort_keys=False)

    # --- public interface ---

    def get_next_task(self) -> Optional[Dict]:
        """Return the next pending task, or None if queue is empty/done."""
        for task in self._load().get("tasks", []):
            if task.get("status") == "pending":
                return task
        return None

    def update_task(self, task_id: str, updates: Dict):
        data = self._load()
        for task in data.get("tasks", []):
            if task["task_id"] == task_id:
                task.update(updates)
        self._save(data)

    def add_feedback(self, task_id: str, comments: List[Dict], max_retries: int = 3):
        """Append reviewer comments, increment retry counter, escalate if limit hit."""
        data = self._load()
        for task in data.get("tasks", []):
            if task["task_id"] == task_id:
                task.setdefault("comments", [])
                task["comments"].extend(comments)
                task["retry_count"] = task.get("retry_count", 0) + 1
                if task["retry_count"] >= max_retries:
                    task["status"] = "escalated"
                    print(f"[Queue] Task {task_id} escalated after {task['retry_count']} retries.")
                else:
                    task["status"] = "pending"
        self._save(data)

    def mark_in_progress(self, task_id: str):
        self.update_task(task_id, {
            "status": "in_progress",
            "started_at": _now(),
        })

    def mark_complete(self, task_id: str):
        self.update_task(task_id, {
            "status": "completed",
            "completed_at": _now(),
        })

    def load_all(self) -> List[Dict]:
        return self._load().get("tasks", [])

    def replace_all(self, tasks: List[Dict]):
        """Replace entire task list (called after planning)."""
        self._save({"tasks": tasks})

    def has_escalated(self) -> bool:
        return any(t.get("status") == "escalated" for t in self.load_all())

    def summary(self) -> Dict:
        tasks = self.load_all()
        counts = {}
        for t in tasks:
            s = t.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return counts


# ============================================================
# CHECKPOINT
# Persists orchestrator execution state between sessions.
# Designed for migration to a queuing/state system later.
# ============================================================

class Checkpoint:

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
        with open(self.path, "w") as f:
            yaml.dump(state, f, sort_keys=False)

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
# AGENT RUNNER
# Model-agnostic — invokes any LLM CLI via stdin/stdout.
# Change `command` in project.yaml to swap models.
# ============================================================

class AgentRunner:

    def __init__(self, command: str, agents_dir: str = "agents"):
        self.command = command
        self.agents_dir = agents_dir

    def _load_prompt(self, name: str) -> str:
        path = os.path.join(self.agents_dir, f"{name}.md")
        with open(path, "r") as f:
            return f.read()

    def _run(self, prompt: str) -> str:
        result = subprocess.run(
            [self.command],
            input=prompt.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            print(f"[Agent] stderr: {result.stderr.decode()}")
        return result.stdout.decode()

    def run_planner(self, prd_text: str) -> str:
        prompt = self._load_prompt("planner") + "\n\nPRD:\n" + prd_text
        return self._run(prompt)

    def run_builder(self, task: Dict) -> str:
        prompt = self._load_prompt("builder") + "\n\nTASK:\n" + yaml.dump(task)
        return self._run(prompt)

    def run_reviewer(self, task: Dict, builder_output: str) -> Dict:
        prompt = (
            self._load_prompt("reviewer")
            + "\n\nTASK:\n" + yaml.dump(task)
            + "\n\nBUILDER OUTPUT:\n" + builder_output
        )
        raw = self._run(prompt)
        return self._parse_reviewer_output(raw, task["task_id"])

    def _parse_reviewer_output(self, raw: str, task_id: str) -> Dict:
        """Parse structured YAML from reviewer. Defaults to REJECTED on parse failure."""
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
            print(f"[Reviewer] Parse error: {e}\nRaw output:\n{raw}")
            return {
                "decision": "REJECTED",
                "task_id": task_id,
                "summary": "Reviewer output could not be parsed",
                "comments": [{"category": "parse_error", "severity": "blocking", "detail": raw[:500]}],
                "next_action": "retry",
            }


# ============================================================
# GIT OPS
# ============================================================

class GitOps:

    def __init__(self, base_branch: str = "main"):
        self.base_branch = base_branch

    def _run(self, cmd: List[str]):
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def checkout_base(self):
        self._run(["git", "checkout", self.base_branch])
        self._run(["git", "pull", "origin", self.base_branch])

    def create_branch(self, task_id: str) -> str:
        branch = f"feature/{task_id}"
        self._run(["git", "checkout", "-b", branch])
        return branch

    def commit_and_push(self, branch: str, message: str):
        self._run(["git", "add", "."])
        self._run(["git", "commit", "-m", message])
        self._run(["git", "push", "-u", "origin", branch])

    def reset_workspace(self):
        self._run(["git", "reset", "--hard"])
        self._run(["git", "clean", "-fd"])


# ============================================================
# SCM API — GitHub implementation
# Extend with GitLabAPI / BitbucketAPI implementing same interface
# ============================================================

class GitHubAPI:

    def __init__(self, owner: str, repo: str, token: str, base_branch: str = "main"):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.base_branch = base_branch
        self._base = f"https://api.github.com/repos/{owner}/{repo}"

    def _headers(self) -> Dict:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def create_pr(self, branch: str, title: str, body: str) -> Dict:
        r = requests.post(
            f"{self._base}/pulls",
            json={"title": title, "head": branch, "base": self.base_branch, "body": body},
            headers=self._headers(),
        )
        return r.json()

    def merge_pr(self, pr_number: int) -> Dict:
        r = requests.put(
            f"{self._base}/pulls/{pr_number}/merge",
            headers=self._headers(),
        )
        return r.json()

    def add_pr_comment(self, pr_number: int, body: str) -> Dict:
        r = requests.post(
            f"{self._base}/issues/{pr_number}/comments",
            json={"body": body},
            headers=self._headers(),
        )
        return r.json()


def _build_scm_api(scm_cfg: Dict) -> GitHubAPI:
    """Factory — extend here to support gitlab/bitbucket."""
    scm_type = scm_cfg.get("type", "github")
    if scm_type == "github":
        return GitHubAPI(
            owner=scm_cfg.get("owner", ""),
            repo=scm_cfg.get("repo", ""),
            token=os.getenv("GITHUB_TOKEN", ""),
            base_branch=scm_cfg.get("base_branch", "main"),
        )
    raise ValueError(f"Unsupported SCM type: {scm_type}")


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
    result = subprocess.run(
        ["docker", "compose", "build"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        print(result.stderr.decode())
    return result.returncode == 0


# ============================================================
# ORCHESTRATOR
# ============================================================

class Orchestrator:

    def __init__(self, config_path: str = "project.yaml"):
        cfg = load_config(config_path)

        scm_cfg = cfg.get("scm", {})
        build_cfg = cfg.get("build", {})
        agent_cfg = cfg.get("agent", {})
        project_cfg = cfg.get("project", {})

        self.queue = TaskQueue()
        self.checkpoint = Checkpoint()
        self.agent = AgentRunner(command=agent_cfg.get("command", "claude"))
        self.git = GitOps(base_branch=scm_cfg.get("base_branch", "main"))
        self.scm = _build_scm_api(scm_cfg)

        self.max_retries = build_cfg.get("max_retries", 3)
        self.sleep_interval = build_cfg.get("sleep_interval", 2)
        self.prd_path = project_cfg.get("prd", "PRD.md")

    # --- plan phase ---

    def plan_phase(self):
        tasks = self.queue.load_all()
        if tasks:
            print(f"[Plan] Task queue has {len(tasks)} tasks — skipping planning.")
            return

        print("[Plan] Task queue empty — running Planner agent...")
        self.checkpoint.update(phase="planning", last_action="running_planner")

        with open(self.prd_path, "r") as f:
            prd_text = f.read()

        raw = self.agent.run_planner(prd_text)

        try:
            plan = yaml.safe_load(raw)
            tasks = plan.get("tasks", [])
            for task in tasks:
                task.setdefault("status", "pending")
                task.setdefault("retry_count", 0)
                task.setdefault("comments", [])
            self.queue.replace_all(tasks)
            print(f"[Plan] Generated {len(tasks)} tasks: {[t['task_id'] for t in tasks]}")
        except Exception as e:
            print(f"[Plan] Failed to parse planner output: {e}\n{raw}")
            return

        self.checkpoint.update(phase="building", last_action="plan_complete")

    # --- build loop ---

    def build_loop(self):
        while True:
            task = self.queue.get_next_task()

            if not task:
                if self.queue.has_escalated():
                    print("\n[Loop] One or more tasks are escalated — human review required.")
                    escalated = [t["task_id"] for t in self.queue.load_all() if t.get("status") == "escalated"]
                    self.checkpoint.update(
                        phase="complete",
                        tasks_escalated=escalated,
                        last_action="loop_ended_with_escalations",
                    )
                else:
                    print("\n[Loop] All tasks completed.")
                    self.checkpoint.update(phase="complete", last_action="all_tasks_done")
                break

            task_id = task["task_id"]
            print(f"\n{'='*60}")
            print(f"[Task] {task_id}: {task['task_name']}")
            print(f"[Task] Retry: {task.get('retry_count', 0)}/{self.max_retries}")
            if task.get("comments"):
                print(f"[Task] Prior feedback: {len(task['comments'])} comment(s)")

            self.queue.mark_in_progress(task_id)
            self.checkpoint.update(current_task_id=task_id, last_action=f"started_{task_id}")

            self.git.checkout_base()
            branch = self.git.create_branch(task_id)

            # BUILD
            print(f"[Builder] Building {task_id}...")
            builder_output = self.agent.run_builder(task)

            # REVIEW
            print(f"[Reviewer] Reviewing {task_id}...")
            review = self.agent.run_reviewer(task, builder_output)
            decision = review.get("decision", "REJECTED")
            print(f"[Reviewer] Decision: {decision} — {review.get('summary', '')}")

            if decision == "APPROVED":
                self._handle_approved(task, branch)
            else:
                self._handle_rejected(task_id, review)

            time.sleep(self.sleep_interval)

    def _handle_approved(self, task: Dict, branch: str):
        task_id = task["task_id"]

        # Test gate
        if not tests_exist():
            print("[Gate] No tests found.")
            self.git.reset_workspace()
            self.queue.add_feedback(task_id, [
                {"category": "testing", "severity": "blocking", "detail": "No test files found in tests/"}
            ], self.max_retries)
            self.checkpoint.update(last_action=f"gate_failed_no_tests_{task_id}")
            return

        if not run_tests():
            print("[Gate] Tests failed.")
            self.git.reset_workspace()
            self.queue.add_feedback(task_id, [
                {"category": "testing", "severity": "blocking", "detail": "pytest returned non-zero exit code"}
            ], self.max_retries)
            self.checkpoint.update(last_action=f"gate_failed_tests_{task_id}")
            return

        # Docker gate
        if not validate_docker_build():
            print("[Gate] Docker build failed.")
            self.git.reset_workspace()
            self.queue.add_feedback(task_id, [
                {"category": "docker", "severity": "blocking", "detail": "docker compose build failed"}
            ], self.max_retries)
            self.checkpoint.update(last_action=f"gate_failed_docker_{task_id}")
            return

        # Commit + PR + Merge
        commit_msg = f"{task_id}: {task['task_name']}"
        self.git.commit_and_push(branch, commit_msg)

        pr_body = (
            f"Auto-generated PR for task `{task_id}`\n\n"
            f"**Objective:** {task.get('objective', '')}\n\n"
            f"**Acceptance criteria:**\n" +
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
            self.checkpoint.update(
                tasks_completed=completed,
                current_task_id=None,
                last_action=f"completed_{task_id}",
            )
            print(f"[Done] Task {task_id} merged as PR #{pr_number}.")
        else:
            print(f"[Error] PR creation failed: {pr.get('message', pr)}")
            self.queue.update_task(task_id, {"status": "pending"})
            self.checkpoint.update(last_action=f"pr_failed_{task_id}")

    def _handle_rejected(self, task_id: str, review: Dict):
        comments = review.get("comments", [])
        print(f"[Reviewer] Rejected with {len(comments)} comment(s).")
        for c in comments:
            print(f"  [{c.get('category')}] {c.get('detail')}")

        self.git.reset_workspace()
        self.queue.add_feedback(task_id, comments, self.max_retries)

        task_after = next((t for t in self.queue.load_all() if t["task_id"] == task_id), {})
        if task_after.get("status") == "escalated":
            state = self.checkpoint.load()
            escalated = state.get("tasks_escalated", [])
            if task_id not in escalated:
                escalated.append(task_id)
            self.checkpoint.update(
                tasks_escalated=escalated,
                last_action=f"escalated_{task_id}",
            )
            print(f"[Escalated] Task {task_id} needs human intervention.")
        else:
            retry = task_after.get("retry_count", "?")
            self.checkpoint.update(last_action=f"rejected_{task_id}_retry{retry}")

    # --- entry points ---

    def plan(self):
        state = self.checkpoint.load()
        print(f"[Init] Phase: {state['phase']} | Last: {state['last_action']}")
        self.plan_phase()

    def run(self):
        state = self.checkpoint.load()
        print(f"[Init] Phase: {state['phase']} | Last: {state['last_action']}")
        print(f"[Init] Completed: {state.get('tasks_completed', [])} | Escalated: {state.get('tasks_escalated', [])}")

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
    parser = argparse.ArgumentParser(description="Agentic Build Framework Orchestrator")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Run planner agent only (populate task queue from PRD), then exit",
    )
    parser.add_argument(
        "--config",
        default="project.yaml",
        help="Path to project config file (default: project.yaml)",
    )
    args = parser.parse_args()

    orch = Orchestrator(config_path=args.config)
    if args.plan_only:
        orch.plan()
    else:
        orch.run()
