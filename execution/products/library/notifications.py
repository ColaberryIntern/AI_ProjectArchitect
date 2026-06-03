"""[Workflow 1] Notification fan-out.

V1 implementation: append-only JSONL log per company. A daily roll-up
job (cron / APScheduler) reads pending events and renders a single
email per company-per-day (anti-fatigue per the ticket).

The actual SMTP send is OUT OF SCOPE for Workflow 1 — that's wired
when [Admin 2] credentials for Gmail/Mandrill are set in prod. Until
then, the events sit in the JSONL and the daily roll-up renderer
writes the email body to disk for inspection.

Files:
    output/library/_tenants/notifications/{company_id}.jsonl
    output/library/_tenants/notifications/digest_{company}_{date}.html
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import tenancy


def _notif_root() -> Path:
    p = tenancy._root() / "notifications"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _company_log(company_id: str) -> Path:
    return _notif_root() / f"{company_id}.jsonl"


@dataclass
class NotificationEvent:
    kind: str             # "submission" | "review_decision" | "comment"
    company_id: str
    actor_user_id: str
    target_user_id: str   # who receives the notification
    item_kind: str
    item_id: str
    category: str
    summary: str
    detail: str = ""
    at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                                  time.gmtime()))
    read: bool = False


def emit(event: NotificationEvent) -> None:
    log = _company_log(event.company_id)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(event)) + "\n")


def notify_submission(company_id: str, author_user_id: str,
                                item_kind: str, item_id: str, category: str) -> int:
    """Fan-out: notify every admin in the company that a new item is awaiting review.
    Returns count of notifications emitted."""
    admins = [u for u in tenancy.list_users(company_id=company_id)
                   if "admin" in (u.roles or [])]
    n = 0
    for admin in admins:
        if admin.user_id == author_user_id:
            continue  # don't notify yourself
        emit(NotificationEvent(
            kind="submission", company_id=company_id,
            actor_user_id=author_user_id, target_user_id=admin.user_id,
            item_kind=item_kind, item_id=item_id, category=category,
            summary=f"{item_id} submitted for review",
        ))
        n += 1
    return n


def notify_decision(company_id: str, reviewer_user_id: str,
                              author_user_id: str, item_kind: str, item_id: str,
                              category: str, decision: str, notes: str = "") -> None:
    """Tell the author the decision."""
    summary_map = {
        "approved": f"{item_id} approved for {company_id}",
        "rejected": f"{item_id} rejected",
        "changes_requested": f"Changes requested on {item_id}",
    }
    emit(NotificationEvent(
        kind="review_decision", company_id=company_id,
        actor_user_id=reviewer_user_id, target_user_id=author_user_id,
        item_kind=item_kind, item_id=item_id, category=category,
        summary=summary_map.get(decision, f"{item_id}: {decision}"),
        detail=notes,
    ))


def unread_for_user(user_id: str, company_id: str) -> list[dict]:
    log = _company_log(company_id)
    if not log.exists():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("target_user_id") == user_id and not rec.get("read"):
            out.append(rec)
    return out


def unread_count_for_user(user_id: str, company_id: str) -> int:
    return len(unread_for_user(user_id, company_id))


def mark_all_read(user_id: str, company_id: str) -> int:
    """Rewrite the log marking this user's notifs as read. Returns count marked."""
    log = _company_log(company_id)
    if not log.exists():
        return 0
    lines = log.read_text(encoding="utf-8").splitlines()
    out_lines = []
    n = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            out_lines.append(line)
            continue
        if rec.get("target_user_id") == user_id and not rec.get("read"):
            rec["read"] = True
            n += 1
        out_lines.append(json.dumps(rec))
    log.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return n


# ── Daily digest renderer (anti-fatigue) ─────────────────────────


def render_daily_digest(company_id: str, date_yyyymmdd: str | None = None) -> str:
    """Produce one HTML email body summarizing the day's activity.
    Pure render — no SMTP. Writes alongside the JSONL for inspection.
    Returns the file path written."""
    log = _company_log(company_id)
    if not log.exists():
        return ""
    target_date = date_yyyymmdd or time.strftime("%Y-%m-%d", time.gmtime())
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("at", "").startswith(target_date):
            rows.append(r)
    if not rows:
        return ""
    by_target: dict[str, list[dict]] = {}
    for r in rows:
        by_target.setdefault(r["target_user_id"], []).append(r)

    html = [f"<h2>Library activity digest — {company_id} — {target_date}</h2>"]
    for uid, items in by_target.items():
        user = tenancy.get_user(uid)
        name = user.display_name if user else uid
        html.append(f"<h3>For {name}</h3><ul>")
        for it in items:
            html.append(f"<li>{it['summary']}"
                              + (f" <em>— {it['detail']}</em>" if it.get('detail') else "")
                              + "</li>")
        html.append("</ul>")
    body = "\n".join(html)

    out = _notif_root() / f"digest_{company_id}_{target_date}.html"
    out.write_text(body, encoding="utf-8")
    return str(out)
