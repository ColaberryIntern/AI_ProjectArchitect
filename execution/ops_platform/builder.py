"""AI-assisted workflow builder — turn business intent into draft capability
and pipeline manifests, schema-valid and ready to publish.

End-to-end flow
---------------
1. Operator types a plain-English intent ("Help sales summarize RFPs and
   draft follow-up emails").
2. ``generate()`` queries: search_index (lexical) → semantic_analyzer
   (existing patterns / overlap) → operational_graph (typical sequences) →
   organizational_memory (what already succeeds) → workflow_optimizer
   (auto-pipeline suggestions).
3. The intent + signals are folded into a structured response:
     - reused_capabilities      (which existing capabilities to compose)
     - draft_pipeline_manifest  (schema-valid, ready for save_pipeline)
     - draft_capability_manifest (a brand-new capability stub if nothing
                                   suitable exists for one of the steps)
     - suggested_prompts        (per step)
     - suggested_mcp_servers
     - rollout_recommendation   ("start as experimental at 10%")
     - confidence_score         (0..1)
     - maintenance_complexity   (1..5)
     - rationales               (per generated artifact, the signals used)
4. The draft is persisted to ``output/ops_platform/drafts/{draft_id}.json``
   so the operator can iterate via the UI without losing state.

Optional LLM polish
-------------------
When ``llm_client.is_available()`` and ``polish=True``, the manifests are
sent to the LLM for naming/copywriting cleanup. The structural fields
(steps, dependencies, bindings) are NEVER mutated by the LLM — they're
derived deterministically and locked. This keeps the builder explainable:
the structural reason a step exists is always one of the signals listed
in the rationale.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution import llm_client
from execution.ops_platform import (
    organizational_memory,
    recommendation_engine,
    search_index,
    semantic_analyzer,
    workflow_optimizer,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_DRAFTS_DIR = OUTPUT_DIR / "ops_platform" / "drafts"


@dataclass
class BuilderDraft:
    draft_id: str
    intent: str
    role: str | None
    department: str | None
    workspace_id: str | None
    created_at: str
    reused_capabilities: list = field(default_factory=list)
    draft_pipeline_manifest: dict | None = None
    draft_capability_manifests: list = field(default_factory=list)
    suggested_prompts: dict = field(default_factory=dict)
    suggested_mcp_servers: list = field(default_factory=list)
    rollout_recommendation: dict = field(default_factory=dict)
    confidence_score: float = 0.0
    maintenance_complexity: int = 3
    rationales: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def generate(
    intent: str,
    *,
    role: str | None = None,
    department: str | None = None,
    workspace_id: str | None = None,
    polish: bool = False,
    registry: CapabilityRegistry | None = None,
) -> BuilderDraft:
    if not intent or len(intent.strip()) < 10:
        raise ValueError("intent must be at least 10 characters of plain English")

    reg = registry or default_registry()

    # --- Signals ----------------------------------------------------------
    rec_results = recommendation_engine.recommend(
        query=intent, role=role, department=department, top_k=8, registry=reg,
    )
    pipeline_matches = recommendation_engine.recommend_pipelines_for_query(intent, top_k=3)
    optimizer_hints = workflow_optimizer.auto_pipeline_suggestions(
        registry=reg, min_occurrences=2, window=3,
    )
    memory = organizational_memory.build_snapshot(registry=reg, persist=False)
    overlap = semantic_analyzer.workflow_overlap(registry=reg, threshold=0.5)
    op_patterns = semantic_analyzer.operational_patterns(registry=reg)

    # --- Decide reuse vs new capability ----------------------------------
    intent_tokens = _tokenize(intent)
    candidates = []
    for r in rec_results:
        if r.final_score >= 0.25:
            candidates.append(r)
    reused = [{
        "capability_id": r.capability_id,
        "name": r.name,
        "type": r.type,
        "match_score": r.final_score,
        "reasons": r.reasons[:3],
    } for r in candidates[:5]]

    # --- Draft pipeline (compose existing capabilities) ------------------
    pipeline = None
    rationales: list[dict] = []
    if len(reused) >= 2:
        pipeline = _draft_pipeline_from_candidates(intent, reused)
        rationales.append({
            "artifact": "pipeline",
            "based_on": "recommendation_engine + intent tokenization",
            "candidate_count": len(candidates),
        })
    elif optimizer_hints:
        # Borrow an existing auto-pipeline suggestion that matches the intent
        first = optimizer_hints[0]
        pipeline = first.evidence.get("draft_pipeline_id") and _pipeline_stub_from_hint(intent, first)
        if pipeline:
            rationales.append({
                "artifact": "pipeline",
                "based_on": "workflow_optimizer.AUTO_PIPELINE",
                "occurrences": first.evidence.get("occurrences"),
            })

    # --- Draft new capability when no reuse fits -------------------------
    new_caps: list[dict] = []
    if not reused:
        stub = _draft_new_capability_stub(intent, department=department)
        new_caps.append(stub)
        rationales.append({
            "artifact": "capability",
            "based_on": "no existing capability matched intent",
            "intent_tokens": sorted(intent_tokens),
        })

    # --- Suggested prompts per step --------------------------------------
    suggested_prompts: dict[str, str] = {}
    if pipeline:
        for step in pipeline["steps"]:
            suggested_prompts[step["step_id"]] = _suggest_step_prompt(step, intent)
    for cap in new_caps:
        suggested_prompts[cap["id"]] = cap.get("_suggested_prompt", "")

    # --- MCP server suggestions (from successful peers) ------------------
    mcp_servers: list[str] = []
    for r in candidates[:5]:
        cap = reg.get(r.capability_id)
        if cap:
            mcp_servers.extend(cap.get("mcp_servers_used") or [])
    mcp_servers = sorted(set(mcp_servers))[:5]

    # --- Confidence + complexity scoring ---------------------------------
    confidence = _confidence_score(reused, optimizer_hints, memory)
    complexity = _complexity_score(pipeline, new_caps)

    # --- Rollout recommendation ------------------------------------------
    rollout = _rollout_recommendation(confidence)

    # --- Warnings --------------------------------------------------------
    warnings: list[str] = []
    if overlap:
        warnings.append(f"{len(overlap)} pair(s) of existing capabilities overlap — "
                       "consider consolidation before adding new ones.")
    if not reused and not optimizer_hints:
        warnings.append("No existing capability matches your intent — drafting "
                       "a brand-new capability. Plan ownership before publishing.")
    if pipeline and len(pipeline["steps"]) > 5:
        warnings.append("Pipeline has more than 5 steps; consider splitting.")

    draft = BuilderDraft(
        draft_id=str(uuid.uuid4()),
        intent=intent,
        role=role, department=department, workspace_id=workspace_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        reused_capabilities=reused,
        draft_pipeline_manifest=pipeline,
        draft_capability_manifests=new_caps,
        suggested_prompts=suggested_prompts,
        suggested_mcp_servers=mcp_servers,
        rollout_recommendation=rollout,
        confidence_score=round(confidence, 2),
        maintenance_complexity=complexity,
        rationales=rationales,
        warnings=warnings,
    )

    if polish and llm_client.is_available() and pipeline:
        try:
            _polish_pipeline_copy(draft)
        except Exception:
            logger.warning("builder polish failed", exc_info=True)

    _persist(draft)
    return draft


def get_draft(draft_id: str) -> dict | None:
    path = _DRAFTS_DIR / f"{draft_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_drafts(*, limit: int = 50) -> list[dict]:
    if not _DRAFTS_DIR.exists():
        return []
    paths = sorted(_DRAFTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict] = []
    for p in paths[:limit]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def publish_draft(draft_id: str, *, actor: dict | str | None = None) -> dict:
    """Persist the draft's pipeline manifest via pipeline_engine.save_pipeline.
    Returns {published_pipeline_id} on success or {error} otherwise."""
    from execution.ops_platform import audit_log, pipeline_engine
    draft = get_draft(draft_id)
    if draft is None:
        return {"error": "draft not found"}
    manifest = draft.get("draft_pipeline_manifest")
    if not manifest:
        return {"error": "draft has no pipeline manifest to publish"}
    try:
        pipeline_engine.save_pipeline(manifest)
    except (ValueError, OSError) as e:
        return {"error": str(e)}
    audit_log.record(
        action="builder.published", entity_type="pipeline",
        entity_id=manifest["pipeline_id"],
        actor=actor or "anonymous",
        new_state={"pipeline_id": manifest["pipeline_id"], "draft_id": draft_id},
    )
    return {"published_pipeline_id": manifest["pipeline_id"]}


# ── Internal ───────────────────────────────────────────────────────────


_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "with",
    "is", "are", "be", "by", "as", "at", "i", "we", "my", "need", "want",
    "help", "please", "do",
})


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _draft_pipeline_from_candidates(intent: str, reused: list[dict]) -> dict:
    name = f"Builder-generated: {intent[:60]}"
    pipeline_id = "draft-" + (uuid.uuid4().hex[:8])
    steps = []
    for i, r in enumerate(reused[:4]):
        step = {
            "step_id": f"step_{i + 1}",
            "capability_id": r["capability_id"],
            "depends_on": ([f"step_{i}"] if i > 0 else []),
            "on_failure": "abort",
            "input_bindings": {},
        }
        steps.append(step)
    return {
        "pipeline_id": pipeline_id,
        "name": name,
        "description": f"Auto-drafted pipeline for the intent: {intent}",
        "version": "0.1.0",
        "created_by": {"name": "builder", "team": "Operations Platform"},
        "execution_strategy": "sequential",
        "tags": ["draft", "ai-builder"],
        "steps": steps,
    }


def _pipeline_stub_from_hint(intent: str, hint) -> dict:
    seq = hint.evidence.get("sequence") or []
    if not seq:
        return None
    return {
        "pipeline_id": f"builder-{uuid.uuid4().hex[:8]}",
        "name": f"Builder pipeline for: {intent[:60]}",
        "description": "Drafted from an auto-discovered pattern that matches your intent.",
        "version": "0.1.0",
        "created_by": {"name": "builder", "team": "Operations Platform"},
        "execution_strategy": "sequential",
        "tags": ["draft", "ai-builder", "discovery-derived"],
        "steps": [
            {
                "step_id": f"step_{i + 1}",
                "capability_id": cid,
                "depends_on": ([f"step_{i}"] if i > 0 else []),
                "on_failure": "abort",
                "input_bindings": {},
            }
            for i, cid in enumerate(seq)
        ],
    }


def _draft_new_capability_stub(intent: str, *, department: str | None) -> dict:
    cap_id = f"draft_{_slugify(intent)}_{uuid.uuid4().hex[:6]}"
    suggested_prompt = (
        f"You are a workflow agent helping with: {intent}. "
        "Produce a structured response that maps directly to the response contract."
    )
    return {
        "id": cap_id,
        "name": intent[:80].title(),
        "type": "workflow",
        "category": department or "Uncategorized",
        "subcategory": "Auto-drafted",
        "description": f"Auto-drafted capability for the intent: {intent}",
        "business_value": "Defined by the operator after review.",
        "version": "0.1.0",
        "owner": {"name": "Unassigned (builder draft)", "team": department or ""},
        "inputs": [{"name": "task", "type": "text",
                     "description": "What you want this capability to do.",
                     "required": True}],
        "outputs": [{"name": "result", "type": "markdown",
                      "description": "The structured output."}],
        "tags": ["draft", "ai-builder"],
        "difficulty": "intermediate",
        "estimated_time_savings": {"minutes_per_run": 15, "runs_per_week_estimate": 5},
        "feedback_enabled": True,
        "_draft": True,
        "_suggested_prompt": suggested_prompt,
    }


def _suggest_step_prompt(step: dict, intent: str) -> str:
    return (
        f"For step '{step['step_id']}' of the pipeline serving: '{intent[:100]}', "
        f"use the existing prompt for capability '{step['capability_id']}'. "
        "Pass through the upstream step's primary output."
    )


def _confidence_score(reused, optimizer_hints, memory) -> float:
    score = 0.0
    if reused:
        # Average the top-3 recommendation final_score
        scores = [r["match_score"] for r in reused[:3]]
        score += min(0.6, sum(scores) / len(scores))
    if optimizer_hints:
        score += 0.15
    if memory.what_succeeds:
        score += 0.10
    if not reused and not optimizer_hints:
        score = max(0.10, score)  # baseline floor for new-capability drafts
    return min(1.0, score)


def _complexity_score(pipeline: dict | None, new_caps: list) -> int:
    if pipeline and len(pipeline.get("steps") or []) >= 4:
        return 4
    if new_caps:
        return 3
    if pipeline:
        return 2
    return 1


def _rollout_recommendation(confidence: float) -> dict:
    if confidence >= 0.7:
        return {"strategy": "approved", "rollout_percentage": 100,
                "reason": "High-confidence reuse of well-validated capabilities."}
    if confidence >= 0.4:
        return {"strategy": "experimental", "rollout_percentage": 20,
                "reason": "Moderate confidence — recommend rollout at 20% with metrics review."}
    return {"strategy": "draft", "rollout_percentage": 0,
            "reason": "Low confidence — keep in draft until validated by a small operator pilot."}


def _persist(draft: BuilderDraft) -> None:
    _DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    target = _DRAFTS_DIR / f"{draft.draft_id}.json"
    target.write_text(json.dumps(draft.to_dict(), indent=2, ensure_ascii=False),
                       encoding="utf-8")


def _polish_pipeline_copy(draft: BuilderDraft) -> None:
    """Ask the LLM to clean up name + description ONLY. Structural fields
    (steps, bindings) are not mutated."""
    if draft.draft_pipeline_manifest is None:
        return
    manifest = draft.draft_pipeline_manifest
    prompt = (
        f"Improve only the 'name' and 'description' fields of this pipeline manifest "
        f"to be clearer for a non-technical operator. Keep them concise.\n\n"
        f"Intent: {draft.intent}\n\n"
        f"Current name: {manifest.get('name')}\n"
        f"Current description: {manifest.get('description')}\n\n"
        f"Respond ONLY with JSON: {{\"name\": \"...\", \"description\": \"...\"}}"
    )
    try:
        resp = llm_client.chat(
            system_prompt="You polish operator-facing copy.",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200,
            response_format={"type": "json_object"},
        )
        polish = json.loads(resp.content or "{}")
        if isinstance(polish, dict):
            if polish.get("name"):
                manifest["name"] = polish["name"][:120]
            if polish.get("description"):
                manifest["description"] = polish["description"][:600]
    except (llm_client.LLMClientError, json.JSONDecodeError):
        pass
