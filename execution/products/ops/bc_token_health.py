"""Deterministic Basecamp token-health preflight.

Runs daily on the ops scheduler. Probes every Basecamp identity the MCP
server can author as -- each operator's per-user "X AI" grant plus the shared
CB System identity -- and reports which are healthy, which are near expiry, and
which are already failing. When anything needs a human (near expiry without a
refresh_token, or a hard auth failure), it emails Ali + Kes BEFORE the 401
hits, not after.

This is the early-warning + verification half of the token-health process; the
self-refresh + self-heal halves live in basecamp_oauth_token.py and
mcp_tools.py. See directives/basecamp-token-health.md.

Pure / failure-first:
    - probe_token() does one short whoami GET; never raises.
    - check_all() catalogs every principal; never raises.
    - No live BC calls in tests (probe is injectable).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

WHOAMI_URL = "https://launchpad.37signals.com/authorization.json"
USER_AGENT = "Colaberry MCP token-health (ali@colaberry.com)"
HTTP_TIMEOUT = 10

DEFAULT_WARN_DAYS = 3
SECONDS_PER_DAY = 86400


# ── Probe ────────────────────────────────────────────────────────────


@dataclass
class ProbeResult:
    ok: bool
    status: str          # "ok" | "unauthorized" | "http_error" | "network_error" | "no_token"
    detail: str = ""


def probe_token(token: str, *, _opener: Optional[Callable] = None) -> ProbeResult:
    """One cheap whoami against Launchpad to confirm a token is live.

    Returns a ProbeResult; never raises. `_opener` is a test injection point
    (callable(req, timeout) -> context-managed response).
    """
    if not token or not token.strip():
        return ProbeResult(ok=False, status="no_token", detail="empty token")
    req = urllib.request.Request(
        WHOAMI_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    opener = _opener or (lambda r, timeout: urllib.request.urlopen(r, timeout=timeout))
    try:
        with opener(req, HTTP_TIMEOUT) as resp:
            resp.read()
        return ProbeResult(ok=True, status="ok")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ProbeResult(ok=False, status="unauthorized",
                               detail=f"HTTP {e.code} from whoami")
        return ProbeResult(ok=False, status="http_error", detail=f"HTTP {e.code}")
    except urllib.error.URLError as e:
        return ProbeResult(ok=False, status="network_error",
                           detail=f"could not reach launchpad: {type(e).__name__}")
    except Exception as e:  # noqa: BLE001
        return ProbeResult(ok=False, status="network_error",
                           detail=f"{type(e).__name__}")


# ── Catalog ──────────────────────────────────────────────────────────


@dataclass
class PrincipalHealth:
    principal: str                 # user_id or "cb-system" or "static-env"
    label: str                     # human-friendly name / email
    tier: str                      # "operator" | "shared" | "static-env"
    has_refresh_token: bool
    expires_at: Optional[float]    # epoch, or None if unknown
    days_to_expiry: Optional[float]
    severity: str = "ok"           # "ok" | "warn" | "critical"
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class HealthReport:
    generated_at: float
    principals: list = field(default_factory=list)  # list[PrincipalHealth]

    @property
    def needs_attention(self) -> list:
        return [p for p in self.principals if p.severity in ("warn", "critical")]

    @property
    def worst_severity(self) -> str:
        sev = {p.severity for p in self.principals}
        if "critical" in sev:
            return "critical"
        if "warn" in sev:
            return "warn"
        return "ok"


def _warn_days() -> int:
    try:
        return int(os.environ.get("BC_TOKEN_HEALTH_WARN_DAYS", DEFAULT_WARN_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_WARN_DAYS


def _classify(has_refresh: bool, expires_at: Optional[float],
              now: float, warn_days: int) -> tuple[Optional[float], str, str]:
    """Return (days_to_expiry, severity, reason) from grant metadata alone.

    A grant WITH a refresh_token that is near/after expiry is fine -- it will
    self-refresh. The danger cases are: near/after expiry with NO refresh_token
    (a human must re-consent), or a legacy/missing grant.
    """
    if expires_at is None:
        if not has_refresh:
            return None, "critical", "no expiry + no refresh_token (legacy/unconnected grant)"
        return None, "ok", "refresh_token present (expiry unknown but self-refreshes)"
    days = (expires_at - now) / SECONDS_PER_DAY
    if has_refresh:
        # Self-refreshing: expiry is a non-event. Only flag if we can't refresh.
        return days, "ok", "self-refreshes via refresh_token"
    if days <= 0:
        return days, "critical", "expired, no refresh_token -- re-consent required"
    if days <= warn_days:
        return days, "warn", f"expires in {days:.1f}d, no refresh_token"
    return days, "ok", "static token still valid"


def check_all(*, probe: Optional[Callable[[str], ProbeResult]] = None,
              now: Optional[float] = None) -> HealthReport:
    """Catalog the health of every BC identity the MCP can author as.

    `probe` (default: live probe_token) lets tests inject a stub. `now` lets
    tests pin the clock. Never raises -- partial catalog beats a crashed job.
    """
    from ...products.library import basecamp_oauth_token as bt
    from ...products.library import tenancy

    now = now if now is not None else time.time()
    warn_days = _warn_days()
    probe = probe or probe_token
    out: list[PrincipalHealth] = []

    def _grant_meta(principal) -> Optional[dict]:
        try:
            return bt.get_grant_metadata(principal)
        except Exception:  # noqa: BLE001
            return None

    # 1. Per-operator grants.
    try:
        users = tenancy.list_users(active_only=False)
    except Exception:  # noqa: BLE001
        users = []
    for u in users:
        meta = _grant_meta(u)
        if not meta:
            continue  # operator simply hasn't connected a BC AI persona
        # A legacy (bare-token) grant has no refresh_token by definition.
        has_refresh = not bool(meta.get("legacy"))
        exp = meta.get("access_token_expires_at")
        days, sev, reason = _classify(has_refresh, exp, now, warn_days)
        out.append(PrincipalHealth(
            principal=u.user_id,
            label=meta.get("bc_user_email") or getattr(u, "email", "") or u.user_id,
            tier="operator", has_refresh_token=has_refresh,
            expires_at=exp, days_to_expiry=days, severity=sev, reason=reason,
        ))

    # 2. Shared CB System self-refresh grant.
    shared_meta = _grant_meta(bt.shared_cb_system_principal())
    if shared_meta:
        has_refresh = not bool(shared_meta.get("legacy"))
        exp = shared_meta.get("access_token_expires_at")
        days, sev, reason = _classify(has_refresh, exp, now, warn_days)
        out.append(PrincipalHealth(
            principal=bt.SHARED_CB_SYSTEM_USER_ID,
            label=shared_meta.get("bc_user_email") or "CB System (shared)",
            tier="shared", has_refresh_token=has_refresh,
            expires_at=exp, days_to_expiry=days, severity=sev, reason=reason,
        ))
    else:
        # No shared self-refresh grant stored yet -> still on the static env
        # token, which is the recurring-incident path.
        env_tok = os.environ.get("BASECAMP_ACCESS_TOKEN", "")
        if env_tok:
            res = probe(env_tok)
            sev = "ok" if res.ok else "critical"
            reason = ("static BASECAMP_ACCESS_TOKEN still valid, but NO self-refresh "
                      "grant stored -- it will 401 at the next ~14d rotation; "
                      "store the CB System grant (see runbook)") if res.ok else \
                     f"static BASECAMP_ACCESS_TOKEN failing: {res.detail}"
            # A live-but-unmanaged static token is a standing warning, not ok.
            if res.ok:
                sev = "warn"
            out.append(PrincipalHealth(
                principal="static-env", label="CB System (static env)",
                tier="static-env", has_refresh_token=False,
                expires_at=None, days_to_expiry=None, severity=sev, reason=reason,
            ))

    return HealthReport(generated_at=now, principals=out)


# ── Alert rendering ──────────────────────────────────────────────────


def render_alert_html(report: HealthReport) -> str:
    """One compact HTML body summarizing principals that need attention."""
    rows = []
    for p in sorted(report.principals,
                    key=lambda x: {"critical": 0, "warn": 1, "ok": 2}[x.severity]):
        color = {"critical": "#b91c1c", "warn": "#b45309", "ok": "#15803d"}[p.severity]
        days = "—" if p.days_to_expiry is None else f"{p.days_to_expiry:.1f}d"
        rows.append(
            f"<tr>"
            f"<td style='padding:4px 10px;font-weight:600;color:{color}'>{p.severity.upper()}</td>"
            f"<td style='padding:4px 10px'>{p.label}</td>"
            f"<td style='padding:4px 10px;color:#475569'>{p.tier}</td>"
            f"<td style='padding:4px 10px'>{days}</td>"
            f"<td style='padding:4px 10px;color:#475569'>{p.reason}</td>"
            f"</tr>"
        )
    worst = report.worst_severity
    headline = {
        "critical": "Action needed: a Basecamp identity needs re-consent",
        "warn": "Heads up: a Basecamp identity is near expiry / unmanaged",
        "ok": "All Basecamp identities healthy",
    }[worst]
    return (
        f"<h2 style='font-family:Arial,sans-serif'>{headline}</h2>"
        f"<p style='font-family:Arial,sans-serif;color:#475569'>"
        f"Daily Basecamp token-health preflight. Remediation steps: "
        f"see directives/basecamp-token-health.md.</p>"
        f"<table style='font-family:Arial,sans-serif;border-collapse:collapse'>"
        f"<tr style='background:#f1f5f9'>"
        f"<th style='padding:4px 10px;text-align:left'>Severity</th>"
        f"<th style='padding:4px 10px;text-align:left'>Identity</th>"
        f"<th style='padding:4px 10px;text-align:left'>Tier</th>"
        f"<th style='padding:4px 10px;text-align:left'>Expiry</th>"
        f"<th style='padding:4px 10px;text-align:left'>Note</th></tr>"
        f"{''.join(rows)}</table>"
    )


def should_alert(report: HealthReport) -> bool:
    return bool(report.needs_attention)


# ── Delivery ─────────────────────────────────────────────────────────


def _load_recipients() -> dict:
    """Recipients for the token-health alert from config/report_recipients.json
    (bc_token_health block). Falls back to ali only."""
    default = {"to": ["ali@colaberry.com"], "bcc": [],
               "subject_prefix": "Basecamp token health",
               "from_name": "Colaberry MCP Token Health"}
    try:
        from config.settings import PROJECT_ROOT
        path = PROJECT_ROOT / "config" / "report_recipients.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        block = cfg.get("bc_token_health")
        if isinstance(block, dict) and block.get("to"):
            return block
    except Exception:  # noqa: BLE001
        pass
    return default


def send_alert(report: HealthReport, *, _smtp_factory=None) -> dict:
    """Email Ali + Kes when a BC identity needs attention.

    Mirrors the productivity report's transport resolution (Gmail dev ->
    Mandrill prod). Returns a small status dict; never raises. No-op (status
    'no_alert') when nothing needs attention, so this is safe to call every
    day -- it only sends on a real signal.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if os.environ.get("BC_TOKEN_HEALTH_ENABLED", "1") != "1":
        return {"status": "disabled"}
    if not should_alert(report):
        return {"status": "no_alert"}

    # Transport: Gmail (dev) then Mandrill (prod) -- same precedence as delivery.py.
    gmail_user = os.environ.get("GMAIL_SMTP_USERNAME", "").strip()
    gmail_pw = os.environ.get("GMAIL_SMTP_APP_PASSWORD", "").strip()
    mandrill_key = os.environ.get("MANDRILL_API_KEY", "").strip()
    if gmail_user and gmail_pw:
        smtp = {"host": "smtp.gmail.com", "port": 587, "user": gmail_user,
                "password": gmail_pw, "transport": "gmail"}
    elif mandrill_key:
        smtp = {"host": "smtp.mandrillapp.com", "port": 587,
                "user": os.environ.get("MANDRILL_USERNAME", "ali@colaberry.com").strip()
                or "ali@colaberry.com",
                "password": mandrill_key, "transport": "mandrill"}
    else:
        return {"status": "skipped_no_creds"}

    cfg = _load_recipients()
    to_list = list(cfg.get("to", [])) or ["ali@colaberry.com"]
    bcc = list(cfg.get("bcc", []))
    from_email = os.environ.get("PRODUCTIVITY_FROM_EMAIL", "ali@colaberry.com")
    worst = report.worst_severity
    subject = (f"{cfg.get('subject_prefix', 'Basecamp token health')} - "
               f"{'ACTION NEEDED' if worst == 'critical' else 'warning'}")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{cfg.get('from_name', 'Colaberry MCP Token Health')} <{from_email}>"
    msg["To"] = ", ".join(to_list)
    msg["Reply-To"] = from_email
    msg["Subject"] = subject
    msg["X-MC-Track"] = "none"
    msg["X-MC-AutoText"] = "false"
    msg.attach(MIMEText(render_alert_html(report), "html", "utf-8"))
    envelope = list(dict.fromkeys(to_list + bcc))

    try:
        if _smtp_factory:
            with _smtp_factory() as s:
                s.login(smtp["user"], smtp["password"])
                s.sendmail(from_email, envelope, msg.as_string())
        else:
            with smtplib.SMTP(smtp["host"], smtp["port"], timeout=20) as s:
                s.ehlo(); s.starttls(); s.ehlo()
                s.login(smtp["user"], smtp["password"])
                s.sendmail(from_email, envelope, msg.as_string())
        logger.info("bc_token_health alert sent via %s to=%s severity=%s",
                    smtp["transport"], envelope, worst)
        return {"status": "ok", "transport": smtp["transport"],
                "recipients": envelope, "severity": worst}
    except Exception as e:  # noqa: BLE001
        logger.warning("bc_token_health alert delivery failed: %s", e, exc_info=True)
        return {"status": "failed", "error": str(e)}


def run() -> dict:
    """Entry point for the scheduler: catalog + alert. Never raises."""
    try:
        report = check_all()
    except Exception as e:  # noqa: BLE001
        logger.warning("bc_token_health.check_all failed: %s", e, exc_info=True)
        return {"status": "check_failed", "error": str(e)}
    result = send_alert(report)
    result["principals"] = len(report.principals)
    result["needs_attention"] = len(report.needs_attention)
    return result
