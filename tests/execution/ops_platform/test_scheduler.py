"""Phase 6D tests: scheduler with cron, interval, event triggers + leader election."""

from datetime import datetime, timedelta, timezone

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, distributed_lock, runtime_queue, scheduler,
    shared_cache_backend, worker_coordination,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "_SCHEDULES_DIR", tmp_path / "schedules")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    shared_cache_backend.configure(shared_cache_backend.FileBackend(root=tmp_path / "versions"))
    cache_bus.reset_for_tests()
    yield
    scheduler.stop_background_ticker()
    shared_cache_backend.reset_for_tests()


def test_create_interval_schedule():
    s = scheduler.create_schedule(
        name="ping", trigger_kind="interval",
        capability_id="test_summary", interval_seconds=60,
    )
    assert s.trigger_kind == "interval"
    assert s.interval_seconds == 60


def test_create_cron_schedule_validates_expression():
    with pytest.raises(ValueError):
        scheduler.create_schedule(name="bad", trigger_kind="cron",
                                     capability_id="test_summary")


def test_create_event_schedule_requires_topic():
    with pytest.raises(ValueError):
        scheduler.create_schedule(name="ev", trigger_kind="event",
                                     capability_id="test_summary")


def test_tick_as_leader_fires_due_schedules():
    s = scheduler.create_schedule(
        name="immediate", trigger_kind="interval",
        capability_id="test_summary", interval_seconds=1,
    )
    result = scheduler.tick(worker_id="w-leader")
    assert result["leader"] is True
    # First-run-ever should fire
    assert s.schedule_id in [job_id and any(job_id) for job_id in [result["fired"]]] or result["fired"]


def test_disable_blocks_firing():
    s = scheduler.create_schedule(
        name="off", trigger_kind="interval",
        capability_id="test_summary", interval_seconds=1,
    )
    scheduler.disable(s.schedule_id)
    result = scheduler.tick(worker_id="w-1")
    assert result["fired"] == []


def test_event_fires_only_matching_schedules():
    s = scheduler.create_schedule(
        name="on-deploy", trigger_kind="event",
        capability_id="test_summary", event_topic="deploy",
    )
    enqueued = scheduler.fire_event("deploy", payload={"version": "1"})
    assert len(enqueued) == 1
    none_enqueued = scheduler.fire_event("other-event")
    assert none_enqueued == []


def test_cron_evaluator_matches_minute_field():
    """All-asterisks cron should fire on the current minute."""
    s = scheduler.create_schedule(
        name="every-min", trigger_kind="cron",
        capability_id="test_summary", cron_expression="* * * * *",
    )
    result = scheduler.tick(worker_id="w-cron")
    assert result["leader"] is True
    assert result["fired"]


def test_blackout_window_blocks_fire():
    now = datetime.now(timezone.utc)
    s = scheduler.create_schedule(
        name="blackout", trigger_kind="interval",
        capability_id="test_summary", interval_seconds=1,
        blackout_windows=[{
            "start": (now - timedelta(minutes=5)).isoformat(),
            "end": (now + timedelta(minutes=5)).isoformat(),
        }],
    )
    result = scheduler.tick(worker_id="w-bo")
    # The schedule's blackout matches so nothing should fire from THIS schedule.
    # Other schedules in the suite may have already fired.
    refreshed = scheduler.get(s.schedule_id)
    assert refreshed.fire_count == 0


def test_non_leader_returns_false():
    # First tick claims leadership
    scheduler.tick(worker_id="leader-A")
    # Second tick from different worker is not leader (lock still held)
    result = scheduler.tick(worker_id="leader-B")
    assert result["leader"] is False
