"""Tests for AI-account email detection in app/routers/basecamp_connect.py.

The detection drives a green AI ACCOUNT badge on /profile/connect-basecamp;
false positives would mean a user sees the badge when their human account
is actually wired up (and their posts would NOT show as 'X AI'). False
negatives would mean a user's AI account is correctly wired but the page
fails to confirm it -- confusing but harmless."""
from __future__ import annotations

import pytest

from app.routers.basecamp_connect import (
    is_ai_account_email, _friendly_ai_name,
)


# ── Positive cases (should be detected as AI account) ────────────────


@pytest.mark.parametrize("email,reason", [
    ("karun-ai@colaberry.com", "dash-ai"),
    ("KARUN-AI@COLABERRY.COM", "case-insensitive"),
    ("ali-ai@colaberry.com", "Ali's AI account"),
    ("kes-ai@colaberry.com", "Kes AI"),
    ("ram-ai@colaberry.com", "Ram AI"),
    ("karun+ai@colaberry.com", "Gmail plus alias"),
    ("karun.ai@colaberry.com", "dot alias"),
    ("contractor-ai@example.org", "non-colaberry domain still detected"),
    ("ai@colaberry.com", "bare 'ai' local part"),
    ("  karun-ai@colaberry.com  ", "whitespace stripped"),
])
def test_is_ai_account_true(email, reason):
    assert is_ai_account_email(email), f"expected AI for {email!r} ({reason})"


# ── Negative cases (should NOT be detected as AI account) ────────────


@pytest.mark.parametrize("email,reason", [
    ("karun@colaberry.com", "human account"),
    ("ali@colaberry.com", "Ali's human account"),
    ("KARUN@COLABERRY.COM", "case-insensitive human"),
    ("aimee@colaberry.com", "name contains 'ai' but isn't suffix"),
    ("daimler@colaberry.com", "ai in middle of name"),
    ("karun.aimee@colaberry.com", "dot+name not the suffix"),
    ("ai-developer@colaberry.com", "ai at start, not end of local"),
    ("", "empty"),
    ("not-an-email", "no @ sign"),
    ("@colaberry.com", "empty local part"),
])
def test_is_ai_account_false(email, reason):
    assert not is_ai_account_email(email), \
        f"expected NOT AI for {email!r} ({reason})"


# ── Friendly name derivation ─────────────────────────────────────────


@pytest.mark.parametrize("email,expected", [
    ("karun-ai@colaberry.com", "Karun AI"),
    ("ali-ai@colaberry.com", "Ali AI"),
    ("kes-ai@colaberry.com", "Kes AI"),
    ("karun+ai@colaberry.com", "Karun AI"),
    ("karun.ai@colaberry.com", "Karun AI"),
    ("KARUN-AI@COLABERRY.COM", "Karun AI"),  # case
])
def test_friendly_ai_name_for_ai_accounts(email, expected):
    assert _friendly_ai_name(email) == expected


def test_friendly_ai_name_for_human_with_fallback_suffix():
    # Human account: no automatic " AI" appended unless caller asks for it
    assert _friendly_ai_name("karun@colaberry.com") == "Karun"
    assert _friendly_ai_name("karun@colaberry.com", fallback_suffix=" AI") \
        == "Karun AI"


def test_friendly_ai_name_handles_compound_local_parts():
    # John Smith -> "John Smith AI" not "Johnsmith AI"
    assert _friendly_ai_name("john.smith-ai@colaberry.com") == "John Smith AI"
    assert _friendly_ai_name("mary-jane-ai@colaberry.com") == "Mary Jane AI"


def test_friendly_ai_name_empty_safe():
    assert _friendly_ai_name("") == ""
    assert _friendly_ai_name("not-an-email") == ""


# ── _resolve_account_person_id — store the right id at connect time ──
# Regression guard for the 2026-06-16 Swati incident: the callback must
# persist the ACCOUNT-scoped person id (from {account}/my/profile.json),
# NOT the Launchpad identity id from /authorization.json. The two live in
# different namespaces and never match, so caching the Launchpad id left
# the My Day classifier matching zero todo assignees.


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_resolve_account_person_id_picks_colaberry_account(monkeypatch):
    import json as _json
    from app.routers import basecamp_connect as bc
    info = {
        "identity": {"id": 27309320, "email_address": "swati@colaberry.com"},
        "accounts": [
            {"id": 3945211, "href": "https://3.basecampapi.com/3945211"},
            {"id": 9999999, "href": "https://3.basecampapi.com/9999999"},
        ],
    }
    captured = {}
    def _fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        return _FakeResp(_json.dumps({"id": 48041031,
                                      "email_address": "swati@colaberry.com"}).encode())
    monkeypatch.setattr(bc.urllib.request, "urlopen", _fake_urlopen)

    pid = bc._resolve_account_person_id("tok", info)

    assert pid == 48041031                       # account id, NOT 27309320
    assert "3945211/my/profile.json" in captured["url"]


def test_resolve_account_person_id_returns_zero_on_failure(monkeypatch):
    from app.routers import basecamp_connect as bc
    info = {"accounts": [{"id": 3945211, "href": "https://3.basecampapi.com/3945211"}]}
    def _boom(req, timeout=15):
        raise OSError("network down")
    monkeypatch.setattr(bc.urllib.request, "urlopen", _boom)
    # 0 lets the caller fall back to the Launchpad id rather than crash.
    assert bc._resolve_account_person_id("tok", info) == 0


def test_resolve_account_person_id_no_accounts_returns_zero():
    from app.routers import basecamp_connect as bc
    assert bc._resolve_account_person_id("tok", {"accounts": []}) == 0
    assert bc._resolve_account_person_id("tok", {}) == 0
