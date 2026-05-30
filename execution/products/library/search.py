"""Global search across all Library categories.

Scores matches by where the term appears (name/tags > description > readme).
Returns up to N results sorted by score.

No external index — scans the classified catalog in memory. For 500-ish
items this is < 50 ms; adequate for the current scale. A real index
(SQLite FTS5 or Whoosh) is a Phase-next item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import inventory, store

LAYER = "product"
PRODUCT = "library"


@dataclass
class SearchHit:
    category: str
    asset_id: str
    name: str
    description: str
    tags: list[str]
    score: float
    snippet: str = ""


def _tokenize(q: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_-]+", q) if len(t) >= 2]


def _score_row(tokens: list[str], row: dict, readme: str = "") -> tuple[float, str]:
    if not tokens:
        return (0.0, "")
    name = (row.get("name") or "").lower()
    description = (row.get("description") or "").lower()
    tags_str = " ".join(row.get("tags") or []).lower()
    body = readme.lower()
    score = 0.0
    snippet = ""
    for tok in tokens:
        # Heavy weight: exact substring in name
        if tok in name:
            score += 5.0
        if tok in tags_str:
            score += 3.0
        if tok in description:
            score += 2.0
            if not snippet:
                idx = description.find(tok)
                start = max(0, idx - 40)
                snippet = (row.get("description") or "")[start:start + 160]
        if tok in body:
            score += 1.0
            if not snippet:
                idx = body.find(tok)
                start = max(0, idx - 40)
                snippet = readme[start:start + 160]
    return (score, snippet)


def search(q: str, workspace: str = "global", limit: int = 50,
            only_vetted: bool = False,
            categories: list[str] | None = None) -> list[SearchHit]:
    tokens = _tokenize(q)
    if not tokens:
        return []
    hits: list[SearchHit] = []
    cats = categories or [c.key for c in inventory.CATEGORIES]
    for cat in cats:
        rows = inventory.load_category(cat) or []
        for row in rows:
            asset_id = row.get("name") or row.get("id") or ""
            if not asset_id:
                continue
            meta = store.get_metadata(workspace, cat, asset_id)
            if only_vetted and not meta.vetted:
                continue
            score, snippet = _score_row(tokens, row,
                                                       meta.readme_markdown or "")
            if score > 0:
                hits.append(SearchHit(
                    category=cat, asset_id=asset_id,
                    name=row.get("name") or asset_id,
                    description=row.get("description") or meta.description or "",
                    tags=row.get("tags") or meta.tags or [],
                    score=score, snippet=snippet,
                ))
    hits.sort(key=lambda h: -h.score)
    return hits[:limit]
