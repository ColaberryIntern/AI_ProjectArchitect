"""SMTP delivery for the daily productivity report.

Same shape as execution/products/pilot/delivery.py (Gmail SMTP, MIME HTML,
test-injectable factory) but with its own gating + recipients so enabling the
productivity email never touches the pilot dashboards.

Gating:
    PRODUCTIVITY_REPORT_DELIVERY=1  -> delivery enabled (default OFF for safety)

Recipients + branding: config/report_recipients.json.
Credentials: GMAIL_SMTP_USERNAME + GMAIL_SMTP_APP_PASSWORD (shared with pilot).

Delivery failures are non-fatal: the HTML on disk is the source of truth.
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
SMTP_HOST = os.environ.get("PRODUCTIVITY_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("PRODUCTIVITY_SMTP_PORT", "587"))


@dataclass
class DeliveryResult:
    recipients: list = field(default_factory=list)
    status: str = "disabled"     # ok | disabled | skipped_no_creds | failed
    error: str = ""


def _load_recipients() -> dict | None:
    if not RECIPIENTS_PATH.exists():
        return None
    try:
        return json.loads(RECIPIENTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _smtp_creds() -> tuple[str | None, str | None]:
    user = os.environ.get("GMAIL_SMTP_USERNAME", "").strip()
    pw = os.environ.get("GMAIL_SMTP_APP_PASSWORD", "").strip()
    if not user or not pw:
        return None, None
    return user, pw


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

    user, pw = _smtp_creds()
    if not user or not pw:
        return DeliveryResult(
            recipients=to_list, status="skipped_no_creds",
            error="GMAIL_SMTP_USERNAME / GMAIL_SMTP_APP_PASSWORD not set",
        )

    try:
        html = Path(html_path).read_text(encoding="utf-8")
    except OSError as e:
        return DeliveryResult(recipients=to_list, status="failed", error=f"could not read html: {e}")

    subject = f"{cfg.get('subject_prefix', 'Productivity report')} - {date_iso[:10] or 'today'}"
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{cfg.get('from_name', 'Productivity Report')} <{user}>"
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))
    envelope = to_list + bcc

    try:
        if _smtp_factory:
            with _smtp_factory() as s:
                s.login(user, pw)
                s.sendmail(user, envelope, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(user, pw)
                s.sendmail(user, envelope, msg.as_string())
        logger.info("productivity report delivered: to=%s subject=%r", envelope, subject)
        return DeliveryResult(recipients=envelope, status="ok")
    except Exception as e:
        logger.warning("productivity report delivery failed: %s", e, exc_info=True)
        return DeliveryResult(recipients=to_list, status="failed", error=str(e))
