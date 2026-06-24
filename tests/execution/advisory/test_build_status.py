"""Unit tests for the My-Day build status file."""
import json

import pytest

from execution.advisory import build_status as bs


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "OUTPUT_DIR", tmp_path)
    return tmp_path


def test_read_missing_returns_none(out_dir):
    assert bs.read_status("nope") is None


def test_write_then_read_roundtrip(out_dir):
    bs.write_status("proj-a", phase="advisory", message="Designing…", session_id="s1")
    data = bs.read_status("proj-a")
    assert data["phase"] == "advisory"
    assert data["message"] == "Designing…"
    assert data["session_id"] == "s1"
    # phase default percent filled in
    assert data["percent"] == bs.PHASE_PERCENT["advisory"]
    # timestamps stamped
    assert data["started_at"] and data["updated_at"]


def test_write_merges_and_preserves_other_fields(out_dir):
    bs.write_status("proj-b", session_id="s2", bc_project_id=123, phase="advisory")
    started = bs.read_status("proj-b")["started_at"]
    bs.write_status("proj-b", phase="basecamp", message="Creating tasks…")
    data = bs.read_status("proj-b")
    # earlier fields survive the merge
    assert data["session_id"] == "s2"
    assert data["bc_project_id"] == 123
    # started_at is not overwritten by later writes
    assert data["started_at"] == started
    assert data["phase"] == "basecamp"
    assert data["percent"] == bs.PHASE_PERCENT["basecamp"]


def test_explicit_percent_overrides_phase_default(out_dir):
    bs.write_status("proj-c", phase="tasks", percent=42)
    assert bs.read_status("proj-c")["percent"] == 42


def test_atomic_write_no_temp_files_left(out_dir):
    bs.write_status("proj-d", phase="done")
    leftovers = list((out_dir / "proj-d").glob("*.tmp*"))
    assert leftovers == []


def test_active_builds_filters_by_operator_and_phase(out_dir):
    bs.write_status("a1", operator_email="ali@colaberry.com", phase="advisory")
    bs.write_status("a2", operator_email="ali@colaberry.com", phase="basecamp")
    bs.write_status("a3", operator_email="ali@colaberry.com", phase="done")   # finished
    bs.write_status("a4", operator_email="other@colaberry.com", phase="tasks")  # other op
    bs.write_status("a5", operator_email="ali@colaberry.com", phase="error")   # failed

    active = bs.active_builds_for_operator("ALI@colaberry.com")  # case-insensitive
    slugs = {b["slug"] for b in active}
    assert slugs == {"a1", "a2"}


def test_active_builds_empty_for_unknown_operator(out_dir):
    bs.write_status("z1", operator_email="ali@colaberry.com", phase="advisory")
    assert bs.active_builds_for_operator("nobody@x.com") == []
    assert bs.active_builds_for_operator("") == []


def test_active_builds_ignores_corrupt_files(out_dir):
    bs.write_status("good", operator_email="ali@colaberry.com", phase="advisory")
    bad = out_dir / "bad"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "build_status.json").write_text("{not json", encoding="utf-8")
    active = bs.active_builds_for_operator("ali@colaberry.com")
    assert {b["slug"] for b in active} == {"good"}
