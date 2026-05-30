"""Access review campaigns — quarterly-style permission audit.

Synthesizes from existing audit data + service_identities + presence:
  - actors who acted in the window
  - stale identities (no activity)
  - orphaned service accounts (active but unused)
  - operators with admin actions

Persisted under ``output/ops_platform/access_reviews/{campaign_id}.json``.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, service_identities

logger = logging.getLogger(__name__)

_REVIEWS_DIR = OUTPUT_DIR / "ops_platform" / "access_reviews"


@dataclass
class AccessReviewCampaign:
    campaign_id: str
    name: str
    lookback_days: int
    generated_at: str
    summary: dict
    actor_review: list
    stale_identities: list
    orphaned_service_accounts: list
    elevated_actions: list

    def to_dict(self) -> dict:
        return asdict(self)


def run_campaign(
    *,
    name: str = "quarterly_access_review",
    lookback_days: int = 90,
    inactivity_days: int = 30,
) -> AccessReviewCampaign:
    rows = audit_log.list_entries(days=lookback_days, limit=20000)
    now = datetime.now(timezone.utc)
    inactivity_cutoff = now - timedelta(days=inactivity_days)

    # Group actors → last activity + action breakdown
    actor_last: dict[str, str] = {}
    actor_actions: dict[str, Counter] = {}
    for r in rows:
        actor_name = (r.get("actor") or {}).get("name", "anonymous")
        ts = r.get("timestamp", "")
        if ts > actor_last.get(actor_name, ""):
            actor_last[actor_name] = ts
        actor_actions.setdefault(actor_name, Counter())[r.get("action", "unknown")] += 1

    actor_review = [
        {"actor": actor_name, "last_activity_at": actor_last[actor_name],
         "action_count": sum(actor_actions[actor_name].values()),
         "top_actions": dict(actor_actions[actor_name].most_common(5))}
        for actor_name in actor_last
    ]
    actor_review.sort(key=lambda r: r["action_count"], reverse=True)

    # Service identities with no activity
    orphaned: list[dict] = []
    for si in service_identities.list_all():
        last = actor_last.get(si.service_id)
        if last is None:
            orphaned.append({"service_id": si.service_id,
                              "display_name": si.display_name,
                              "roles": si.roles,
                              "created_at": si.created_at,
                              "reason": "no audit activity in window"})
            continue
        try:
            if datetime.fromisoformat(last) < inactivity_cutoff:
                orphaned.append({"service_id": si.service_id,
                                  "display_name": si.display_name,
                                  "roles": si.roles,
                                  "last_activity_at": last,
                                  "reason": f"no activity in {inactivity_days}d"})
        except ValueError:
            continue

    # Stale identities (any actor inactive)
    stale = [r for r in actor_review
              if datetime.fromisoformat(r["last_activity_at"]) < inactivity_cutoff]

    # Elevated actions — anything that mutated capability_version / controls / policies
    elevated_kinds = {"capability_version.promoted", "capability_version.deprecated",
                        "rollback.executed", "controls.frozen", "controls.unfrozen",
                        "controls.quarantined", "controls.rollback",
                        "policy.upserted", "policy.deleted",
                        "service_identity.created", "service_identity.revoked",
                        "agent.registered", "agent.paused", "agent.resumed"}
    elevated = []
    for r in rows:
        if r.get("action") in elevated_kinds:
            elevated.append({"timestamp": r.get("timestamp"),
                              "actor": (r.get("actor") or {}).get("name", "anonymous"),
                              "action": r.get("action"),
                              "entity_type": r.get("entity_type"),
                              "entity_id": r.get("entity_id")})

    summary = {
        "total_actors": len(actor_last),
        "stale_actor_count": len(stale),
        "orphaned_service_account_count": len(orphaned),
        "elevated_action_count": len(elevated),
        "lookback_days": lookback_days,
    }

    campaign = AccessReviewCampaign(
        campaign_id=f"AR-{uuid.uuid4().hex[:10].upper()}",
        name=name, lookback_days=lookback_days,
        generated_at=now.isoformat(), summary=summary,
        actor_review=actor_review,
        stale_identities=stale,
        orphaned_service_accounts=orphaned,
        elevated_actions=elevated,
    )

    _REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    (_REVIEWS_DIR / f"{campaign.campaign_id}.json").write_text(
        json.dumps(campaign.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    audit_log.record(
        action="access_review.generated", entity_type="access_review",
        entity_id=campaign.campaign_id,
        actor={"name": "access_review_engine", "system": True},
        new_state=summary,
    )
    return campaign


def list_campaigns(*, limit: int = 50) -> list[AccessReviewCampaign]:
    if not _REVIEWS_DIR.exists():
        return []
    out: list[AccessReviewCampaign] = []
    for p in sorted(_REVIEWS_DIR.glob("AR-*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(AccessReviewCampaign(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return out


def get_campaign(campaign_id: str) -> AccessReviewCampaign | None:
    path = _REVIEWS_DIR / f"{campaign_id}.json"
    if not path.exists():
        return None
    try:
        return AccessReviewCampaign(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
