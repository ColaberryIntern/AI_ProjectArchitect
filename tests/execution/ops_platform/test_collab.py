"""Phase 8A tests: collab_sessions + WebSocket gateway + concurrency adoption."""

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, capability_versions, collab_sessions,
    distributed_lock, optimistic_concurrency, realtime_bus, ws_gateway,
)
from execution.ops_platform.identity import IdentityContext, anonymous_identity


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(collab_sessions, "_SESSIONS_DIR", tmp_path / "collab/sessions")
    monkeypatch.setattr(collab_sessions, "_REVISIONS_DIR", tmp_path / "collab/revisions")
    monkeypatch.setattr(collab_sessions, "_COMMENTS_DIR", tmp_path / "collab/comments")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(realtime_bus, "_EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(realtime_bus, "_SEQUENCE_PATH", tmp_path / "sequence.json")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    realtime_bus.reset_for_tests()
    yield
    realtime_bus.reset_for_tests()


def _identity(user_id="alice"):
    return IdentityContext(user_id=user_id, display_name=user_id.title(),
                              auth_provider="HEADER_AUTH", authenticated=True,
                              roles=["operator"], workspace_ids=["sales"])


# ── collab_sessions ───────────────────────────────────────────────────


def test_open_edit_session_acquires_lock(isolated):
    s = collab_sessions.open_session(entity_type="approval",
                                        entity_id="appr-1",
                                        editor=_identity("alice"))
    assert s.intent == "edit"
    held = distributed_lock.is_held("collab.approval.appr-1")
    assert held is not None


def test_anonymous_cannot_open_edit_session(isolated):
    with pytest.raises(PermissionError):
        collab_sessions.open_session(entity_type="approval", entity_id="x",
                                        editor=anonymous_identity())


def test_second_editor_raises_edit_lock_held(isolated):
    collab_sessions.open_session(entity_type="approval", entity_id="appr-2",
                                    editor=_identity("alice"))
    with pytest.raises(collab_sessions.EditLockHeld):
        collab_sessions.open_session(entity_type="approval", entity_id="appr-2",
                                        editor=_identity("bob"))


def test_view_session_does_not_take_lock(isolated):
    collab_sessions.open_session(entity_type="approval", entity_id="appr-3",
                                    editor=_identity("alice"), intent="view")
    held = distributed_lock.is_held("collab.approval.appr-3")
    assert held is None


def test_close_releases_lock(isolated):
    s = collab_sessions.open_session(entity_type="approval", entity_id="appr-4",
                                        editor=_identity("alice"))
    assert collab_sessions.close_session(s.session_id, editor=_identity("alice"))
    assert distributed_lock.is_held("collab.approval.appr-4") is None


def test_close_rejected_for_wrong_owner(isolated):
    s = collab_sessions.open_session(entity_type="approval", entity_id="appr-5",
                                        editor=_identity("alice"))
    assert not collab_sessions.close_session(s.session_id, editor=_identity("bob"))


def test_heartbeat_updates_cursor(isolated):
    s = collab_sessions.open_session(entity_type="approval", entity_id="appr-6",
                                        editor=_identity("alice"))
    refreshed = collab_sessions.heartbeat_session(s.session_id, editor=_identity("alice"),
                                                       cursor_position="line:42")
    assert refreshed.cursor_position == "line:42"


def test_record_revision_appends(isolated):
    rev1 = collab_sessions.record_revision(entity_type="approval", entity_id="rev-1",
                                               author="alice", summary="first edit")
    rev2 = collab_sessions.record_revision(entity_type="approval", entity_id="rev-1",
                                               author="alice", summary="second edit")
    rows = collab_sessions.list_revisions("rev-1")
    assert len(rows) == 2
    assert rows[0].summary == "second edit"  # newest-first


def test_post_comment(isolated):
    cm = collab_sessions.post_comment(entity_type="approval", entity_id="cm-1",
                                          author=_identity("alice"),
                                          body="please review", anchor="field:status")
    rows = collab_sessions.list_comments("cm-1")
    assert len(rows) == 1
    assert rows[0].body == "please review"


def test_anonymous_cannot_post_comment(isolated):
    with pytest.raises(PermissionError):
        collab_sessions.post_comment(entity_type="x", entity_id="y",
                                        author=anonymous_identity(), body="hi")


def test_empty_comment_rejected(isolated):
    with pytest.raises(ValueError):
        collab_sessions.post_comment(entity_type="x", entity_id="z",
                                        author=_identity("alice"), body="   ")


def test_resolve_comment(isolated):
    cm = collab_sessions.post_comment(entity_type="x", entity_id="cm-r",
                                          author=_identity("alice"), body="check")
    assert collab_sessions.resolve_comment(cm.comment_id, entity_id="cm-r",
                                              actor="bob")
    active = collab_sessions.list_comments("cm-r")
    assert not active
    all_inc = collab_sessions.list_comments("cm-r", include_resolved=True)
    assert all_inc[0].resolved


# ── ws_gateway ────────────────────────────────────────────────────────


def test_mode_reports_local_only_without_redis(isolated):
    m = ws_gateway.mode()
    assert m["coordination_scope"] in ("local-only-single-process",
                                           "redis-pubsub-multi-host")
    # Without explicit Redis activation, expect local-only
    if not m["redis_client_wired"]:
        assert m["coordination_scope"] == "local-only-single-process"


def test_broadcast_emits_local(isolated):
    result = ws_gateway.broadcast("test.broadcast", {"k": 1}, actor={"name": "x"})
    assert result["local"] is True
    # No Redis wired in test → redis fanout is False
    assert result["redis"] is False
    assert int(result["event_id"]) > 0


# ── optimistic_concurrency adoption ──────────────────────────────────


def test_capability_versions_save_with_revision_check(isolated):
    # We need a registered capability; use a stub manifest
    cap_id = "stub-cap"
    monkeypatch_versions_dir = capability_versions
    import execution.ops_platform.capability_versions as cv
    v = cv.CapabilityVersion(
        version_id="v-1", capability_id=cap_id, semver="1.0.0", status="draft",
        parent_version_id=None, changelog="x", migration_notes="",
        compatibility_notes="", rollout_percentage=0.0,
        created_by={"name": "alice"},
        created_at="2026-05-26T10:00:00+00:00",
        approved_by=None, approval_timestamp=None, deprecated_at=None,
        manifest_snapshot={"id": cap_id, "name": "X"}, prompt_snapshot=None,
        tags=[],
    )
    cv._persist(v)
    assert v.revision_id  # set on first persist
    # Stale write rejected
    v2 = cv.get_version("v-1")
    with pytest.raises(optimistic_concurrency.ConcurrencyConflict):
        cv.save_with_revision_check(v2, observed_revision="stale-rev")
