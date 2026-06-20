"""Runtime control store — the operator's real kill-switch over live AI autonomy.

The live My Day workers (cb_mention_worker, autopickup_worker, productivity) and
the advisory pipeline do NOT execute through agent_runtime, so agent_registry.pause()
would not stop them. This file-backed store is what they actually consult at
CALL-TIME, so a pause from the Trust Command Center takes effect on the next worker
tick — no redeploy, no env change.

Store: output/ops_platform/runtime_controls.json
  { "global_paused": bool, "agents": { "<id>": {paused, by, reason, at} } }

Default (missing file) = nothing paused = today's behavior. Read path (is_paused)
is import-light and never raises; mutations audit via audit_log (lazy import).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from config.settings import OUTPUT_DIR

logger = logging.getLogger(__name__)

_STORE_PATH = OUTPUT_DIR / "ops_platform" / "runtime_controls.json"

# The governed runtime agents (mirror of config/tbi_runtime_agents.json ids).
KNOWN_RUNTIME_AGENTS = (
    "cb_mention_responder",
    "autopickup_worker",
    "advisory_pipeline",
    "productivity_report",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    try:
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("global_paused", False)
            data.setdefault("agents", {})
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"global_paused": False, "agents": {}}


def _save(state: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Hot read path (used by the workers) ──


def is_paused(agent_id: str) -> bool:
    """True if all autonomy is paused globally, or this agent is paused.
    Never raises — a broken/missing store means 'not paused' (today's behavior)."""
    try:
        state = _load()
        if state.get("global_paused"):
            return True
        return bool((state.get("agents", {}).get(agent_id) or {}).get("paused"))
    except Exception:  # pragma: no cover - defensive
        return False


# ── Mutations (used by the Trust Command Center, audited) ──


def set_agent_paused(agent_id: str, paused: bool, *, actor, reason: str = "") -> dict:
    state = _load()
    state.setdefault("agents", {})[agent_id] = {
        "paused": bool(paused),
        "by": _actor_name(actor),
        "reason": reason,
        "at": _now(),
    }
    _save(state)
    _audit("runtime_agent.paused" if paused else "runtime_agent.resumed",
           entity_id=agent_id, actor=actor, reason=reason)
    return get_state()


def set_global_paused(paused: bool, *, actor, reason: str = "") -> dict:
    state = _load()
    state["global_paused"] = bool(paused)
    state["global_meta"] = {"by": _actor_name(actor), "reason": reason, "at": _now()}
    _save(state)
    _audit("runtime.global_paused" if paused else "runtime.global_resumed",
           entity_id="global", actor=actor, reason=reason)
    return get_state()


def get_state() -> dict:
    """Full state for the dashboard: global flag + every known agent's pause state."""
    state = _load()
    agents = dict(state.get("agents", {}))
    out_agents = {}
    for aid in KNOWN_RUNTIME_AGENTS:
        cell = agents.get(aid) or {}
        out_agents[aid] = {
            "paused": bool(cell.get("paused", False)),
            "by": cell.get("by"),
            "reason": cell.get("reason"),
            "at": cell.get("at"),
        }
    # Include any extra declared agents not in the known list
    for aid, cell in agents.items():
        if aid not in out_agents:
            out_agents[aid] = {"paused": bool(cell.get("paused", False)),
                               "by": cell.get("by"), "reason": cell.get("reason"),
                               "at": cell.get("at")}
    return {
        "global_paused": bool(state.get("global_paused", False)),
        "global_meta": state.get("global_meta"),
        "agents": out_agents,
    }


# ── Internal ──


def _actor_name(actor) -> str:
    if isinstance(actor, dict):
        return actor.get("name") or actor.get("email") or "anonymous"
    return str(actor) if actor else "anonymous"


def _audit(action: str, *, entity_id: str, actor, reason: str) -> None:
    try:
        from execution.ops_platform import audit_log
        audit_log.record(
            action=action, entity_type="runtime_control", entity_id=entity_id,
            actor=actor if isinstance(actor, dict) else {"name": _actor_name(actor)},
            metadata={"reason": reason},
        )
    except Exception:  # pragma: no cover
        logger.warning("runtime_controls audit emit failed for %s", action, exc_info=True)
