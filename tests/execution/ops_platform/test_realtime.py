"""Phase 7A tests: realtime_bus, presence, optimistic_concurrency."""

import json
import threading
import time

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, optimistic_concurrency, presence, realtime_bus,
)
from execution.ops_platform.identity import IdentityContext


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(realtime_bus, "_EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(realtime_bus, "_SEQUENCE_PATH", tmp_path / "sequence.json")
    monkeypatch.setattr(presence, "_PRESENCE_DIR", tmp_path / "presence")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    realtime_bus.reset_for_tests()
    yield
    realtime_bus.reset_for_tests()


# ── realtime_bus ──────────────────────────────────────────────────────


def test_emit_persists_and_returns_sequence():
    e1 = realtime_bus.emit("workflow.started", actor="alice")
    e2 = realtime_bus.emit("workflow.completed", actor="alice")
    assert e2.sequence > e1.sequence
    rows = realtime_bus.replay()
    assert len(rows) >= 2


def test_replay_since_sequence_skips_old():
    realtime_bus.emit("x.a", actor="a")
    pivot = realtime_bus.emit("x.b", actor="a")
    realtime_bus.emit("x.c", actor="a")
    out = realtime_bus.replay(since_sequence=pivot.sequence)
    assert all(e.sequence > pivot.sequence for e in out)


def test_replay_filters_by_event_type():
    realtime_bus.emit("workflow.started", actor="a")
    realtime_bus.emit("alert.opened", actor="a")
    rows = realtime_bus.replay(event_types=["alert.opened"])
    assert all(e.event_type == "alert.opened" for e in rows)


def test_replay_filters_by_workspace():
    realtime_bus.emit("x", actor="a", workspace_id="ws-1")
    realtime_bus.emit("x", actor="a", workspace_id="ws-2")
    rows = realtime_bus.replay(workspace_id="ws-1")
    assert all(e.workspace_id == "ws-1" for e in rows)


def test_subscribe_and_unsubscribe():
    sub_id, q, notify = realtime_bus.subscribe(event_types=["t.x"])
    realtime_bus.emit("t.x", actor="a")
    realtime_bus.emit("t.y", actor="a")
    time.sleep(0.05)
    # Only matching events land in the queue
    seen = list(q)
    assert all(e.event_type == "t.x" for e in seen)
    realtime_bus.unsubscribe(sub_id)


def test_stream_yields_wire_format_and_replays_first():
    realtime_bus.emit("workflow.started", actor="a")
    realtime_bus.emit("workflow.completed", actor="a")
    chunks = []
    gen = realtime_bus.stream(stop_after_seconds=0.5, heartbeat_seconds=0.2)
    for chunk in gen:
        chunks.append(chunk)
    assert any("event: workflow.started" in c for c in chunks)
    assert any("event: workflow.completed" in c for c in chunks)


def test_emit_mirrors_to_audit():
    realtime_bus.emit("workflow.started", actor={"name": "alice"})
    rows = audit_log.list_entries(action="realtime.workflow.started")
    assert rows


# ── presence ──────────────────────────────────────────────────────────


def _identity(user_id, *, roles=None):
    return IdentityContext(
        user_id=user_id, display_name=user_id.title(),
        auth_provider="HEADER_AUTH", authenticated=True,
        roles=list(roles or ["operator"]), workspace_ids=["sales"],
    )


def test_presence_heartbeat_records_active_user():
    presence.heartbeat(workspace_id="sales", identity=_identity("alice"))
    rows = presence.active_in_workspace("sales")
    assert any(r["user_id"] == "alice" for r in rows)


def test_anonymous_cannot_heartbeat():
    from execution.ops_platform.identity import anonymous_identity
    entry = presence.heartbeat(workspace_id="sales", identity=anonymous_identity())
    assert entry is None


def test_stale_presence_swept_on_read(monkeypatch):
    presence.heartbeat(workspace_id="sales", identity=_identity("bob"))
    # Force stale by rewriting last_seen
    p = presence._PRESENCE_DIR / "sales.json"
    rows = json.loads(p.read_text())
    rows["bob"]["last_seen_at"] = "2020-01-01T00:00:00+00:00"
    p.write_text(json.dumps(rows))
    fresh = presence.active_in_workspace("sales")
    assert not any(r["user_id"] == "bob" for r in fresh)


def test_leave_emits_event_and_removes_row():
    identity = _identity("carol")
    presence.heartbeat(workspace_id="sales", identity=identity)
    assert presence.leave(workspace_id="sales", identity=identity) is True
    # Idempotent
    assert presence.leave(workspace_id="sales", identity=identity) is False


# ── optimistic_concurrency ───────────────────────────────────────────


def test_new_revision_returns_unique():
    r1 = optimistic_concurrency.new_revision()
    r2 = optimistic_concurrency.new_revision()
    assert r1 != r2


def test_compare_passes_when_revisions_match():
    rev = optimistic_concurrency.new_revision()
    optimistic_concurrency.compare(entity_type="x", entity_id="1",
                                       observed_revision=rev, current_revision=rev)


def test_compare_raises_on_stale():
    with pytest.raises(optimistic_concurrency.ConcurrencyConflict):
        optimistic_concurrency.compare(entity_type="x", entity_id="1",
                                           observed_revision="old",
                                           current_revision="new",
                                           actor="alice")
    rows = audit_log.list_entries(action="optimistic.conflict")
    assert rows


def test_compare_create_only_succeeds_when_current_none():
    # No prior revision exists — writer didn't declare one, accepted
    optimistic_concurrency.compare(entity_type="x", entity_id="1",
                                       observed_revision=None, current_revision=None)


def test_compare_undeclared_against_existing_raises():
    with pytest.raises(optimistic_concurrency.ConcurrencyConflict):
        optimistic_concurrency.compare(entity_type="x", entity_id="1",
                                           observed_revision=None,
                                           current_revision="abc")
