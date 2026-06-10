"""Unit tests for execution/products/ops/cb_webhooks.py.

Covers subscription idempotency, event-routing decisions (skip /
respond / sentinel), and the no-secret guard rails.
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from execution.products.ops import cb_webhooks


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_webhooks, "SUBS_PATH", tmp_path / "webhook_subs.json")
    monkeypatch.setattr(cb_webhooks, "EVENT_LOG_PATH",
                        tmp_path / "webhook_events.jsonl")
    monkeypatch.setattr(cb_webhooks.cb_mention_worker, "SEEN_PATH",
                        tmp_path / "seen.json")
    # Default: no secret, no base URL — each test sets what it needs
    for k in ("OPS_CB_WEBHOOK_SECRET", "OPS_CB_WEBHOOK_BASE_URL",
              "OPS_CB_WEBHOOK_DEFAULT_USER"):
        monkeypatch.delenv(k, raising=False)
    yield


# ── configuration ─────────────────────────────────────────────────


def test_webhook_secret_returns_none_when_unset():
    assert cb_webhooks.webhook_secret() is None


def test_webhook_secret_strips_whitespace(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "  abc123  ")
    assert cb_webhooks.webhook_secret() == "abc123"


def test_payload_url_is_none_when_secret_unset():
    assert cb_webhooks.payload_url() is None


def test_payload_url_uses_base_url_and_secret(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("OPS_CB_WEBHOOK_BASE_URL", "https://advisor.example.com")
    assert cb_webhooks.payload_url() == \
        "https://advisor.example.com/webhooks/basecamp/s3cret"


# ── subscribe_user_buckets ───────────────────────────────────────


def test_subscribe_refuses_without_secret(monkeypatch):
    """Refusing to subscribe when secret is unset prevents creating BC
    subscriptions that POST to an unauthenticated URL."""
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))
    r = cb_webhooks.subscribe_user_buckets("ali@colaberry.com")
    assert r["status"] == "no_secret"
    assert r["buckets_added"] == 0


def test_subscribe_idempotent_skips_existing(monkeypatch):
    """Re-subscribing must skip buckets already in webhook_subs.json so
    BC doesn't send duplicate events per comment."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [{"id": 100}, {"id": 200}, {"id": 300}],
    )
    # Pre-seed: 100 already subscribed
    cb_webhooks._save_subs({"ali@colaberry.com": {"100": 9999}})

    created: list = []
    def _create(bucket, url, token):
        created.append(bucket)
        return bucket * 10, "ok"  # fake webhook id
    monkeypatch.setattr(cb_webhooks, "_create_webhook", _create)

    r = cb_webhooks.subscribe_user_buckets("ali@colaberry.com")
    assert r["buckets_added"] == 2
    assert r["buckets_existing"] == 1
    assert created == [200, 300]  # 100 was skipped


def test_subscribe_records_failures(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [{"id": 100}, {"id": 200}],
    )
    def _create(bucket, url, token):
        if bucket == 200:
            return None, "http_403"
        return 999, "ok"
    monkeypatch.setattr(cb_webhooks, "_create_webhook", _create)

    r = cb_webhooks.subscribe_user_buckets("ali@colaberry.com")
    assert r["buckets_added"] == 1
    assert r["failed"] == 1
    assert r["errors"] == [{"bucket": 200, "detail": "http_403"}]


# ── handle_event ──────────────────────────────────────────────────


def _payload(*, body: str = "@CB please help", rec_type: str = "Comment",
             comment_id: int = 1, parent_id: int = 2,
             parent_url: str = "https://3.basecamp.com/x/buckets/47502609/todos/2",
             bucket_id: int = 47502609) -> dict:
    return {
        "id": 999, "kind": "comment_created",
        "recording": {
            "id": comment_id, "type": rec_type,
            "content": f"<p>{body}</p>",
            "parent": {"id": parent_id, "type": "Todo", "app_url": parent_url},
            "bucket": {"id": bucket_id},
        },
    }


def test_handle_event_skips_non_comment_recordings():
    r = cb_webhooks.handle_event(_payload(rec_type="Todo"),
                                 user_email="ali@colaberry.com")
    assert r["skipped"] == "non_comment"


def test_handle_event_skips_when_trigger_not_in_body():
    r = cb_webhooks.handle_event(_payload(body="just some comment"),
                                 user_email="ali@colaberry.com")
    assert r["skipped"] == "no_trigger"


def test_handle_event_skips_when_parent_or_bucket_missing():
    p = _payload()
    p["recording"]["parent"] = {}  # no parent
    r = cb_webhooks.handle_event(p, user_email="ali@colaberry.com")
    assert r["skipped"] == "missing_parent_or_bucket"


def test_handle_event_skips_already_seen_comments(monkeypatch):
    """seen.json is shared with the polling path — webhook + poll must
    NOT both respond to the same mention."""
    cb_webhooks.cb_mention_worker._save_seen({"comment:42"})
    r = cb_webhooks.handle_event(_payload(comment_id=42),
                                 user_email="ali@colaberry.com")
    assert r["skipped"] == "already_seen"


def test_handle_event_skips_no_token_without_marking_seen(monkeypatch):
    """If our token is missing, don't burn the seen-set — the poll path
    should still be able to handle it once auth comes back."""
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: (None, "missing"))
    r = cb_webhooks.handle_event(_payload(comment_id=42),
                                 user_email="ali@colaberry.com")
    assert r["skipped"] == "no_token:missing"
    assert "comment:42" not in cb_webhooks.cb_mention_worker._seen()


def test_handle_event_skips_when_parent_already_closed(monkeypatch):
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))
    monkeypatch.setattr(cb_webhooks.cb_mention_worker, "_parent_is_closed",
                        lambda b, p, t: True)
    r = cb_webhooks.handle_event(_payload(),
                                 user_email="ali@colaberry.com")
    assert r["skipped"] == "parent_closed"


def test_handle_event_responds_and_marks_seen(monkeypatch):
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))
    monkeypatch.setattr(cb_webhooks.cb_mention_worker, "_parent_is_closed",
                        lambda b, p, t: False)
    monkeypatch.setattr("execution.products.ops.context_collector.collect",
                        lambda *a, **kw: {})
    monkeypatch.setattr("execution.products.ops.plan_inference.infer",
                        lambda **kw: {"anticipated_goal": "help"})
    posted: list = []
    def _post(bucket, rec, html, token):
        posted.append(html)
        return True, "ok"
    monkeypatch.setattr(cb_webhooks.cb_mention_worker, "_post_comment", _post)

    r = cb_webhooks.handle_event(_payload(comment_id=1234),
                                 user_email="ali@colaberry.com")
    assert r["responded"] is True
    assert len(posted) == 1
    assert "comment:1234" in cb_webhooks.cb_mention_worker._seen()


def test_handle_event_falls_back_to_sentinel_on_post_failure(monkeypatch):
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))
    monkeypatch.setattr(cb_webhooks.cb_mention_worker, "_parent_is_closed",
                        lambda b, p, t: False)
    monkeypatch.setattr("execution.products.ops.context_collector.collect",
                        lambda *a, **kw: {})
    monkeypatch.setattr("execution.products.ops.plan_inference.infer",
                        lambda **kw: {})
    posted: list = []
    def _post(bucket, rec, html, token):
        posted.append(html)
        if len(posted) == 1:
            return False, "http_500"  # rich fails
        return True, "ok"               # sentinel succeeds
    monkeypatch.setattr(cb_webhooks.cb_mention_worker, "_post_comment", _post)

    r = cb_webhooks.handle_event(_payload(),
                                 user_email="ali@colaberry.com")
    assert r["responded"] is False
    assert r["sentinel_posted"] is True
    assert posted[1] == cb_webhooks.cb_mention_worker.SENTINEL_HTML


# ── event log ─────────────────────────────────────────────────────


def test_event_log_appended_on_every_handle_event_call(monkeypatch):
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))
    monkeypatch.setattr(cb_webhooks.cb_mention_worker, "_parent_is_closed",
                        lambda b, p, t: False)
    monkeypatch.setattr("execution.products.ops.context_collector.collect",
                        lambda *a, **kw: {})
    monkeypatch.setattr("execution.products.ops.plan_inference.infer",
                        lambda **kw: {})
    monkeypatch.setattr(cb_webhooks.cb_mention_worker, "_post_comment",
                        lambda *a: (True, "ok"))

    cb_webhooks.handle_event(_payload(rec_type="Todo"),
                             user_email="ali@colaberry.com")
    cb_webhooks.handle_event(_payload(comment_id=99),
                             user_email="ali@colaberry.com")

    lines = cb_webhooks.EVENT_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["skipped"] == "non_comment"
    assert json.loads(lines[1])["responded"] is True
