"""Bridge committed runtime-agent declarations -> agent_registry governance.

`config/tbi_runtime_agents.json` is the committed, reviewable source of truth for
governed RUNTIME AI agents (code under /execution that acts autonomously in prod —
the My Day @CB auto-responder and auto-pickup worker).

At deploy, `upsert_runtime_agents()` converges the agent_registry to that
declaration using a STABLE id per agent (so it is idempotent — see
`agent_registry.upsert_agent`). This is a deploy/runtime step, not something run
during a build (CLAUDE.md: Claude is not the runtime executor of business logic).

The TBI CI gate (`scripts/tbi_compliance_check.py`) reads the same declaration to
decide which runtime entrypoints require a `<entrypoint>.tbi.json` attestation.
"""

from __future__ import annotations

import json
import logging

from config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

DECLARATION_PATH = PROJECT_ROOT / "config" / "tbi_runtime_agents.json"

# Stable agent_id prefix. Must start with "agent_" so agent_registry.list_agents
# (which globs agent_*.json) picks these up.
_ID_PREFIX = "agent_runtime_"


def load_declarations(path=None) -> list[dict]:
    """Return the declared runtime agents (the `agents` array). Empty on any error."""
    p = path or DECLARATION_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("runtime_agents: could not read declaration at %s", p, exc_info=True)
        return []
    agents = raw.get("agents")
    return agents if isinstance(agents, list) else []


def registry_id(declared_id: str) -> str:
    """Map a declaration id to its stable agent_registry id."""
    return f"{_ID_PREFIX}{declared_id}"


def upsert_runtime_agents(*, declarations=None, created_by="runtime_agents.bootstrap") -> list:
    """Register/update every declared runtime agent in agent_registry. Idempotent.

    Returns the list of resulting Agent objects. Run at deploy.
    """
    from execution.ops_platform import agent_registry  # local import: keeps CI light

    decls = declarations if declarations is not None else load_declarations()
    out = []
    for d in decls:
        did = d.get("id")
        if not did:
            continue
        agent = agent_registry.upsert_agent(
            agent_id=registry_id(did),
            name=d.get("name") or did,
            description=d.get("notes") or d.get("name") or did,
            autonomy_policy=d["autonomy_policy"],
            confidence_threshold=float(d.get("confidence_threshold", 0.5)),
            permitted_actions=list(d.get("permitted_actions") or []),
            scope=dict(d.get("scope") or {}),
            rollback_required=bool(d.get("rollback_required", True)),
            created_by=created_by,
        )
        out.append(agent)
    return out
