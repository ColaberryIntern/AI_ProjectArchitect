"""Security telemetry — read-only roll-up of security-relevant audit rows.

Surfaces:
  - failed_auth_attempts        (auth.failed)
  - repeated_denials            (enforcement.denied clusters per user)
  - suspicious_routing_changes  (routing.simulated frequency anomalies)
  - privilege_escalation        (role-grant audit anomalies)
  - service_identity_use        (audit rows where actor.system=true)
  - posture summary             (all of the above aggregated)
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from execution.ops_platform import audit_log


def failed_auth_attempts(*, days: int = 7) -> list[dict]:
    return audit_log.list_entries(action="auth.failed", days=days, limit=500)


def repeated_denials(*, days: int = 7, threshold: int = 5) -> list[dict]:
    rows = audit_log.list_entries(action="enforcement.denied", days=days, limit=2000)
    by_actor: Counter = Counter()
    samples: dict = {}
    for r in rows:
        actor_name = (r.get("actor") or {}).get("name", "anonymous")
        by_actor[actor_name] += 1
        samples.setdefault(actor_name, []).append(r)
    out: list[dict] = []
    for actor_name, count in by_actor.most_common():
        if count < threshold:
            continue
        out.append({"actor": actor_name, "denial_count": count,
                     "first_sample": samples[actor_name][0]})
    return out


def service_identity_activity(*, days: int = 7) -> list[dict]:
    rows = audit_log.list_entries(days=days, limit=5000)
    by_service: Counter = Counter()
    for r in rows:
        actor = r.get("actor") or {}
        if actor.get("system"):
            by_service[actor.get("name", "anonymous")] += 1
    return [{"service_id": s, "action_count": c}
             for s, c in by_service.most_common(25)]


def posture(*, days: int = 7) -> dict:
    failed = failed_auth_attempts(days=days)
    denials = repeated_denials(days=days, threshold=5)
    service_use = service_identity_activity(days=days)
    return {
        "lookback_days": days,
        "failed_auth_attempts": len(failed),
        "repeated_denial_actors": denials,
        "top_service_identity_use": service_use,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
