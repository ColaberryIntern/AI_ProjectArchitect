"""Unit tests for per-user webhook URL derivation + resolution.

PR #10 routed every BC event to a single OPS_CB_WEBHOOK_DEFAULT_USER.
This follow-up gives each operator their own URL token so events route
to the right user's credentials. These tests pin the token-derivation
contract and the resolver behavior.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from execution.products.ops import cb_webhooks


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_webhooks, "SUBS_PATH", tmp_path / "webhook_subs.json")
    monkeypatch.setattr(cb_webhooks, "EVENT_LOG_PATH",
                        tmp_path / "webhook_events.jsonl")
    for k in ("OPS_CB_WEBHOOK_SECRET", "OPS_CB_WEBHOOK_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    yield


# ── _user_token_for ───────────────────────────────────────────────


def test_user_token_is_24_lowercase_hex_chars():
    tok = cb_webhooks._user_token_for("ali@colaberry.com", "s3cret")
    assert len(tok) == 24
    assert re.fullmatch(r"[0-9a-f]{24}", tok)


def test_user_token_deterministic_for_same_inputs():
    a = cb_webhooks._user_token_for("ali@colaberry.com", "s3cret")
    b = cb_webhooks._user_token_for("ali@colaberry.com", "s3cret")
    assert a == b


def test_user_token_differs_per_email_for_same_secret():
    a = cb_webhooks._user_token_for("ali@colaberry.com", "s3cret")
    b = cb_webhooks._user_token_for("kes@colaberry.com", "s3cret")
    assert a != b


def test_user_token_changes_when_secret_rotates():
    """Rotating OPS_CB_WEBHOOK_SECRET must reseat every operator's token
    — that's the rotation story for the whole webhook fleet."""
    a = cb_webhooks._user_token_for("ali@colaberry.com", "old")
    b = cb_webhooks._user_token_for("ali@colaberry.com", "new")
    assert a != b


# ── payload_url_for ───────────────────────────────────────────────


def test_payload_url_for_returns_none_without_secret():
    assert cb_webhooks.payload_url_for("ali@colaberry.com") is None


def test_payload_url_for_includes_secret_and_user_token(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("OPS_CB_WEBHOOK_BASE_URL", "https://advisor.example.com")
    url = cb_webhooks.payload_url_for("ali@colaberry.com")
    expected_tok = cb_webhooks._user_token_for("ali@colaberry.com", "s3cret")
    assert url == (
        f"https://advisor.example.com/webhooks/basecamp/s3cret/{expected_tok}"
    )


def test_payload_url_for_differs_per_user(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("OPS_CB_WEBHOOK_BASE_URL", "https://advisor.example.com")
    a = cb_webhooks.payload_url_for("ali@colaberry.com")
    b = cb_webhooks.payload_url_for("kes@colaberry.com")
    assert a != b


# ── resolve_user_email_from_token ─────────────────────────────────


def _fake_users(*emails):
    return [SimpleNamespace(email=e) for e in emails]


def test_resolver_returns_none_without_secret(monkeypatch):
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: _fake_users("ali@colaberry.com"),
    )
    # No OPS_CB_WEBHOOK_SECRET in env — can't validate against any user.
    assert cb_webhooks.resolve_user_email_from_token("anything") is None


def test_resolver_returns_email_for_matching_token(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: _fake_users(
            "ali@colaberry.com", "kes@colaberry.com",
        ),
    )
    tok = cb_webhooks._user_token_for("kes@colaberry.com", "s3cret")
    assert cb_webhooks.resolve_user_email_from_token(tok) == "kes@colaberry.com"


def test_resolver_returns_none_for_unknown_token(monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: _fake_users("ali@colaberry.com"),
    )
    assert cb_webhooks.resolve_user_email_from_token("0" * 24) is None


def test_resolver_swallows_tenancy_errors(monkeypatch):
    """If tenancy.list_users blows up we treat it as 'no match' (= 401)
    rather than 500ing the webhook — BC retries 5xx and we'd rather log
    + 401 than hammer the system."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")

    def _boom(active_only=True):
        raise RuntimeError("db down")
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users", _boom,
    )
    assert cb_webhooks.resolve_user_email_from_token("0" * 24) is None


# ── subscribe_user_buckets uses per-user URL ─────────────────────


def test_subscribe_user_buckets_passes_per_user_url(monkeypatch):
    """The URL handed to BC's webhook subscription must be the new
    per-user form, not the legacy single-segment URL."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("OPS_CB_WEBHOOK_BASE_URL", "https://advisor.example.com")
    monkeypatch.setattr(cb_webhooks.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [{"id": 100}, {"id": 200}],
    )

    captured_urls: list[str] = []

    def _create(bucket, url, token):
        captured_urls.append(url)
        return bucket * 10, "ok"
    monkeypatch.setattr(cb_webhooks, "_create_webhook", _create)

    cb_webhooks.subscribe_user_buckets("kes@colaberry.com")
    expected_tok = cb_webhooks._user_token_for("kes@colaberry.com", "s3cret")
    assert captured_urls, "expected _create_webhook to be called"
    for url in captured_urls:
        assert url == (
            "https://advisor.example.com/webhooks/basecamp/"
            f"s3cret/{expected_tok}"
        )
