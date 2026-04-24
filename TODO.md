# AgentForge — Deferred Improvements

Items listed here are scoped, prioritized, and ready to be picked up. Each entry includes full implementation detail so no prior context is needed.

---

## TODO-1 — Process Supervision

**Priority:** P1
**Area:** Loop Reliability
**File(s) to change:** `orchestrator.py`, add `supervisord.conf` or `docker-compose.yml` at framework level

### Problem
The orchestrator runs as a plain Python process with no crash recovery. If it dies mid-task:
- The git branch may be left in a dirty state
- The checkpoint reflects the last saved state, not the crash point
- The process must be restarted manually

### Required Changes

#### 1. Graceful shutdown + checkpoint on crash (`orchestrator.py`)
Wrap the main loop in a signal handler and top-level exception handler that checkpoints before exit:

```python
import signal

class Orchestrator:
    def _handle_signal(self, signum, frame):
        print(f"[Orchestrator] Signal {signum} received — checkpointing before exit...")
        self.checkpoint.update(last_action="interrupted_by_signal")
        raise SystemExit(0)

    def run(self):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        try:
            ...
        except Exception as e:
            self.checkpoint.update(last_action=f"crashed: {str(e)}")
            raise
```

#### 2. Supervisor config (`framework/supervisord.conf`)
```ini
[supervisord]
nodaemon=true
logfile=/var/log/agentforge/supervisord.log

[program:orchestrator]
command=python orchestrator.py
directory=/app
autostart=true
autorestart=true
startretries=5
stderr_logfile=/var/log/agentforge/orchestrator.err.log
stdout_logfile=/var/log/agentforge/orchestrator.out.log
environment=HOME="/root"
```

#### 3. Docker service mode (`framework/Dockerfile.orchestrator`)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt supervisor
COPY . .
CMD ["supervisord", "-c", "supervisord.conf"]
```

Add to `docker-compose.yml` in the project:
```yaml
services:
  orchestrator:
    build:
      context: .
      dockerfile: .framework/framework/Dockerfile.orchestrator
    restart: always
    volumes:
      - .:/app
    env_file: .env
```

### Acceptance Criteria
- [ ] `SIGTERM` and `SIGINT` trigger a checkpoint write before exit
- [ ] Unhandled exceptions checkpoint before propagating
- [ ] `docker compose up orchestrator` runs the loop as a supervised daemon
- [ ] Killing the container and restarting resumes from last checkpoint

---

## TODO-6 — Structured Logging

**Priority:** P2
**Area:** Observability
**File(s) to change:** `orchestrator.py`

### Problem
All output uses `print()`. In production:
- No log levels (can't filter warnings vs info vs errors)
- No structured fields (can't query by `task_id` or `phase` in a log aggregator)
- No timestamps
- Cannot route to CloudWatch, Datadog, Loki, or any log sink

### Required Changes

#### 1. Replace all `print()` with structured logger (`orchestrator.py`)

Add a logger factory at the top of `orchestrator.py`:

```python
import logging
import json

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        # Merge any extra fields passed via extra={}
        for key in ("task_id", "phase", "agent", "action"):
            if hasattr(record, key):
                log[key] = getattr(record, key)
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        return json.dumps(log)

def get_logger(name: str = "agentforge") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    return logger

logger = get_logger()
```

Replace all `print(...)` calls with:
```python
logger.info("Task started", extra={"task_id": task_id, "phase": "building"})
logger.error("Tests failed", extra={"task_id": task_id, "action": "test_gate"})
```

#### 2. Add `LOG_LEVEL` to `.env.example`
```
LOG_LEVEL=INFO   # DEBUG | INFO | WARNING | ERROR
```

#### 3. Add `LOG_LEVEL` to `project.yaml` (optional override)
```yaml
build:
  log_level: INFO
```

### Log sink integration (no code change needed)
- **Docker + Loki:** Use `docker compose` logging driver `loki`
- **AWS CloudWatch:** Use `awslogs` logging driver in `docker-compose.yml`
- **Datadog:** Mount the Datadog agent and set `DD_LOGS_ENABLED=true`

### Acceptance Criteria
- [ ] All orchestrator output is valid JSON, one object per line
- [ ] Every log line includes `timestamp`, `level`, `message`
- [ ] Task-specific logs include `task_id`
- [ ] `LOG_LEVEL=DEBUG` surfaces agent prompt/response details
- [ ] `LOG_LEVEL=ERROR` suppresses all info output

---

## TODO-8 — Notifications on Escalation and Completion

**Priority:** P2
**Area:** Observability
**File(s) to change:** `orchestrator.py`, `project.yaml`

### Problem
When a task is escalated (max retries exceeded) or the full loop completes, nobody is notified. The operator must poll the checkpoint file manually.

### Required Changes

#### 1. Add `notifications` block to `project.yaml`
```yaml
notifications:
  enabled: true
  webhook_url: ""          # Slack / Teams / custom HTTP endpoint
  on_escalation: true      # notify when a task is escalated
  on_completion: true      # notify when all tasks are done
  on_task_complete: false  # notify after each individual task (noisy — off by default)
```

#### 2. Add `Notifier` class to `orchestrator.py`

```python
class Notifier:
    """Sends webhook notifications. Extend with email/PagerDuty as needed."""

    def __init__(self, config: Dict):
        self.enabled = config.get("enabled", False)
        self.webhook_url = config.get("webhook_url", "")
        self.on_escalation = config.get("on_escalation", True)
        self.on_completion = config.get("on_completion", True)
        self.on_task_complete = config.get("on_task_complete", False)

    def _send(self, message: str):
        if not self.enabled or not self.webhook_url:
            return
        try:
            requests.post(
                self.webhook_url,
                json={"text": message},   # Slack-compatible payload
                timeout=10,
            )
        except Exception as e:
            print(f"[Notifier] Failed to send notification: {e}")

    def task_completed(self, task_id: str, task_name: str, pr_number: int):
        if self.on_task_complete:
            self._send(f"[AgentForge] Task {task_id} completed — PR #{pr_number} merged: {task_name}")

    def task_escalated(self, task_id: str, task_name: str, comments: List[Dict]):
        if self.on_escalation:
            issues = "\n".join(f"• [{c.get('category')}] {c.get('detail')}" for c in comments)
            self._send(
                f"[AgentForge] ESCALATION: Task {task_id} needs human review.\n"
                f"Task: {task_name}\nIssues:\n{issues}"
            )

    def loop_completed(self, completed: List[str], escalated: List[str]):
        if self.on_completion:
            status = "SUCCESS" if not escalated else "PARTIAL — some tasks escalated"
            self._send(
                f"[AgentForge] Build loop finished — {status}\n"
                f"Completed: {completed}\nEscalated: {escalated}"
            )
```

#### 3. Wire `Notifier` into `Orchestrator`

In `Orchestrator.__init__`:
```python
self.notifier = Notifier(cfg.get("notifications", {}))
```

In `_handle_approved` after merge:
```python
self.notifier.task_completed(task_id, task["task_name"], pr_number)
```

In `_handle_rejected` after escalation:
```python
self.notifier.task_escalated(task_id, task["task_name"], comments)
```

In `build_loop` after loop ends:
```python
self.notifier.loop_completed(
    completed=self.checkpoint.load().get("tasks_completed", []),
    escalated=self.checkpoint.load().get("tasks_escalated", []),
)
```

#### 4. Slack webhook setup (operator docs)
- Go to https://api.slack.com/apps → Create App → Incoming Webhooks
- Enable and copy the webhook URL into `project.yaml → notifications.webhook_url`
- For Teams: use a Teams Incoming Webhook connector URL (same payload format)

### Acceptance Criteria
- [ ] `notifications.enabled: false` sends nothing (safe default)
- [ ] Escalation fires a webhook with task ID, name, and all reviewer comments
- [ ] Loop completion fires a webhook with completed vs escalated task lists
- [ ] Webhook failures are logged but do not crash the orchestrator
- [ ] Works with Slack, Teams, and any HTTP endpoint accepting `{"text": "..."}`
