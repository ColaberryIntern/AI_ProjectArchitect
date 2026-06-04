"""[Security cleanup 2026-06-04] Tests for POST /projects/new hardening.

Covers:
    - Probe-shaped names rejected (rce / ssti / ssrf / pwn / template / shell)
    - Length cap enforced
    - Empty/all-symbol names rejected
    - Per-IP rate limit enforced
    - Legitimate names still succeed
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import projects as projects_router


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Clear in-process rate-limit state between tests."""
    projects_router._RL_PER_IP.clear()
    projects_router._RL_GLOBAL.clear()
    yield
    projects_router._RL_PER_IP.clear()
    projects_router._RL_GLOBAL.clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with output dir pointed at tmp_path."""
    from execution import state_manager
    monkeypatch.setattr(state_manager, "OUTPUT_DIR", tmp_path)
    return TestClient(app)


# ── Probe rejection ────────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "rce-test",
    "ssti-test",
    "ssrf-internal",
    "pwn2",
    "var-www-html-shell",
    "{{ 7*7 }}",
    "{%print('x')%}",
    "../../../etc/passwd",
    "%2F..%2F..",
    "name;rm -rf /",
    "name`whoami`",
    "name | id",
    "name<script>",
    "eval_test",
    "rce8080",
    "lipsum-globals-os-popen-id-read",
])
def test_probe_names_rejected(client, name):
    resp = client.post(
        "/projects/new",
        data={"project_name": name},
        follow_redirects=False,
    )
    assert resp.status_code == 400, f"expected 400 for probe name {name!r}, got {resp.status_code}"


# ── Length / empty rejection ───────────────────────────────────────


def test_empty_name_rejected(client):
    resp = client.post("/projects/new", data={"project_name": ""}, follow_redirects=False)
    # 400 (our check) OR 422 (FastAPI form validation) both signal rejected.
    assert resp.status_code in (400, 422)


def test_whitespace_only_rejected(client):
    resp = client.post("/projects/new", data={"project_name": "   "}, follow_redirects=False)
    assert resp.status_code == 400


def test_all_symbols_rejected(client):
    # Slugs to empty (no [a-z0-9] survives)
    resp = client.post("/projects/new", data={"project_name": "@#$%^&*()"}, follow_redirects=False)
    assert resp.status_code == 400


def test_too_long_rejected(client):
    long_name = "a" * 300
    resp = client.post(
        "/projects/new",
        data={"project_name": long_name},
        follow_redirects=False,
    )
    assert resp.status_code == 400


# ── Rate limiting ──────────────────────────────────────────────────


def test_per_ip_rate_limit_kicks_in(client):
    # _RL_PER_IP_LIMIT = 5 per 60s — 6th request should be 429
    for i in range(5):
        resp = client.post(
            "/projects/new",
            data={"project_name": f"Real Project {i}"},
            follow_redirects=False,
        )
        assert resp.status_code == 303, f"create #{i+1} expected 303, got {resp.status_code}"
    # 6th
    resp = client.post(
        "/projects/new",
        data={"project_name": "Real Project 6"},
        follow_redirects=False,
    )
    assert resp.status_code == 429


# ── Happy path: legitimate names still work ────────────────────────


def test_legitimate_name_creates(client):
    resp = client.post(
        "/projects/new",
        data={"project_name": "Customer Insight Dashboard"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/projects/customer-insight-dashboard/idea-intake" in resp.headers["location"]


def test_unicode_name_passes_after_strip(client):
    # No control chars, no shell meta, passes
    resp = client.post(
        "/projects/new",
        data={"project_name": "Café Loyalty App"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_name_with_control_chars_stripped(client):
    """Control chars should be stripped, but the visible name remains valid."""
    resp = client.post(
        "/projects/new",
        data={"project_name": "My\x00Project\x01Name"},
        follow_redirects=False,
    )
    # Slug = 'myprojectname' (control chars stripped)
    assert resp.status_code == 303
    assert "/projects/myprojectname/idea-intake" in resp.headers["location"]
