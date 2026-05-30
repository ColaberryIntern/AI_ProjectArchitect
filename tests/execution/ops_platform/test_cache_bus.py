"""Tests for execution/ops_platform/cache_bus.py"""

import pytest

from execution.ops_platform import cache_bus


@pytest.fixture(autouse=True)
def isolated_bus(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    yield
    cache_bus.reset_for_tests()


def test_subscribe_and_emit_fans_out():
    seen = []
    cache_bus.subscribe(cache_bus.Topic.RUN_RECORDED, lambda e: seen.append(e))
    cache_bus.emit(cache_bus.Topic.RUN_RECORDED, {"x": 1})
    assert len(seen) == 1
    assert seen[0].payload == {"x": 1}


def test_emit_bumps_version_stamp():
    before = cache_bus.current_version(cache_bus.Topic.RUN_RECORDED)
    cache_bus.emit(cache_bus.Topic.RUN_RECORDED, {})
    after = cache_bus.current_version(cache_bus.Topic.RUN_RECORDED)
    assert after > before


def test_subscriber_exception_is_swallowed():
    def bad(_event):
        raise RuntimeError("intentional")
    cache_bus.subscribe(cache_bus.Topic.FEEDBACK_SUBMITTED, bad)
    # Must not raise
    cache_bus.emit(cache_bus.Topic.FEEDBACK_SUBMITTED, {})


def test_unsubscribe_removes_listener():
    seen = []
    fn = lambda e: seen.append(e)
    cache_bus.subscribe(cache_bus.Topic.PIPELINE_CREATED, fn)
    cache_bus.unsubscribe(cache_bus.Topic.PIPELINE_CREATED, fn)
    cache_bus.emit(cache_bus.Topic.PIPELINE_CREATED, {})
    assert seen == []


def test_reset_for_tests_clears_subscribers_and_stamps():
    cache_bus.subscribe(cache_bus.Topic.RUN_RECORDED, lambda e: None)
    cache_bus.emit(cache_bus.Topic.RUN_RECORDED, {})
    cache_bus.reset_for_tests()
    assert cache_bus.current_version(cache_bus.Topic.RUN_RECORDED) == 0.0
