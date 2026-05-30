"""'Prompt of the Day' / 'Workflow of the Day' featured-asset rotation.

Deterministic per-day pick from the set of recently-rated or recently-commented
assets. Same input on the same UTC date → same featured asset (so users see
a consistent "today's pick" while they're working).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from . import inventory
from . import store

LAYER = "product"
PRODUCT = "library"


def _today_seed() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _score(meta: store.AssetMetadata, raw: dict[str, Any]) -> float:
    """Higher = more featurable."""
    s = 0.0
    s += meta.rating_avg * 2.0
    s += min(meta.rating_count, 50) * 0.2
    s += min(meta.comment_count, 30) * 0.4
    if meta.vetted:
        s += 5.0
    # Cool factor for prompts + workflows (user-driven categories)
    if meta.category in ("prompts", "workflows"):
        s += 2.0
    return s


def pick_featured(workspace: str = "global",
                          categories: list[str] | None = None) -> dict[str, Any] | None:
    """Return the featured asset for today, or None if nothing to feature.

    Picks the highest-scoring asset across the requested categories. Ties broken
    by a daily-seeded hash so different days surface different assets.
    """
    cats = categories or ["prompts", "workflows", "agents", "skills"]
    candidates: list[tuple[float, dict[str, Any], store.AssetMetadata, str]] = []

    for cat in cats:
        rows = inventory.load_category(cat) or []
        for raw in rows:
            asset_id = (raw.get("name") or raw.get("id") or "").strip()
            if not asset_id:
                continue
            meta = store.get_metadata(workspace, cat, asset_id)
            score = _score(meta, raw)
            candidates.append((score, raw, meta, cat))

    if not candidates:
        return None

    # Daily-seeded tiebreaker
    seed = _today_seed()
    candidates.sort(
        key=lambda t: (
            -t[0],
            hashlib.sha256(f"{seed}|{t[3]}|{t[1].get('name','')}".encode()).hexdigest(),
        ),
    )
    score, raw, meta, cat = candidates[0]
    return {
        "category": cat,
        "name": raw.get("name", ""),
        "description": raw.get("description", "") or meta.description,
        "tags": raw.get("tags", []) or meta.tags,
        "version": raw.get("version", meta.version),
        "owner": raw.get("owner", "") or meta.owner,
        "rating_avg": meta.rating_avg,
        "rating_count": meta.rating_count,
        "vetted": meta.vetted,
        "score": round(score, 2),
        "url": f"/library/{cat}/{raw.get('name', '')}",
    }
