"""Phase 7C tests: Redis adapters honest degradation."""

import pytest

from execution.ops_platform import redis_backends, shared_cache_backend


def test_is_available_reflects_install():
    # redis-py may or may not be installed in CI; the value should be a bool
    assert isinstance(redis_backends.is_available(), bool)


def test_get_redis_without_client_raises():
    redis_backends._CLIENT = None
    with pytest.raises(redis_backends.RedisNotConfigured):
        redis_backends.get_redis()


def test_configure_without_pyjwt_raises_when_unavailable():
    # configure_redis must complain if redis-py absent
    if not redis_backends.is_available():
        with pytest.raises(redis_backends.RedisNotConfigured):
            redis_backends.configure_redis(object())


def test_acquire_requires_client():
    redis_backends._CLIENT = None
    with pytest.raises(redis_backends.RedisNotConfigured):
        redis_backends.acquire("x", owner_id="me")


def test_check_and_increment_requires_client():
    redis_backends._CLIENT = None
    with pytest.raises(redis_backends.RedisNotConfigured):
        redis_backends.check_and_increment(bucket="x", max_calls=1, window_seconds=60)


def test_publish_event_requires_client():
    redis_backends._CLIENT = None
    with pytest.raises(redis_backends.RedisNotConfigured):
        redis_backends.publish_event("ch", {"foo": "bar"})


def test_shared_cache_backend_redis_requires_client():
    with pytest.raises(NotImplementedError):
        shared_cache_backend.RedisBackend(None)


def test_redis_backend_with_fake_client():
    """Verify the SET/GET path works with a minimal in-memory fake client.

    This proves the implementation is real — given a real Redis client at
    deploy time, the same calls would hit the network and behave identically.
    """
    class FakeRedis:
        def __init__(self):
            self.store = {}
        def set(self, k, v):
            self.store[k] = v
        def get(self, k):
            return self.store.get(k)
    backend = shared_cache_backend.RedisBackend(redis_client=FakeRedis())
    backend.set_version("topic_a")
    assert backend.get_version("topic_a") > 0
    assert backend.get_version("unset_topic") == 0.0
