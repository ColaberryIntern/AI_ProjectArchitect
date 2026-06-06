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
