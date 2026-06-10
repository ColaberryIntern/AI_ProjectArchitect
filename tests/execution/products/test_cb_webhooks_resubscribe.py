"""Unit tests for cb_webhooks.resubscribe_all_users.

Covers the daily cron's no-secret guard, per-user iteration, error
isolation, integration with subscribe_user_buckets, and the
resubscribe_history.jsonl ledger.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from execution.products.ops import cb_webhooks


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_webhooks, "SUBS_PATH",
                        tmp_path / "webhook_subs.json")
    monkeypatch.setattr(cb_webhooks, "RESUBSCRIBE_HISTORY_PATH",
                        tmp_path / "resubscribe_history.jsonl")
    for k in ("OPS_CB_WEBHOOK_SECRET", "OPS_CB_WEBHOOK_BASE_URL",
              "OPS_CB_WEBHOOK_DEFAULT_USER"):
        monkeypatch.delenv(k, raising=False)
    yield


def _user(user_id: str, email: str) -> MagicMock:
    u = MagicMock()
    u.user_id = user_id
    u.email = email
    return u


def _vault_row(tool_name: str = "basecamp_ai_clone") -> MagicMock:
    c = MagicMock()
    c.tool_name = tool_name
    return c


# ── no-secret guard ───────────────────────────────────────────────


def test_resubscribe_noop_without_secret(monkeypatch):
    """No subscriptions get created and no history is written when
    OPS_CB_WEBHOOK_SECRET is unset."""
    called: list = []
    monkeypatch.setattr("execution.products.library.tenancy.list_users",
                        lambda active_only=True: called.append("list_users") or [])
    r = cb_webhooks.resubscribe_all_users()
    assert r == {"status": "no_secret"}
    assert called == []  # short-circuited before touching tenancy
    assert not cb_webhooks.RESUBSCRIBE_HISTORY_PATH.exists()


# ── happy path: aggregation across users ──────────────────────────


def test_resubscribe_aggregates_totals_across_users(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")

    users = [
        _user("u1", "ali@colaberry.com"),
        _user("u2", "bob@colaberry.com"),
    ]
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: users,
    )
    monkeypatch.setattr(
        "execution.products.library.vault.list_for_user",
        lambda user_id, caller_id=None: [_vault_row()],
    )

    def _fake_subscribe(email, max_buckets=None):
        if email == "ali@colaberry.com":
            return {"status": "ok", "buckets_added": 3,
                    "buckets_existing": 1, "failed": 0, "errors": []}
        return {"status": "ok", "buckets_added": 2,
                "buckets_existing": 4, "failed": 1, "errors": []}
    monkeypatch.setattr(cb_webhooks, "subscribe_user_buckets", _fake_subscribe)

    r = cb_webhooks.resubscribe_all_users()
    assert r["users_processed"] == 2
    assert r["total_added"] == 5
    assert r["total_existing"] == 5
    assert r["total_failed"] == 1
    assert r["fatal_error"] is None
    assert len(r["per_user"]) == 2
    assert {p["email"] for p in r["per_user"]} == \
        {"ali@colaberry.com", "bob@colaberry.com"}


def test_resubscribe_skips_users_without_vault_token(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")

    users = [
        _user("u1", "ali@colaberry.com"),
        _user("u2", "no-bc@colaberry.com"),
    ]
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: users,
    )

    def _list_for_user(user_id, caller_id=None):
        if user_id == "u1":
            return [_vault_row()]
        return [_vault_row(tool_name="github")]
    monkeypatch.setattr(
        "execution.products.library.vault.list_for_user",
        _list_for_user,
    )

    sub_calls: list = []
    def _fake_subscribe(email, max_buckets=None):
        sub_calls.append(email)
        return {"status": "ok", "buckets_added": 0,
                "buckets_existing": 0, "failed": 0, "errors": []}
    monkeypatch.setattr(cb_webhooks, "subscribe_user_buckets", _fake_subscribe)

    r = cb_webhooks.resubscribe_all_users()
    assert sub_calls == ["ali@colaberry.com"]
    assert r["users_processed"] == 1


# ── error isolation ──────────────────────────────────────────────


def test_resubscribe_records_per_user_exception_and_continues(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")

    users = [
        _user("u1", "boom@colaberry.com"),
        _user("u2", "ok@colaberry.com"),
    ]
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: users,
    )
    monkeypatch.setattr(
        "execution.products.library.vault.list_for_user",
        lambda user_id, caller_id=None: [_vault_row()],
    )

    def _fake_subscribe(email, max_buckets=None):
        if email == "boom@colaberry.com":
            raise RuntimeError("boom")
        return {"status": "ok", "buckets_added": 1,
                "buckets_existing": 0, "failed": 0, "errors": []}
    monkeypatch.setattr(cb_webhooks, "subscribe_user_buckets", _fake_subscribe)

    r = cb_webhooks.resubscribe_all_users()
    assert r["users_processed"] == 2
    by_email = {p["email"]: p for p in r["per_user"]}
    assert by_email["boom@colaberry.com"]["status"] == "exception"
    assert by_email["boom@colaberry.com"]["error"] == "RuntimeError"
    assert by_email["ok@colaberry.com"]["status"] == "ok"
    assert r["total_added"] == 1


# ── integration: real subscribe_user_buckets path ────────────────


def test_resubscribe_writes_subs_through_real_subscribe_path(monkeypatch):
    """End-to-end: one mocked user, real subscribe_user_buckets, mocked
    discover_projects + _create_webhook. Verifies _save_subs is hit and
    webhook_subs.json reflects the new subscriptions."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")

    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: [_user("u1", "ali@colaberry.com")],
    )
    monkeypatch.setattr(
        "execution.products.library.vault.list_for_user",
        lambda user_id, caller_id=None: [_vault_row()],
    )
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [{"id": 100}, {"id": 200}],
    )

    def _create(bucket, url, token):
        return bucket * 10, "ok"
    monkeypatch.setattr(cb_webhooks, "_create_webhook", _create)

    r = cb_webhooks.resubscribe_all_users()
    assert r["total_added"] == 2
    assert r["total_existing"] == 0

    saved = json.loads(cb_webhooks.SUBS_PATH.read_text(encoding="utf-8"))
    assert saved["ali@colaberry.com"] == {"100": 1000, "200": 2000}


# ── history ledger ───────────────────────────────────────────────


def test_resubscribe_history_appends_one_line_per_call(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: [],
    )

    cb_webhooks.resubscribe_all_users()
    cb_webhooks.resubscribe_all_users()
    cb_webhooks.resubscribe_all_users()

    lines = cb_webhooks.RESUBSCRIBE_HISTORY_PATH.read_text(
        encoding="utf-8",
    ).strip().splitlines()
    assert len(lines) == 3
    for line in lines:
        rec = json.loads(line)
        assert "started_at" in rec
        assert "finished_at" in rec
        assert rec["users_processed"] == 0


def test_resubscribe_handles_tenancy_list_users_failure(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")

    def _boom(active_only=True):
        raise RuntimeError("db down")
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users", _boom,
    )

    r = cb_webhooks.resubscribe_all_users()
    assert r["fatal_error"] == "list_users_failed:RuntimeError"
    assert r["users_processed"] == 0
