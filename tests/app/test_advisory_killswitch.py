"""Tests for the OPS_ADVISORY_ENABLED kill-switch (P1.5 hardening)."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.advisory.routes import router, _advisory_enabled, require_advisory_enabled


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_helper_default_enabled(monkeypatch):
    monkeypatch.delenv("OPS_ADVISORY_ENABLED", raising=False)
    assert _advisory_enabled() is True


def test_helper_disabled(monkeypatch):
    monkeypatch.setenv("OPS_ADVISORY_ENABLED", "false")
    assert _advisory_enabled() is False


def test_require_raises_503_when_disabled(monkeypatch):
    monkeypatch.setenv("OPS_ADVISORY_ENABLED", "false")
    with pytest.raises(HTTPException) as e:
        require_advisory_enabled()
    assert e.value.status_code == 503


def test_require_passes_when_enabled(monkeypatch):
    monkeypatch.delenv("OPS_ADVISORY_ENABLED", raising=False)
    assert require_advisory_enabled() is None


def test_start_route_503_when_disabled(monkeypatch):
    monkeypatch.setenv("OPS_ADVISORY_ENABLED", "false")
    r = _client().post("/advisory/start", data={"business_idea": "x"}, follow_redirects=False)
    assert r.status_code == 503
