"""Tests for execution/products/library/subscriptions.py [Workflow 3b].

Storage redirected to a tmp_path so tests don't touch the real
output/library/_subscriptions/subscriptions.jsonl.
"""
from __future__ import annotations

import pytest

from execution.products.library import subscriptions as subs_mod
from execution.products.library.subscriptions import (
    ItemSubscription,
    is_subscribed,
    list_subscriptions_for_item,
    list_subscriptions_for_user,
    mark_notified,
    subscribe,
    unsubscribe,
)


@pytest.fixture(autouse=True)
def tmp_subscriptions_file(tmp_path, monkeypatch):
    """Redirect SUB_DIR + SUB_FILE so each test runs against a fresh file."""
    sub_dir = tmp_path / "_subscriptions"
    sub_file = sub_dir / "subscriptions.jsonl"
    monkeypatch.setattr(subs_mod, "SUB_DIR", sub_dir)
    monkeypatch.setattr(subs_mod, "SUB_FILE", sub_file)
    yield sub_file


def test_initial_state_is_unsubscribed():
    assert is_subscribed("u1", "skill", "s1") is False
    assert list_subscriptions_for_item("skill", "s1") == []
    assert list_subscriptions_for_user("u1") == []


def test_subscribe_creates_active_row():
    s = subscribe("u1", "skill", "s1", "github.com/u1/repo")
    assert isinstance(s, ItemSubscription)
    assert s.user_id == "u1"
    assert s.item_kind == "skill"
    assert s.item_id == "s1"
    assert s.target_repo == "github.com/u1/repo"
    assert s.subscribed_at  # non-empty timestamp
    assert s.unsubscribed_at is None
    assert is_subscribed("u1", "skill", "s1") is True


def test_subscribe_is_idempotent():
    s1 = subscribe("u1", "skill", "s1", "github.com/u1/repo")
    s2 = subscribe("u1", "skill", "s1", "github.com/u1/repo")
    # Same row returned, no duplicate appended
    assert s1.subscribed_at == s2.subscribed_at
    items = list_subscriptions_for_item("skill", "s1")
    assert len(items) == 1


def test_unsubscribe_tombstones_the_row():
    subscribe("u1", "skill", "s1", "github.com/u1/repo")
    assert unsubscribe("u1", "skill", "s1") is True
    assert is_subscribed("u1", "skill", "s1") is False
    # list_for_item filters out tombstoned rows
    assert list_subscriptions_for_item("skill", "s1") == []
    # Second unsubscribe is a no-op (returns False, nothing to change)
    assert unsubscribe("u1", "skill", "s1") is False


def test_resubscribe_reactivates_tombstone_without_duplicate():
    subscribe("u1", "skill", "s1", "github.com/u1/old-repo")
    unsubscribe("u1", "skill", "s1")
    reactivated = subscribe("u1", "skill", "s1", "github.com/u1/new-repo")
    # Re-activated with the NEW target_repo, no duplicate row appended
    assert reactivated.target_repo == "github.com/u1/new-repo"
    assert reactivated.unsubscribed_at is None
    items_for_user = list_subscriptions_for_user("u1")
    assert len(items_for_user) == 1


def test_list_for_item_returns_only_active_subscribers():
    subscribe("u1", "skill", "s1", "github.com/u1/r")
    subscribe("u2", "skill", "s1", "github.com/u2/r")
    subscribe("u3", "skill", "s1", "github.com/u3/r")
    unsubscribe("u2", "skill", "s1")
    items = list_subscriptions_for_item("skill", "s1")
    user_ids = {i.user_id for i in items}
    assert user_ids == {"u1", "u3"}


def test_list_for_user_returns_all_active_kinds_and_items():
    subscribe("u1", "skill", "s1", "github.com/u1/r")
    subscribe("u1", "agent", "a1", "github.com/u1/r")
    subscribe("u1", "mcp", "m1", "github.com/u1/r")
    subscribe("u2", "skill", "s1", "github.com/u2/r")
    items = list_subscriptions_for_user("u1")
    kinds = {i.item_kind for i in items}
    assert kinds == {"skill", "agent", "mcp"}
    assert len(items) == 3


def test_mark_notified_stamps_last_notified_at():
    subscribe("u1", "skill", "s1", "github.com/u1/r")
    assert mark_notified("u1", "skill", "s1") is True
    items = list_subscriptions_for_item("skill", "s1")
    assert items[0].last_notified_at is not None
    assert items[0].unsubscribed_at is None


def test_mark_notified_on_unsubscribed_row_returns_false():
    subscribe("u1", "skill", "s1", "github.com/u1/r")
    unsubscribe("u1", "skill", "s1")
    # Tombstoned rows do not get notified
    assert mark_notified("u1", "skill", "s1") is False


def test_subscribe_rejects_empty_required_fields():
    with pytest.raises(ValueError):
        subscribe("", "skill", "s1", "github.com/u1/r")
    with pytest.raises(ValueError):
        subscribe("u1", "", "s1", "github.com/u1/r")
    with pytest.raises(ValueError):
        subscribe("u1", "skill", "", "github.com/u1/r")
    with pytest.raises(ValueError):
        subscribe("u1", "skill", "s1", "")


def test_storage_survives_module_reload(tmp_subscriptions_file):
    """Writing then re-reading preserves all rows including tombstones."""
    subscribe("u1", "skill", "s1", "github.com/u1/r")
    subscribe("u2", "agent", "a1", "github.com/u2/r")
    unsubscribe("u1", "skill", "s1")
    # Direct file read — confirm both rows persisted, including tombstoned
    raw = tmp_subscriptions_file.read_text(encoding="utf-8")
    assert raw.count("\n") == 2
    assert "u1" in raw and "u2" in raw
    assert "unsubscribed_at" in raw
