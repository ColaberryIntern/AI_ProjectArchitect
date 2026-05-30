"""Alert engine — threshold + anomaly + correlation alerts with dedup,
suppression, and escalation chains.

State machine:  open → acknowledged → resolved
                      → suppressed

Persistence
-----------
``output/ops_platform/alerts/rules/{rule_id}.json``     — rule definitions
``output/ops_platform/alerts/active/{alert_id}.json``   — open/ack alerts
``output/ops_platform/alerts/history/{date}.jsonl``     — append-only history

Notifications are dispatched via the ``notifications`` module's adapters;
this module only generates + tracks alerts.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, realtime_bus

logger = logging.getLogger(__name__)

_ALERTS_DIR = OUTPUT_DIR / "ops_platform" / "alerts"
_RULES_DIR = _ALERTS_DIR / "rules"
_ACTIVE_DIR = _ALERTS_DIR / "active"
_HISTORY_DIR = _ALERTS_DIR / "history"


@dataclass
class AlertRule:
    rule_id: str
    name: str
    metric: str                       # e.g. "queue.depth", "incidents.open_count"
    operator: str                     # > | >= | < | <= | ==
    threshold: float
    severity: int                     # 1..5
    description: str = ""
    suppress_for_seconds: int = 300
    escalation_after_seconds: int = 600
    notify_channels: list = field(default_factory=list)   # list of notification channel ids
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Alert:
    alert_id: str
    rule_id: str
    metric: str
    observed_value: float
    threshold: float
    severity: int
    state: str                        # open | acknowledged | resolved | suppressed
    opened_at: str
    correlation_id: str
    acknowledged_at: str | None = None
    resolved_at: str | None = None
    last_notified_at: str | None = None
    notification_count: int = 0
    suppression_expires_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Rule CRUD ──────────────────────────────────────────────────────────


def upsert_rule(*, rule_id: str, name: str, metric: str, operator: str,
                  threshold: float, severity: int = 3,
                  description: str = "", suppress_for_seconds: int = 300,
                  escalation_after_seconds: int = 600,
                  notify_channels: list | None = None,
                  enabled: bool = True) -> AlertRule:
    _RULES_DIR.mkdir(parents=True, exist_ok=True)
    rule = AlertRule(
        rule_id=rule_id, name=name, metric=metric, operator=operator,
        threshold=float(threshold), severity=int(severity),
        description=description,
        suppress_for_seconds=int(suppress_for_seconds),
        escalation_after_seconds=int(escalation_after_seconds),
        notify_channels=list(notify_channels or []),
        enabled=bool(enabled),
    )
    (_RULES_DIR / f"{rule_id}.json").write_text(
        json.dumps(rule.to_dict(), indent=2), encoding="utf-8",
    )
    audit_log.record(
        action="alert.rule_upserted", entity_type="alert_rule",
        entity_id=rule_id,
        actor={"name": "alert_admin", "system": True},
        new_state=rule.to_dict(),
    )
    return rule


def list_rules() -> list[AlertRule]:
    if not _RULES_DIR.exists():
        return []
    out: list[AlertRule] = []
    for p in _RULES_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(AlertRule(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return out


def delete_rule(rule_id: str) -> bool:
    path = _RULES_DIR / f"{rule_id}.json"
    if not path.exists():
        return False
    try:
        path.unlink()
        audit_log.record(
            action="alert.rule_deleted", entity_type="alert_rule",
            entity_id=rule_id, actor={"name": "alert_admin", "system": True},
        )
        return True
    except OSError:
        return False


# ── Alert evaluation ───────────────────────────────────────────────────


def evaluate_rules(*, metric_values: dict) -> list[Alert]:
    """Evaluate all rules against the provided metric snapshot. Returns
    alerts that fired this tick (new or re-fired)."""
    out: list[Alert] = []
    now = datetime.now(timezone.utc)
    for rule in list_rules():
        if not rule.enabled:
            continue
        value = metric_values.get(rule.metric)
        if value is None:
            continue
        if not _matches(value, rule.operator, rule.threshold):
            # Resolve any open alert for this rule
            for existing in list_active(rule_id=rule.rule_id):
                resolve(existing.alert_id, actor={"name": "alert_engine", "system": True},
                          reason="condition no longer met")
            continue
        # Condition met
        active = list_active(rule_id=rule.rule_id)
        if active:
            # Suppression check
            alert = active[0]
            if alert.suppression_expires_at:
                try:
                    if datetime.fromisoformat(alert.suppression_expires_at) > now:
                        continue
                except ValueError:
                    pass
            # Escalation: re-notify
            if alert.last_notified_at:
                try:
                    last = datetime.fromisoformat(alert.last_notified_at)
                    if (now - last).total_seconds() >= rule.escalation_after_seconds:
                        _mark_notified(alert)
                        out.append(alert)
                except ValueError:
                    pass
            continue
        # New alert
        alert = Alert(
            alert_id=f"alert_{uuid.uuid4().hex[:12]}", rule_id=rule.rule_id,
            metric=rule.metric, observed_value=float(value),
            threshold=rule.threshold, severity=rule.severity,
            state="open", opened_at=now.isoformat(),
            correlation_id=str(uuid.uuid4()),
        )
        _persist_active(alert)
        _append_history(alert, event="opened")
        audit_log.record(
            action="alert.opened", entity_type="alert",
            entity_id=alert.alert_id,
            actor={"name": "alert_engine", "system": True},
            correlation_id=alert.correlation_id,
            new_state={"rule_id": rule.rule_id, "metric": rule.metric,
                       "observed": value, "threshold": rule.threshold},
        )
        realtime_bus.emit("alert.opened",
                            actor={"name": "alert_engine", "system": True},
                            correlation_id=alert.correlation_id,
                            payload=alert.to_dict(),
                            mirror_to_audit=False)
        _mark_notified(alert)
        out.append(alert)
    return out


def acknowledge(alert_id: str, *, actor: dict | str = "anonymous",
                  reason: str = "") -> Alert | None:
    alert = get(alert_id)
    if alert is None or alert.state != "open":
        return alert
    alert.state = "acknowledged"
    alert.acknowledged_at = datetime.now(timezone.utc).isoformat()
    _persist_active(alert)
    _append_history(alert, event="acknowledged")
    audit_log.record(
        action="alert.acknowledged", entity_type="alert", entity_id=alert_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        correlation_id=alert.correlation_id, metadata={"reason": reason},
    )
    realtime_bus.emit("alert.acknowledged",
                        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
                        correlation_id=alert.correlation_id,
                        payload=alert.to_dict(), mirror_to_audit=False)
    return alert


def resolve(alert_id: str, *, actor: dict | str = "anonymous",
              reason: str = "") -> Alert | None:
    alert = get(alert_id)
    if alert is None or alert.state == "resolved":
        return alert
    alert.state = "resolved"
    alert.resolved_at = datetime.now(timezone.utc).isoformat()
    _persist_active(alert)
    _append_history(alert, event="resolved")
    # Remove from active dir
    try:
        (_ACTIVE_DIR / f"{alert.alert_id}.json").unlink()
    except OSError:
        pass
    audit_log.record(
        action="alert.resolved", entity_type="alert", entity_id=alert_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        correlation_id=alert.correlation_id, metadata={"reason": reason},
    )
    realtime_bus.emit("alert.resolved",
                        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
                        correlation_id=alert.correlation_id,
                        payload=alert.to_dict(), mirror_to_audit=False)
    return alert


def suppress(alert_id: str, *, seconds: int,
              actor: dict | str = "anonymous", reason: str = "") -> Alert | None:
    alert = get(alert_id)
    if alert is None:
        return None
    alert.state = "suppressed"
    alert.suppression_expires_at = (datetime.now(timezone.utc)
                                       + timedelta(seconds=int(seconds))).isoformat()
    _persist_active(alert)
    audit_log.record(
        action="alert.suppressed", entity_type="alert", entity_id=alert_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        correlation_id=alert.correlation_id,
        metadata={"seconds": seconds, "reason": reason},
    )
    return alert


def list_active(*, rule_id: str | None = None) -> list[Alert]:
    if not _ACTIVE_DIR.exists():
        return []
    out: list[Alert] = []
    for p in _ACTIVE_DIR.glob("alert_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(Alert(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    if rule_id:
        out = [a for a in out if a.rule_id == rule_id]
    out.sort(key=lambda a: a.opened_at, reverse=True)
    return out


def get(alert_id: str) -> Alert | None:
    path = _ACTIVE_DIR / f"{alert_id}.json"
    if not path.exists():
        return None
    try:
        return Alert(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


# ── Internal ───────────────────────────────────────────────────────────


def _matches(value: float, op: str, threshold: float) -> bool:
    if op == ">": return value > threshold
    if op == ">=": return value >= threshold
    if op == "<": return value < threshold
    if op == "<=": return value <= threshold
    if op == "==": return value == threshold
    return False


def _persist_active(alert: Alert) -> None:
    _ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    (_ACTIVE_DIR / f"{alert.alert_id}.json").write_text(
        json.dumps(alert.to_dict(), indent=2), encoding="utf-8",
    )


def _append_history(alert: Alert, *, event: str) -> None:
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    path = _HISTORY_DIR / f"{day}.jsonl"
    payload = alert.to_dict()
    payload["history_event"] = event
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _mark_notified(alert: Alert) -> None:
    alert.last_notified_at = datetime.now(timezone.utc).isoformat()
    alert.notification_count += 1
    _persist_active(alert)
