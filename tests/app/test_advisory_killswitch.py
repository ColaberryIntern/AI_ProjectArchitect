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


# ── rate limiter (P1.5) ──


class _Req:
    headers = {"x-forwarded-for": "1.2.3.4"}

    class client:
        host = "1.2.3.4"


def test_rate_limit_blocks_after_max(monkeypatch):
    from app.advisory import routes
    monkeypatch.setattr(routes, "_ADV_RATE_MAX", 3)
    monkeypatch.setattr(routes, "_ADV_RATE_WINDOW", 600)
    routes._ADV_RATE.clear()
    req = _Req()
    for _ in range(3):
        assert routes.rate_limit_advisory(req) is None
    with pytest.raises(HTTPException) as e:
        routes.rate_limit_advisory(req)
    assert e.value.status_code == 429


def test_rate_limit_disabled_when_zero(monkeypatch):
    from app.advisory import routes
    monkeypatch.setattr(routes, "_ADV_RATE_MAX", 0)
    routes._ADV_RATE.clear()
    assert routes.rate_limit_advisory(_Req()) is None
