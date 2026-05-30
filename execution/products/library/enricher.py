"""Enricher — turns a ParsedSurface + ClassificationResult into a fully-
populated EnrichedAsset ready for the pending-review queue.

Fills blanks with smart defaults; never invents data that wasn't there
(unless `LIBRARY_ENRICH_WITH_LLM=1` and an LLM hook is wired).

Validates a minimum quality bar — name + description are required.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from .classifier import ClassificationResult, classify
from .parser import ParsedSurface

LAYER = "product"
PRODUCT = "library"


@dataclass
class EnrichedAsset:
    name: str
    description: str
    category: str
    how_to_use: str = ""
    example: str = ""
    tags: list[str] = field(default_factory=list)
    version: str = "1.0"
    owner: str = ""
    source_url: str = ""
    classification: dict[str, Any] = field(default_factory=dict)
    quality_score: float = 0.0     # 0.0–1.0: how rich is this asset?
    warnings: list[str] = field(default_factory=list)


# ── Quality scoring ───────────────────────────────────────────────────


def _score_quality(a: EnrichedAsset) -> float:
    score = 0.0
    if len(a.name) >= 3: score += 0.15
    if len(a.description) >= 40: score += 0.25
    if len(a.how_to_use) >= 20: score += 0.20
    if a.example: score += 0.15
    if len(a.tags) >= 2: score += 0.10
    if a.version: score += 0.05
    if a.owner: score += 0.05
    if a.source_url: score += 0.05
    return round(min(score, 1.0), 2)


# ── README section extraction (for how-to-use + example) ─────────────


_USAGE_HEADINGS = re.compile(
    r"^#{1,3}\s+(?:how to use|usage|getting started|installation|quick ?start|example|examples)\s*$",
    re.I | re.M,
)


def _extract_section(markdown: str, heading_re: re.Pattern) -> str:
    """Extract the body of the first matching heading until the next heading."""
    m = heading_re.search(markdown)
    if not m:
        return ""
    rest = markdown[m.end():]
    next_h = re.search(r"^#{1,6}\s+", rest, re.M)
    body = rest[: next_h.start()] if next_h else rest
    return body.strip()[:1500]


# ── Main enricher ─────────────────────────────────────────────────────


def enrich(parsed: ParsedSurface,
              raw_content: str = "",
              source_url: str = "",
              category_hint: str | None = None,
              raw_item_for_classify: dict[str, Any] | None = None) -> EnrichedAsset:
    """Build an EnrichedAsset from parsed surface + raw text."""
    name = (parsed.title or "").strip()
    if not name and parsed.manifest:
        name = (parsed.manifest.get("name") or parsed.manifest.get("title") or "").strip()
    if not name:
        # Fallback to source URL's last path segment
        name = (source_url.rstrip("/").split("/")[-1] or "Untitled").replace("-", " ").replace("_", " ").title()

    description = parsed.description.strip() or parsed.body_text[:400]
    how_to_use = _extract_section(raw_content, _USAGE_HEADINGS)
    if not how_to_use and parsed.code_blocks:
        how_to_use = parsed.code_blocks[0][:1000]
    example = parsed.code_blocks[1] if len(parsed.code_blocks) >= 2 else (
        parsed.code_blocks[0] if parsed.code_blocks else ""
    )

    tags = list(parsed.tags or [])
    # Manifest keywords / categories (if any)
    for k in ("keywords", "topics", "tags"):
        if isinstance(parsed.manifest.get(k), list):
            tags.extend(str(t) for t in parsed.manifest[k] if t)

    version = parsed.version or "1.0"
    owner = parsed.owner

    # Classify
    classify_input: dict[str, Any] = {
        "name": name, "description": description,
        "tags": tags, "source_url": source_url,
        **(raw_item_for_classify or {}),
    }
    if category_hint:
        classify_input["category"] = category_hint
    result = classify(classify_input, source_url=source_url)

    asset = EnrichedAsset(
        name=name,
        description=description[:600],
        category=result.category,
        how_to_use=how_to_use[:1500],
        example=example[:1500],
        tags=list(dict.fromkeys(tags))[:10],   # dedupe, cap 10
        version=str(version)[:24],
        owner=str(owner)[:120],
        source_url=source_url,
        classification=result.to_dict(),
    )

    # Validate quality
    if not asset.name or len(asset.name) < 2:
        asset.warnings.append("name is missing or too short")
    if not asset.description or len(asset.description) < 20:
        asset.warnings.append("description is missing or too short")
    if asset.classification.get("confidence", 1.0) < 0.5:
        asset.warnings.append(
            f"low classification confidence ({asset.classification['confidence']}) — curator should review"
        )

    asset.quality_score = _score_quality(asset)

    # Optional LLM enrichment
    if os.environ.get("LIBRARY_ENRICH_WITH_LLM") == "1" and asset.quality_score < 0.6:
        asset = _llm_enrich(asset, raw_content)

    return asset


# ── Optional LLM enrichment (opt-in via env) ──────────────────────────


def _llm_enrich(asset: EnrichedAsset, raw_content: str) -> EnrichedAsset:
    """Stub for LLM enrichment. Wired only when LIBRARY_ENRICH_WITH_LLM=1
    AND the project has an llm_client configured.

    Default behavior: no-op (returns asset unchanged). Real implementation
    would call execution.llm_client to fill description/how_to_use/example
    blanks. Left as a hook so the pipeline shape is right but no tokens
    burn unless explicitly enabled.
    """
    try:
        from execution.llm_client import is_available
        if not is_available():
            asset.warnings.append("LLM enrichment requested but llm_client is unavailable")
            return asset
    except Exception:
        return asset

    # In a future PR we'd call llm here. For now mark that enrichment was attempted.
    asset.warnings.append("LLM enrichment hook reached but no real prompt wired yet")
    return asset
