"""Tests for _natural_flow_sync — the page-load freshness gate.

The 2026-06-09 audit's H4 finding: the gate only checked age, so a
partial sync that errored on the user's most-active project still
bumped last_sync_at and the gate said 'fresh' — masking staleness
for the project the user actually cares about. Phase 2 C4 tightens
the gate to require BOTH age<90s AND last_sync_status=='ok'.

These tests lock in the new gate semantics. A future change that
re-introduces age-only checking will fail these tests loudly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.routers import my_day as my_day_router
from execution.products.ops import store, sync_coordinator


@pytest.fixture(autouse=True)
def _fresh_coordinator():
    sync_coordinator.reset_coordinator_for_tests()
    yield
    sync_coordinator.reset_coordinator_for_tests()


@pytest.fixture
def stub_kick(monkeypatch):
    """Replace _kick_bg_full_sync with a recorder so tests can assert
    whether a sync was kicked without actually firing one."""
    calls: list[str] = []
    monkeypatch.setattr(
        my_day_router, "_kick_bg_full_sync",
        lambda email: calls.append(email),
    )
    return calls


def _state(*, age_seconds: float, status: str) -> store.OpsState:
    """Build an OpsState whose last_sync_at is `age_seconds` in the past."""
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return store.OpsState(
        user_id="u@x.com",
        last_sync_at=ts.isoformat(),
        last_sync_status=status,
    )


class TestNaturalFlowSync:
    def test_fresh_and_ok_does_not_kick(self, stub_kick):
        """Age 30s + status='ok' → store is fresh enough, no sync."""
        state = _state(age_seconds=30, status="ok")
        result = my_day_router._natural_flow_sync("u@x.com", state, None)
        assert result is False
        assert stub_kick == []

    def test_stale_and_ok_kicks(self, stub_kick):
        """Age 300s + status='ok' → past 90s threshold, kick a refresh."""
        state = _state(age_seconds=300, status="ok")
        my_day_router._natural_flow_sync("u@x.com", state, None)
        assert stub_kick == ["u@x.com"]

    def test_fresh_but_partial_kicks(self, stub_kick):
        """H4 fix: age 30s but last sync was 'partial' → kick anyway
        to give the failed project a chance to retry. Without this fix,
        the user could sit on stale data forever for the one project
        that consistently errors during sync."""
        state = _state(age_seconds=30, status="partial")
        my_day_router._natural_flow_sync("u@x.com", state, None)
        assert stub_kick == ["u@x.com"], (
            "fresh-but-partial state must trigger retry — H4 regression"
        )

    def test_fresh_but_failed_kicks(self, stub_kick):
        """Same as partial: a 'failed' status means the last sync didn't
        complete; we should retry rather than report stale data as fresh."""
        state = _state(age_seconds=30, status="failed")
        my_day_router._natural_flow_sync("u@x.com", state, None)
        assert stub_kick == ["u@x.com"]

    def test_never_synced_kicks(self, stub_kick):
        """Brand-new user with no sync history → always kick. Documents
        the boundary: empty last_sync_status defaults to '' which is not
        'ok', so the gate falls open."""
        state = store.OpsState(user_id="u@x.com")
        my_day_router._natural_flow_sync("u@x.com", state, None)
        assert stub_kick == ["u@x.com"]

    def test_never_returns_true_inline_sync(self, stub_kick):
        """Per the docstring contract: this function NEVER reloads state
        inline (returns False unconditionally). Anything else would
        re-introduce the 30s page freeze the natural-flow refactor
        deliberately removed."""
        for age, status in [(0, "ok"), (30, "ok"), (300, "ok"),
                            (30, "partial"), (30, "failed")]:
            state = _state(age_seconds=age, status=status)
            assert my_day_router._natural_flow_sync("u@x.com", state, None) is False


class TestLogBgException:
    """Phase 3 C6 / audit M5 fix: every BG sync site now routes its
    top-level exception through _log_bg_exception → sync._record_error,
    so /my-day/_health can show them. Previously these went to bare
    'except Exception: pass' and vanished without trace."""

    def test_records_into_ring_buffer(self):
        from execution.products.ops import sync as _sync
        _sync.clear_recent_errors()
        try:
            raise RuntimeError("BC down")
        except RuntimeError as e:
            my_day_router._log_bg_exception("u@x.com", "bg_full_sync", e)
        errs = _sync.recent_errors()
        assert len(errs) == 1
        assert errs[0]["user_id"] == "u@x.com"
        assert errs[0]["kind"] == "bg_full_sync"
        assert "RuntimeError" in errs[0]["detail"]
        assert "BC down" in errs[0]["detail"]
        _sync.clear_recent_errors()

    def test_recorder_failure_does_not_propagate(self, monkeypatch):
        """Defensive: even if _record_error itself raises, the BG thread
        must not crash. The whole point of routing exceptions through
        this helper is that NOTHING in this path can take down a thread."""
        def _broken(*a, **kw):
            raise RuntimeError("ring buffer broken")
        from execution.products.ops import sync as _sync
        monkeypatch.setattr(_sync, "_record_error", _broken)
        # Must not raise:
        my_day_router._log_bg_exception("u@x.com", "x", RuntimeError("boom"))


class TestKickBgFullSync:
    """The bg-sync kicker is a thin wrapper around pull_todos_for_user.
    Mutual exclusion now lives in the SyncCoordinator inside that
    function, NOT in a router-level lock dict. These tests verify
    that contract — and that calling it doesn't crash regardless of
    whether a sync is in flight."""

    def test_spawns_thread_calling_pull_todos_for_user(self, monkeypatch):
        called: list[str] = []
        def _stub_pull(email, *a, **kw):
            called.append(email)
            return {"status": "ok"}
        monkeypatch.setattr(my_day_router.sync, "pull_todos_for_user", _stub_pull)
        monkeypatch.setattr(my_day_router.scorer, "score_all_todos",
                            lambda *a, **kw: None)
        my_day_router._kick_bg_full_sync("u@x.com")
        # Daemon thread — give it a moment to start
        import time as _t
        for _ in range(50):
            if called: break
            _t.sleep(0.01)
        assert called == ["u@x.com"]

    def test_router_does_not_maintain_lock_dict_anymore(self):
        """Regression guard: the function-attribute `_locks` dict on
        _maybe_async_sync was the home of audit H1/H2/L1. If a future
        change re-introduces it (perhaps as 'cleaner' or 'safer'),
        this test fails to remind that the SyncCoordinator is the
        canonical place for mutual exclusion."""
        assert not hasattr(my_day_router._maybe_async_sync, "_locks"), (
            "Router-level lock dict is back — coordinator is the "
            "single-flight authority. Remove the lock dict and use "
            "SyncCoordinator instead."
        )
