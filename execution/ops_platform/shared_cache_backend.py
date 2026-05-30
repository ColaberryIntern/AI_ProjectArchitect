"""Shared cache backend abstraction — abstracts the version-stamp store
that Phase 3's cache_bus uses for cross-process invalidation.

Scope honesty
-------------
- ``FileBackend`` (default) — uses ``output/ops_platform/cache_versions/*.stamp``
  files. Coordination scope: single host, multi-process.
- ``RedisBackend`` (interface only) — declares the methods that a Redis
  adapter would implement. The actual Redis client is NOT bundled. When
  a multi-host deployment lands, implement ``set_version`` / ``get_version``
  against your Redis client and inject the instance.
- ``InMemoryBackend`` — process-local. Useful for tests and ephemeral
  workloads. DOES NOT coordinate across processes.

The default backend is selected by ``OPS_CACHE_BACKEND`` env var
(``file`` | ``memory`` | ``redis``). Anything else falls back to file.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path

from config.settings import OUTPUT_DIR

logger = logging.getLogger(__name__)


class CacheBackend(ABC):
    @abstractmethod
    def set_version(self, topic: str) -> float:
        """Bump the topic's version to "now"; return the new version."""

    @abstractmethod
    def get_version(self, topic: str) -> float:
        """Return the current version for a topic, or 0.0 if unset."""

    @abstractmethod
    def reset(self) -> None:
        """Drop all known versions. Test helper — production code never calls this."""

    @abstractmethod
    def name(self) -> str:
        """Diagnostic — returns the backend's identity string."""


class FileBackend(CacheBackend):
    """File-mtime version stamps. Coordination scope: single host, multi-process.

    Each topic gets one ``{topic}.stamp`` file. ``set_version`` touches the
    file; ``get_version`` reads the file's mtime. Multiple workers on the
    same host see the same value within the OS's filesystem coherence window
    (immediate on local disks).
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or (OUTPUT_DIR / "ops_platform" / "cache_versions")
        self._root.mkdir(parents=True, exist_ok=True)

    def name(self) -> str:
        return f"file({self._root})"

    def _path(self, topic: str) -> Path:
        return self._root / f"{topic}.stamp"

    def set_version(self, topic: str) -> float:
        path = self._path(topic)
        try:
            path.touch(exist_ok=True)
            now = time.time()
            os.utime(path, (now, now))
            return now
        except OSError:
            logger.warning("FileBackend.set_version failed for %s", topic, exc_info=True)
            return 0.0

    def get_version(self, topic: str) -> float:
        try:
            return self._path(topic).stat().st_mtime
        except FileNotFoundError:
            return 0.0

    def reset(self) -> None:
        if not self._root.exists():
            return
        for p in self._root.glob("*.stamp"):
            try:
                p.unlink()
            except OSError:
                pass


class InMemoryBackend(CacheBackend):
    """Process-local. Coordination scope: ONE process only.

    Useful for tests + scripts where multiple workers aren't involved.
    """

    def __init__(self) -> None:
        self._versions: dict[str, float] = {}
        self._lock = threading.Lock()

    def name(self) -> str:
        return "memory"

    def set_version(self, topic: str) -> float:
        now = time.time()
        with self._lock:
            self._versions[topic] = now
        return now

    def get_version(self, topic: str) -> float:
        with self._lock:
            return self._versions.get(topic, 0.0)

    def reset(self) -> None:
        with self._lock:
            self._versions.clear()


class RedisBackend(CacheBackend):
    """Interface stub for a Redis-backed cache backend. Coordination scope:
    multi-host, network-bound. Bring your own Redis client and inject it.

    Example wiring (not bundled):

        import redis
        backend = RedisBackend(redis.Redis(host="...", db=0))
        configure(backend)
    """

    def __init__(self, redis_client=None, *, key_prefix: str = "ops_cache:") -> None:
        if redis_client is None:
            raise NotImplementedError(
                "RedisBackend requires a Redis client. "
                "Install redis-py and pass the client to RedisBackend(client). "
                "Until then, use FileBackend (default) or InMemoryBackend."
            )
        self._client = redis_client
        self._prefix = key_prefix

    def name(self) -> str:
        return f"redis(prefix={self._prefix})"

    def set_version(self, topic: str) -> float:
        now = time.time()
        self._client.set(f"{self._prefix}{topic}", str(now))
        return now

    def get_version(self, topic: str) -> float:
        raw = self._client.get(f"{self._prefix}{topic}")
        if raw is None:
            return 0.0
        try:
            return float(raw if isinstance(raw, (int, float, str)) else raw.decode("utf-8"))
        except (TypeError, ValueError):
            return 0.0

    def reset(self) -> None:
        try:
            keys = self._client.keys(f"{self._prefix}*")
            if keys:
                self._client.delete(*keys)
        except Exception:
            logger.warning("RedisBackend.reset failed", exc_info=True)


# ── Module-level singleton ─────────────────────────────────────────────


_BACKEND: CacheBackend | None = None


def configure(backend: CacheBackend) -> None:
    """Override the active backend (used by tests + production wiring)."""
    global _BACKEND
    _BACKEND = backend


def get_backend() -> CacheBackend:
    global _BACKEND
    if _BACKEND is None:
        choice = os.environ.get("OPS_CACHE_BACKEND", "file").lower()
        if choice == "memory":
            _BACKEND = InMemoryBackend()
        elif choice == "redis":
            # Cannot construct a real Redis client without configuration.
            # Fall back to file so the platform stays functional and log loudly.
            logger.warning(
                "OPS_CACHE_BACKEND=redis but no Redis client wired; "
                "falling back to FileBackend. Inject via configure(RedisBackend(client))."
            )
            _BACKEND = FileBackend()
        else:
            _BACKEND = FileBackend()
    return _BACKEND


def reset_for_tests() -> None:
    global _BACKEND
    _BACKEND = None
