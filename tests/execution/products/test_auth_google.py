"""[Auth 2] auth_google tests — fully offline (no network calls).

The OAuth code-exchange path is monkeypatched. Real Google integration
is verified manually after Ali registers the OAuth app in Cloud Console.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from execution.products.library import auth_google, tenancy


@pytest.fixture
def env_full(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI",
                              "https://advisor.colaberry.ai/auth/callback")
    monkeypatch.setenv("LIBRARY_SESSION_SECRET", "test-session-secret-12345")


@pytest.fixture
def env_partial(monkeypatch):
    """Only some env set → SSO disabled."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    # Intentionally not setting the others


@pytest.fixture
def isolated_tenancy(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "TENANT_ROOT", tmp_path / "_tenants")
    tenancy.seed_initial_companies_and_users()
    yield tmp_path


# ── Enablement check ────────────────────────────────────────────


def test_disabled_when_env_missing(env_partial):
    assert not auth_google.is_enabled()
    assert "missing env" in auth_google.disabled_reason()


def test_enabled_when_env_full(env_full):
    assert auth_google.is_enabled()
    assert auth_google.disabled_reason() == "enabled"


# ── URL building ─────────────────────────────────────────────────


def test_build_login_url_contains_required_params(env_full):
    url = auth_google.build_login_url(state="abc123")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=test-client-id" in url
    assert "state=abc123" in url
    assert "redirect_uri=https%3A%2F%2Fadvisor.colaberry.ai%2Fauth%2Fcallback" in url
    assert "scope=openid+email+profile" in url
    assert "access_type=offline" in url


def test_build_login_url_fails_when_disabled(env_partial):
    with pytest.raises(RuntimeError):
        auth_google.build_login_url()


# ── Domain resolution ───────────────────────────────────────────


def test_resolve_company_known_domain():
    company_id, mapping = auth_google.resolve_company_for_email("ali@colaberry.com")
    assert company_id == "colaberry"
    assert mapping["auto_provision"] is True


def test_resolve_company_unknown_domain():
    company_id, _ = auth_google.resolve_company_for_email("stranger@example.com")
    assert company_id is None


# ── Path policy ─────────────────────────────────────────────────


def test_library_requires_login():
    assert auth_google.path_requires_login("/library/")
    assert auth_google.path_requires_login("/library/use-cases?ws=global")
    assert not auth_google.path_requires_login("/")


def test_marketing_paths_are_anonymous():
    assert auth_google.path_is_anonymous("/")
    assert auth_google.path_is_anonymous("/advisory/anything")
    assert auth_google.path_is_anonymous("/static/css/x.css")


# ── Provisioning ────────────────────────────────────────────────


def test_existing_user_login_records_login(isolated_tenancy):
    ali = tenancy.get_user("ali@colaberry.com")
    assert ali.last_login_at is None
    user, status = auth_google.provision_or_lookup_user({
        "email": "ali@colaberry.com", "email_verified": True,
        "sub": "google-sub-123", "name": "Ali",
    })
    assert status == "ok"
    assert user.user_id == ali.user_id
    assert tenancy.get_user(ali.user_id).last_login_at is not None
    assert tenancy.get_user(ali.user_id).google_subject == "google-sub-123"


def test_new_user_from_known_domain_provisions(isolated_tenancy):
    user, status = auth_google.provision_or_lookup_user({
        "email": "newperson@colaberry.com", "email_verified": True,
        "sub": "google-sub-new", "name": "New Person",
    })
    assert status == "provisioned"
    assert user.company_id == "colaberry"
    assert "consumer" in user.roles


def test_unknown_domain_queued_for_review(isolated_tenancy):
    user, status = auth_google.provision_or_lookup_user({
        "email": "outsider@unknown.com", "email_verified": True,
        "sub": "x", "name": "Outsider",
    })
    assert user is None
    assert status == "queued_for_review"


def test_unverified_email_rejected(isolated_tenancy):
    user, status = auth_google.provision_or_lookup_user({
        "email": "fake@colaberry.com", "email_verified": False,
        "sub": "x", "name": "Fake",
    })
    assert user is None
    assert status == "rejected_unverified"


def test_missing_email_rejected(isolated_tenancy):
    user, status = auth_google.provision_or_lookup_user({
        "email_verified": True, "sub": "x",
    })
    assert user is None
    assert status == "rejected_unverified"


# ── Session JWT ─────────────────────────────────────────────────


def test_issue_and_verify_session_token_roundtrip(env_full, isolated_tenancy):
    ali = tenancy.get_user("ali@colaberry.com")
    token = auth_google.issue_session_token(ali)
    assert token.count(".") == 2
    payload = auth_google.verify_session_token(token)
    assert payload is not None
    assert payload["sub"] == ali.user_id
    assert payload["email"] == "ali@colaberry.com"
    assert payload["company"] == "colaberry"


def test_verify_session_rejects_tampered_token(env_full, isolated_tenancy):
    ali = tenancy.get_user("ali@colaberry.com")
    token = auth_google.issue_session_token(ali)
    # flip a char in the payload
    parts = token.split(".")
    tampered = f"{parts[0]}.{parts[1][:-1]}X.{parts[2]}"
    assert auth_google.verify_session_token(tampered) is None


def test_verify_session_rejects_expired_token(env_full, isolated_tenancy):
    ali = tenancy.get_user("ali@colaberry.com")
    token = auth_google.issue_session_token(ali, ttl_seconds=-10)
    assert auth_google.verify_session_token(token) is None


def test_current_user_from_cookie_resolves(env_full, isolated_tenancy):
    ali = tenancy.get_user("ali@colaberry.com")
    token = auth_google.issue_session_token(ali)
    resolved = auth_google.current_user_from_cookie(token)
    assert resolved is not None
    assert resolved.user_id == ali.user_id


def test_current_user_from_cookie_none_on_empty():
    assert auth_google.current_user_from_cookie(None) is None
    assert auth_google.current_user_from_cookie("") is None
    assert auth_google.current_user_from_cookie("garbage") is None
