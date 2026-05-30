"""Incident model — formal record of operational degradation events.

An Incident bundles related findings + autonomous actions + manual remediation
into one record with a timeline. Audit rows tagged with the incident's
correlation_id let the platform replay everything that happened.

States:  open → mitigating → resolved → postmortem_drafted
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)

_INCIDENTS_DIR = OUTPUT_DIR / "ops_platform" / "incidents"


@dataclass
class TimelineEntry:
    at: str
    actor: dict
    note: str
    related_audit_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Incident:
    incident_id: str
    correlation_id: str
    title: str
    severity: int                  # 1..5
    state: str                     # open | mitigating | resolved | postmortem_drafted
    detector: str                  # which detector / human raised it
    impacted_capabilities: list = field(default_factory=list)
    impacted_workspaces: list = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    timeline: list = field(default_factory=list)
    remediation_steps: list = field(default_factory=list)
    postmortem: str = ""
    revision_id: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timeline"] = [t if isinstance(t, dict) else t.to_dict() for t in d["timeline"]]
        return d


# ── Public API ─────────────────────────────────────────────────────────


def open_incident(
    *,
    title: str,
    severity: int,
    detector: str,
    impacted_capabilities: list | None = None,
    impacted_workspaces: list | None = None,
    initial_note: str = "",
    actor: dict | str = "system",
) -> Incident:
    _INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    incident = Incident(
        incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
        correlation_id=str(uuid.uuid4()),
        title=title, severity=severity,
        state="open", detector=detector,
        impacted_capabilities=list(impacted_capabilities or []),
        impacted_workspaces=list(impacted_workspaces or []),
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    _append_timeline(incident, actor=actor, note=initial_note or "incident opened")
    _persist(incident)
    audit_log.record(
        action="incident.opened", entity_type="incident",
        entity_id=incident.incident_id, actor=_normalize_actor(actor),
        correlation_id=incident.correlation_id,
        new_state={"title": title, "severity": severity, "detector": detector},
    )
    return incident


def add_timeline_entry(incident_id: str, *, note: str,
                        actor: dict | str = "anonymous",
                        related_audit_id: str | None = None) -> Incident | None:
    incident = get(incident_id)
    if incident is None:
        return None
    _append_timeline(incident, actor=actor, note=note,
                       related_audit_id=related_audit_id)
    _persist(incident)
    return incident


def transition(incident_id: str, *, to_state: str,
                 actor: dict | str = "anonymous",
                 note: str = "") -> Incident | None:
    if to_state not in ("open", "mitigating", "resolved", "postmortem_drafted"):
        raise ValueError(f"invalid state {to_state}")
    incident = get(incident_id)
    if incident is None:
        return None
    previous = incident.state
    incident.state = to_state
    incident.updated_at = datetime.now(timezone.utc).isoformat()
    _append_timeline(incident, actor=actor,
                       note=note or f"transition {previous} → {to_state}")
    _persist(incident)
    audit_log.record(
        action="incident.transitioned", entity_type="incident",
        entity_id=incident_id, actor=_normalize_actor(actor),
        correlation_id=incident.correlation_id,
        previous_state={"state": previous}, new_state={"state": to_state},
    )
    return incident


def draft_postmortem(incident_id: str, *, actor: dict | str = "system") -> Incident | None:
    """Generate a markdown draft from the timeline + audit replay."""
    incident = get(incident_id)
    if incident is None:
        return None
    related_audit = audit_log.list_entries(correlation_id=incident.correlation_id, limit=500)
    lines = [
        f"# {incident.incident_id}: {incident.title}",
        "",
        f"- Severity: {incident.severity}/5",
        f"- Detector: {incident.detector}",
        f"- Opened: {incident.created_at}",
        f"- Impacted capabilities: {', '.join(incident.impacted_capabilities) or 'none'}",
        f"- Impacted workspaces: {', '.join(incident.impacted_workspaces) or 'none'}",
        "",
        "## Timeline",
    ]
    for entry in incident.timeline:
        if isinstance(entry, dict):
            lines.append(f"- {entry.get('at')} — {entry.get('actor', {}).get('name', '')}: {entry.get('note', '')}")
    lines.extend(["", "## Related Audit Events", ""])
    for row in related_audit[:50]:
        lines.append(f"- {row.get('timestamp')} — {row.get('action')} on {row.get('entity_type')}:{row.get('entity_id')}")
    incident.postmortem = "\n".join(lines)
    incident.state = "postmortem_drafted"
    incident.updated_at = datetime.now(timezone.utc).isoformat()
    _append_timeline(incident, actor=actor, note="postmortem drafted")
    _persist(incident)
    return incident


def get(incident_id: str) -> Incident | None:
    path = _INCIDENTS_DIR / f"{incident_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Incident(**data)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_incidents(*, state: str | None = None) -> list[Incident]:
    if not _INCIDENTS_DIR.exists():
        return []
    out: list[Incident] = []
    for p in _INCIDENTS_DIR.glob("INC-*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(Incident(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    if state:
        out = [i for i in out if i.state == state]
    out.sort(key=lambda i: i.created_at, reverse=True)
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _append_timeline(incident: Incident, *, actor, note: str,
                       related_audit_id: str | None = None) -> None:
    incident.timeline.append(TimelineEntry(
        at=datetime.now(timezone.utc).isoformat(),
        actor=_normalize_actor(actor),
        note=note, related_audit_id=related_audit_id,
    ).to_dict())


def _normalize_actor(actor) -> dict:
    if isinstance(actor, dict):
        out = dict(actor); out.setdefault("name", "anonymous"); return out
    return {"name": str(actor)}


def _persist(incident: Incident) -> None:
    from execution.ops_platform import optimistic_concurrency
    incident.revision_id = optimistic_concurrency.new_revision()
    _INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    (_INCIDENTS_DIR / f"{incident.incident_id}.json").write_text(
        json.dumps(incident.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def save_with_revision_check(incident: Incident, *,
                                observed_revision: str | None,
                                actor: dict | str | None = None) -> Incident:
    from execution.ops_platform import optimistic_concurrency
    current = get(incident.incident_id)
    optimistic_concurrency.compare(
        entity_type="incident", entity_id=incident.incident_id,
        observed_revision=observed_revision,
        current_revision=current.revision_id if current else None,
        actor=actor,
    )
    _persist(incident)
    return incident
