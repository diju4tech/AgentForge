"""Tests for Checkpoint — save/load, atomic writes, defaults, update."""
import os
import tempfile
import pytest
import yaml
from orchestrator import Checkpoint


@pytest.fixture
def cp(tmp_path):
    path = str(tmp_path / "CHECKPOINT.yaml")
    return Checkpoint(path=path)


# ---- defaults ----

def test_load_returns_default_when_missing(cp):
    state = cp.load()
    assert state["phase"] == "init"
    assert state["tasks_completed"] == []
    assert state["current_task_id"] is None


# ---- save / load round-trip ----

def test_save_and_load(cp):
    cp.save({"phase": "building", "tasks_completed": ["T1"], "current_task_id": "T2",
             "tasks_escalated": [], "last_action": "started_T2", "notes": ""})
    state = cp.load()
    assert state["phase"] == "building"
    assert state["tasks_completed"] == ["T1"]
    assert state["current_task_id"] == "T2"


def test_save_sets_last_updated(cp):
    cp.save({"phase": "init", "tasks_completed": [], "tasks_escalated": [],
             "current_task_id": None, "last_action": None, "notes": ""})
    assert cp.load()["last_updated"] is not None


# ---- atomic write ----

def test_atomic_write_no_tmp_left(cp):
    cp.save({"phase": "init", "tasks_completed": [], "tasks_escalated": [],
             "current_task_id": None, "last_action": None, "notes": ""})
    assert not os.path.exists(cp.path + ".tmp")


def test_atomic_write_file_valid_yaml_after_save(cp):
    cp.save({"phase": "complete", "tasks_completed": ["T1", "T2"],
             "tasks_escalated": [], "current_task_id": None,
             "last_action": "loop_done", "notes": "all good"})
    with open(cp.path) as f:
        data = yaml.safe_load(f)
    assert data["phase"] == "complete"


# ---- update ----

def test_update_merges_fields(cp):
    cp.save({"phase": "building", "tasks_completed": ["T1"],
             "tasks_escalated": [], "current_task_id": "T2",
             "last_action": "started", "notes": ""})
    cp.update(phase="complete", last_action="loop_done")
    state = cp.load()
    assert state["phase"] == "complete"
    assert state["tasks_completed"] == ["T1"]   # unchanged
    assert state["last_action"] == "loop_done"


def test_update_does_not_lose_existing_keys(cp):
    cp.save({"phase": "building", "tasks_completed": ["T1", "T2"],
             "tasks_escalated": [], "current_task_id": "T3",
             "last_action": "x", "notes": "some note"})
    cp.update(current_task_id="T4")
    state = cp.load()
    assert state["tasks_completed"] == ["T1", "T2"]
    assert state["notes"] == "some note"
