"""Outcome tracking + adaptive learning engine for AI Advisory.

Tracks what happens after system generation (conversions, drop-offs,
bookings) and uses this data to adjust capability/domain weights over time.

Also generates optimization suggestions and confidence scores.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config.settings import ADVISORY_OUTPUT_DIR

logger = logging.getLogger(__name__)

_OUTCOMES_DB_PATH = ADVISORY_OUTPUT_DIR / "_outcomes_db.json"
_ADAPTIVE_WEIGHTS_PATH = ADVISORY_OUTPUT_DIR / "_adaptive_weights.json"

LEARNING_RATE = 0.03
WEIGHT_DECAY = 0.995  # Gradual decay per cycle to prevent bias lock-in
MAX_WEIGHT_CHANGE = 0.15  # Safety: max absolute weight change from baseline


# ── Outcome Tracking ────────────────────────────────────────────────

def record_outcome(session_id: str, outcome_type: str, details: dict = None) -> dict:
    """Record a user outcome after system generation.

    Args:
        session_id: Advisory session ID.
        outcome_type: One of: viewed_results, downloaded_pdf, booked_call,
                      completed_flow, dropped_off, revisited.
        details: Additional context (stage, capabilities, domain, etc.)

    Returns:
        The recorded outcome dict.
    """
    outcome = {
        "session_id": session_id,
        "outcome_type": outcome_type,
        "details": details or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    outcomes = _load_outcomes()
    outcomes.append(outcome)
    _save_outcomes(outcomes)

    # Trigger learning if it's a significant event
    if outcome_type in ("booked_call", "downloaded_pdf", "completed_flow"):
        _process_positive_signal(session_id, outcome_type, details or {})
    elif outcome_type == "dropped_off":
        _process_negative_signal(session_id, details or {})

    return outcome


def get_session_outcomes(session_id: str) -> list[dict]:
    """Get all outcomes for a session."""
    return [o for o in _load_outcomes() if o["session_id"] == session_id]


# ── Adaptive Weights ────────────────────────────────────────────────

def get_adaptive_weights() -> dict:
    """Load current adaptive weights."""
    if not _ADAPTIVE_WEIGHTS_PATH.exists():
        return _default_weights()
    try:
        with open(_ADAPTIVE_WEIGHTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _default_weights()


def get_capability_weight_adjustment(cap_id: str) -> float:
    """Get the learned weight adjustment for a capability.

    Returns a multiplier (0.85 - 1.15) that can be applied to the base score.
    """
    weights = get_adaptive_weights()
    adjustment = weights.get("capability_adjustments", {}).get(cap_id, 0.0)
    return 1.0 + max(-MAX_WEIGHT_CHANGE, min(MAX_WEIGHT_CHANGE, adjustment))


def get_domain_weight_adjustment(domain: str) -> float:
    """Get the learned weight adjustment for a domain."""
    weights = get_adaptive_weights()
    adjustment = weights.get("domain_adjustments", {}).get(domain, 0.0)
    return 1.0 + max(-MAX_WEIGHT_CHANGE, min(MAX_WEIGHT_CHANGE, adjustment))


# ── Learning Engine ─────────────────────────────────────────────────

def _process_positive_signal(session_id: str, event_type: str, details: dict):
    """Boost weights for capabilities/domains in successful sessions."""
    weights = get_adaptive_weights()
    caps = details.get("capabilities", [])
    domain = details.get("domain", "")

    strength = {"booked_call": 0.8, "downloaded_pdf": 0.5, "completed_flow": 0.3}.get(event_type, 0.3)

    for cap_id in caps:
        current = weights["capability_adjustments"].get(cap_id, 0.0)
        delta = LEARNING_RATE * strength
        weights["capability_adjustments"][cap_id] = _clamp(current + delta)

    if domain:
        current = weights["domain_adjustments"].get(domain, 0.0)
        delta = LEARNING_RATE * strength
        weights["domain_adjustments"][domain] = _clamp(current + delta)

    weights["total_signals"] = weights.get("total_signals", 0) + 1
    weights["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save_adaptive_weights(weights)


def _process_negative_signal(session_id: str, details: dict):
    """Reduce weights for capabilities/domains in sessions where users dropped off."""
    weights = get_adaptive_weights()
    caps = details.get("capabilities", [])
    domain = details.get("domain", "")
    stage = details.get("stage", "")

    strength = 0.3 if stage == "results" else 0.2

    for cap_id in caps:
        current = weights["capability_adjustments"].get(cap_id, 0.0)
        delta = LEARNING_RATE * strength
        weights["capability_adjustments"][cap_id] = _clamp(current - delta)

    if domain:
        current = weights["domain_adjustments"].get(domain, 0.0)
        delta = LEARNING_RATE * strength * 0.5
        weights["domain_adjustments"][domain] = _clamp(current - delta)

    weights["total_signals"] = weights.get("total_signals", 0) + 1
    weights["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save_adaptive_weights(weights)


def apply_weight_decay():
    """Apply gradual decay to all weights. Call periodically."""
    weights = get_adaptive_weights()
    for cap_id in weights["capability_adjustments"]:
        weights["capability_adjustments"][cap_id] *= WEIGHT_DECAY
    for domain in weights["domain_adjustments"]:
        weights["domain_adjustments"][domain] *= WEIGHT_DECAY
    _save_adaptive_weights(weights)


# ── Optimization Suggestions ────────────────────────────────────────

def generate_optimization_suggestions(session: dict) -> list[dict]:
    """Generate improvement suggestions for the current architecture.

    Looks at the selected capabilities, architecture, and learned weights
    to suggest additions, removals, or adjustments.
    """
    suggestions = []
    caps = session.get("selected_capabilities", [])
    architecture = session.get("architecture", {})
    engines = architecture.get("engines", {})
    problem = session.get("problem_analysis", {})
    primary_domain = problem.get("primary_problem", "")

    # Suggestion 1: Missing critical capabilities
    _suggest_missing_capabilities(caps, primary_domain, suggestions)

    # Suggestion 2: Over-complexity
    if len(caps) > 10:
        suggestions.append({
            "type": "reduce_complexity",
            "title": "Consider simplifying",
            "description": f"Your system has {len(caps)} capabilities. Systems with 6-8 focused capabilities tend to perform better.",
            "impact": "moderate",
        })

    # Suggestion 3: Missing monitoring
    has_monitoring = any(c in caps for c in ["quality_monitoring", "system_health_monitoring", "sentiment_monitoring"])
    if not has_monitoring and len(engines) > 2:
        suggestions.append({
            "type": "add_capability",
            "title": "Add system monitoring",
            "description": "Multi-engine systems benefit from real-time monitoring. Consider adding quality or system health monitoring.",
            "capability_id": "quality_monitoring",
            "impact": "high",
        })

    # Suggestion 4: Learned insights (from adaptive weights)
    weights = get_adaptive_weights()
    if weights.get("total_signals", 0) >= 5:
        top_performing = sorted(
            weights.get("capability_adjustments", {}).items(),
            key=lambda x: x[1], reverse=True,
        )[:3]
        for cap_id, adj in top_performing:
            if cap_id not in caps and adj > 0.05:
                from execution.advisory.capability_catalog import get_capabilities_by_ids
                cap_details = get_capabilities_by_ids([cap_id])
                if cap_details:
                    suggestions.append({
                        "type": "learned_recommendation",
                        "title": f"Consider: {cap_details[0]['name']}",
                        "description": f"This capability appears in {int(adj * 100)}% more successful systems similar to yours.",
                        "capability_id": cap_id,
                        "impact": "high",
                    })

    return suggestions[:5]


def _suggest_missing_capabilities(caps, primary_domain, suggestions):
    """Suggest capabilities that are commonly paired with the selected ones."""
    common_pairings = {
        "operations": {
            "route_optimization": "inventory_optimization",
            "workflow_automation": "quality_monitoring",
            "resource_scheduling": "workflow_automation",
        },
        "sales": {
            "auto_lead_scoring": "outreach_automation",
            "sales_pipeline_forecast": "deal_intelligence",
            "outreach_automation": "email_drafting",
        },
        "customer": {
            "ai_chat_support": "knowledge_base_qa",
            "ticket_auto_triage": "sentiment_monitoring",
        },
    }
    pairings = common_pairings.get(primary_domain, {})
    for existing, missing in pairings.items():
        if existing in caps and missing not in caps:
            from execution.advisory.capability_catalog import get_capabilities_by_ids
            cap_details = get_capabilities_by_ids([missing])
            if cap_details:
                suggestions.append({
                    "type": "add_capability",
                    "title": f"Add {cap_details[0]['name']}",
                    "description": f"Commonly paired with {existing.replace('_', ' ').title()} for better results.",
                    "capability_id": missing,
                    "impact": "high",
                })


# ── Confidence Score ────────────────────────────────────────────────

def calculate_system_confidence(session: dict) -> dict:
    """Calculate confidence score for the generated architecture.

    Based on: signal strength, capability coverage, learning data.
    """
    caps = session.get("selected_capabilities", [])
    problem = session.get("problem_analysis", {})
    weights = get_adaptive_weights()
    total_signals = weights.get("total_signals", 0)

    # Base confidence from capability count (sweet spot: 5-10)
    cap_count = len(caps)
    if 5 <= cap_count <= 10:
        count_score = 0.9
    elif 3 <= cap_count <= 12:
        count_score = 0.7
    else:
        count_score = 0.5

    # Problem clarity (how dominant is the primary problem?)
    primary_weight = problem.get("primary_weight", 0.3)
    clarity_score = min(primary_weight * 1.5, 1.0)

    # Learning confidence (more data = more confident)
    learning_score = min(total_signals / 50, 1.0) if total_signals > 0 else 0.5

    overall = round((count_score * 0.3 + clarity_score * 0.4 + learning_score * 0.3) * 100)

    return {
        "score": min(overall, 98),  # Never show 100%
        "based_on": max(total_signals, 1),
        "factors": {
            "architecture_fit": int(count_score * 100),
            "problem_clarity": int(clarity_score * 100),
            "learning_data": int(learning_score * 100),
        },
    }


# ── Persistence Helpers ─────────────────────────────────────────────

def _load_outcomes() -> list[dict]:
    if not _OUTCOMES_DB_PATH.exists():
        return []
    with open(_OUTCOMES_DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_outcomes(outcomes: list[dict]):
    ADVISORY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(ADVISORY_OUTPUT_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(outcomes, f, indent=2, ensure_ascii=False)
        os.replace(tmp, str(_OUTCOMES_DB_PATH))
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)


def _save_adaptive_weights(weights: dict):
    ADVISORY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(ADVISORY_OUTPUT_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(weights, f, indent=2, ensure_ascii=False)
        os.replace(tmp, str(_ADAPTIVE_WEIGHTS_PATH))
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)


def _default_weights() -> dict:
    return {
        "capability_adjustments": {},
        "domain_adjustments": {},
        "entity_adjustments": {},
        "total_signals": 0,
        "last_updated": None,
    }


def _clamp(value: float) -> float:
    """Clamp weight adjustment to safety bounds."""
    return max(-MAX_WEIGHT_CHANGE, min(MAX_WEIGHT_CHANGE, value))
