"""TBI compliance scorer — decides whether an AI artifact's attestation passes the
Trust Before Intelligence gate.

Deterministic and LLM-free (per CLAUDE.md: "prefer deterministic verification").
Top-level imports are stdlib-only so this module loads in a bare CI runner; the
audit-log and trust-engine couplings are lazy/injected.

Inputs
------
A TBI attestation dict (shape: config/schemas/ops/tbi_attestation.schema.json) and,
optionally, a `trust_profile` (a trust_engine.TrustProfile or its .to_dict()) for
runtime capabilities.

Verdict
-------
- non_compliant  -> any blocking issue (gate FAILS)
- conditional    -> passes, but caveated (justified n_a, or trust profile in caution)
- compliant      -> fully satisfied

Canonical framework: directives/compliance/trust-before-intelligence.md
Gate procedure:      directives/compliance/tbi-compliance-gate.md
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)

# Must match `framework_version` in the vendored snapshot
# (directives/compliance/trust-before-intelligence.md). Bumping this forces every
# existing attestation to be re-validated against the new framework version.
CURRENT_FRAMEWORK_VERSION = "TBI-2025.12.0"

INPACT_DIMENSIONS = ("instant", "natural", "permitted", "adaptive", "contextual", "transparent")
GOALS_TARGETS = ("governance", "observability", "availability", "lexicon", "solid")

_VALID_STATUS = {"satisfied", "n_a", "gap"}
_DO_NOT_DEPLOY = "DO_NOT_DEPLOY"
_CAUTION_RECS = {"REQUIRES_REVIEW", "LIMITED_ROLLOUT"}


@dataclass
class TbiVerdict:
    artifact_id: str
    verdict: str  # compliant | conditional | non_compliant
    blocking_issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    inpact_satisfied: int = 0
    goals_satisfied: int = 0
    framework_version: str = CURRENT_FRAMEWORK_VERSION

    @property
    def passed(self) -> bool:
        return self.verdict != "non_compliant"

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_attestation(
    attestation: dict,
    *,
    trust_profile=None,
    record_audit: bool = False,
) -> TbiVerdict:
    """Score one attestation. Pure: no I/O unless ``record_audit`` is True."""
    if not isinstance(attestation, dict):
        return TbiVerdict(
            artifact_id="<unknown>",
            verdict="non_compliant",
            blocking_issues=["attestation is not a JSON object"],
        )

    artifact_id = attestation.get("artifact_id") or "<unknown>"
    blocking: list[str] = []
    warnings: list[str] = []

    fv = attestation.get("framework_version")
    if fv != CURRENT_FRAMEWORK_VERSION:
        blocking.append(
            f"framework_version {fv!r} != current {CURRENT_FRAMEWORK_VERSION!r} "
            "— artifact must be re-attested against the current snapshot"
        )

    inpact = attestation.get("inpact") or {}
    goals = attestation.get("goals") or {}
    _check_group("inpact", inpact, INPACT_DIMENSIONS, blocking, warnings)
    _check_group("goals", goals, GOALS_TARGETS, blocking, warnings)

    layers = attestation.get("layers")
    if not isinstance(layers, list) or len(layers) == 0:
        blocking.append("layers: at least one of the 7 trust layers must be mapped")

    if trust_profile is not None:
        rec = _deployment_rec(trust_profile)
        if rec == _DO_NOT_DEPLOY:
            ref = attestation.get("trust_score_ref") or artifact_id
            blocking.append(f"trust_engine recommends {rec} for {ref}")
        elif rec in _CAUTION_RECS:
            warnings.append(f"trust_engine recommends {rec} — proceed with caution")

    inpact_sat = sum(1 for k in INPACT_DIMENSIONS if _status(inpact, k) == "satisfied")
    goals_sat = sum(1 for k in GOALS_TARGETS if _status(goals, k) == "satisfied")

    if blocking:
        verdict = "non_compliant"
    elif warnings:
        verdict = "conditional"
    else:
        verdict = "compliant"

    result = TbiVerdict(
        artifact_id=artifact_id,
        verdict=verdict,
        blocking_issues=blocking,
        warnings=warnings,
        inpact_satisfied=inpact_sat,
        goals_satisfied=goals_sat,
        framework_version=CURRENT_FRAMEWORK_VERSION,
    )
    if record_audit:
        _emit_audit(attestation, result)
    return result


# ── Internal ───────────────────────────────────────────────────────────


def _status(group: dict, key: str):
    cell = group.get(key)
    return cell.get("status") if isinstance(cell, dict) else None


def _check_group(group_name: str, group: dict, keys, blocking: list, warnings: list) -> None:
    if not isinstance(group, dict):
        blocking.append(f"{group_name}: missing or not an object")
        return
    for key in keys:
        cell = group.get(key)
        if not isinstance(cell, dict):
            blocking.append(f"{group_name}.{key}: missing")
            continue
        status = cell.get("status")
        if status not in _VALID_STATUS:
            blocking.append(f"{group_name}.{key}: invalid status {status!r}")
        elif status == "gap":
            blocking.append(f"{group_name}.{key}: status 'gap' is unresolved")
        elif status == "n_a":
            if not str(cell.get("evidence") or "").strip():
                blocking.append(f"{group_name}.{key}: 'n_a' without written justification")
            else:
                warnings.append(f"{group_name}.{key}: not applicable (justified)")


def _deployment_rec(trust_profile):
    if hasattr(trust_profile, "deployment_recommendation"):
        return getattr(trust_profile, "deployment_recommendation")
    if isinstance(trust_profile, dict):
        return trust_profile.get("deployment_recommendation")
    return None


def _emit_audit(attestation: dict, result: TbiVerdict) -> None:
    """Best-effort audit trail. Never raises out — failing to log must not fail a build."""
    try:
        from execution.ops_platform import audit_log  # lazy: keeps CI imports light
        audit_log.record(
            action="tbi.evaluated",
            entity_type=attestation.get("artifact_kind") or "ai_artifact",
            entity_id=result.artifact_id,
            actor={"name": "tbi_compliance", "system": True},
            metadata={
                "verdict": result.verdict,
                "framework_version": result.framework_version,
                "blocking_issues": result.blocking_issues[:5],
                "warnings": result.warnings[:5],
            },
        )
    except Exception:  # pragma: no cover - logging side-channel
        logger.warning("tbi.evaluated audit emit failed", exc_info=True)
