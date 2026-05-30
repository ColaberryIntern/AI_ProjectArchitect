"""Cache invalidation bus — tiny in-process pub/sub for cache busting.

Why this exists
---------------
Phase 2 introduced several read-side caches (operational_graph snapshot,
search_index, recommendation candidate pool, reputation lookup) that all
became stale whenever something on the write-side changed. The router could
explicitly call ``reset_*`` after every mutation, but that's brittle: it forces
every new endpoint author to remember every cache.

This module flips that. Mutators emit `Topic` events; readers subscribe at
import time. When ``workflow_runner.run_workflow`` succeeds, it calls
``cache_bus.emit(Topic.RUN_RECORDED, ...)`` exactly once. Subscribers update
or invalidate themselves.

Topics (named so the eye scans them):
  RUN_RECORDED          — a workflow or agent run finished (any status)
  FEEDBACK_SUBMITTED    — a feedback record was persisted
  PIPELINE_CREATED      — a pipeline manifest was saved
  PIPELINE_RUN_RECORDED — a pipeline execution finished
  SEMANTIC_ENRICHED     — a capability's enrichment was (re)computed
  REPUTATION_RECORDED   — a reputation score was persisted
  REGISTRY_REFRESHED    — the capability registry reloaded
  DISCOVERY_UPDATED     — discovery queue gained/lost/updated an item

Coordination across workers
---------------------------
Subscribers are in-process, but each topic also bumps a file-stamped
``cache_version`` so a second worker on the same host can detect "the world
changed since I last read" without IPC. ``current_version(topic)`` is a
single mtime() read — cheap. This is the minimum coordination we need until
the platform actually goes multi-host. When it does, swap _VERSION_DIR for
Redis without changing the public API.

Thread safety
-------------
Subscriber list mutations and emit-fan-out are guarded by a single lock.
Subscribers themselves are responsible for their own internal locking.
Subscribers MUST NOT raise; the bus swallows exceptions to keep one bad
subscriber from breaking unrelated subscribers (logged at WARNING).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from config.settings import OUTPUT_DIR

logger = logging.getLogger(__name__)

_VERSION_DIR = OUTPUT_DIR / "ops_platform" / "cache_versions"
_LOCK = threading.Lock()


class Topic(str, Enum):
    RUN_RECORDED = "run_recorded"
    FEEDBACK_SUBMITTED = "feedback_submitted"
    PIPELINE_CREATED = "pipeline_created"
    PIPELINE_RUN_RECORDED = "pipeline_run_recorded"
    SEMANTIC_ENRICHED = "semantic_enriched"
    REPUTATION_RECORDED = "reputation_recorded"
    REGISTRY_REFRESHED = "registry_refreshed"
    DISCOVERY_UPDATED = "discovery_updated"


@dataclass(frozen=True)
class Event:
    topic: Topic
    payload: dict
    timestamp: float


_Listener = Callable[[Event], None]
_subscribers: dict[Topic, list[_Listener]] = defaultdict(list)


def subscribe(topic: Topic, listener: _Listener) -> None:
    """Register a listener. Idempotent — same callable subscribed twice is
    still called twice; the caller is expected to track its own membership."""
    with _LOCK:
        _subscribers[topic].append(listener)


def unsubscribe(topic: Topic, listener: _Listener) -> None:
    """Remove a previously registered listener. No-op if absent."""
    with _LOCK:
        try:
            _subscribers[topic].remove(listener)
        except ValueError:
            pass


def emit(topic: Topic, payload: dict | None = None) -> None:
    """Fan out an event. Bumps the on-disk version stamp for the topic so
    other workers see the change. Never raises out of subscribers."""
    event = Event(topic=topic, payload=payload or {}, timestamp=time.time())
    _bump_version(topic)
    # Snapshot the subscriber list under the lock to avoid mutation-during-iteration.
    with _LOCK:
        listeners = list(_subscribers.get(topic, ()))
    for fn in listeners:
        try:
            fn(event)
        except Exception:
            logger.warning("cache_bus subscriber raised for %s", topic.value, exc_info=True)


def current_version(topic: Topic) -> float:
    """Return the current version for a topic, 0.0 if never set.

    Reads from the configured shared_cache_backend (file by default,
    swappable to Redis/InMemory). Falls back to a direct file stat
    when the backend hasn't been imported yet (avoids circular import).
    """
    try:
        from execution.ops_platform import shared_cache_backend
        return shared_cache_backend.get_backend().get_version(topic.value)
    except Exception:
        path = _path_for(topic)
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return 0.0


def reset_for_tests() -> None:
    """Drop all subscribers + version stamps. Call from test setup.

    Also re-points the shared_cache_backend at the current _VERSION_DIR
    (which may have been monkey-patched by the test fixture).
    """
    with _LOCK:
        _subscribers.clear()
    if _VERSION_DIR.exists():
        for p in _VERSION_DIR.glob("*.stamp"):
            try:
                p.unlink()
            except OSError:
                pass
    try:
        from execution.ops_platform import shared_cache_backend
        shared_cache_backend.reset_for_tests()
        # Reseat the backend at the current _VERSION_DIR so subsequent reads
        # don't leak from a previously-active production backend.
        shared_cache_backend.configure(shared_cache_backend.FileBackend(root=_VERSION_DIR))
    except Exception:
        pass


# ── Internal ───────────────────────────────────────────────────────────


def _path_for(topic: Topic) -> Path:
    return _VERSION_DIR / f"{topic.value}.stamp"


def _bump_version(topic: Topic) -> None:
    """Bump the topic's version via the configured shared cache backend.
    The FileBackend (default) writes to ``output/ops_platform/cache_versions/``
    — same on-disk path the prior Phase 3 implementation used, so existing
    tests + readers continue to work without change."""
    try:
        from execution.ops_platform import shared_cache_backend
        # Honor any test-time override of _VERSION_DIR by re-pointing the
        # backend when its root differs.
        backend = shared_cache_backend.get_backend()
        if isinstance(backend, shared_cache_backend.FileBackend) and backend._root != _VERSION_DIR:
            shared_cache_backend.configure(shared_cache_backend.FileBackend(root=_VERSION_DIR))
            backend = shared_cache_backend.get_backend()
        backend.set_version(topic.value)
    except Exception:
        # Fallback to direct file write so cache_bus never breaks if the
        # backend module hasn't loaded yet.
        _VERSION_DIR.mkdir(parents=True, exist_ok=True)
        path = _path_for(topic)
        try:
            path.touch(exist_ok=True)
            now = time.time()
            import os
            os.utime(path, (now, now))
        except OSError:
            logger.warning("cache_bus failed to bump version for %s", topic.value, exc_info=True)
