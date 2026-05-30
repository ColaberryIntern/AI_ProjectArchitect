"""Capability registry — the in-memory lookup layer for plugins.

Every other part of the ops platform (router, search, workflow_runner,
verification, training) goes through this registry rather than touching the
loader directly. That gives us:
- one place to refresh / hot-reload
- filtering helpers (by type, category, tag, ownership)
- stable usage_count / ratings aggregation that survives reloads

Persistence model: capability *metadata* lives in the plugin manifest (read-
only). Dynamic stats (usage_count, ratings_summary) live in
output/ops_platform/registry_stats.json, keyed by capability id. The registry
merges the two on read.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import OUTPUT_DIR

from execution.ops_platform.plugin_loader import LoadResult, load_plugins

logger = logging.getLogger(__name__)

_STATS_PATH = OUTPUT_DIR / "ops_platform" / "registry_stats.json"
_LOCK = threading.Lock()


@dataclass
class RegistrySnapshot:
    """Immutable view of the registry at a point in time."""

    capabilities: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def by_id(self) -> dict[str, dict]:
        return {c["id"]: c for c in self.capabilities}

    def by_type(self, type_name: str) -> list[dict]:
        return [c for c in self.capabilities if c.get("type") == type_name]

    def by_category(self, category: str) -> list[dict]:
        return [c for c in self.capabilities if c.get("category") == category]

    def by_tag(self, tag: str) -> list[dict]:
        return [c for c in self.capabilities if tag in (c.get("tags") or [])]

    def departments(self) -> list[str]:
        """Distinct categories sorted alphabetically."""
        return sorted({c.get("category", "Uncategorized") for c in self.capabilities})


class CapabilityRegistry:
    """Holds the latest LoadResult plus a small mutable stats overlay.

    The registry is constructed lazily and refreshable. Tests can inject a
    custom load_fn to point at a fixture plugin tree.
    """

    def __init__(self, load_fn=None):
        self._load_fn = load_fn or load_plugins
        self._snapshot: RegistrySnapshot | None = None
        self._stats: dict[str, dict] = {}

    # ── Loading ───────────────────────────────────────────────────────────

    def refresh(self) -> RegistrySnapshot:
        """Reload everything. Safe to call repeatedly."""
        with _LOCK:
            result: LoadResult = self._load_fn()
            self._snapshot = RegistrySnapshot(
                capabilities=[self._merge_stats(c) for c in result.capabilities],
                errors=list(result.errors),
                skipped=list(result.skipped),
            )
            self._load_stats()
        try:
            from execution.ops_platform import cache_bus
            cache_bus.emit(cache_bus.Topic.REGISTRY_REFRESHED, {
                "capability_count": len(self._snapshot.capabilities),
            })
        except Exception:
            logger.warning("cache_bus emit failed for REGISTRY_REFRESHED", exc_info=True)
        return self._snapshot

    def snapshot(self) -> RegistrySnapshot:
        """Return the latest snapshot, loading on first call."""
        if self._snapshot is None:
            return self.refresh()
        return self._snapshot

    # ── Lookups ───────────────────────────────────────────────────────────

    def get(self, capability_id: str) -> dict | None:
        return self.snapshot().by_id().get(capability_id)

    def list(self, *, type_name: str | None = None, category: str | None = None,
             tag: str | None = None) -> list[dict]:
        snap = self.snapshot()
        items = snap.capabilities
        if type_name:
            items = [c for c in items if c.get("type") == type_name]
        if category:
            items = [c for c in items if c.get("category") == category]
        if tag:
            items = [c for c in items if tag in (c.get("tags") or [])]
        return items

    # ── Stats ─────────────────────────────────────────────────────────────

    def record_usage(self, capability_id: str) -> int:
        """Increment usage_count and persist. Returns the new count."""
        with _LOCK:
            self._load_stats()
            entry = self._stats.setdefault(capability_id, {"usage_count": 0, "ratings": {}})
            entry["usage_count"] = int(entry.get("usage_count", 0)) + 1
            self._save_stats()
            # refresh in-memory snapshot
            if self._snapshot:
                for c in self._snapshot.capabilities:
                    if c["id"] == capability_id:
                        c["usage_count"] = entry["usage_count"]
            return entry["usage_count"]

    def set_rating_aggregate(self, capability_id: str, aggregate: dict) -> None:
        """Called by feedback_store after each new feedback record. Persists."""
        with _LOCK:
            self._load_stats()
            entry = self._stats.setdefault(capability_id, {"usage_count": 0, "ratings": {}})
            entry["ratings"] = aggregate
            self._save_stats()
            if self._snapshot:
                for c in self._snapshot.capabilities:
                    if c["id"] == capability_id:
                        c["ratings"] = aggregate

    # ── Internal ──────────────────────────────────────────────────────────

    def _merge_stats(self, capability: dict) -> dict:
        """Add usage_count / ratings to a freshly loaded manifest."""
        merged = dict(capability)
        cid = merged["id"]
        stats = self._stats.get(cid) or {}
        merged.setdefault("usage_count", int(stats.get("usage_count", 0)))
        merged.setdefault("ratings", stats.get("ratings") or {})
        return merged

    def _load_stats(self) -> None:
        if not _STATS_PATH.exists():
            self._stats = {}
            return
        try:
            with open(_STATS_PATH, "r", encoding="utf-8") as f:
                self._stats = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.warning("registry_stats.json unreadable; starting fresh", exc_info=True)
            self._stats = {}

    def _save_stats(self) -> None:
        _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATS_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._stats, f, indent=2, sort_keys=True)
        tmp.replace(_STATS_PATH)


# Module-level singleton — used by router + workflow_runner + search.
# Tests construct their own CapabilityRegistry to isolate state.
_DEFAULT_REGISTRY: CapabilityRegistry | None = None


def default_registry() -> CapabilityRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = CapabilityRegistry()
    return _DEFAULT_REGISTRY


def reset_default_registry() -> None:
    """Test helper — drop the singleton so the next call gets a fresh load."""
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None
