"""Search index — keyword + tag + category search across capabilities.

Design choice: pure-Python inverted index. No Typesense / Meilisearch
dependency. With <1000 capabilities this is plenty fast (sub-millisecond
per query). When the catalog crosses ~5000 entries we'll swap to Typesense,
but that's far away.

Indexed fields per capability:
- name (weight 5)
- description + business_value (weight 3)
- tags (weight 4)
- category + subcategory (weight 2)
- inputs + outputs names (weight 1)

Ranking: TF (term frequency) per document × per-field weight, summed across
query tokens. Ties broken by usage_count desc, then name asc.

The index is rebuilt lazily on the first query and cached on the registry
singleton. Calling ``rebuild()`` is cheap and idempotent.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_FIELD_WEIGHTS = {
    "name": 5,
    "description": 3,
    "business_value": 3,
    "tags": 4,
    "category": 2,
    "subcategory": 2,
    "io": 1,
    # Feedback-derived fields. Operational insights from real users are
    # weighted highly so "what people actually said when using this"
    # surfaces alongside the static manifest text.
    "feedback_notes": 4,
    "feedback_improvements": 4,
    "feedback_suggestions": 5,
}

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "with",
    "is", "are", "be", "by", "as", "at", "this", "that", "it",
})


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOPWORDS]


def _load_feedback_for(capability_id: str) -> list[dict]:
    """Lazy load feedback records for a capability. Tolerates missing files."""
    try:
        from execution.ops_platform import feedback_store
        return feedback_store.list_feedback(capability_id)
    except Exception:
        return []


@dataclass
class SearchResult:
    capability_id: str
    score: float
    capability: dict
    matched_tokens: list[str] = field(default_factory=list)


@dataclass
class SearchIndex:
    """Inverted index from (token, field) → list[(capability_id, count)]."""

    # token -> {field -> {capability_id -> count}}
    postings: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(Counter)))
    docs: dict = field(default_factory=dict)  # capability_id -> capability
    built: bool = False

    def rebuild(self, capabilities: list[dict]) -> None:
        self.postings.clear()
        self.docs.clear()
        for cap in capabilities:
            self._index_one(cap)
        self.built = True

    def _index_one(self, cap: dict) -> None:
        cid = cap["id"]
        self.docs[cid] = cap

        def add(field_key: str, text: str) -> None:
            for token in _tokenize(text):
                self.postings[token][field_key][cid] += 1

        add("name", cap.get("name", ""))
        add("description", cap.get("description", ""))
        add("business_value", cap.get("business_value", ""))
        add("category", cap.get("category", ""))
        add("subcategory", cap.get("subcategory", ""))
        for tag in cap.get("tags") or []:
            add("tags", str(tag))
        for io_name in [i.get("name", "") for i in cap.get("inputs") or []]:
            add("io", str(io_name))
        for io_name in [o.get("name", "") for o in cap.get("outputs") or []]:
            add("io", str(io_name))

        # Feedback corpus — what employees actually said when using this
        # capability. Indexed at higher weight than description because
        # operational truth tends to be more honest than marketing copy.
        for record in _load_feedback_for(cid):
            notes = record.get("operational_notes") or {}
            if isinstance(notes, dict):
                add("feedback_notes", notes.get("how_used", ""))
                add("feedback_improvements", notes.get("improvements_discovered", ""))
                add("feedback_notes", notes.get("edge_cases", ""))
            for suggestion in record.get("suggested_enhancements") or []:
                if isinstance(suggestion, dict):
                    add("feedback_suggestions", suggestion.get("title", ""))
                    add("feedback_suggestions", suggestion.get("description", ""))
                elif isinstance(suggestion, str):
                    add("feedback_suggestions", suggestion)

    def search(self, query: str, *, top_k: int = 20, type_filter: str | None = None,
               category_filter: str | None = None) -> list[SearchResult]:
        if not self.built:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []

        scores: dict[str, float] = defaultdict(float)
        matched_by_doc: dict[str, set[str]] = defaultdict(set)

        for token in tokens:
            posting = self.postings.get(token)
            if not posting:
                continue
            for field_key, counts in posting.items():
                weight = _FIELD_WEIGHTS.get(field_key, 1)
                for cid, count in counts.items():
                    scores[cid] += count * weight
                    matched_by_doc[cid].add(token)

        results: list[SearchResult] = []
        for cid, score in scores.items():
            cap = self.docs[cid]
            if type_filter and cap.get("type") != type_filter:
                continue
            if category_filter and cap.get("category") != category_filter:
                continue
            results.append(SearchResult(
                capability_id=cid,
                score=score,
                capability=cap,
                matched_tokens=sorted(matched_by_doc[cid]),
            ))

        results.sort(key=lambda r: (-r.score, -int(r.capability.get("usage_count", 0)), r.capability.get("name", "")))
        return results[:top_k]


# Module-level cache so the index survives across requests.
_INDEX_CACHE: SearchIndex | None = None
_INDEX_REGISTRY_ID: int | None = None  # id(registry) used to detect swaps in tests
_INDEX_CACHE_VERSIONS: dict = {}  # topic versions captured at build time


def search(
    query: str,
    *,
    top_k: int = 20,
    type_filter: str | None = None,
    category_filter: str | None = None,
    registry: CapabilityRegistry | None = None,
) -> list[SearchResult]:
    """Convenience wrapper: builds the index on first call, then reuses."""
    reg = registry or default_registry()
    _ensure_index(reg)
    return _INDEX_CACHE.search(
        query, top_k=top_k, type_filter=type_filter, category_filter=category_filter
    )


def rebuild(registry: CapabilityRegistry | None = None) -> int:
    """Force a rebuild and return the count of indexed capabilities."""
    reg = registry or default_registry()
    snap = reg.snapshot()
    _build_index_for(reg, snap.capabilities)
    return len(snap.capabilities)


def recommend_related(capability_id: str, *, top_k: int = 5,
                      registry: CapabilityRegistry | None = None) -> list[SearchResult]:
    """Find capabilities related to a given one (shared tags + category)."""
    reg = registry or default_registry()
    base = reg.get(capability_id)
    if not base:
        return []
    query_parts = [base.get("name", ""), base.get("category", "")]
    query_parts.extend(base.get("tags") or [])
    query = " ".join(query_parts)
    raw = search(query, top_k=top_k + 1, registry=reg)
    return [r for r in raw if r.capability_id != capability_id][:top_k]


# ── Internal ────────────────────────────────────────────────────────────


def _ensure_index(registry: CapabilityRegistry) -> None:
    global _INDEX_CACHE, _INDEX_REGISTRY_ID, _INDEX_CACHE_VERSIONS
    from execution.ops_platform import cache_bus
    relevant = (
        cache_bus.Topic.REGISTRY_REFRESHED,
        cache_bus.Topic.FEEDBACK_SUBMITTED,
        cache_bus.Topic.SEMANTIC_ENRICHED,
    )
    current = {t.value: cache_bus.current_version(t) for t in relevant}
    if (
        _INDEX_CACHE is not None
        and _INDEX_REGISTRY_ID == id(registry)
        and current == _INDEX_CACHE_VERSIONS
    ):
        return
    snap = registry.snapshot()
    _build_index_for(registry, snap.capabilities)
    _INDEX_CACHE_VERSIONS = current


def _build_index_for(registry: CapabilityRegistry, capabilities: list[dict]) -> None:
    global _INDEX_CACHE, _INDEX_REGISTRY_ID
    idx = SearchIndex()
    idx.rebuild(capabilities)
    _INDEX_CACHE = idx
    _INDEX_REGISTRY_ID = id(registry)
    logger.info("Search index rebuilt with %d capabilities", len(capabilities))


def reset_index() -> None:
    """Test helper — drop the cached index."""
    global _INDEX_CACHE, _INDEX_REGISTRY_ID, _INDEX_CACHE_VERSIONS
    _INDEX_CACHE = None
    _INDEX_REGISTRY_ID = None
    _INDEX_CACHE_VERSIONS = {}
