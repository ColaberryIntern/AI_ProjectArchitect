"""Unit tests for execution/products/ops/purge.py -- audit M6 fix.

The sync walker drops todos whose updated_at is past OPS_FRESHNESS_DAYS
AND that have no future due date. A task that was active at last sync,
then completed in BC AFTER it aged out, becomes a permanent zombie
'active' row locally because the walker no longer touches it.

These tests pin down the contract: which rows the sweep targets, what
it does on each BC response (completed / still-active / 404), and the
cap that prevents one user from monopolizing the cron budget.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from execution.products.ops import purge, store, sync


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OPS_ROOT", tmp_path / "ops")
    monkeypatch.setattr(sync, "HTTP_THROTTLE_SECONDS", 0)
    sync.clear_recent_errors()
    yield
    sync.clear_recent_errors()


def _todo(bc_id: int, **overrides) -> store.OpsTodo:
    defaults = dict(
        bc_id=bc_id, bc_project_id=101, bc_project_name="P",
        bc_todolist_id=7001, bc_todolist_name="L",
        title=f"Task {bc_id}",
        status="active",
    )
    defaults.update(overrides)
    return store.OpsTodo(**defaults)


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# ════════════════════════════════════════════════════════════════════
# is_purge_due -- interval gate
# ════════════════════════════════════════════════════════════════════


class TestIsPurgeDue:
    def test_never_purged_is_due(self):
        assert purge.is_purge_due("new@x.com") is True

    def test_recently_purged_is_not_due(self):
        state = store.OpsState(
            user_id="u@x.com",
            last_purge_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        store.save_state(state)
        assert purge.is_purge_due("u@x.com") is False

    def test_old_purge_is_due(self, monkeypatch):
        monkeypatch.setattr(purge, "PURGE_INTERVAL_HOURS", 24)
        state = store.OpsState(
            user_id="u@x.com",
            last_purge_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        )
        store.save_state(state)
        assert purge.is_purge_due("u@x.com") is True

    def test_malformed_timestamp_treated_as_due(self):
        """A garbled last_purge_at must not strand a user forever;
        treat as 'never purged' = due."""
        state = store.OpsState(user_id="u@x.com", last_purge_at="not-a-date")
        store.save_state(state)
        assert purge.is_purge_due("u@x.com") is True


# ════════════════════════════════════════════════════════════════════
# purge_stale_active_rows -- main logic
# ════════════════════════════════════════════════════════════════════


class TestPurgeStaleActiveRows:
    def test_token_missing_records_failed_state_and_returns(self, monkeypatch):
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=(None, "missing")))
        result = purge.purge_stale_active_rows("u@x.com")
        assert result["status"] == "token_missing"
        state = store.load_state("u@x.com")
        assert state.last_purge_status == "failed"
        assert state.last_purge_at != ""

    def test_no_active_rows_returns_ok_no_bc_calls(self, monkeypatch):
        """No active rows (only completed/dismissed) -> nothing to
        reconcile. Must not fire BC calls."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(1, bc_updated_at=_days_ago(1), status="completed"),
        ])
        bc_calls = []
        monkeypatch.setattr(sync, "_bc_get",
                            lambda p, *a, **kw: (bc_calls.append(p), None)[1])

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["status"] == "ok"
        assert result["active_found"] == 0
        assert result["checked"] == 0
        assert bc_calls == []

    def test_fresh_active_rows_are_rechecked(self, monkeypatch):
        """Option 2 (2026-06-10): the sweep no longer gates on the 30-day
        freshness cutoff. Recently-updated active rows ARE re-fetched now,
        so a list deleted while its rows are still fresh gets caught."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(1, bc_updated_at=_days_ago(1)),
            _todo(2, bc_updated_at=_days_ago(5)),
        ])
        bc_calls: list[str] = []
        monkeypatch.setattr(sync, "_bc_get",
                            lambda p, *a, **kw: (bc_calls.append(p),
                                                 {"id": 1, "completed": False})[1])

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["active_found"] == 2
        assert result["checked"] == 2
        assert len(bc_calls) == 2

    def test_fresh_deleted_list_row_archived(self, monkeypatch):
        """The headline Option-2 case: an operator deletes a todolist in
        BC (the old 'approval queue') while its rows are still fresh. The
        walker stops returning them; this sweep re-fetches, gets 400/404
        (BC returns None), and archives the orphan so the report stops
        showing it. The old cutoff-gated purge would have skipped this
        row for 30 days."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(4040, bc_updated_at=_days_ago(2)),  # fresh, < cutoff
        ])
        monkeypatch.setattr(sync, "_bc_get", MagicMock(return_value=None))

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["archived_missing"] == 1
        assert store.get_todo("u@x.com", 4040).status == "archived"

    def test_stale_row_completed_in_bc_is_mirrored_locally(self, monkeypatch):
        """The actual M6 fix: stale-active row whose BC counterpart is
        now completed -> local status flips to completed with completion
        metadata. Tests the headline behavior the audit was about."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(99, bc_updated_at=_days_ago(60)),
        ])
        # BC says: yes, that one's completed
        bc_response = {
            "id": 99, "completed": True,
            "completion": {
                "created_at": "2026-06-01T10:00:00Z",
                "creator": {"id": 555, "name": "Jane"},
            },
        }
        monkeypatch.setattr(sync, "_bc_get",
                            MagicMock(return_value=bc_response))

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["updated_completed"] == 1
        loaded = store.get_todo("u@x.com", 99)
        assert loaded.status == "completed"
        assert loaded.completed_at == "2026-06-01T10:00:00Z"
        assert loaded.completed_by_name == "Jane"
        # State updated
        state = store.load_state("u@x.com")
        assert state.last_purge_status == "ok"
        assert state.last_purge_archived == 1

    def test_stale_row_still_active_in_bc_left_alone(self, monkeypatch):
        """If BC says the task is still active, the local row should not
        change (it remains active and will be re-checked next sweep)."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(7, bc_updated_at=_days_ago(60)),
        ])
        monkeypatch.setattr(sync, "_bc_get",
                            MagicMock(return_value={"id": 7, "completed": False}))

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["updated_completed"] == 0
        assert result["archived_missing"] == 0
        assert store.get_todo("u@x.com", 7).status == "active"

    def test_stale_row_deleted_in_bc_gets_archived(self, monkeypatch):
        """BC returns None (400/404) -> task was deleted. Mark archived
        locally so it stops cluttering the queue, but keep the audit row."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(404, bc_updated_at=_days_ago(60)),
        ])
        monkeypatch.setattr(sync, "_bc_get", MagicMock(return_value=None))

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["archived_missing"] == 1
        assert store.get_todo("u@x.com", 404).status == "archived"

    def test_trashed_in_bc_gets_archived(self, monkeypatch):
        """BC soft-delete ('trash') does NOT 404 -- the todo endpoint still
        returns a JSON object, just with status='trashed', completed=False.
        The 404/completed checks miss it, so the old sweep left it 'active'
        and it kept ranking #1 on the human queue. It must now be archived.
        Regression for the 'deleted project still shows' report (2026-06-14).
        """
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(9946498251, bc_updated_at=_days_ago(14)),
        ])
        monkeypatch.setattr(sync, "_bc_get", MagicMock(
            return_value={"id": 9946498251, "status": "trashed", "completed": False}))

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["archived_trashed"] == 1
        assert result["archived_missing"] == 0
        assert store.get_todo("u@x.com", 9946498251).status == "archived"
        assert store.load_state("u@x.com").last_purge_archived == 1

    def test_archived_in_bc_gets_archived(self, monkeypatch):
        """BC archive (status='archived', not a 404) is also gone-from-queue
        and must be mirrored as archived locally."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [_todo(50, bc_updated_at=_days_ago(3))])
        monkeypatch.setattr(sync, "_bc_get", MagicMock(
            return_value={"id": 50, "status": "archived", "completed": False}))

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["archived_trashed"] == 1
        assert store.get_todo("u@x.com", 50).status == "archived"

    def test_active_status_in_bc_left_alone(self, monkeypatch):
        """A genuinely-active BC todo (status='active', as BC really sends)
        must NOT be archived by the new non-active branch."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [_todo(8, bc_updated_at=_days_ago(2))])
        monkeypatch.setattr(sync, "_bc_get", MagicMock(
            return_value={"id": 8, "status": "active", "completed": False}))

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["archived_trashed"] == 0
        assert result["archived_missing"] == 0
        assert store.get_todo("u@x.com", 8).status == "active"

    def test_dismissed_rows_skipped_even_if_stale(self, monkeypatch):
        """An operator-dismissed row is a deliberate signal; the purge
        must NOT touch it. Otherwise we'd un-dismiss the task on the
        next sweep, defeating the Skip button."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(1, bc_updated_at=_days_ago(60), is_dismissed=True),
        ])
        bc_calls: list[str] = []
        monkeypatch.setattr(sync, "_bc_get",
                            lambda p, *a, **kw: (bc_calls.append(p), None)[1])

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["active_found"] == 0
        assert bc_calls == []

    def test_already_completed_rows_skipped(self, monkeypatch):
        """Rows already marked completed locally aren't 'stale-active'."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(1, bc_updated_at=_days_ago(60), status="completed"),
        ])
        bc_calls: list[str] = []
        monkeypatch.setattr(sync, "_bc_get",
                            lambda p, *a, **kw: (bc_calls.append(p), None)[1])

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["active_found"] == 0
        assert bc_calls == []

    def test_cap_per_user_respected(self, monkeypatch):
        """A user with 100 stale-active rows must not blow the cron
        budget. Cap at CAP_PER_USER; remaining rows roll over to next
        sweep."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(purge, "CAP_PER_USER", 3)
        store.upsert_todos("u@x.com", [
            _todo(i, bc_updated_at=_days_ago(60 + i)) for i in range(10)
        ])
        bc_calls: list[str] = []
        def _bc(path, *a, **kw):
            bc_calls.append(path)
            return {"id": 0, "completed": False}
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["active_found"] == 10
        assert result["checked"] == 3
        assert result["capped"] is True
        assert len(bc_calls) == 3

    def test_budget_stops_sweep(self, monkeypatch):
        """The wall-clock budget is the primary bound (2026-06-22). With a
        per-call cost that exceeds the budget after a few rows, the sweep stops
        and reports budget_hit; the unswept tail rolls to the next run. Without
        this, a heavy operator's ~785 rows (or a 522 storm) could blow the cron
        budget on one user."""
        import types
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(purge, "PURGE_BUDGET_SECONDS", 5.0)
        monkeypatch.setattr(purge, "CAP_PER_USER", 1000)
        store.upsert_todos("u@x.com", [
            _todo(i, bc_updated_at=_days_ago(60 + i)) for i in range(10)
        ])
        clock = [1000.0]
        # Scope the fake clock to purge only (don't patch the global time module).
        monkeypatch.setattr(purge, "time", types.SimpleNamespace(time=lambda: clock[0]))
        def _bc(path, *a, **kw):
            clock[0] += 3.0   # each reconcile "costs" 3s of wall clock
            return {"id": 0, "completed": False, "status": "active"}
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = purge.purge_stale_active_rows("u@x.com")
        # budget 5s: row0 @0s ok(->3), row1 @3s ok(->6), row2 @6s>5 -> stop
        assert result["checked"] == 2
        assert result["budget_hit"] is True
        assert result["active_found"] == 10

    def test_archived_list_phantom_row_retired(self, monkeypatch):
        """A todo whose Basecamp TODOLIST was archived keeps completed:false but
        flips to status:'archived' and drops out of the walk's active
        todolists.json — so it lingers as a phantom 'active' row. The purge must
        retire it. Regression lock for the 2026-06-22 Gov Contracts incident
        (21 phantom rows from an archived 'Multifamily Management System' list)."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [_todo(7, bc_updated_at=_days_ago(5))])
        # BC returns the row with status archived but completed False (the trap).
        monkeypatch.setattr(
            sync, "_bc_get",
            lambda path, *a, **kw: {"id": 7, "completed": False, "status": "archived"})
        result = purge.purge_stale_active_rows("u@x.com")
        assert result["archived_trashed"] == 1
        assert store.get_todo("u@x.com", 7).status == "archived"

    def test_bc_exception_recorded_and_other_rows_continue(self, monkeypatch):
        """One row's BC fetch raising must not abort the whole sweep.
        Other stale rows still get checked, error captured in ring buffer."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        store.upsert_todos("u@x.com", [
            _todo(1, bc_updated_at=_days_ago(60)),
            _todo(2, bc_updated_at=_days_ago(70)),
        ])
        call_count = {"n": 0}
        def _bc(path, *a, **kw):
            call_count["n"] += 1
            if "/todos/1.json" in path:
                raise RuntimeError("BC angry")
            return {"id": 2, "completed": False}
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = purge.purge_stale_active_rows("u@x.com")
        assert result["status"] == "partial"
        assert result["errors"] >= 1
        # Both rows attempted (resilience)
        assert call_count["n"] == 2
        # Error captured in ring buffer
        errs = sync.recent_errors()
        assert any("purge_fetch:1" in e["kind"] for e in errs)

    def test_state_last_purge_at_advanced_after_run(self, monkeypatch):
        """Even when nothing changes, last_purge_at must advance so
        is_purge_due returns False until the next interval."""
        monkeypatch.setattr(purge.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        # No stale rows -> nothing changes -> but state still advances.
        state = store.load_state("u@x.com")
        assert state.last_purge_at == ""

        purge.purge_stale_active_rows("u@x.com")
        state = store.load_state("u@x.com")
        assert state.last_purge_at != ""
        # And now is_purge_due returns False because we just ran.
        assert purge.is_purge_due("u@x.com") is False
