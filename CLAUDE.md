# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is a **generic agentic build framework**. It orchestrates three agents (Planner, Builder, Reviewer) to incrementally build any software project from a PRD. Project-specific context lives in `context/` — never in this file.

---

## Initialization Protocol

**Every session start or reinit, read in this exact order:**

1. This file
2. `context/PROJECT_CONTEXT.md` — project purpose, architecture decisions, current status
3. `context/CHECKPOINT.yaml` — last phase, last completed task, what to do next
4. `project.yaml` — SCM config, agent command, build settings

The checkpoint + context files give you full situational awareness without scanning the repo. Do not skip them.

**Before context compaction:** write the current task state and phase to `context/CHECKPOINT.yaml` using the checkpoint format below.

---

## Commands

```bash
make init         # Pull framework repo, create venv, install deps
make plan         # Run Planner agent against PRD.md → writes tasks/task_queue.yaml
make run          # Run full build loop (plan if needed, then build → review → commit)
make test         # Run pytest
make checkpoint   # Force-save current checkpoint to context/CHECKPOINT.yaml
make clean        # Remove venv and cache
```

Run a single test:
```bash
. .venv/bin/activate && pytest tests/<file>::<test_name> -q
```

---

## Framework Architecture

### Agent Roles

| Agent | Prompt file | Input | Output |
|---|---|---|---|
| Planner | `agents/planner.md` | PRD.md | `tasks/task_queue.yaml` |
| Builder | `agents/builder.md` | One task from queue | Code + tests + docker changes + commit msg |
| Reviewer | `agents/reviewer.md` | Task + builder output | Structured YAML: `APPROVED` or `REJECTED` with comments |

### Orchestrator Loop

```
[init]  load project.yaml → load checkpoint → resume from last phase
  ↓
[plan]  if task queue empty: Planner reads PRD → writes task_queue.yaml → checkpoint
  ↓
[loop]  for each pending task:
          Builder → Reviewer
            ├─ APPROVED → pytest gate → docker gate → commit → PR → merge → checkpoint
            └─ REJECTED → write comments to task → reset pending → retry
                            └─ retry_count >= max_retries → escalated → pause for human
  ↓
[done]  all tasks completed or escalated → final checkpoint
```

### TaskQueue (`orchestrator.py: TaskQueue`)

Abstracts storage. Currently YAML-backed (`tasks/task_queue.yaml`). Interface is designed for future swap to Redis/SQS without changing orchestrator logic:

- `get_next_task()` — next `pending` task
- `update_task(task_id, updates)`
- `add_feedback(task_id, comments)` — appends reviewer comments, increments `retry_count`, escalates at max
- `mark_complete(task_id)`
- `mark_in_progress(task_id)`

### Task Status Flow

```
pending → in_progress → [APPROVED + gates pass] → completed
                      → [REJECTED or gate fail]  → pending (comments added, retry_count++)
                                                  → escalated (retry_count >= max_retries)
```

### Reviewer Output (parsed YAML, not string match)

```yaml
decision: APPROVED | REJECTED
task_id: T1
summary: "one-line summary"
comments:                          # empty list if APPROVED
  - category: testing | docker | architecture | code_quality | config
    severity: blocking | warning
    detail: "specific actionable issue"
next_action: retry | escalate      # only on REJECTED
```

### Checkpoint Format (`context/CHECKPOINT.yaml`)

```yaml
phase: init | planning | building | complete
current_task_id: T3
tasks_completed: [T1, T2]
tasks_escalated: []
last_action: "completed_T2"
last_updated: "2026-04-24T10:00:00"
notes: "free-form notes for next session"
```

---

## Configuration (`project.yaml`)

All project-specific settings. Committed to the project repo SCM.

```yaml
project:
  name: <project name>
  prd: PRD.md

scm:
  type: github          # github | gitlab | bitbucket
  owner: <username>
  repo: <repo-name>
  base_branch: main

agent:
  command: claude       # CLI command — swap to any LLM CLI without code changes

build:
  max_retries: 3        # per task before escalation
  sleep_interval: 2     # seconds between tasks
```

Environment variables (`.env`, never committed):
- `GITHUB_TOKEN` — SCM authentication token
- Any project-specific secrets

---

## File Ownership

| File | Belongs to | In project SCM? | Notes |
|---|---|---|---|
| `PRD.md` | Project | Yes | Fill from `PRD_TEMPLATE.md` |
| `project.yaml` | Project | Yes | SCM, agent, build config |
| `CLAUDE.md` | Framework → Project | **Yes** | Copied from framework on first `make init`; commit and customise freely |
| `Makefile` | Project | Yes | Bootstrap only — framework targets loaded via `-include` |
| `context/PROJECT_CONTEXT.md` | Project | Yes | Update after each milestone |
| `context/CHECKPOINT.yaml` | Project | Yes | Auto-managed by orchestrator |
| `tasks/task_queue.yaml` | Project (runtime) | Yes | Auto-managed by orchestrator |
| `.env` | Project (secrets) | No | Never commit |
| `orchestrator.py` | Framework | No (gitignored) | Synced on `make init` |
| `agents/` | Framework | No (gitignored) | Synced on `make init` |
| `requirements.txt` | Framework | No (gitignored) | Synced on `make init` |
| `docker-compose.redis.yml` | Framework | No (gitignored) | Synced on `make init` |
| `.framework/` | Framework repo clone | No (gitignored) | Pulled by `make init` |

**Customising CLAUDE.md:** The framework ships a generic `CLAUDE.md`. After `make init` copies it to your project root, append a `## Project Notes` section with anything project-specific. The framework's generic sections stay unchanged — update them by editing `framework/CLAUDE.md` in the AgentForge repo and running `make init` again (it will not overwrite if the file already exists).
