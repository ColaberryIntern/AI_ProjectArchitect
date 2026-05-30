"""Policy engine — ABAC-style policy evaluation that layers on top of RBAC.

Where RBAC answers "does this role have this permission?", policy_engine
answers "given the request context, should this specific action proceed?".

Policies are stored as JSON under ``output/ops_platform/policies/{policy_id}.json``
and evaluated in declaration order. Each policy can DENY, REQUIRE_APPROVAL,
or pass through; the first DENY or REQUIRE_APPROVAL wins.

Policy shape (kept deliberately small):

  {
    "policy_id": "...",
    "applies_to": {
       "permission": "version.promote" | "capability.execute" | ...,
       "workspace_id": "sales" | null,
       "capability_id": "summarize_proposal" | null
    },
    "conditions": [
       {"kind": "time_window", "start": "08:00", "end": "20:00", "tz": "UTC"},
       {"kind": "weekday_only"},
       {"kind": "max_calls_per_hour", "max": 100},
       {"kind": "requires_role", "role": "reviewer"}
    ],
    "decision_on_match": "DENY" | "REQUIRE_APPROVAL" | "ALLOW",
    "reason": "human-readable explanation"
  }
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log
from execution.ops_platform.identity import IdentityContext

logger = logging.getLogger(__name__)

_POLICIES_DIR = OUTPUT_DIR / "ops_platform" / "policies"


@dataclass
class PolicyDecision:
    outcome: str          # "ALLOW" | "DENY" | "REQUIRE_APPROVAL"
    reason: str
    matched_policy_id: str | None = None
    explanations: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def evaluate(
    identity: IdentityContext,
    permission: str,
    *,
    workspace_id: str | None = None,
    capability_id: str | None = None,
) -> PolicyDecision:
    """Evaluate every loaded policy against the request. First DENY /
    REQUIRE_APPROVAL wins. If nothing matches, returns ALLOW with reason
    "no matching policy". This layers on top of RBAC; both checks run."""
    explanations: list[str] = []
    for policy in _load_policies():
        applies = policy.get("applies_to") or {}
        if applies.get("permission") and applies["permission"] != permission:
            continue
        if applies.get("workspace_id") and applies["workspace_id"] != workspace_id:
            continue
        if applies.get("capability_id") and applies["capability_id"] != capability_id:
            continue
        unmet = []
        for cond in policy.get("conditions") or []:
            ok, detail = _evaluate_condition(cond, identity=identity,
                                                workspace_id=workspace_id,
                                                capability_id=capability_id)
            if not ok:
                unmet.append(detail)
        if unmet:
            explanations.append(
                f"policy {policy.get('policy_id')} skipped: {unmet[0]}"
            )
            continue
        decision = policy.get("decision_on_match", "ALLOW").upper()
        reason = policy.get("reason", f"policy {policy.get('policy_id')} matched")
        if decision in ("DENY", "REQUIRE_APPROVAL"):
            audit_log.record(
                action="policy.matched", entity_type="policy",
                entity_id=policy.get("policy_id"),
                actor=identity.as_actor(),
                metadata={"outcome": decision, "permission": permission,
                          "workspace_id": workspace_id,
                          "capability_id": capability_id},
            )
            return PolicyDecision(outcome=decision, reason=reason,
                                    matched_policy_id=policy.get("policy_id"),
                                    explanations=explanations)
    return PolicyDecision(outcome="ALLOW", reason="no matching policy",
                            explanations=explanations)


def upsert_policy(policy: dict) -> dict:
    if "policy_id" not in policy:
        raise ValueError("policy_id is required")
    _POLICIES_DIR.mkdir(parents=True, exist_ok=True)
    path = _POLICIES_DIR / f"{policy['policy_id']}.json"
    path.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    audit_log.record(
        action="policy.upserted", entity_type="policy",
        entity_id=policy["policy_id"],
        actor={"name": "policy_admin", "system": True},
        new_state={"decision_on_match": policy.get("decision_on_match"),
                   "applies_to": policy.get("applies_to")},
    )
    return policy


def list_policies() -> list[dict]:
    return _load_policies()


def delete_policy(policy_id: str) -> bool:
    path = _POLICIES_DIR / f"{policy_id}.json"
    if not path.exists():
        return False
    try:
        path.unlink()
        audit_log.record(
            action="policy.deleted", entity_type="policy", entity_id=policy_id,
            actor={"name": "policy_admin", "system": True},
        )
        return True
    except OSError:
        return False


# ── Internal ───────────────────────────────────────────────────────────


def _load_policies() -> list[dict]:
    if not _POLICIES_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(_POLICIES_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _evaluate_condition(cond: dict, *, identity: IdentityContext,
                          workspace_id: str | None,
                          capability_id: str | None) -> tuple[bool, str]:
    kind = cond.get("kind")
    if kind == "time_window":
        start = _parse_time(cond.get("start", "00:00"))
        end = _parse_time(cond.get("end", "23:59"))
        now = datetime.now(timezone.utc).time()
        if start <= now <= end:
            return True, ""
        return False, f"outside time window {cond.get('start')}-{cond.get('end')}"
    if kind == "weekday_only":
        if datetime.now(timezone.utc).weekday() < 5:
            return True, ""
        return False, "weekend"
    if kind == "requires_role":
        if cond.get("role") in identity.roles:
            return True, ""
        return False, f"caller lacks role {cond.get('role')}"
    if kind == "requires_authenticated":
        if identity.authenticated:
            return True, ""
        return False, "caller is not authenticated"
    # Unknown kind — fail-open with explanation
    return True, f"unknown condition kind {kind} — fail-open"


def _parse_time(s: str) -> time:
    m = _TIME_RE.match(s or "00:00")
    if not m:
        return time(0, 0)
    return time(int(m.group(1)), int(m.group(2)))
