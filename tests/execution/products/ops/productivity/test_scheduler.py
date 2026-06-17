"""Daily scheduler — job registration, idempotency, and the fire target."""
from __future__ import annotations

import pytest

from execution.products.ops.productivity import scheduler


@pytest.fixture(autouse=True)
def _clean():
    scheduler.stop_scheduler()
    yield
    scheduler.stop_scheduler()


def test_fire_invokes_runner(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler.runner, "run",
                        lambda: calls.append(True) or _Result())
    scheduler._fire()
    assert calls == [True]


class _Result:
    status = "ok"; operators = 0; verdict = "BASELINE"
    delivery_status = "not_attempted"; output_path = "/x.html"


def test_start_registers_job_and_is_idempotent():
    s1 = scheduler.start_scheduler()
    assert s1.get_job(scheduler.JOB_ID) is not None
    s2 = scheduler.start_scheduler()
    assert s1 is s2                      # idempotent: same instance


def test_stop_clears_scheduler():
    scheduler.start_scheduler()
    scheduler.stop_scheduler()
    assert scheduler._scheduler is None
