"""Strict past-month retention in the My Day view (2026-06-22).

A todo can be `active` in Basecamp yet quiet past the freshness window (no
activity in OPS_FRESHNESS_DAYS, no future due date). The walk's inclusion
filter (`_todo_is_relevant`) already refuses to ADD such a row at sync time,
but a row added while fresh used to linger as `active` forever once it aged
out — so My Day showed more than its "past month" promise.

`sync.row_is_recent` is the retention analogue of that inclusion filter, and
`my_day._view_todos` applies it at render time: stale-active rows are hidden
(the store still mirrors BC; this is a pure view projection). These tests pin
both down.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.routers import my_day as my_day_router
from execution.products.ops import store, sync


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OPS_ROOT", tmp_path / "ops")


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _due(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _todo(bc_id: int, **kw) -> store.OpsTodo:
    base = dict(bc_id=bc_id, bc_project_id=1, bc_project_name="P",
                bc_todolist_id=1, bc_todolist_name="L", title=f"t{bc_id}",
                status="active")
    base.update(kw)
    return store.OpsTodo(**base)


class TestRowIsRecent:
    def test_fresh_active_is_recent(self):
        assert sync.row_is_recent(SimpleNamespace(bc_updated_at=_ago(5), due_on=None)) is True

    def test_stale_no_due_not_recent(self):
        assert sync.row_is_recent(SimpleNamespace(bc_updated_at=_ago(40), due_on=None)) is False

    def test_stale_but_future_due_is_recent(self):
        assert sync.row_is_recent(SimpleNamespace(bc_updated_at=_ago(40), due_on=_due(10))) is True

    def test_stale_past_due_not_recent(self):
        assert sync.row_is_recent(SimpleNamespace(bc_updated_at=_ago(40), due_on=_due(-3))) is False

    def test_undateable_row_kept_conservatively(self):
        """No parseable updated_at -> treated as recent (don't hide a row just
        because we can't date it). Mirrors sync._is_fresh's conservative None."""
        assert sync.row_is_recent(SimpleNamespace(bc_updated_at="", due_on=None)) is True


class TestViewTodos:
    def test_stale_active_dropped_others_kept(self):
        store.upsert_todos("u@x.com", [
            _todo(1, bc_updated_at=_ago(5)),                       # fresh active -> keep
            _todo(2, bc_updated_at=_ago(40)),                      # stale active, no due -> DROP
            _todo(3, bc_updated_at=_ago(40), due_on=_due(10)),     # stale but future due -> keep
            _todo(4, bc_updated_at=_ago(40), status="completed"),  # completed (timeline) -> keep
            _todo(5, bc_updated_at=_ago(40), status="archived"),   # archived -> keep (not active)
        ])
        ids = {t.bc_id for t in my_day_router._view_todos("u@x.com")}
        assert ids == {1, 3, 4, 5}

    def test_dismissed_stale_active_kept(self):
        """A dismissed row passes through untouched (downstream `not
        is_dismissed` filters handle it); retention must not double-hide it."""
        store.upsert_todos("u@x.com", [
            _todo(9, bc_updated_at=_ago(40), is_dismissed=True),
        ])
        ids = {t.bc_id for t in my_day_router._view_todos("u@x.com")}
        assert ids == {9}

    def test_all_fresh_unchanged(self):
        store.upsert_todos("u@x.com", [
            _todo(i, bc_updated_at=_ago(1)) for i in range(1, 6)
        ])
        assert len(my_day_router._view_todos("u@x.com")) == 5
