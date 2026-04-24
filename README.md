# AgentForge

A generic multi-agent framework that builds software applications iteratively from a PRD.
Three agents — Planner, Builder, Reviewer — run in a loop until all tasks are complete.

---

## How it works

```
PRD.md
  └─► Planner agent  →  tasks/task_queue.yaml
                              │
                    ┌─────────┘
                    ▼
              Builder agent  →  code + tests
                    │
                    ▼
             Reviewer agent  →  APPROVED / REJECTED (structured YAML)
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
    pytest gate          REJECTED: add comments
    docker gate          to task, retry (up to
    git commit           max_retries), then escalate
    PR create + merge
```

The loop runs until all tasks are `completed` or `escalated`. State is checkpointed after every significant action so the loop can resume from where it stopped.

---

## Quickstart

### 1. Create your project repo

```bash
mkdir my-project && cd my-project
git init
```

### 2. Add the minimum project files

**`PRD.md`** — copy `PRD_TEMPLATE.md` from this repo, fill in every `[REQUIRED]` section.

**`project.yaml`**:
```yaml
project:
  name: My Project
  prd: PRD.md

scm:
  type: github
  owner: your-username
  repo: your-repo
  base_branch: main

agent:
  command: claude       # any LLM CLI — swap without code changes
  timeout: 300
  max_retries: 3

build:
  max_retries: 3
  sleep_interval: 2
  max_global_iterations: 50
  parallel_tasks: false
```

**`Makefile`** (copy from this repo's example or write):
```makefile
FRAMEWORK_REPO = https://github.com/diju4tech/AgentForge
FRAMEWORK_VERSION = main    # or pin to a tag: v1.1.0
FRAMEWORK_DIR = .framework
...
```

**`.env`**:
```
GITHUB_TOKEN=your_pat_here
```

**`.gitignore`** — must include:
```
.framework/
orchestrator.py
agents/
requirements.txt
.venv/
.env
```

### 3. Initialise

```bash
make init     # clones framework, creates venv, installs deps
make plan     # Planner reads PRD.md → writes tasks/task_queue.yaml
make run      # Builder → Reviewer loop starts
```

---

## Commands

| Command | Description |
|---|---|
| `make init` | Pull framework, create venv, install dependencies |
| `make plan` | Run Planner agent only — populate task queue from PRD |
| `make run` | Run full loop (plans if queue empty, then builds) |
| `make test` | Run pytest against project tests |
| `make checkpoint` | Force-save current state to `context/CHECKPOINT.yaml` |
| `make status` | Print task queue summary |
| `make clean` | Remove venv and cache |

---

## Configuration reference (`project.yaml`)

| Key | Default | Description |
|---|---|---|
| `project.name` | `"Unnamed Project"` | Project display name |
| `project.prd` | `"PRD.md"` | Path to PRD file |
| `scm.type` | `"github"` | SCM provider (`github` only today) |
| `scm.owner` | — | GitHub username / org |
| `scm.repo` | — | Repository name |
| `scm.base_branch` | `"main"` | Branch to merge PRs into |
| `agent.command` | `"claude"` | CLI command to invoke the LLM |
| `agent.timeout` | `300` | Seconds before an agent call times out |
| `agent.max_retries` | `3` | Retries on agent timeout/error |
| `build.max_retries` | `3` | Per-task retries before escalation |
| `build.sleep_interval` | `2` | Seconds between task iterations |
| `build.max_global_iterations` | `50` | Hard stop on total loop iterations |
| `build.parallel_tasks` | `false` | Run independent tasks simultaneously |
| `build.max_workers` | `2` | Worker threads when parallel is enabled |
| `build.queue_backend` | `"yaml"` | `"yaml"` or `"redis"` |
| `build.redis.host` | `"localhost"` | Redis host (if queue_backend=redis) |
| `build.redis.port` | `6379` | Redis port |
| `build.redis.db` | `0` | Redis DB index |

---

## Task status flow

```
pending  →  in_progress  →  [APPROVED + gates pass]  →  completed
                          →  [REJECTED or gate fail]  →  pending   (retry_count++)
                                                       →  escalated (retry_count >= max_retries)
```

Escalated tasks need human review. Inspect `tasks/task_queue.yaml` for comments explaining why.

---

## Parallel task execution

Set `build.parallel_tasks: true` and `build.max_workers: N` in `project.yaml`.

Independent tasks (those whose `dependencies` are all in `completed`) will run simultaneously using git worktrees — each task gets an isolated directory so agents don't interfere with each other.

---

## Switching LLM backends

Change `agent.command` in `project.yaml` — the orchestrator pipes prompts to the CLI via stdin and reads stdout. No code changes needed:

```yaml
agent:
  command: claude      # Anthropic Claude Code CLI
  # command: gpt4      # any CLI that reads stdin, writes stdout
```

---

## Switching to Redis task queue

1. Uncomment `redis` in `requirements.txt` and run `make init`
2. Update `project.yaml`:
```yaml
build:
  queue_backend: redis
  redis:
    host: redis
    port: 6379
    db: 0
```
3. Add a Redis service to your `docker-compose.yml`

---

## Agent I/O logs

Every agent call is logged to `logs/<task_id>/<timestamp>_<agent>.txt` containing the full prompt and output. Use these to debug rejected tasks without re-running.

---

## Metrics

After each loop run, `logs/metrics.yaml` is written with:
- Task counts (planned, completed, rejected, escalated)
- Agent latency (avg, max, total per agent per task)
- Gate failure counts (tests, docker, no-tests)

---

## Versioning

Pin your project to a specific framework version in the Makefile:

```makefile
FRAMEWORK_VERSION = v1.1.0
# then in _pull_framework:
git clone --branch $(FRAMEWORK_VERSION) $(FRAMEWORK_REPO) $(FRAMEWORK_DIR)
```

See [releases](https://github.com/diju4tech/AgentForge/releases) for available versions.

---

## Deferred improvements

See [`TODO.md`](TODO.md) for planned features (process supervision, structured logging, notifications).

---

## Framework file layout

```
framework/
├── orchestrator.py       Main orchestrator — all agent/git/queue/checkpoint logic
├── agents/
│   ├── planner.md        Planner agent system prompt
│   ├── builder.md        Builder agent system prompt
│   └── reviewer.md       Reviewer agent system prompt
├── tests/
│   ├── test_task_queue.py
│   ├── test_checkpoint.py
│   └── test_agent_runner.py
├── PRD_TEMPLATE.md       Copy to project root as PRD.md and fill in
├── requirements.txt
├── VERSION
└── TODO.md               Deferred production improvements
```
