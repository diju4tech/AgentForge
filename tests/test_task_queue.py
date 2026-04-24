"""Tests for TaskQueue — status transitions, feedback, escalation, dependency filtering."""
import os
import tempfile
import pytest
import yaml
from orchestrator import TaskQueue


def _make_queue(tasks: list) -> tuple:
    """Return (TaskQueue, tmp_path) with pre-populated tasks."""
    tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
    yaml.dump({"tasks": tasks}, tmp)
    tmp.close()
    q = TaskQueue(path=tmp.name)
    return q, tmp.name


def _task(task_id: str, status: str = "pending", deps: list = None,
          retry: int = 0, comments: list = None) -> dict:
    return {
        "task_id": task_id,
        "task_name": f"Task {task_id}",
        "status": status,
        "retry_count": retry,
        "dependencies": deps or [],
        "comments": comments or [],
    }


@pytest.fixture(autouse=True)
def cleanup(request):
    paths = []
    def register(p): paths.append(p)
    request.addfinalizer(lambda: [os.unlink(p) for p in paths if os.path.exists(p)])
    return register


# ---- get_next_task ----

def test_get_next_task_returns_first_pending(cleanup):
    q, p = _make_queue([_task("T1", "completed"), _task("T2", "pending"), _task("T3", "pending")])
    cleanup(p)
    assert q.get_next_task()["task_id"] == "T2"


def test_get_next_task_none_when_empty(cleanup):
    q, p = _make_queue([])
    cleanup(p)
    assert q.get_next_task() is None


def test_get_next_task_none_when_all_complete(cleanup):
    q, p = _make_queue([_task("T1", "completed"), _task("T2", "escalated")])
    cleanup(p)
    assert q.get_next_task() is None


# ---- status transitions ----

def test_mark_in_progress(cleanup):
    q, p = _make_queue([_task("T1")])
    cleanup(p)
    q.mark_in_progress("T1")
    task = q.load_all()[0]
    assert task["status"] == "in_progress"
    assert "started_at" in task


def test_mark_complete(cleanup):
    q, p = _make_queue([_task("T1", "in_progress")])
    cleanup(p)
    q.mark_complete("T1")
    task = q.load_all()[0]
    assert task["status"] == "completed"
    assert "completed_at" in task


# ---- add_feedback ----

def test_add_feedback_appends_comments(cleanup):
    q, p = _make_queue([_task("T1")])
    cleanup(p)
    q.add_feedback("T1", [{"category": "testing", "detail": "missing tests"}], max_retries=3)
    task = q.load_all()[0]
    assert len(task["comments"]) == 1
    assert task["comments"][0]["category"] == "testing"


def test_add_feedback_increments_retry(cleanup):
    q, p = _make_queue([_task("T1", retry=1)])
    cleanup(p)
    q.add_feedback("T1", [{"category": "docker", "detail": "build failed"}], max_retries=3)
    assert q.load_all()[0]["retry_count"] == 2


def test_add_feedback_resets_to_pending_below_max(cleanup):
    q, p = _make_queue([_task("T1", retry=1)])
    cleanup(p)
    q.add_feedback("T1", [{"category": "testing", "detail": "x"}], max_retries=3)
    assert q.load_all()[0]["status"] == "pending"


def test_add_feedback_escalates_at_max_retries(cleanup):
    q, p = _make_queue([_task("T1", retry=2)])
    cleanup(p)
    q.add_feedback("T1", [{"category": "testing", "detail": "x"}], max_retries=3)
    assert q.load_all()[0]["status"] == "escalated"


def test_add_feedback_accumulates_across_calls(cleanup):
    q, p = _make_queue([_task("T1")])
    cleanup(p)
    q.add_feedback("T1", [{"category": "a", "detail": "1"}], max_retries=5)
    q.add_feedback("T1", [{"category": "b", "detail": "2"}], max_retries=5)
    assert len(q.load_all()[0]["comments"]) == 2


# ---- has_escalated ----

def test_has_escalated_true(cleanup):
    q, p = _make_queue([_task("T1", "escalated")])
    cleanup(p)
    assert q.has_escalated() is True


def test_has_escalated_false(cleanup):
    q, p = _make_queue([_task("T1", "completed"), _task("T2", "pending")])
    cleanup(p)
    assert q.has_escalated() is False


# ---- get_independent_tasks ----

def test_get_independent_tasks_no_deps(cleanup):
    q, p = _make_queue([_task("T1"), _task("T2"), _task("T3")])
    cleanup(p)
    tasks = q.get_independent_tasks(2, completed_ids=[])
    assert len(tasks) == 2


def test_get_independent_tasks_respects_deps(cleanup):
    q, p = _make_queue([_task("T1"), _task("T2", deps=["T1"])])
    cleanup(p)
    tasks = q.get_independent_tasks(2, completed_ids=[])
    ids = [t["task_id"] for t in tasks]
    assert "T1" in ids
    assert "T2" not in ids


def test_get_independent_tasks_unlocks_after_completion(cleanup):
    q, p = _make_queue([_task("T1", "completed"), _task("T2", deps=["T1"])])
    cleanup(p)
    tasks = q.get_independent_tasks(2, completed_ids=["T1"])
    assert tasks[0]["task_id"] == "T2"


# ---- replace_all ----

def test_replace_all(cleanup):
    q, p = _make_queue([_task("T1")])
    cleanup(p)
    q.replace_all([_task("T2"), _task("T3")])
    ids = [t["task_id"] for t in q.load_all()]
    assert ids == ["T2", "T3"]
