"""SMTP delivery for the pilot pre-1:1 dashboard.

Reads recipients from config/pilot_recipients.json. Reads SMTP credentials
from env (GMAIL_SMTP_USERNAME + GMAIL_SMTP_APP_PASSWORD). Sends the
dashboard HTML as the email body to Ali + the DRI 30 minutes before the
standing 1:1.

Gating:
    PILOT_DASH_DELIVERY=1     → delivery enabled (default OFF for safety)
    PILOT_DASH_TEST_MODE=1    → DRI emails suppressed; Ali-only (default
                                ON until Ali confirms Karun/Kes want the
                                email).

Failure modes are non-fatal: a delivery error is logged + returned, the
scheduler keeps running. The HTML file on disk is the source of truth;
delivery is best-effort.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
RECIPIENTS_PATH = ROOT / "config" / "pilot_recipients.json"

SMTP_HOST = os.environ.get("PILOT_DASH_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("PILOT_DASH_SMTP_PORT", "587"))


@dataclass
class DeliveryResult:
    """Outcome of one delivery attempt."""
    dri: str
    recipients: list[str]
    status: str           # 'ok' | 'disabled' | 'skipped_no_creds' | 'failed'
    error: str = ""


def _load_recipients(dri: str) -> dict | None:
    if not RECIPIENTS_PATH.exists():
        return None
    try:
        cfg = json.loads(RECIPIENTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return cfg.get(dri)


def _resolve_recipients(dri: str) -> tuple[list[str], dict | None]:
    """Returns (to_list, recipients_config) honoring PILOT_DASH_TEST_MODE."""
    cfg = _load_recipients(dri)
    if not cfg:
        return [], None
    test_mode = os.environ.get("PILOT_DASH_TEST_MODE", "1") == "1"
    if test_mode:
        return [cfg["ali_email"]], cfg
    return [cfg["ali_email"], cfg["dri_email"]], cfg


def _smtp_creds() -> tuple[str | None, str | None]:
    user = os.environ.get("GMAIL_SMTP_USERNAME", "").strip()
    pw = os.environ.get("GMAIL_SMTP_APP_PASSWORD", "").strip()
    if not user or not pw:
        return None, None
    return user, pw


def send_dashboard(dri: str, html_path: str, date_iso: str = "",
                              _smtp_factory=None) -> DeliveryResult:
    """Send the dashboard HTML at `html_path` to the configured recipients.

    `_smtp_factory` is an optional injection point for tests — pass a
    callable that returns a context-managed SMTP object (e.g. a MagicMock).
    """
    if os.environ.get("PILOT_DASH_DELIVERY", "0") != "1":
        return DeliveryResult(dri=dri, recipients=[], status="disabled")

    to_list, cfg = _resolve_recipients(dri)
    if not cfg or not to_list:
        return DeliveryResult(
            dri=dri, recipients=[], status="failed",
            error=f"no recipients configured for dri={dri!r}",
        )

    user, pw = _smtp_creds()
    if not user or not pw:
        return DeliveryResult(
            dri=dri, recipients=to_list, status="skipped_no_creds",
            error="GMAIL_SMTP_USERNAME / GMAIL_SMTP_APP_PASSWORD not set",
        )

    try:
        html = Path(html_path).read_text(encoding="utf-8")
    except OSError as e:
        return DeliveryResult(
            dri=dri, recipients=to_list, status="failed",
            error=f"could not read html: {e}",
        )

    subject = f"{cfg['subject_prefix']} — {date_iso[:10] or 'today'}"
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{cfg['from_name']} <{user}>"
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if _smtp_factory:
            client = _smtp_factory()
            with client as s:
                s.login(user, pw)
                s.sendmail(user, to_list, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(user, pw)
                s.sendmail(user, to_list, msg.as_string())
        logger.info(
            "pilot dash delivery: dri=%s to=%s subject=%r status=ok",
            dri, to_list, subject,
        )
        return DeliveryResult(dri=dri, recipients=to_list, status="ok")
    except Exception as e:
        logger.warning("pilot dash delivery failed: %s", e, exc_info=True)
        return DeliveryResult(
            dri=dri, recipients=to_list, status="failed", error=str(e),
        )
