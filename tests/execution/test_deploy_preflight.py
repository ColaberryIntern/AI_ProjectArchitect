"""Tests for [Deploy 1] preflight script.

Verifies the env-var validation and exit-code policy so the deploy
gate actually blocks bad configs.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


# Add scripts/ to path so we can import the preflight as a module
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def preflight(monkeypatch, tmp_path):
    # Reload to pick up any TENANT_ROOT changes
    if "deploy_preflight" in sys.modules:
        del sys.modules["deploy_preflight"]
    mod = importlib.import_module("deploy_preflight")
    # Redirect TENANT_ROOT + REPO_ROOT to tmp so tests don't touch real data
    monkeypatch.setattr(mod, "TENANT_ROOT", tmp_path / "_tenants")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    return mod


def _clear_env(monkeypatch):
    for name in ["OPENAI_API_KEY", "GOOGLE_OAUTH_CLIENT_ID",
                       "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
                       "LIBRARY_SESSION_SECRET", "LIBRARY_VAULT_MASTER_KEY",
                       "GITHUB_ADMIN_TOKEN", "GITHUB_LIBRARY_REPO"]:
        monkeypatch.delenv(name, raising=False)


def test_hard_failure_when_openai_key_missing(preflight, monkeypatch, capsys):
    _clear_env(monkeypatch)
    rc = preflight.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert "DO NOT DEPLOY" in out


def test_soft_warning_when_only_soft_required_missing(preflight, monkeypatch, capsys):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234")
    rc = preflight.main()
    # All soft-required missing → exit 2 (warnings, proceed)
    assert rc == 2
    out = capsys.readouterr().out
    assert "Deploy may proceed" in out


def test_session_secret_too_short_fails(preflight, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LIBRARY_SESSION_SECRET", "tooshort")
    ok, msg = preflight._check_env_present("LIBRARY_SESSION_SECRET")
    assert ok is False
    assert "< 32 chars" in msg


def test_vault_key_too_short_fails(preflight, monkeypatch):
    monkeypatch.setenv("LIBRARY_VAULT_MASTER_KEY", "shortkey")
    ok, msg = preflight._check_env_present("LIBRARY_VAULT_MASTER_KEY")
    assert ok is False
    assert "AES-GCM-256" in msg


def test_all_green_when_full_env_present(preflight, monkeypatch, capsys):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://x/cb")
    monkeypatch.setenv("LIBRARY_SESSION_SECRET", "x" * 32)
    monkeypatch.setenv("LIBRARY_VAULT_MASTER_KEY", "k" * 44)
    monkeypatch.setenv("GITHUB_ADMIN_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_LIBRARY_REPO", "Org/Repo")
    # Seed minimal tenant data
    (preflight.TENANT_ROOT).mkdir(parents=True, exist_ok=True)
    (preflight.TENANT_ROOT / "companies.json").write_text(
        '[{"company_id": "colaberry"}]', encoding="utf-8")
    (preflight.TENANT_ROOT / "users.json").write_text(
        '[{"user_id": "u1"}]', encoding="utf-8")
    rc = preflight.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "All checks green" in out


def test_tenant_seed_missing_is_soft_warning(preflight, monkeypatch):
    """First-time install legitimately has no seed yet — shouldn't be hard fail."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # No tenant files created
    rc = preflight.main()
    # Other soft-required also missing → exit 2 regardless
    assert rc == 2
