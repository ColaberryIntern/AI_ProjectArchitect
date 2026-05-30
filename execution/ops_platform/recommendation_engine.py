"""Recommendation engine — ranks capabilities for a user query by combining
four orthogonal signals:

  1. Lexical relevance   — search_index TF score against the query tokens.
  2. Semantic match      — overlap between query tokens and a capability's
                           semantic_tags / business_domains / personas.
  3. Graph proximity     — how often this capability appears as the *next*
                           step after the user's recent runs (followed_by edges)
                           or co-occurs with their recently-used capabilities.
  4. Reputation          — capability's standing reputation_score (0-100).

Score blend (tunable via WEIGHTS):
  final = 0.30·lex + 0.25·sem + 0.20·graph + 0.25·rep

The engine returns ranked Recommendation objects with a human-readable
``reason`` explaining *why* each item surfaced. Reasons matter more than
scores for the UX: the user has to trust the suggestion before clicking it.

Inputs the engine accepts:
  - query              free-text ("I need to respond to an RFP")
  - role / department  optional persona/department filter
  - recent_run_ids     optional list of the user's recent runs
  - top_k              cap on returned items
  - kinds              {"workflow","pipeline","agent","mcp_server"} filter

The engine is pure read: it never persists anything. Cheap to call (<100 ms
on a registry of ~500 capabilities), so the router calls it on every page
load without caching.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from execution.ops_platform import (
    operational_graph,
    pipeline_engine,
    reputation_scorer,
    search_index,
    semantic_analyzer,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

# Final blend weights — must sum to 1.0
WEIGHTS = {
    "lexical": 0.30,
    "semantic": 0.25,
    "graph": 0.20,
    "reputation": 0.25,
}

# How many capabilities to score before final ranking.
_CANDIDATE_POOL_CAP = 80

# Default reputation when a capability has never been scored (so unknown
# capabilities aren't penalized into oblivion on their first appearance).
_DEFAULT_REPUTATION = 35.0


@dataclass
class Recommendation:
    capability_id: str
    name: str
    type: str            # "workflow" | "pipeline" | "agent" | "mcp_server"
    final_score: float   # 0..1, blended
    sub_scores: dict     # {"lexical":..., "semantic":..., "graph":..., "reputation":...}
    reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    capability: dict | None = None

    def to_dict(self) -> dict:
        out = self.__dict__.copy()
        out.pop("capability", None)
        return out


# ── Public API ─────────────────────────────────────────────────────────


def recommend(
    query: str = "",
    *,
    role: str | None = None,
    department: str | None = None,
    recent_run_ids: list[str] | None = None,
    kinds: list[str] | None = None,
    top_k: int = 8,
    registry: CapabilityRegistry | None = None,
) -> list[Recommendation]:
    """Return up to ``top_k`` ranked recommendations for the user's context.

    None of the inputs are required. With an empty query and no history the
    engine falls back to top-reputation-in-department, then top-reputation
    overall — the home page always has *something* to show.
    """
    reg = registry or default_registry()
    snap = reg.snapshot()
    all_caps = {c["id"]: c for c in snap.capabilities}
    if not all_caps:
        return []

    kind_set = set(kinds) if kinds else None

    candidates = _candidate_pool(
        query=query, department=department, role=role,
        recent_run_ids=recent_run_ids or [], registry=reg, all_caps=all_caps,
        kind_set=kind_set,
    )
    if not candidates:
        return []

    # Cheap signals computed once, reused per candidate.
    lex_scores = _lexical_scores(query, registry=reg)
    sem_signals = _semantic_signals(query=query, role=role, department=department)
    graph_signals = _graph_signals(recent_run_ids or [])
    rep_lookup = _reputation_lookup(candidates)

    out: list[Recommendation] = []
    for cid in candidates:
        cap = all_caps[cid]
        lex = lex_scores.get(cid, 0.0)
        sem, sem_reasons = _semantic_match(cap, sem_signals)
        graph, graph_reasons = _graph_match(cid, graph_signals)
        rep = rep_lookup.get(cid, _DEFAULT_REPUTATION) / 100.0

        final = (
            lex * WEIGHTS["lexical"]
            + sem * WEIGHTS["semantic"]
            + graph * WEIGHTS["graph"]
            + rep * WEIGHTS["reputation"]
        )

        evidence = _build_evidence(cid, cap, role=role, department=department)

        reasons = []
        if lex > 0.2:
            reasons.append(f"Matches your query terms (lexical score {lex:.2f})")
        reasons.extend(sem_reasons)
        reasons.extend(graph_reasons)

        # Reputation reasoning with evidence
        if rep >= 0.6 and evidence.get("succeeded_runs", 0) > 0:
            reasons.append(
                f"High reputation ({rep * 100:.0f}/100) backed by "
                f"{evidence['succeeded_runs']} successful run"
                f"{'s' if evidence['succeeded_runs'] != 1 else ''}"
            )
        elif rep <= 0.2 and rep_lookup.get(cid) is not None:
            reasons.append("New or under-validated capability — try with caution")

        # Execution evidence
        if evidence.get("reliability_pct") is not None:
            reasons.append(
                f"{evidence['reliability_pct']}% success rate across "
                f"{evidence['total_runs']} prior run"
                f"{'s' if evidence['total_runs'] != 1 else ''}"
            )

        # Organization-level usage
        if evidence.get("distinct_initiators", 0) >= 2:
            reasons.append(
                f"Used by {evidence['distinct_initiators']} different people in the org"
            )

        # Role-based reasoning
        if role and role in (evidence.get("personas") or []):
            reasons.append(f"Recommended for {role}s by the platform's role mapping")

        # Trend hint
        if evidence.get("reputation_trend") == "rising":
            reasons.append("Reputation is rising over recent observations")
        elif evidence.get("reputation_trend") == "falling":
            reasons.append("Heads up: reputation has been declining recently")

        if not reasons:
            reasons.append("Suggested based on availability and category match")

        out.append(Recommendation(
            capability_id=cid,
            name=cap.get("name", cid),
            type=cap.get("type", "workflow"),
            final_score=round(final, 4),
            sub_scores={
                "lexical": round(lex, 3),
                "semantic": round(sem, 3),
                "graph": round(graph, 3),
                "reputation": round(rep, 3),
            },
            reasons=reasons,
            evidence=evidence,
            capability=cap,
        ))

    out.sort(key=lambda r: r.final_score, reverse=True)
    return out[:top_k]


def recommend_next_after_run(
    run_id: str,
    *,
    top_k: int = 5,
    registry: CapabilityRegistry | None = None,
) -> list[Recommendation]:
    """After a successful run, recommend what to do next.

    Reads the followed_by edges from the operational graph + reputation +
    similarity to surface 'most users do X next' answers.
    """
    reg = registry or default_registry()
    record = workflow_runner.get_run(run_id)
    if record is None or record.status != "succeeded":
        return []

    # Use the just-run capability as the anchor — graph followed_by gives
    # us strong signal, then we blend in reputation + semantic similarity.
    anchor = record.capability_id
    return recommend(
        query=anchor.replace("-", " ").replace("_", " "),
        recent_run_ids=[run_id],
        top_k=top_k,
        registry=reg,
    )


def recommend_pipelines_for_query(
    query: str,
    *,
    top_k: int = 5,
) -> list[dict]:
    """Lightweight pipeline-only recommendation: matches pipeline manifests by
    name / description / tag overlap with the query tokens."""
    if not query:
        return []
    query_tokens = _tokenize_simple(query)
    if not query_tokens:
        return []

    scored: list[tuple[float, dict]] = []
    for manifest in pipeline_engine.list_pipelines():
        text = " ".join([
            manifest.get("name", ""),
            manifest.get("description", ""),
            " ".join(manifest.get("tags") or []),
        ]).lower()
        manifest_tokens = set(_tokenize_simple(text))
        if not manifest_tokens:
            continue
        overlap = len(query_tokens & manifest_tokens)
        if overlap == 0:
            continue
        score = overlap / len(query_tokens)
        scored.append((score, manifest))

    scored.sort(key=lambda kv: kv[0], reverse=True)
    return [
        {"pipeline": m, "score": round(s, 3)}
        for s, m in scored[:top_k]
    ]


# ── Internal: candidate pool & signal computation ──────────────────────


def _candidate_pool(
    *,
    query: str,
    department: str | None,
    role: str | None,
    recent_run_ids: list[str],
    registry: CapabilityRegistry,
    all_caps: dict[str, dict],
    kind_set: set[str] | None,
) -> list[str]:
    """Build the set of capability IDs we'll score. Pulls from:
      - search hits for the query (top 40)
      - department members (top 20 by usage)
      - persona/role members (top 20)
      - graph-neighbours of recent runs (top 20)
      - reputation top-N as a baseline
    """
    pool: set[str] = set()

    if query:
        for r in search_index.search(query, top_k=40, registry=registry):
            pool.add(r.capability_id)

    if department:
        for cap in registry.snapshot().by_category(department)[:20]:
            pool.add(cap["id"])

    if role:
        # Use persona edges from the graph; fall back to tag containment.
        g = _cached_graph()
        for cid, _w in g.persona_capabilities(role, top_k=20):
            pool.add(cid)

    if recent_run_ids:
        for rid in recent_run_ids:
            rec = workflow_runner.get_run(rid)
            if not rec or rec.capability_id not in all_caps:
                continue
            g = _cached_graph()
            for nbr_id, _ in g.top_followed_by(rec.capability_id, top_k=10):
                pool.add(nbr_id)
            for nbr_id, _ in g.top_co_occurs(rec.capability_id, top_k=10):
                pool.add(nbr_id)

    # Baseline: top-reputation always-in. Keeps the pool non-empty when the
    # user's query produces zero hits.
    if not pool:
        for cap in sorted(
            all_caps.values(),
            key=lambda c: c.get("usage_count", 0),
            reverse=True,
        )[:30]:
            pool.add(cap["id"])

    # Apply kind filter
    if kind_set:
        pool = {cid for cid in pool if all_caps.get(cid, {}).get("type") in kind_set}

    # Apply department filter (post-pool, in case search/graph dragged in
    # other departments).
    if department:
        pool = {cid for cid in pool if all_caps.get(cid, {}).get("category") == department}

    return list(pool)[:_CANDIDATE_POOL_CAP]


def _lexical_scores(query: str, *, registry: CapabilityRegistry) -> dict[str, float]:
    """Run the search index and normalize raw TF scores into 0..1."""
    if not query:
        return {}
    raw = search_index.search(query, top_k=_CANDIDATE_POOL_CAP, registry=registry)
    if not raw:
        return {}
    max_score = max(r.score for r in raw) or 1.0
    return {r.capability_id: r.score / max_score for r in raw}


def _semantic_signals(*, query: str, role: str | None, department: str | None) -> dict:
    """Pre-compute the tokenized query + role/dept tokens once."""
    return {
        "query_tokens": set(_tokenize_simple(query)) if query else set(),
        "role_tokens": set(_tokenize_simple(role)) if role else set(),
        "department_tokens": set(_tokenize_simple(department)) if department else set(),
        "role_raw": role,
        "department_raw": department,
    }


def _semantic_match(cap: dict, signals: dict) -> tuple[float, list[str]]:
    """Score a capability against the user's query/role/department semantically.

    Returns (score, reasons) where score is 0..1 and reasons is a list of
    sentences explaining why it matched.
    """
    enrichment = semantic_analyzer.load_enrichment(cap["id"]) or {}
    if not enrichment:
        return 0.0, []

    query_tokens = signals["query_tokens"]
    role_raw = signals["role_raw"]
    department_raw = signals["department_raw"]

    semantic_tags = set(enrichment.get("semantic_tags") or [])
    domains = set(enrichment.get("business_domains") or [])
    personas = set(enrichment.get("recommended_user_personas") or [])
    patterns = set(enrichment.get("workflow_patterns") or [])

    reasons: list[str] = []
    score = 0.0

    if query_tokens:
        tag_overlap = len(query_tokens & {t.replace("_", " ") for t in semantic_tags}) \
                    + len(query_tokens & semantic_tags)
        pattern_overlap = sum(1 for p in patterns if any(qt in p for qt in query_tokens))
        if tag_overlap:
            score += min(0.5, tag_overlap * 0.15)
            reasons.append(
                f"Tagged with concepts from your query: "
                f"{', '.join(sorted(query_tokens & semantic_tags))[:80] or 'related concepts'}"
            )
        if pattern_overlap:
            score += min(0.2, pattern_overlap * 0.10)
            matched_patterns = [p for p in patterns if any(qt in p for qt in query_tokens)]
            reasons.append(f"Matches workflow pattern(s): {', '.join(matched_patterns)}")

    if role_raw and role_raw in personas:
        score += 0.25
        reasons.append(f"Designed for {role_raw}s")

    if department_raw and department_raw in domains:
        score += 0.15
        reasons.append(f"Fits {department_raw} domain")

    # Boost capabilities with stated high business impact
    impact = enrichment.get("business_impact_score") or 0
    if isinstance(impact, (int, float)) and impact >= 4:
        score += 0.10

    return min(1.0, score), reasons


def _graph_signals(recent_run_ids: list[str]) -> dict[str, float]:
    """Build a {capability_id: weight} map for capabilities that the graph
    suggests as natural next-steps given the user's recent runs.
    """
    if not recent_run_ids:
        return {}

    g = _cached_graph()
    weights: dict[str, float] = defaultdict(float)
    for rid in recent_run_ids:
        rec = workflow_runner.get_run(rid)
        if rec is None:
            continue
        anchor = rec.capability_id
        for nbr_id, w in g.top_followed_by(anchor, top_k=10):
            weights[nbr_id] += w
        for nbr_id, w in g.top_co_occurs(anchor, top_k=10):
            weights[nbr_id] += w * 0.5

    if not weights:
        return {}
    max_w = max(weights.values()) or 1.0
    return {cid: w / max_w for cid, w in weights.items()}


def _graph_match(capability_id: str, graph_signals: dict[str, float]) -> tuple[float, list[str]]:
    weight = graph_signals.get(capability_id, 0.0)
    if weight <= 0:
        return 0.0, []
    if weight >= 0.7:
        return weight, ["Frequently run right after your recent activity"]
    if weight >= 0.3:
        return weight, ["Often co-occurs with what you've been working on"]
    return weight, ["Has a weak historical link to your recent runs"]


def _reputation_lookup(capability_ids: list[str]) -> dict[str, float]:
    """Load persisted reputation scores; compute on miss only if cheap."""
    out: dict[str, float] = {}
    for cid in capability_ids:
        persisted = reputation_scorer.load_score(cid)
        if persisted:
            out[cid] = float(persisted.get("reputation_score", _DEFAULT_REPUTATION))
    return out


def _build_evidence(capability_id: str, cap: dict, *,
                     role: str | None, department: str | None) -> dict:
    """Compile the evidence payload that the UI shows below each recommendation.
    Cheap reads only — pulls from run history, feedback aggregates, and the
    persisted enrichment. No LLM calls."""
    from execution.ops_platform import feedback_store, workflow_runner as wr
    runs = wr.list_runs(capability_id=capability_id, limit=200)
    total = len(runs)
    succeeded = sum(1 for r in runs if r.status == "succeeded")
    reliability = round(succeeded / total * 100, 1) if total else None

    initiators = set()
    for r in runs:
        initiator = (r.inputs.get("__initiator") if isinstance(r.inputs, dict) else None) or "anonymous"
        initiators.add(initiator)

    enrichment = semantic_analyzer.load_enrichment(capability_id) or {}
    aggregate = feedback_store.get_aggregate(capability_id)

    trend_info = None
    try:
        trend_info = reputation_scorer.trend(capability_id)
    except Exception:
        trend_info = None

    return {
        "total_runs": total,
        "succeeded_runs": succeeded,
        "reliability_pct": reliability,
        "distinct_initiators": len(initiators),
        "feedback_count": aggregate.get("total_feedback", 0),
        "feedback_average": aggregate.get("overall_average"),
        "personas": enrichment.get("recommended_user_personas") or [],
        "departments": enrichment.get("recommended_departments") or [],
        "estimated_roi": enrichment.get("estimated_roi"),
        "reputation_trend": trend_info["direction"] if trend_info else None,
        "reputation_delta": trend_info["delta"] if trend_info else None,
        "category": cap.get("category"),
    }


# ── Graph cache ────────────────────────────────────────────────────────


_GRAPH_CACHE: operational_graph.OperationalGraph | None = None
_GRAPH_CACHE_VERSIONS: dict = {}  # topic.value -> mtime at last build


def _cached_graph() -> operational_graph.OperationalGraph:
    """Build the graph once per process and invalidate when a relevant cache
    bus topic has been bumped since the last build. Tests can force a rebuild
    with ``reset_graph_cache()``."""
    global _GRAPH_CACHE, _GRAPH_CACHE_VERSIONS
    from execution.ops_platform import cache_bus
    relevant = (
        cache_bus.Topic.RUN_RECORDED,
        cache_bus.Topic.FEEDBACK_SUBMITTED,
        cache_bus.Topic.PIPELINE_CREATED,
        cache_bus.Topic.PIPELINE_RUN_RECORDED,
        cache_bus.Topic.SEMANTIC_ENRICHED,
        cache_bus.Topic.REGISTRY_REFRESHED,
    )
    current = {t.value: cache_bus.current_version(t) for t in relevant}
    if _GRAPH_CACHE is None or current != _GRAPH_CACHE_VERSIONS:
        _GRAPH_CACHE = operational_graph.build_graph(persist=False)
        _GRAPH_CACHE_VERSIONS = current
    return _GRAPH_CACHE


def reset_graph_cache() -> None:
    global _GRAPH_CACHE, _GRAPH_CACHE_VERSIONS
    _GRAPH_CACHE = None
    _GRAPH_CACHE_VERSIONS = {}


# ── Token helper ───────────────────────────────────────────────────────


import re as _re
_TOKEN_RE = _re.compile(r"[a-zA-Z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "with",
    "is", "are", "be", "by", "as", "at", "this", "that", "it", "i", "we", "my",
    "need", "want", "help", "please",
})


def _tokenize_simple(text: str) -> set[str]:
    if not text:
        return set()
    return {t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOPWORDS}
