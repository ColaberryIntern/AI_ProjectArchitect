"""Runtime version routing — decides which capability_version handles a
runtime call.

Routing rules
-------------
- ``approved`` version absorbs (100 − Σ experimental.rollout_percentage)% of traffic.
- Each ``experimental`` version consumes its declared ``rollout_percentage``.
- Deterministic bucketing: ``hash(session_id + capability_id) % 100`` so a
  given session always lands on the same version for the same capability —
  reproducible post-hoc.
- If no version exists at all, the runtime falls through to the live
  registry capability (the Phase 1-4 default).

Every routing decision is audit-logged with ``routing.selected`` and the
metadata necessary to replay the choice.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import asdict, dataclass

from execution.ops_platform import audit_log, capability_versions

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    capability_id: str
    selected_version_id: str | None
    selected_semver: str | None
    rollout_source: str       # "approved" | "experimental" | "fallback"
    routing_reason: str
    bucket: int               # 0..99
    correlation_id: str

    def to_dict(self) -> dict:
        return asdict(self)


def route(
    capability_id: str,
    *,
    session_id: str = "",
    correlation_id: str | None = None,
    record_audit: bool = True,
) -> RoutingDecision:
    """Resolve the version that should handle a runtime call. Always
    returns a RoutingDecision; selected_version_id may be None to indicate
    "fall through to the live registry capability"."""
    versions = capability_versions.list_versions(capability_id)
    routable = [v for v in versions if v.status in ("approved", "experimental")]
    bucket = _deterministic_bucket(session_id, capability_id)
    cid = correlation_id or str(uuid.uuid4())

    if not routable:
        decision = RoutingDecision(
            capability_id=capability_id,
            selected_version_id=None,
            selected_semver=None,
            rollout_source="fallback",
            routing_reason="no approved or experimental versions registered",
            bucket=bucket,
            correlation_id=cid,
        )
        if record_audit:
            _emit(decision, capability_id)
        return decision

    # Order experimentals deterministically by semver so distribution is stable
    experimentals = sorted(
        [v for v in routable if v.status == "experimental"],
        key=lambda v: _semver_tuple(v.semver),
    )
    cumulative = 0.0
    for v in experimentals:
        cumulative += float(v.rollout_percentage or 0)
        if bucket < cumulative:
            decision = RoutingDecision(
                capability_id=capability_id,
                selected_version_id=v.version_id,
                selected_semver=v.semver,
                rollout_source="experimental",
                routing_reason=(
                    f"bucket {bucket} < cumulative experimental rollout "
                    f"{cumulative:.1f}% — experimental version {v.semver}"
                ),
                bucket=bucket,
                correlation_id=cid,
            )
            if record_audit:
                _emit(decision, capability_id)
            return decision

    approved = next((v for v in routable if v.status == "approved"), None)
    if approved is None:
        # All routable versions are experimental but none caught the bucket.
        # Fall back to the highest-rollout experimental as best effort.
        chosen = experimentals[-1] if experimentals else routable[0]
        decision = RoutingDecision(
            capability_id=capability_id,
            selected_version_id=chosen.version_id,
            selected_semver=chosen.semver,
            rollout_source="experimental",
            routing_reason="no approved version; fell back to highest experimental",
            bucket=bucket,
            correlation_id=cid,
        )
    else:
        decision = RoutingDecision(
            capability_id=capability_id,
            selected_version_id=approved.version_id,
            selected_semver=approved.semver,
            rollout_source="approved",
            routing_reason=(
                f"bucket {bucket} above experimental cumulative "
                f"{cumulative:.1f}% — approved version {approved.semver}"
            ),
            bucket=bucket,
            correlation_id=cid,
        )
    if record_audit:
        _emit(decision, capability_id)
    return decision


def simulate(
    capability_id: str,
    *,
    samples: int = 1000,
    seed_prefix: str = "sim",
) -> dict:
    """Run N synthetic routes and report the empirical distribution.
    Useful for previewing a rollout_percentage change before promoting."""
    counts: dict[str, int] = {}
    for i in range(samples):
        d = route(capability_id, session_id=f"{seed_prefix}-{i}", record_audit=False)
        key = (d.selected_semver or "fallback") + f"|{d.rollout_source}"
        counts[key] = counts.get(key, 0) + 1
    distribution = {k: round(v / samples * 100, 2) for k, v in counts.items()}
    audit_log.record(
        action="routing.simulated", entity_type="capability", entity_id=capability_id,
        actor={"name": "runtime_router", "system": True},
        metadata={"samples": samples, "distribution": distribution},
    )
    return {"capability_id": capability_id, "samples": samples,
            "distribution": distribution}


# ── Internal ───────────────────────────────────────────────────────────


def _emit(decision: RoutingDecision, capability_id: str) -> None:
    audit_log.record(
        action="routing.selected", entity_type="capability",
        entity_id=capability_id,
        actor={"name": "runtime_router", "system": True},
        metadata={
            "selected_version_id": decision.selected_version_id,
            "selected_semver": decision.selected_semver,
            "rollout_source": decision.rollout_source,
            "bucket": decision.bucket,
            "routing_reason": decision.routing_reason,
        },
        correlation_id=decision.correlation_id,
    )


def _deterministic_bucket(session_id: str, capability_id: str) -> int:
    seed = f"{session_id or 'anonymous'}:{capability_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(seed).digest()[:4], "big") % 100


def _semver_tuple(s: str) -> tuple:
    try:
        return tuple(int(p) for p in s.split("."))
    except ValueError:
        return (0, 0, 0)
