"""Tests for the plan↔Basecamp manifest store."""
import pytest

from execution.advisory import bc_manifest as bm


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "OUTPUT_DIR", tmp_path)
    return tmp_path


def test_load_missing_returns_none(out_dir):
    assert bm.load_manifest("nope") is None


def test_ensure_creates_and_persists(out_dir):
    m = bm.ensure_manifest("demo", 123, "3945211", start_date="2026-06-24")
    assert m["projectSlug"] == "demo"
    assert m["bcProjectId"] == 123
    assert m["startDate"] == "2026-06-24"
    assert m["entries"] == {}
    # persisted
    assert bm.load_manifest("demo")["bcProjectId"] == 123


def test_start_date_set_once(out_dir):
    bm.ensure_manifest("demo", 123, "3945211", start_date="2026-06-24")
    again = bm.ensure_manifest("demo", 123, "3945211", start_date="2099-01-01")
    assert again["startDate"] == "2026-06-24"  # not overwritten


def test_upsert_and_get_entry(out_dir):
    m = bm.ensure_manifest("demo", 123, "3945211")
    bm.upsert_entry(m, "INIT.ch04-x", bc_type="todolist", bc_id=555, content_hash="sha256:a")
    bm.upsert_entry(m, "TODO.ch04.x.y", bc_type="todo", bc_id=777,
                    content_hash="sha256:b", parent_bc_id=666, due_on="2026-07-01")
    bm.save_manifest("demo", m)

    reloaded = bm.load_manifest("demo")
    init = bm.get_entry(reloaded, "INIT.ch04-x")
    assert init["bcId"] == 555 and init["bcType"] == "todolist"
    todo = bm.get_entry(reloaded, "TODO.ch04.x.y")
    assert todo["parentBcId"] == 666 and todo["due_on"] == "2026-07-01"


def test_upsert_updates_existing_hash(out_dir):
    m = bm.ensure_manifest("demo", 123, "3945211")
    bm.upsert_entry(m, "TODO.a", bc_type="todo", bc_id=1, content_hash="sha256:old")
    bm.upsert_entry(m, "TODO.a", bc_type="todo", bc_id=1, content_hash="sha256:new")
    assert bm.get_entry(m, "TODO.a")["contentHash"] == "sha256:new"


def test_mark_retired(out_dir):
    m = bm.ensure_manifest("demo", 123, "3945211")
    bm.upsert_entry(m, "TODO.a", bc_type="todo", bc_id=1, content_hash="sha256:x")
    bm.mark_retired(m, "TODO.a")
    assert bm.get_entry(m, "TODO.a")["status"] == "retired"
