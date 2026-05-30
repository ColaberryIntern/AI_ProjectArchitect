"""Semantic analyzer — turns a capability's manifest + README + prompt into the
17-field semantic enrichment payload defined by semantic_enrichment.schema.json.

Strategy:
1. Try the LLM with a tight rubric + JSON-mode output. ~500-800 tokens out.
2. On any failure (LLM unavailable, JSON parse error, schema error), fall back
   to a deterministic heuristic analyzer that infers everything it can from
   the manifest alone (tags, category, description, business_value).
3. Result is cached at output/ops_platform/semantic/{capability_id}.json
   keyed by a content hash of the inputs. A registry refresh that doesn't
   change a capability's inputs reuses the cached enrichment for free.
4. Duplicate detection is a separate pure-Python pass: pairwise Jaccard on
   semantic_tags across all enriched capabilities, threshold 0.6.

The analyzer never raises — every public function returns a structured result
even when the LLM is unavailable. This is the contract the registry depends on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from config.settings import OUTPUT_DIR, PROJECT_ROOT, SCHEMAS_DIR
from execution import llm_client
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_CACHE_DIR = OUTPUT_DIR / "ops_platform" / "semantic"
_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "semantic_enrichment.schema.json"

# Jaccard threshold above which two capabilities are flagged as duplicate candidates.
DUPLICATE_THRESHOLD = 0.6

# Token budget per call — capped to keep cost predictable on bulk imports.
ANALYSIS_MAX_TOKENS = 1500


@dataclass
class EnrichmentResult:
    """Wrapper for an enrichment pass."""

    capability_id: str
    payload: dict
    from_cache: bool
    source: str  # 'llm', 'heuristic', 'manual', 'cache'


# ── Public API ──────────────────────────────────────────────────────────


def enrich_capability(
    capability: dict,
    *,
    force_refresh: bool = False,
) -> EnrichmentResult:
    """Generate (or load from cache) the semantic enrichment for a capability.

    Caller is the registry or an admin endpoint. The function:
      - Reads the manifest + README + prompt file
      - Computes a content hash
      - Returns cached enrichment when the hash matches
      - Otherwise calls the LLM, then falls back to heuristics on failure
      - Persists the result keyed by capability_id
    """
    capability_id = capability["id"]
    inputs = _gather_inputs(capability)
    content_hash = _content_hash(inputs)

    cached = _load_cached(capability_id)
    if cached and cached.get("_content_hash") == content_hash and not force_refresh:
        payload = {k: v for k, v in cached.items() if not k.startswith("_")}
        return EnrichmentResult(
            capability_id=capability_id,
            payload=payload,
            from_cache=True,
            source=payload.get("source", "cache"),
        )

    if llm_client.is_available():
        llm_payload = _try_llm(capability, inputs)
        if llm_payload is not None:
            payload = _finalize(capability_id, llm_payload, "llm")
            _persist(payload, content_hash)
            return EnrichmentResult(capability_id, payload, False, "llm")

    payload = _finalize(capability_id, _heuristic_enrichment(capability), "heuristic")
    _persist(payload, content_hash)
    return EnrichmentResult(capability_id, payload, False, "heuristic")


def enrich_all(
    *,
    registry: CapabilityRegistry | None = None,
    force_refresh: bool = False,
) -> dict[str, EnrichmentResult]:
    """Enrich every capability in the registry. Used by bulk repository imports."""
    reg = registry or default_registry()
    snap = reg.snapshot()
    out: dict[str, EnrichmentResult] = {}
    for cap in snap.capabilities:
        out[cap["id"]] = enrich_capability(cap, force_refresh=force_refresh)
    return out


def detect_duplicates(
    *,
    registry: CapabilityRegistry | None = None,
    threshold: float = DUPLICATE_THRESHOLD,
) -> dict[str, list[str]]:
    """Return {capability_id: [duplicate_capability_id, ...]} based on
    semantic_tags Jaccard similarity. Runs over cached enrichments —
    callers must enrich_all first if their cache is cold.
    """
    reg = registry or default_registry()
    enriched: dict[str, set[str]] = {}
    for cap in reg.snapshot().capabilities:
        cached = _load_cached(cap["id"])
        if not cached:
            continue
        tags = set(cached.get("semantic_tags") or [])
        if tags:
            enriched[cap["id"]] = tags

    out: dict[str, list[str]] = {cid: [] for cid in enriched}
    ids = list(enriched.keys())
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            j = _jaccard(enriched[a], enriched[b])
            if j >= threshold:
                out[a].append(b)
                out[b].append(a)
    return {k: v for k, v in out.items() if v}


def load_enrichment(capability_id: str) -> dict | None:
    """Return the persisted enrichment for a capability, or None."""
    cached = _load_cached(capability_id)
    if not cached:
        return None
    return {k: v for k, v in cached.items() if not k.startswith("_")}


# ── Phase 3 deeper repository intelligence ─────────────────────────────


_ANTI_PATTERN_KEYWORDS = {
    "deprecated_tooling": [
        "deprecated", "legacy", "old api", "removed",
        "will be removed", "soon to be replaced",
    ],
    "incomplete_capability": [
        "todo", "fixme", "wip", "work in progress", "not implemented",
        "stub", "placeholder",
    ],
    "manual_step_required": [
        "manually", "by hand", "human required", "manual step", "out-of-band",
    ],
    "potentially_destructive": [
        "delete", "drop table", "force push", "overwrite", "destructive",
        "rm -rf",
    ],
}


def detect_anti_patterns(
    *,
    registry: CapabilityRegistry | None = None,
) -> dict[str, list[dict]]:
    """Scan capability manifests + READMEs + prompts for anti-pattern signals.

    Returns {anti_pattern_kind: [{capability_id, name, evidence_excerpt}, ...]}.
    Pure substring scanner — no LLM, deterministic. Used to surface 'these are
    the warts your operators should know about' in the analytics dashboard.
    """
    reg = registry or default_registry()
    out: dict[str, list[dict]] = {k: [] for k in _ANTI_PATTERN_KEYWORDS}
    for cap in reg.snapshot().capabilities:
        inputs = _gather_inputs(cap)
        text_pool = " ".join([
            cap.get("description", ""), cap.get("business_value", ""),
            inputs.get("readme", "")[:5000], inputs.get("prompt", "")[:3000],
        ]).lower()
        for kind, keywords in _ANTI_PATTERN_KEYWORDS.items():
            for kw in keywords:
                idx = text_pool.find(kw)
                if idx == -1:
                    continue
                excerpt_start = max(0, idx - 40)
                excerpt_end = min(len(text_pool), idx + len(kw) + 40)
                out[kind].append({
                    "capability_id": cap["id"],
                    "name": cap.get("name", cap["id"]),
                    "keyword": kw,
                    "excerpt": text_pool[excerpt_start:excerpt_end].strip(),
                })
                break  # one hit per kind per capability is enough signal
    return out


def workflow_overlap(
    *,
    registry: CapabilityRegistry | None = None,
    threshold: float = 0.4,
) -> list[dict]:
    """Pairs of capabilities whose semantic_tags overlap above a lower
    threshold than detect_duplicates. Useful for 'these two probably should
    be merged or one is a special case of the other'."""
    reg = registry or default_registry()
    enriched: dict[str, set[str]] = {}
    for cap in reg.snapshot().capabilities:
        cached = _load_cached(cap["id"])
        if not cached:
            continue
        tags = set(cached.get("semantic_tags") or [])
        if tags:
            enriched[cap["id"]] = tags

    pairs: list[dict] = []
    ids = list(enriched.keys())
    by_id = reg.snapshot().by_id()
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            j = _jaccard(enriched[a], enriched[b])
            if threshold <= j < DUPLICATE_THRESHOLD:
                pairs.append({
                    "a": a, "a_name": by_id.get(a, {}).get("name", a),
                    "b": b, "b_name": by_id.get(b, {}).get("name", b),
                    "overlap": round(j, 3),
                    "shared_tags": sorted(enriched[a] & enriched[b])[:6],
                })
    pairs.sort(key=lambda p: p["overlap"], reverse=True)
    return pairs


def operational_patterns(
    *,
    registry: CapabilityRegistry | None = None,
) -> dict[str, list[str]]:
    """Aggregate workflow_patterns across the registry.

    Returns {pattern: [capability_ids]} so the operator can see which
    capabilities collectively implement, say, document_summarization.
    """
    reg = registry or default_registry()
    out: dict[str, list[str]] = defaultdict(list) if False else {}
    from collections import defaultdict as _dd
    out = _dd(list)
    for cap in reg.snapshot().capabilities:
        enrichment = load_enrichment(cap["id"]) or {}
        for p in enrichment.get("workflow_patterns") or []:
            out[p].append(cap["id"])
    return {k: sorted(set(v)) for k, v in out.items()}


# ── LLM path ────────────────────────────────────────────────────────────


_LLM_SYSTEM = """You are a Semantic Analyst for the Colaberry AI Operations
Platform. Given a capability's manifest, README, and primary prompt, you produce
a structured JSON enrichment that the platform uses for search, ranking,
deduplication, and recommendations.

Your response MUST be a single JSON object with EXACTLY these keys (no extras,
no omissions): operational_intent, business_domains, recommended_departments,
workflow_patterns, automation_potential, complexity_score, reusability_score,
business_impact_score, capability_similarity, duplicate_candidates,
recommended_followup_workflows, recommended_preceding_workflows,
execution_dependencies, organizational_value_summary, recommended_user_personas,
estimated_roi, semantic_tags.

Constraints:
- automation_potential ∈ {fully_automatable, human_in_loop, advisory_only, requires_judgment}
- complexity_score, reusability_score, business_impact_score ∈ 1..5
- capability_similarity items: {"capability_id": str, "similarity": 0..1, "reason": str}
- semantic_tags: lowercase, hyphen-or-underscore, no spaces

Do not invent capability IDs. Leave duplicate_candidates,
capability_similarity, recommended_followup_workflows, and
recommended_preceding_workflows as empty arrays unless you have explicit
evidence (the user-supplied known capabilities list)."""


def _try_llm(capability: dict, inputs: dict) -> dict | None:
    """Call the LLM. Return the parsed payload on success, None on any failure."""
    prompt = _build_llm_prompt(capability, inputs)
    try:
        response = llm_client.chat(
            system_prompt=_LLM_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=ANALYSIS_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
    except (llm_client.LLMUnavailableError, llm_client.LLMClientError) as e:
        logger.info("semantic_analyzer LLM call failed for %s: %s", capability["id"], e)
        return None
    except Exception:
        logger.warning("semantic_analyzer LLM call raised", exc_info=True)
        return None

    raw = response.content or ""
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return None

    # Coerce missing fields to safe defaults rather than reject outright.
    parsed = _coerce(parsed)
    errors = _validate_partial(parsed)
    if errors:
        logger.info("semantic_analyzer LLM payload failed validation for %s: %s",
                    capability["id"], errors[:2])
        return None
    return parsed


def _build_llm_prompt(capability: dict, inputs: dict) -> str:
    return (
        "Capability manifest (JSON, condensed):\n"
        f"{_compact_manifest(capability)}\n\n"
        "README (truncated to 3000 chars):\n"
        f"{(inputs.get('readme') or '')[:3000]}\n\n"
        "Primary prompt (truncated to 3000 chars):\n"
        f"{(inputs.get('prompt') or '')[:3000]}\n\n"
        "Return only the JSON enrichment."
    )


# ── Heuristic fallback ─────────────────────────────────────────────────


_DOMAIN_KEYWORDS = {
    "Sales": ["sales", "proposal", "rfp", "deal", "pipeline", "lead", "quote", "customer"],
    "Operations": ["operations", "ops", "workflow", "scheduling", "process", "logistics"],
    "Finance": ["finance", "invoice", "billing", "ar", "ap", "accounting", "payable", "receivable", "settlement"],
    "Engineering": ["code", "review", "deploy", "test", "ci", "build", "refactor", "bug"],
    "Marketing": ["marketing", "campaign", "content", "seo", "social", "brand"],
    "Customer Support": ["support", "ticket", "helpdesk", "kb", "knowledge", "chat", "case"],
    "HR": ["hr", "hiring", "onboarding", "performance", "review", "employee"],
    "Legal": ["legal", "contract", "compliance", "policy", "review", "audit"],
    "Knowledge": ["summarize", "summary", "meeting", "note", "document"],
}


def _heuristic_enrichment(capability: dict) -> dict:
    """Inference from manifest fields only — no LLM, no I/O."""
    name = capability.get("name", "")
    desc = capability.get("description", "")
    bv = capability.get("business_value", "")
    category = capability.get("category", "")
    sub = capability.get("subcategory", "")
    tags = capability.get("tags") or []
    inputs = capability.get("inputs") or []
    difficulty = capability.get("difficulty", "beginner")

    text_pool = " ".join([name, desc, bv, category, sub, " ".join(tags),
                          " ".join(i.get("name", "") for i in inputs)]).lower()

    # Business domains: any keyword match
    domains: list[str] = []
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw in text_pool for kw in keywords):
            domains.append(domain)
    if not domains and category:
        domains = [category]

    # Recommended departments: domains intersected with categories already in manifest
    recommended_depts = list(dict.fromkeys([category] + domains)) if category else domains

    # Workflow patterns: simple keyword tagging
    patterns: list[str] = []
    if any(w in text_pool for w in ["summari", "digest", "executive"]):
        patterns.append("document_summarization")
    if any(w in text_pool for w in ["extract", "parse", "structured"]):
        patterns.append("data_extraction")
    if any(w in text_pool for w in ["triage", "classify", "route"]):
        patterns.append("inbound_triage")
    if any(w in text_pool for w in ["review", "verify", "audit"]):
        patterns.append("quality_review")
    if any(w in text_pool for w in ["generate", "compose", "draft"]):
        patterns.append("content_generation")

    # Automation potential
    if any(w in text_pool for w in ["approve", "decision", "judgment", "human"]):
        automation_potential = "human_in_loop"
    elif any(w in text_pool for w in ["analyze", "summarize", "report"]):
        automation_potential = "advisory_only"
    elif any(w in text_pool for w in ["compose", "generate", "draft", "extract"]):
        automation_potential = "fully_automatable"
    else:
        automation_potential = "requires_judgment"

    # Complexity
    complexity = {"beginner": 2, "intermediate": 3, "advanced": 4}.get(difficulty, 2)
    if capability.get("type") == "pipeline":
        complexity = min(5, complexity + 1)

    # Reusability — high when tags + inputs are generic
    reuse = 3
    if len(tags) >= 4:
        reuse = 4
    if len(inputs) <= 2:
        reuse = min(5, reuse + 1)

    # Business impact — proxy off declared time savings
    impact = 3
    ets = (capability.get("estimated_time_savings") or {}).get("minutes_per_run", 0)
    if ets >= 30:
        impact = 4
    if ets >= 90:
        impact = 5

    # Personas from tags
    personas: list[str] = []
    if "sales" in text_pool:
        personas.append("Account Executive")
    if any(w in text_pool for w in ["controller", "cfo", "finance"]):
        personas.append("Controller")
    if any(w in text_pool for w in ["ops", "operations", "operations manager"]):
        personas.append("Operations Manager")
    if any(w in text_pool for w in ["support", "csm", "customer success"]):
        personas.append("Customer Success Manager")
    if not personas:
        personas.append("Knowledge Worker")

    # Semantic tags — normalized
    base_tags = set(tags) | set(domains) | set(patterns)
    if category:
        base_tags.add(category)
    semantic_tags = sorted({_normalize_tag(t) for t in base_tags if t})

    # Execution dependencies from manifest
    deps = (
        (capability.get("mcp_servers_used") or [])
        + (capability.get("agents_used") or [])
        + (capability.get("dependencies") or [])
    )

    return {
        "operational_intent": desc or bv or f"Run the {name} task.",
        "business_domains": domains,
        "recommended_departments": recommended_depts,
        "workflow_patterns": patterns,
        "automation_potential": automation_potential,
        "complexity_score": complexity,
        "reusability_score": reuse,
        "business_impact_score": impact,
        "capability_similarity": [],
        "duplicate_candidates": [],
        "recommended_followup_workflows": [],
        "recommended_preceding_workflows": [],
        "execution_dependencies": deps,
        "organizational_value_summary": bv or desc or f"Operational capability: {name}.",
        "recommended_user_personas": personas,
        "estimated_roi": (
            f"~{int(ets)} minutes per run"
            + (f"; ~{capability.get('estimated_time_savings',{}).get('runs_per_week_estimate', 0)} runs/week typical"
               if ets else "")
        ),
        "semantic_tags": semantic_tags,
    }


# ── Helpers ─────────────────────────────────────────────────────────────


def _gather_inputs(capability: dict) -> dict:
    """Read README and prompt content (if available) for the LLM prompt."""
    meta = capability.get("_meta") or {}
    abs_dir = meta.get("plugin_dir_absolute")
    plugin_dir = Path(abs_dir) if abs_dir else (PROJECT_ROOT / meta.get("plugin_dir", ""))
    inputs = {"manifest": _compact_manifest(capability)}

    readme_rel = capability.get("readme_path") or "README.md"
    readme_file = plugin_dir / readme_rel
    if readme_file.exists():
        try:
            inputs["readme"] = readme_file.read_text(encoding="utf-8")
        except OSError:
            pass

    prompt_rel = capability.get("prompt_path")
    if prompt_rel:
        prompt_file = plugin_dir / prompt_rel
        if prompt_file.exists():
            try:
                inputs["prompt"] = prompt_file.read_text(encoding="utf-8")
            except OSError:
                pass

    return inputs


def _compact_manifest(capability: dict) -> str:
    """Return a JSON string of just the fields the analyzer cares about."""
    keep = ["id", "name", "type", "category", "subcategory", "description",
            "business_value", "tags", "inputs", "outputs", "difficulty",
            "estimated_time_savings", "mcp_servers_used", "agents_used",
            "dependencies"]
    compact = {k: capability.get(k) for k in keep if k in capability}
    return json.dumps(compact, ensure_ascii=False)


def _content_hash(inputs: dict) -> str:
    h = hashlib.sha256()
    for key in sorted(inputs.keys()):
        h.update(key.encode("utf-8"))
        h.update(str(inputs[key]).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _normalize_tag(tag: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", tag.lower()).strip("_")


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _extract_json(raw: str) -> dict | None:
    """Forgiving JSON extraction — pure / fenced / prose-wrapped."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    if fence:
        try:
            parsed = json.loads(fence.group(1))
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False; continue
        if ch == "\\":
            escape = True; continue
        if ch == '"':
            in_string = not in_string; continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start:i + 1])
                    return parsed if isinstance(parsed, dict) else None
                except (json.JSONDecodeError, TypeError):
                    return None
    return None


def _coerce(payload: dict) -> dict:
    """Fill defaults for any missing field. Used after LLM parse."""
    defaults = {
        "operational_intent": "",
        "business_domains": [],
        "recommended_departments": [],
        "workflow_patterns": [],
        "automation_potential": "advisory_only",
        "complexity_score": 3,
        "reusability_score": 3,
        "business_impact_score": 3,
        "capability_similarity": [],
        "duplicate_candidates": [],
        "recommended_followup_workflows": [],
        "recommended_preceding_workflows": [],
        "execution_dependencies": [],
        "organizational_value_summary": "",
        "recommended_user_personas": [],
        "estimated_roi": "",
        "semantic_tags": [],
    }
    out = {**defaults, **{k: v for k, v in payload.items() if k in defaults}}
    # clamp numeric scores
    for k in ("complexity_score", "reusability_score", "business_impact_score"):
        try:
            v = int(out[k])
            out[k] = max(1, min(5, v))
        except (TypeError, ValueError):
            out[k] = 3
    if out["automation_potential"] not in (
        "fully_automatable", "human_in_loop", "advisory_only", "requires_judgment"
    ):
        out["automation_potential"] = "advisory_only"
    # normalize tags
    out["semantic_tags"] = sorted({_normalize_tag(t) for t in (out["semantic_tags"] or []) if t})
    return out


def _finalize(capability_id: str, payload: dict, source: str) -> dict:
    """Add envelope fields (capability_id, generated_at, source) before persistence."""
    out = dict(payload)
    out["capability_id"] = capability_id
    out["generated_at"] = datetime.now(timezone.utc).isoformat()
    out["source"] = source
    return out


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    try:
        return _SCHEMA_CACHE
    except NameError:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
        return _SCHEMA_CACHE


def _validate_partial(payload: dict) -> list[str]:
    """Validate against schema once envelope is attached. Tolerates the partial
    payload-before-finalize state by stubbing the envelope fields."""
    schema = _load_schema()
    stub = {"capability_id": "stub", "generated_at": "2026-01-01T00:00:00+00:00", "source": "llm"}
    test_payload = {**stub, **payload}
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(test_payload), key=lambda e: e.absolute_path)
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in errors
    ]


def _persist(payload: dict, content_hash: str) -> None:
    """Write the enrichment to disk, keyed by capability_id."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = _CACHE_DIR / f"{payload['capability_id']}.json"
    to_write = dict(payload)
    to_write["_content_hash"] = content_hash
    target.write_text(json.dumps(to_write, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from execution.ops_platform import cache_bus
        cache_bus.emit(cache_bus.Topic.SEMANTIC_ENRICHED, {
            "capability_id": payload["capability_id"],
            "source": payload.get("source", "unknown"),
        })
    except Exception:
        logger.warning("cache_bus emit failed for SEMANTIC_ENRICHED", exc_info=True)


def _load_cached(capability_id: str) -> dict | None:
    target = _CACHE_DIR / f"{capability_id}.json"
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
