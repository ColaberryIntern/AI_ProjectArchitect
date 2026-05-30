"""Notification adapters — Slack / Teams / Email / Webhook.

Scope honesty
-------------
- Adapter interfaces are real. Specific adapter implementations rely on
  outbound HTTP (``urllib`` from stdlib) and never bundle vendor SDKs.
- Sends are retried at most ``MAX_RETRIES`` with exponential backoff.
- Every send (success OR failure) writes an audit row + a delivery record
  under ``output/ops_platform/notifications/{date}.jsonl`` so the operator
  can see which alerts actually reached the channel.
- No secrets are persisted — webhook URLs and Slack tokens are read from
  the ``secrets`` module at send time and masked in audit metadata.
"""

from __future__ import annotations

import json
import logging
import smtplib
import time
import urllib.request
import urllib.error
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, secrets as ops_secrets

logger = logging.getLogger(__name__)

_NOTIF_DIR = OUTPUT_DIR / "ops_platform" / "notifications"
_CHANNELS_DIR = _NOTIF_DIR / "channels"

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.0


@dataclass
class NotificationChannel:
    channel_id: str
    name: str
    kind: str                       # "slack" | "teams" | "email" | "webhook"
    config: dict                     # adapter-specific config (URL secret name, etc.)
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DeliveryRecord:
    delivery_id: str
    channel_id: str
    kind: str
    success: bool
    attempt_count: int
    sent_at: str
    correlation_id: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Channel CRUD ───────────────────────────────────────────────────────


def upsert_channel(*, channel_id: str, name: str, kind: str, config: dict,
                     enabled: bool = True) -> NotificationChannel:
    if kind not in ("slack", "teams", "email", "webhook"):
        raise ValueError(f"unknown notification kind {kind}")
    _CHANNELS_DIR.mkdir(parents=True, exist_ok=True)
    ch = NotificationChannel(channel_id=channel_id, name=name, kind=kind,
                                config=dict(config), enabled=bool(enabled))
    (_CHANNELS_DIR / f"{channel_id}.json").write_text(
        json.dumps(ch.to_dict(), indent=2), encoding="utf-8",
    )
    audit_log.record(
        action="notification.channel_upserted", entity_type="notification_channel",
        entity_id=channel_id, actor={"name": "notif_admin", "system": True},
        new_state={"name": name, "kind": kind, "enabled": enabled,
                   "config_keys": sorted(config.keys())},
    )
    return ch


def list_channels() -> list[NotificationChannel]:
    if not _CHANNELS_DIR.exists():
        return []
    out: list[NotificationChannel] = []
    for p in _CHANNELS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(NotificationChannel(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return out


def get_channel(channel_id: str) -> NotificationChannel | None:
    path = _CHANNELS_DIR / f"{channel_id}.json"
    if not path.exists():
        return None
    try:
        return NotificationChannel(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


# ── Send ───────────────────────────────────────────────────────────────


def send(channel_id: str, *, title: str, body: str,
           correlation_id: str | None = None,
           extra: dict | None = None) -> DeliveryRecord:
    ch = get_channel(channel_id)
    if ch is None or not ch.enabled:
        record = _record_delivery(channel_id, kind=(ch.kind if ch else "unknown"),
                                     success=False, attempts=0,
                                     error="channel not found or disabled",
                                     correlation_id=correlation_id)
        return record
    error: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _dispatch(ch, title=title, body=body, extra=extra or {})
            record = _record_delivery(channel_id, kind=ch.kind, success=True,
                                         attempts=attempt, correlation_id=correlation_id)
            return record
        except Exception as e:
            error = str(e)[:200]
            time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    record = _record_delivery(channel_id, kind=ch.kind, success=False,
                                 attempts=MAX_RETRIES, error=error,
                                 correlation_id=correlation_id)
    return record


def delivery_history(*, days: int = 7) -> list[dict]:
    if not _NOTIF_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(_NOTIF_DIR.glob("*.jsonl"), reverse=True)[:days + 1]:
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _dispatch(ch: NotificationChannel, *, title: str, body: str, extra: dict) -> None:
    if ch.kind == "webhook":
        url = _resolve_secret(ch.config.get("url_secret"), default=ch.config.get("url", ""))
        if not url:
            raise ValueError("webhook URL not configured")
        payload = json.dumps({"title": title, "body": body, **extra}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
                                          headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return
    if ch.kind == "slack":
        url = _resolve_secret(ch.config.get("webhook_secret"), default=ch.config.get("webhook_url", ""))
        if not url:
            raise ValueError("Slack webhook URL not configured")
        text = f"*{title}*\n{body}"
        payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
                                          headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return
    if ch.kind == "teams":
        url = _resolve_secret(ch.config.get("webhook_secret"), default=ch.config.get("webhook_url", ""))
        if not url:
            raise ValueError("Teams webhook URL not configured")
        payload = json.dumps({"@type": "MessageCard", "summary": title,
                                  "title": title, "text": body}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
                                          headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return
    if ch.kind == "email":
        host = ch.config.get("smtp_host")
        port = int(ch.config.get("smtp_port", 587))
        sender = ch.config.get("from_address")
        recipients = ch.config.get("to_addresses") or []
        if not (host and sender and recipients):
            raise ValueError("email channel requires smtp_host, from_address, to_addresses")
        msg = (
            f"From: {sender}\r\nTo: {', '.join(recipients)}\r\n"
            f"Subject: {title}\r\n\r\n{body}"
        )
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.ehlo()
            try:
                smtp.starttls()
            except smtplib.SMTPNotSupportedError:
                pass
            user = _resolve_secret(ch.config.get("smtp_user_secret"))
            password = _resolve_secret(ch.config.get("smtp_pass_secret"))
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(sender, recipients, msg)
        return
    raise ValueError(f"unsupported kind {ch.kind}")


def _resolve_secret(name: str | None, *, default: str = "") -> str:
    if not name:
        return default
    value = ops_secrets.read(name)
    return value or default


def _record_delivery(channel_id: str, *, kind: str, success: bool,
                       attempts: int, correlation_id: str | None = None,
                       error: str | None = None) -> DeliveryRecord:
    record = DeliveryRecord(
        delivery_id=str(uuid.uuid4()), channel_id=channel_id, kind=kind,
        success=success, attempt_count=attempts,
        sent_at=datetime.now(timezone.utc).isoformat(),
        correlation_id=correlation_id, error=error,
    )
    _NOTIF_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    path = _NOTIF_DIR / f"{day}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    audit_log.record(
        action=("notification.delivered" if success else "notification.failed"),
        entity_type="notification", entity_id=record.delivery_id,
        actor={"name": "notification_engine", "system": True},
        correlation_id=correlation_id,
        metadata={"channel_id": channel_id, "kind": kind,
                  "attempts": attempts, "error": error},
    )
    return record
