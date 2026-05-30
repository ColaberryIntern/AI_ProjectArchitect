"""Compliance reports — SOC2-style operational reports synthesized from
existing audit + approval + change-request data. JSON / CSV / Markdown
output. PDF intentionally NOT bundled (use any HTML/MD-to-PDF tool the
operator already runs).

No new persistence beyond the rendered report file.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    approvals, audit_log, change_requests, controls, incidents,
)

_REPORTS_DIR = OUTPUT_DIR / "ops_platform" / "compliance"


def operational_report(*, days: int = 30, format: str = "json") -> str | dict:
    """Return a compliance summary in the requested format."""
    payload = _build_report(days=days)
    if format == "json":
        return payload
    if format == "csv":
        return _to_csv(payload)
    if format == "markdown":
        return _to_markdown(payload, days=days)
    raise ValueError(f"unsupported format {format}")


def export_to_file(*, days: int = 30, format: str = "json",
                     filename: str | None = None) -> Path:
    rendered = operational_report(days=days, format=format)
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    extension = {"json": "json", "csv": "csv", "markdown": "md"}[format]
    path = _REPORTS_DIR / (filename or f"operational_report_{stamp}.{extension}")
    if format == "json":
        path.write_text(json.dumps(rendered, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    else:
        path.write_text(rendered, encoding="utf-8")
    return path


def access_review(*, days: int = 30) -> dict:
    """Synthesize an access-review report: who acted, how often, on what."""
    rows = audit_log.list_entries(days=days, limit=10000)
    by_actor: dict[str, dict] = {}
    for r in rows:
        actor = (r.get("actor") or {}).get("name", "anonymous")
        bucket = by_actor.setdefault(actor, {"action_count": 0, "actions": Counter(),
                                                "entity_types": Counter(),
                                                "first_seen": r.get("timestamp"),
                                                "last_seen": r.get("timestamp")})
        bucket["action_count"] += 1
        bucket["actions"][r.get("action", "unknown")] += 1
        bucket["entity_types"][r.get("entity_type", "unknown")] += 1
        ts = r.get("timestamp", "")
        if ts < bucket["first_seen"]:
            bucket["first_seen"] = ts
        if ts > bucket["last_seen"]:
            bucket["last_seen"] = ts
    for v in by_actor.values():
        v["actions"] = dict(v["actions"].most_common())
        v["entity_types"] = dict(v["entity_types"].most_common())
    return {"lookback_days": days, "actors": by_actor,
            "generated_at": datetime.now(timezone.utc).isoformat()}


def approval_timeline(*, days: int = 30) -> list[dict]:
    """All approval activity in the lookback, ordered oldest-first."""
    requests = approvals.list_requests()
    rows: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for req in requests:
        try:
            created = datetime.fromisoformat(req.created_at)
        except ValueError:
            continue
        if created < cutoff:
            continue
        rows.append({
            "request_id": req.request_id,
            "action": req.action,
            "entity_type": req.entity_type,
            "entity_id": req.entity_id,
            "state": req.state,
            "created_at": req.created_at,
            "final_decision_at": req.final_decision_at,
            "stages": [{"name": s.get("stage_name"),
                          "decision_count": len(s.get("decisions") or [])}
                         for s in req.stages],
        })
    rows.sort(key=lambda r: r["created_at"])
    return rows


def routing_decision_report(*, days: int = 7) -> dict:
    """All routing.selected rows, grouped by capability_id."""
    rows = audit_log.list_entries(action="routing.selected", days=days, limit=10000)
    by_cap: dict[str, Counter] = {}
    for r in rows:
        cap = r.get("entity_id", "unknown")
        meta = r.get("metadata") or {}
        semver = meta.get("selected_semver") or "fallback"
        by_cap.setdefault(cap, Counter())[semver] += 1
    return {"lookback_days": days,
            "by_capability": {cap: dict(counter.most_common())
                                 for cap, counter in by_cap.items()}}


def audit_replay_export(*, correlation_id: str) -> list[dict]:
    """Replay every event under one correlation_id — for incident postmortems."""
    return audit_log.replay(correlation_id)


# ── Internal ───────────────────────────────────────────────────────────


def _build_report(*, days: int) -> dict:
    audit_summary = audit_log.stats(days=days)
    cr_rows = change_requests.list_change_requests()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent_crs = [c.to_dict() for c in cr_rows
                    if datetime.fromisoformat(c.created_at) >= cutoff]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": days,
        "audit_summary": audit_summary,
        "open_incidents": [i.to_dict() for i in incidents.list_incidents(state="open")],
        "active_controls": [c.to_dict() for c in controls.list_active()],
        "approval_timeline": approval_timeline(days=days),
        "change_requests_in_window": recent_crs,
        "routing_decisions": routing_decision_report(days=min(7, days)),
        "access_review": access_review(days=days),
    }


def _to_csv(payload: dict) -> str:
    """Render the access_review section as CSV — the most spreadsheet-friendly part."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["actor", "action_count", "first_seen", "last_seen", "top_actions"])
    for actor, info in (payload.get("access_review", {}).get("actors") or {}).items():
        top = ", ".join(f"{a}:{n}" for a, n in list(info.get("actions", {}).items())[:5])
        w.writerow([actor, info["action_count"], info["first_seen"], info["last_seen"], top])
    return buf.getvalue()


def _to_markdown(payload: dict, *, days: int) -> str:
    lines = [
        f"# Operational Compliance Report ({days}d)",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Audit summary",
        f"- Total events: {payload['audit_summary'].get('total', 0)}",
        f"- Distinct actions: {len(payload['audit_summary'].get('by_action', {}))}",
        f"- Distinct actors: {len(payload['audit_summary'].get('by_actor', {}))}",
        "",
        f"## Open incidents: {len(payload['open_incidents'])}",
    ]
    for inc in payload["open_incidents"][:10]:
        lines.append(f"- **{inc['incident_id']}** — {inc['title']} (severity {inc['severity']})")
    lines.extend(["", f"## Active controls: {len(payload['active_controls'])}"])
    for c in payload["active_controls"][:10]:
        lines.append(f"- {c['kind']} on {c['target_type']}:{c['target_id']} (since {c['activated_at']})")
    lines.extend(["", "## Approval timeline (most recent)"])
    for r in payload["approval_timeline"][-10:]:
        lines.append(f"- {r['created_at']} — {r['action']} on {r['entity_type']}:{r['entity_id']} → {r['state']}")
    lines.extend(["", "## Change requests in window"])
    for cr in payload["change_requests_in_window"][:10]:
        lines.append(f"- **{cr['cr_id']}** — {cr['title']} → {cr['state']}")
    lines.extend(["", "## Access review (top 15)"])
    actors = list((payload["access_review"].get("actors") or {}).items())[:15]
    for actor, info in actors:
        lines.append(f"- **{actor}**: {info['action_count']} actions ({info['first_seen']} → {info['last_seen']})")
    return "\n".join(lines)
