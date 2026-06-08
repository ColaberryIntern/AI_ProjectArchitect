"""Unit tests for basecamp_provisioning helpers.

The BC API call path itself is mocked since hitting BC in tests would
be slow + flaky. The helpers under test are: email/name derivation
(including the CB System hardcoded override for Ali), AI-account
detection, and status_for_user against fake users."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from execution.products.library import basecamp_provisioning as bp


def _u(email, **extra):
    """Minimal user shape for the helpers."""
    base = {
        "email": email,
        "display_name": extra.pop("display_name", "Test User"),
        "personal_bc_project_id": extra.pop("personal_bc_project_id", 7463955),
        "bc_ai_user_email": extra.pop("bc_ai_user_email", None),
        "bc_ai_user_id": extra.pop("bc_ai_user_id", None),
        "bc_ai_provisioned_at": extra.pop("bc_ai_provisioned_at", None),
        "bc_ai_clone_name": extra.pop("bc_ai_clone_name", ""),
        "bc_extra_buckets": extra.pop("bc_extra_buckets", []),
        "user_id": extra.pop("user_id", "usr-test"),
        **extra,
    }
    return SimpleNamespace(**base)


# ── derive_ai_email ───────────────────────────────────────────────────


def test_derive_ai_email_default_plus_scheme():
    assert bp.derive_ai_email("karun@colaberry.com") == "karun+ai@colaberry.com"


def test_derive_ai_email_handles_uppercase():
    assert bp.derive_ai_email("KARUN@COLABERRY.COM") == "karun+ai@colaberry.com"


def test_derive_ai_email_dash_scheme(monkeypatch):
    monkeypatch.setenv("COLABERRY_AI_EMAIL_SCHEME", "dash")
    assert bp.derive_ai_email("karun@colaberry.com") == "karun-ai@colaberry.com"


def test_derive_ai_email_dot_scheme(monkeypatch):
    monkeypatch.setenv("COLABERRY_AI_EMAIL_SCHEME", "dot")
    assert bp.derive_ai_email("karun@colaberry.com") == "karun.ai@colaberry.com"


def test_derive_ai_email_ali_uses_standard_scheme():
    """Ali follows the same +ai@ scheme as everyone else. (CB System
    remains the BASECAMP_ACCESS_TOKEN admin identity but is no longer
    Ali's per-user AI persona since Ali never had Vishnu's login
    password.)"""
    assert bp.derive_ai_email("ali@colaberry.com") == "ali+ai@colaberry.com"


def test_derive_ai_email_empty_safe():
    assert bp.derive_ai_email("") == ""
    assert bp.derive_ai_email("not-an-email") == ""


# ── derive_ai_display_name ────────────────────────────────────────────


def test_derive_display_name_default():
    u = _u("karun@colaberry.com", display_name="Karun Vellanki")
    assert bp.derive_ai_display_name(u) == "Karun Vellanki AI"


def test_derive_display_name_ali_uses_standard_scheme():
    u = _u("ali@colaberry.com", display_name="Ali Muwwakkil")
    assert bp.derive_ai_display_name(u) == "Ali Muwwakkil AI"


def test_derive_display_name_no_display_name_uses_local_part():
    u = _u("kes.developer@colaberry.com", display_name="")
    assert bp.derive_ai_display_name(u) == "Kes Developer AI"


# ── is_ai_account_for_user (context-aware) ────────────────────────────


def test_is_ai_for_user_suffix_works_for_anyone():
    karun = _u("karun@colaberry.com")
    assert bp.is_ai_account_for_user("karun+ai@colaberry.com", karun)
    assert bp.is_ai_account_for_user("karun-ai@colaberry.com", karun)
    assert bp.is_ai_account_for_user("anything-ai@example.org", karun)


def test_is_ai_for_user_no_overrides_currently():
    """HARDCODED_AI_OVERRIDES is empty in the current rollout (Ali
    switched off CB System on 2026-06-08). vishnu@ is no longer
    treated as an AI account for anyone; only suffix-matching
    addresses are. Test guards against silent re-introduction of an
    override (regression check)."""
    ali = _u("ali@colaberry.com")
    karun = _u("karun@colaberry.com")
    # vishnu@ doesn't match -ai/+ai/.ai suffix, so it's not AI for anyone
    assert not bp.is_ai_account_for_user("vishnu@colaberry.com", ali)
    assert not bp.is_ai_account_for_user("vishnu@colaberry.com", karun)
    # The map is empty (no per-user overrides currently)
    assert bp.HARDCODED_AI_OVERRIDES == {}


def test_is_ai_for_user_rejects_lookalike_names():
    """aimee@ contains 'ai' but isn't an AI account by suffix; should
    return False for everyone."""
    karun = _u("karun@colaberry.com")
    assert not bp.is_ai_account_for_user("aimee@colaberry.com", karun)
    assert not bp.is_ai_account_for_user("ali@colaberry.com", karun)


# ── status_for_user ───────────────────────────────────────────────────


def test_status_not_provisioned_when_no_bc_ai_user_id(monkeypatch):
    u = _u("karun@colaberry.com",
                display_name="Karun Vellanki",
                bc_ai_user_id=None, bc_ai_user_email=None)
    # Force the basecamp_oauth_token lookup to return None so the test
    # doesn't depend on a real vault.
    from execution.products.library import basecamp_oauth_token
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                                            MagicMock(return_value=None))
    st = bp.status_for_user(u)
    assert st["state"] == "not_provisioned"
    assert st["provisioned"] is False
    assert st["ai_email"] == "karun+ai@colaberry.com"
    assert st["ai_display_name"] == "Karun Vellanki AI"


def test_status_invited_when_provisioned_but_no_oauth(monkeypatch):
    u = _u("karun@colaberry.com", display_name="Karun Vellanki",
                bc_ai_user_id=99999, bc_ai_user_email="karun+ai@colaberry.com",
                bc_ai_provisioned_at="2026-06-07T12:00:00Z")
    from execution.products.library import basecamp_oauth_token
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                                            MagicMock(return_value=None))
    st = bp.status_for_user(u)
    assert st["state"] == "invited"
    assert st["provisioned"] is True


def test_status_oauth_human_when_user_connected_human_account(monkeypatch):
    u = _u("karun@colaberry.com", display_name="Karun Vellanki")
    from execution.products.library import basecamp_oauth_token
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                                            MagicMock(return_value={
                                                "legacy": False,
                                                "bc_user_email": "karun@colaberry.com",
                                                "bc_user_id": 12345,
                                            }))
    st = bp.status_for_user(u)
    assert st["state"] == "oauth_granted_human"
    assert st["vault_oauth_granted"] is True
    assert st["vault_oauth_is_ai"] is False


def test_status_oauth_ai_when_user_connected_ai_account(monkeypatch):
    u = _u("karun@colaberry.com", display_name="Karun Vellanki",
                bc_ai_user_id=99999)
    from execution.products.library import basecamp_oauth_token
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                                            MagicMock(return_value={
                                                "legacy": False,
                                                "bc_user_email": "karun+ai@colaberry.com",
                                                "bc_user_id": 99999,
                                            }))
    st = bp.status_for_user(u)
    assert st["state"] == "oauth_granted_ai"
    assert st["vault_oauth_is_ai"] is True


def test_status_ali_oauth_ai_when_bound_to_ali_plus_ai(monkeypatch):
    """Post-2026-06-08: Ali uses ali+ai@colaberry.com like everyone
    else. When his vault grant is bound to that, state is
    oauth_granted_ai."""
    u = _u("ali@colaberry.com", display_name="Ali Muwwakkil",
                bc_ai_user_id=99000999)
    from execution.products.library import basecamp_oauth_token
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                                            MagicMock(return_value={
                                                "legacy": False,
                                                "bc_user_email": "ali+ai@colaberry.com",
                                                "bc_user_id": 99000999,
                                            }))
    st = bp.status_for_user(u)
    assert st["state"] == "oauth_granted_ai"
    assert st["vault_oauth_is_ai"] is True
    assert st["ai_email"] == "ali+ai@colaberry.com"
    assert st["ai_display_name"] == "Ali Muwwakkil AI"


# ── provision_bc_ai_account error guards ──────────────────────────────


def test_provision_returns_error_when_no_personal_project(monkeypatch):
    monkeypatch.setenv("BASECAMP_ACCESS_TOKEN", "fake-admin-token")
    u = _u("kes@colaberry.com", personal_bc_project_id=None)
    r = bp.provision_bc_ai_account(u)
    assert r.ok is False
    assert r.error_code == "no_personal_project"


def test_provision_returns_error_when_no_human_email(monkeypatch):
    monkeypatch.setenv("BASECAMP_ACCESS_TOKEN", "fake-admin-token")
    u = _u("", personal_bc_project_id=12345)
    r = bp.provision_bc_ai_account(u)
    assert r.ok is False
    assert r.error_code == "no_human_email"
