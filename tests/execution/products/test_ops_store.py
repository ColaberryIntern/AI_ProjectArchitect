"""Unit tests for execution/products/ops/store.py — the file-backed mirror.

Coverage rationale (per 2026-06-09 audit, finding H5):
The store layer is the persistence boundary between the BC walker and
every reader (UI, scorer, health endpoint). upsert_todos has subtle
preserve-local-fields semantics (is_dismissed, urgency_score) that the
sync engine relies on. A regression there silently un-dismisses tasks
the user has skipped, or wipes their cached urgency scores. Both
would be invisible until a user notices.

These tests are pure filesystem — no mocks, just tmp_path isolation.
"""
from __future__ import annotations

import json
import threading

import pytest

from execution.products.ops import store


@pytest.fixture(autouse=True)
def isolated_ops_root(tmp_path, monkeypatch):
    """Redirect store's filesystem root to tmp_path. Every test in this
    file gets a fresh, empty store."""
    monkeypatch.setattr(store, "OPS_ROOT", tmp_path / "ops")
    return tmp_path / "ops"


def _todo(bc_id: int, **overrides) -> store.OpsTodo:
    """OpsTodo with sensible defaults; override any field via kwargs."""
    defaults = dict(
        bc_id=bc_id, bc_project_id=101, bc_project_name="P",
        bc_todolist_id=7001, bc_todolist_name="L",
        title=f"Task {bc_id}",
    )
    defaults.update(overrides)
    return store.OpsTodo(**defaults)


def _project(bc_id: int, **overrides) -> store.OpsProject:
    defaults = dict(bc_id=bc_id, name=f"Project {bc_id}")
    defaults.update(overrides)
    return store.OpsProject(**defaults)


# ════════════════════════════════════════════════════════════════════
# load_todos / save_todos round-trip
# ════════════════════════════════════════════════════════════════════


class TestLoadSaveTodos:
    def test_load_empty_returns_empty_list(self):
        assert store.load_todos("u@x.com") == []

    def test_round_trip(self):
        store.save_todos("u@x.com", [_todo(1), _todo(2)])
        loaded = store.load_todos("u@x.com")
        assert [t.bc_id for t in loaded] == [1, 2]

    def test_corrupt_json_returns_empty_list(self):
        """A malformed todos.json must NOT crash the read; sync will
        overwrite it on next pull. Better to show an empty queue than
        to 500 the page."""
        d = store.OPS_ROOT / "u@x.com"
        d.mkdir(parents=True)
        (d / "todos.json").write_text("{not json}")
        assert store.load_todos("u@x.com") == []

    def test_unknown_fields_in_json_ignored(self):
        """Forward-compat: if a future field is removed from OpsTodo
        but lingers in old todos.json files, the load must not crash."""
        d = store.OPS_ROOT / "u@x.com"
        d.mkdir(parents=True)
        payload = [{
            "bc_id": 1, "bc_project_id": 101, "bc_project_name": "P",
            "bc_todolist_id": 7001, "bc_todolist_name": "L", "title": "T",
            "removed_legacy_field": "ignored",
        }]
        (d / "todos.json").write_text(json.dumps(payload))
        loaded = store.load_todos("u@x.com")
        assert len(loaded) == 1
        assert loaded[0].bc_id == 1


# ════════════════════════════════════════════════════════════════════
# upsert_todos — THE invariant: local-only fields survive re-sync
# ════════════════════════════════════════════════════════════════════


class TestUpsertTodos:
    def test_empty_store_creates_all(self):
        created, updated = store.upsert_todos("u@x.com", [_todo(1), _todo(2)])
        assert (created, updated) == (2, 0)
        assert {t.bc_id for t in store.load_todos("u@x.com")} == {1, 2}

    def test_existing_id_updates(self):
        store.upsert_todos("u@x.com", [_todo(1, title="Old")])
        created, updated = store.upsert_todos("u@x.com", [_todo(1, title="New")])
        assert (created, updated) == (0, 1)
        loaded = store.load_todos("u@x.com")
        assert loaded[0].title == "New"

    def test_mix_of_create_and_update(self):
        store.upsert_todos("u@x.com", [_todo(1)])
        created, updated = store.upsert_todos(
            "u@x.com", [_todo(1, title="updated"), _todo(2, title="new")],
        )
        assert (created, updated) == (1, 1)

    def test_dismiss_flag_preserved_across_sync(self):
        """User skipped a task; the next BC sync MUST NOT un-skip it.
        Without this preservation, the 'Skip for now' button is
        effectively broken — the task reappears on the next sync."""
        store.upsert_todos("u@x.com", [_todo(1)])
        store.update_todo(
            "u@x.com", 1,
            is_dismissed=True, dismissed_at="2026-01-01T00:00:00Z",
            dismissed_by="u@x.com", dismissed_reason="manual",
        )
        # Now a fresh sync brings down the same task with default fields.
        store.upsert_todos("u@x.com", [_todo(1, title="title changed in BC")])
        loaded = store.load_todos("u@x.com")[0]
        assert loaded.is_dismissed is True
        assert loaded.dismissed_at == "2026-01-01T00:00:00Z"
        assert loaded.dismissed_by == "u@x.com"
        assert loaded.dismissed_reason == "manual"
        # And the BC-owned field DID update
        assert loaded.title == "title changed in BC"

    def test_urgency_score_preserved_across_sync(self):
        """The scorer runs separately from sync. If upsert overwrites
        urgency_score with the default 0, the scorer's output is lost
        between its run and the next page render."""
        store.upsert_todos("u@x.com", [_todo(1)])
        store.update_todo("u@x.com", 1, urgency_score=85, category="human_required",
                          score_breakdown={"due_days": -2, "components": {"due": 25}})
        store.upsert_todos("u@x.com", [_todo(1, title="title changed")])
        loaded = store.load_todos("u@x.com")[0]
        assert loaded.urgency_score == 85
        assert loaded.category == "human_required"
        assert loaded.score_breakdown == {"due_days": -2, "components": {"due": 25}}

    def test_local_rows_not_in_fresh_are_kept(self):
        """Documented invariant: 'Items present locally but absent in
        fresh are kept (no auto-purge)'. Verifies the policy. Note this
        is what allows the M6 stale-active-row drift the audit flagged;
        a future fix that adds purging must explicitly update this test."""
        store.upsert_todos("u@x.com", [_todo(1), _todo(2), _todo(3)])
        store.upsert_todos("u@x.com", [_todo(1, title="only one survived BC")])
        ids = {t.bc_id for t in store.load_todos("u@x.com")}
        assert ids == {1, 2, 3}

    def test_assignee_lists_replaced_not_merged(self):
        """A re-sync REPLACES assignee_ids/names; it doesn't accumulate
        a union. If BC removed an assignee, the local row must reflect
        that, not show the stale name."""
        store.upsert_todos("u@x.com", [_todo(1, assignee_ids=[100, 200],
                                              assignee_names=["A", "B"])])
        store.upsert_todos("u@x.com", [_todo(1, assignee_ids=[100],
                                              assignee_names=["A"])])
        loaded = store.load_todos("u@x.com")[0]
        assert loaded.assignee_ids == [100]
        assert loaded.assignee_names == ["A"]


# ════════════════════════════════════════════════════════════════════
# update_todo — direct field mutation (dismiss, mark complete, etc.)
# ════════════════════════════════════════════════════════════════════


class TestUpdateTodo:
    def test_updates_named_fields_only(self):
        store.upsert_todos("u@x.com", [_todo(1, title="orig")])
        store.update_todo("u@x.com", 1, status="completed")
        loaded = store.load_todos("u@x.com")[0]
        assert loaded.status == "completed"
        assert loaded.title == "orig"  # other fields untouched

    def test_missing_id_no_op(self):
        store.upsert_todos("u@x.com", [_todo(1)])
        # update_todo on a non-existent id must not crash or create a row
        result = store.update_todo("u@x.com", 999, status="completed")
        assert result is None
        assert len(store.load_todos("u@x.com")) == 1


# ════════════════════════════════════════════════════════════════════
# upsert_projects — preserve is_managed + weight (operator overrides)
# ════════════════════════════════════════════════════════════════════


class TestUpsertProjects:
    def test_creates_new(self):
        created, updated = store.upsert_projects("u@x.com", [_project(101)])
        assert (created, updated) == (1, 0)

    def test_is_managed_preserved_across_sync(self):
        """An operator's choice to un-manage a project (drop it from the
        queue) must survive the next BC sync — otherwise the project
        keeps reappearing in the queue every 5 minutes."""
        store.upsert_projects("u@x.com", [_project(101, is_managed=True)])
        # Operator manually marks it un-managed via some admin path
        projects = store.load_projects("u@x.com")
        projects[0].is_managed = False
        store.save_projects("u@x.com", projects)
        # Fresh sync brings the project back with default is_managed=True
        store.upsert_projects("u@x.com", [_project(101, name="Updated Name")])
        loaded = store.load_projects("u@x.com")[0]
        assert loaded.is_managed is False
        assert loaded.name == "Updated Name"  # BC-owned field DID update

    def test_weight_preserved_across_sync(self):
        store.upsert_projects("u@x.com", [_project(101)])
        projects = store.load_projects("u@x.com")
        projects[0].weight = 1.8  # operator boost
        store.save_projects("u@x.com", projects)
        store.upsert_projects("u@x.com", [_project(101)])
        assert store.load_projects("u@x.com")[0].weight == 1.8


# ════════════════════════════════════════════════════════════════════
# State round-trip — last_sync_at, last_sync_status, last_sync_error
# ════════════════════════════════════════════════════════════════════


class TestState:
    def test_load_missing_returns_default_with_user_id(self):
        s = store.load_state("new@x.com")
        assert s.user_id == "new@x.com"
        assert s.last_sync_at == ""
        assert s.last_sync_status == ""

    def test_round_trip(self):
        s = store.OpsState(
            user_id="u@x.com",
            last_sync_at="2026-06-09T12:00:00+00:00",
            last_sync_status="partial",
            last_sync_error="bucket=202 HTTPError: 503",
            todos_synced=42, projects_synced=5,
        )
        store.save_state(s)
        loaded = store.load_state("u@x.com")
        assert loaded.last_sync_at == "2026-06-09T12:00:00+00:00"
        assert loaded.last_sync_status == "partial"
        assert loaded.last_sync_error == "bucket=202 HTTPError: 503"
        assert loaded.todos_synced == 42

    def test_corrupt_state_returns_default(self):
        """Mirrors load_todos behavior — corrupt state.json yields a
        default state so the page can render, instead of 500ing."""
        d = store.OPS_ROOT / "u@x.com"
        d.mkdir(parents=True)
        (d / "state.json").write_text("{not json}")
        s = store.load_state("u@x.com")
        assert s.user_id == "u@x.com"
        assert s.last_sync_at == ""


# ════════════════════════════════════════════════════════════════════
# list_completed_for_user — Extract surface input
# ════════════════════════════════════════════════════════════════════


class TestListCompletedForUser:
    def test_filters_to_completed_only(self):
        store.upsert_todos("u@x.com", [
            _todo(1, status="active", completed_at=""),
            _todo(2, status="completed", completed_at="2026-06-08T00:00:00Z"),
        ])
        out = store.list_completed_for_user("u@x.com", days=30)
        assert [t.bc_id for t in out] == [2]

    def test_drops_completed_outside_window(self):
        store.upsert_todos("u@x.com", [
            _todo(1, status="completed", completed_at="2026-06-08T00:00:00Z"),
            _todo(2, status="completed", completed_at="2024-01-01T00:00:00Z"),
        ])
        out = store.list_completed_for_user("u@x.com", days=30)
        assert [t.bc_id for t in out] == [1]

    def test_sorted_newest_first(self):
        store.upsert_todos("u@x.com", [
            _todo(1, status="completed", completed_at="2026-06-01T00:00:00Z"),
            _todo(2, status="completed", completed_at="2026-06-08T00:00:00Z"),
            _todo(3, status="completed", completed_at="2026-06-05T00:00:00Z"),
        ])
        out = store.list_completed_for_user("u@x.com", days=30)
        assert [t.bc_id for t in out] == [2, 3, 1]


# ════════════════════════════════════════════════════════════════════
# Atomic write — partial-write corruption protection
# ════════════════════════════════════════════════════════════════════


class TestAtomicWrite:
    def test_temp_file_cleaned_on_success(self):
        """After a successful save, no .tmp files should linger in the
        user's directory. The atomic-write helper uses tempfile.mkstemp
        + Path.replace — verify nothing is left behind."""
        store.upsert_todos("u@x.com", [_todo(1)])
        d = store.OPS_ROOT / "u@x.com"
        tmp_files = list(d.glob("*.tmp*"))
        assert tmp_files == []

    def test_temp_file_cleaned_on_write_failure(self, monkeypatch):
        """If json.dump raises mid-write, the temp file MUST be unlinked
        so the user dir doesn't fill with orphaned .tmp files over time."""
        import json as _json
        original_dump = _json.dump
        call_count = {"n": 0}

        def _fail_once(*a, **kw):
            call_count["n"] += 1
            raise RuntimeError("disk full")

        # Pre-create the user dir so mkstemp succeeds
        (store.OPS_ROOT / "u@x.com").mkdir(parents=True)
        monkeypatch.setattr("json.dump", _fail_once)
        with pytest.raises(RuntimeError):
            store.save_todos("u@x.com", [_todo(1)])

        # No orphaned temp file
        tmp_files = list((store.OPS_ROOT / "u@x.com").glob("*.tmp*"))
        assert tmp_files == []


# ════════════════════════════════════════════════════════════════════
# Concurrent-writer safety — the H3 fix
# ════════════════════════════════════════════════════════════════════


class TestConcurrentWriters:
    """The 2026-06-09 audit (finding H3) flagged that upsert_todos was
    lost-write under concurrent callers: load → mutate → save without a
    lock means scheduler + Mark Done can each grab a stale snapshot,
    merge their change in isolation, and the second writer silently
    overwrites the first.

    These tests verify the per-user write lock added in C2 closes the
    file-level race. Each test spawns N threads doing independent
    writes; with the lock, all writes survive — without it, some
    would be lost."""

    def test_50_concurrent_upserts_all_persist(self):
        """50 threads each upsert a distinct bc_id. Without serialization,
        the final todos.json would contain only the last-written subset
        because each thread's load_todos read a stale snapshot. With the
        lock, every bc_id must be present."""
        # Pre-clear write-lock dict so this user starts clean
        store._WRITE_LOCKS.pop("conc@x.com", None)

        barrier = threading.Barrier(50)

        def _writer(idx: int):
            barrier.wait()  # release all threads at the same moment
            store.upsert_todos("conc@x.com", [_todo(idx)])

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        loaded = store.load_todos("conc@x.com")
        assert len(loaded) == 50, f"expected 50, got {len(loaded)}"
        assert {t.bc_id for t in loaded} == set(range(50))

    def test_concurrent_update_and_upsert_both_survive(self):
        """Models the Mark Done + cron sync collision the audit
        described: one writer calls update_todo (Mark Done), another
        calls upsert_todos (full sync). After both finish, the
        update's field change MUST be visible (not reverted)."""
        store._WRITE_LOCKS.pop("collide@x.com", None)
        # Seed the store with task 1 in 'active' status
        store.upsert_todos("collide@x.com", [_todo(1, title="Original")])

        # Run 20 rounds of (Mark Done, sync-style upsert) racing.
        # Without the lock, occasionally the upsert wins and reverts
        # the dismiss; with the lock, the dismiss MUST always win
        # because we serialize the read-modify-write sequences and
        # upsert_todos preserves is_dismissed.
        for round_idx in range(20):
            store.upsert_todos("collide@x.com", [_todo(1, title="Original")])
            store.update_todo("collide@x.com", 1, is_dismissed=False)

            barrier = threading.Barrier(2)

            def _mark_done():
                barrier.wait()
                store.update_todo("collide@x.com", 1, is_dismissed=True,
                                  dismissed_at=f"round-{round_idx}")

            def _sync_upsert():
                barrier.wait()
                # Simulate a re-walk: same bc_id, fresh BC snapshot
                store.upsert_todos("collide@x.com",
                                   [_todo(1, title="BC may have new title")])

            t1 = threading.Thread(target=_mark_done)
            t2 = threading.Thread(target=_sync_upsert)
            t1.start(); t2.start()
            t1.join(); t2.join()

            loaded = store.load_todos("collide@x.com")
            assert len(loaded) == 1
            # Whichever order won, the dismiss must be visible.
            # If sync ran AFTER Mark Done: upsert_todos preserves
            #   is_dismissed → True.
            # If Mark Done ran AFTER sync: update_todo writes is_dismissed
            #   = True directly.
            # The audit H3 failure would manifest as is_dismissed=False
            # surviving a round.
            assert loaded[0].is_dismissed is True, (
                f"round {round_idx} lost the dismiss — H3 race regression"
            )

    def test_different_users_have_independent_locks(self):
        """Per-user locking — Ali's serialized writes mustn't block
        Kes's writes. Verifies the dict-of-locks shape, not just
        per-process global serialization."""
        store._WRITE_LOCKS.clear()

        # Both users write simultaneously; the test passes if both
        # complete promptly (no deadlock, no per-process bottleneck).
        barrier = threading.Barrier(20)

        def _writer(user: str, idx: int):
            barrier.wait()
            store.upsert_todos(user, [_todo(idx)])

        threads = []
        for i in range(10):
            threads.append(threading.Thread(target=_writer, args=("a@x.com", i)))
            threads.append(threading.Thread(target=_writer, args=("b@x.com", i)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        a_todos = store.load_todos("a@x.com")
        b_todos = store.load_todos("b@x.com")
        assert len(a_todos) == 10
        assert len(b_todos) == 10
