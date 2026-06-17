"""SMTP delivery for the daily productivity report.

Internal ops dashboard, same class as the Karun/Kes pilot emails: SMTP + MIME
HTML, no marketing signature. Its own gating + recipients so enabling it never
touches the pilot dashboards.

Gating:
    PRODUCTIVITY_REPORT_DELIVERY=1  -> delivery enabled (default OFF for safety)

Transport resolution (first that is fully configured wins):
    1. Gmail   — GMAIL_SMTP_USERNAME + GMAIL_SMTP_APP_PASSWORD (dev/test)
    2. Mandrill— MANDRILL_API_KEY (+ MANDRILL_USERNAME, default ali@colaberry.com)
                 over smtp.mandrillapp.com:587. This is the live prod path; the
                 app container already carries these creds.

From: ali@colaberry.com (PRODUCTIVITY_FROM_EMAIL override), reply-to same, and
always BCC ali per the house contract. Mandrill tracking is suppressed
(X-MC-Track: none) since this is an internal report.

Recipients + branding: config/report_recipients.json. Failures are non-fatal:
the HTML on disk is the source of truth.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

RECIPIENTS_PATH = PROJECT_ROOT / "config" / "report_recipients.json"
FROM_EMAIL = os.environ.get("PRODUCTIVITY_FROM_EMAIL", "ali@colaberry.com")


@dataclass
class DeliveryResult:
    recipients: list = field(default_factory=list)
    status: str = "disabled"     # ok | disabled | skipped_no_creds | failed
    error: str = ""
    transport: str = ""


def _load_recipients() -> dict | None:
    if not RECIPIENTS_PATH.exists():
        return None
    try:
        return json.loads(RECIPIENTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_smtp() -> dict | None:
    """Pick the first fully-configured transport: Gmail (dev) then Mandrill (prod)."""
    gmail_user = os.environ.get("GMAIL_SMTP_USERNAME", "").strip()
    gmail_pw = os.environ.get("GMAIL_SMTP_APP_PASSWORD", "").strip()
    if gmail_user and gmail_pw:
        return {"transport": "gmail", "host": "smtp.gmail.com", "port": 587,
                "user": gmail_user, "password": gmail_pw}

    mandrill_key = os.environ.get("MANDRILL_API_KEY", "").strip()
    if mandrill_key:
        return {"transport": "mandrill", "host": "smtp.mandrillapp.com", "port": 587,
                "user": os.environ.get("MANDRILL_USERNAME", "ali@colaberry.com").strip()
                or "ali@colaberry.com",
                "password": mandrill_key}
    return None


def send_report(html_path: str, date_iso: str = "", _smtp_factory=None) -> DeliveryResult:
    """Send the report HTML at `html_path` to the configured recipients.

    `_smtp_factory` is a test injection point: a callable returning a
    context-managed SMTP object.
    """
    if os.environ.get("PRODUCTIVITY_REPORT_DELIVERY", "0") != "1":
        return DeliveryResult(status="disabled")

    cfg = _load_recipients()
    to_list = list(cfg.get("to", [])) if cfg else []
    bcc = list(cfg.get("bcc", [])) if cfg else []
    if not cfg or not to_list:
        return DeliveryResult(status="failed", error="no recipients configured")

    smtp = _resolve_smtp()
    if not smtp:
        return DeliveryResult(
            recipients=to_list, status="skipped_no_creds",
            error="no SMTP creds: set GMAIL_SMTP_* or MANDRILL_API_KEY",
        )

    try:
        html = Path(html_path).read_text(encoding="utf-8")
    except OSError as e:
        return DeliveryResult(recipients=to_list, status="failed", error=f"could not read html: {e}")

    subject = f"{cfg.get('subject_prefix', 'Productivity report')} - {date_iso[:10] or 'today'}"
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{cfg.get('from_name', 'Productivity Report')} <{FROM_EMAIL}>"
    msg["To"] = ", ".join(to_list)
    msg["Reply-To"] = FROM_EMAIL
    msg["Subject"] = subject
    # Internal report: suppress Mandrill open/click tracking + auto-text.
    msg["X-MC-Track"] = "none"
    msg["X-MC-AutoText"] = "false"
    msg.attach(MIMEText(html, "html", "utf-8"))
    # Always BCC ali; dedupe so the envelope has no repeats.
    envelope = list(dict.fromkeys(to_list + bcc + ["ali@colaberry.com"]))

    try:
        if _smtp_factory:
            with _smtp_factory() as s:
                s.login(smtp["user"], smtp["password"])
                s.sendmail(FROM_EMAIL, envelope, msg.as_string())
        else:
            with smtplib.SMTP(smtp["host"], smtp["port"], timeout=20) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(smtp["user"], smtp["password"])
                s.sendmail(FROM_EMAIL, envelope, msg.as_string())
        logger.info("productivity report delivered via %s: to=%s subject=%r",
                    smtp["transport"], envelope, subject)
        return DeliveryResult(recipients=envelope, status="ok", transport=smtp["transport"])
    except Exception as e:
        logger.warning("productivity report delivery failed: %s", e, exc_info=True)
        return DeliveryResult(recipients=to_list, status="failed", error=str(e),
                              transport=smtp["transport"])
