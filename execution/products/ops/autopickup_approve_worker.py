"""[Auto-Pickup Approve Worker] Phase 1.5: detect approvals on autopickup drafts.

Sibling to autopickup_worker.py and cb_mention_worker.py. The Phase 1
worker posts "proposed next step" comments with an autopickup_id
marker. This worker scans for human responses to those comments and
logs the approval signal so Phase 2 (execute-on-approval) can read it.

Trigger model: APScheduler cron every 5 min. No-op unless
OPS_AUTOPICKUP_ENABLED=true (same env flag as the writer worker).

Approval signal heuristic (v1, text-reply only):
  - Find each autopickup_id-tagged comment in allowlisted buckets
  - For each, look at comments POSTED AFTER it on the same ticket
  - If a non-CB-System comment contains 'approve', 'approved', 'yes do it',
    'go', 'ship it', or a thumbs-up emoji, treat it as an approval
  - If a non-CB-System comment exists but does not match: treat as
    rejection / clarification (logged with status=rejected)
  - If no later comments yet: leave the autopickup_id as pending

Idempotency: every detected approval / rejection is recorded once,
keyed on autopickup_id, in output/ops/_autopickup_approvals/processed.json.
Worker skips autopickup_ids already in the set.

Audit: every detection appends to
output/ops/_autopickup_approvals/YYYY-MM-DD.jsonl so the operator can
audit approval rates over time and the future Phase 2 executor can
react to status changes.

Optional feedback comment: when approval is detected, the worker posts a
short confirmation back on the same ticket saying "approval recorded for
ap-XXXXX; Phase 2 would now execute." Toggled by
OPS_AUTOPICKUP_FEEDBACK_COMMENTS=true (default false so we do not flood
the ticket on first roll-out).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT

from . import bc_comments, tokens

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────

ENABLED = os.environ.get("OPS_AUTOPICKUP_ENABLED", "false").strip().lower() == "true"
POST_FEEDBACK = os.environ.get("OPS_AUTOPICKUP_FEEDBACK_COMMENTS", "false").strip().lower() == "true"
LOOKBACK_DAYS = int(os.environ.get("OPS_AUTOPICKUP_APPROVE_LOOKBACK_DAYS", "3"))

AUTOPICKUP_AUDIT_DIR = PROJECT_ROOT / "output" / "ops" / "_autopickup"
APPROVAL_DIR = PROJECT_ROOT / "output" / "ops" / "_autopickup_approvals"
PROCESSED_PATH = APPROVAL_DIR / "processed.json"
HEARTBEAT_PATH = APPROVAL_DIR / "heartbeat.json"

# Approval signals (case-insensitive substring match on stripped HTML)
APPROVE_PATTERNS = [
    r"\bapproved?\b",
    r"\bapproved\b",
    r"\byes do it\b",
    r"\bship it\b",
    r"\bgo for it\b",
    r"\blgtm\b",
    "👍",
    "✅",
    "💚",
]
_APPROVE_RE = re.compile("|".join(APPROVE_PATTERNS), re.IGNORECASE)

# Reject / clarification signals
REJECT_PATTERNS = [
    r"\breject(ed)?\b",
    r"\bdo not\b",
    r"\bdon't\b",
    r"\bno\b",
    r"\bstop\b",
    r"\bhold\b",
    r"\bnot yet\b",
    "👎",
    "❌",
]
_REJECT_RE = re.compile("|".join(REJECT_PATTERNS), re.IGNORECASE)


# ── Storage ────────────────────────────────────────────────────────


@dataclass
class ApprovalDetection:
    autopickup_id: str
    todo_id: int
    bucket: int
    status: str          # "approved" | "rejected" | "pending" | "ambiguous"
    signal_source: str   # "reply" | "reaction" (reaction is v1.1)
    signal_text: str     # the matched body text, truncated to 200 chars
    signal_author: str
    signal_comment_id: int | None
    detected_at: str
    feedback_posted: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_file() -> Path:
    APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
    return APPROVAL_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"


def _append_audit(d: ApprovalDetection) -> None:
    try:
        with _audit_file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(d)) + "\n")
    except Exception:
        logger.warning("autopickup_approve: audit write failed", exc_info=True)


def _processed() -> set[str]:
    if not PROCESSED_PATH.exists():
        return set()
    try:
        return set(json.loads(PROCESSED_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_processed(s: set[str]) -> None:
    PROCESSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Cap so the file does not grow without bound; keep the most recent
    # by sort order (autopickup_id includes a uuid suffix so order is
    # approximate but stable enough).
    if len(s) > 10000:
        s = set(sorted(s)[-10000:])
    PROCESSED_PATH.write_text(json.dumps(sorted(s)), encoding="utf-8")


def _write_heartbeat(summary: dict) -> None:
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("autopickup_approve: heartbeat write failed", exc_info=True)


# ── Audit log walk ─────────────────────────────────────────────────


def _load_recent_autopickup_audit() -> list[dict]:
    """Read the last LOOKBACK_DAYS of autopickup_worker audit rows. Each
    row tells us (todo_id, bucket, autopickup_id, comment_id) so we know
    which BC comments to scan for approvals."""
    rows: list[dict] = []
    if not AUTOPICKUP_AUDIT_DIR.exists():
        return rows
    cutoff = datetime.now(timezone.utc).timestamp() - LOOKBACK_DAYS * 86400
    for p in sorted(AUTOPICKUP_AUDIT_DIR.glob("*.jsonl")):
        try:
            file_ts = p.stat().st_mtime
        except OSError:
            continue
        if file_ts < cutoff:
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("status") == "drafted" and row.get("comment_id"):
                    rows.append(row)
        except OSError:
            continue
    return rows


# ── BC helpers ─────────────────────────────────────────────────────


def _fetch_comments(bucket: int, todo_id: int, token: str) -> list[dict]:
    from .sync import _bc_get
    try:
        body = _bc_get(
            f"/buckets/{bucket}/recordings/{todo_id}/comments.json", token, {},
        )
        return body if isinstance(body, list) else []
    except Exception:
        return []


def _post_feedback_comment(bucket: int, todo_id: int, html: str, token: str) -> bool:
    """Optional POST back to BC confirming the approval was recorded."""
    import urllib.error
    import urllib.request
    url = (
        f"https://3.basecampapi.com/3945211/buckets/{bucket}/"
        f"recordings/{todo_id}/comments.json"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps({"content": html}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Colaberry-autopickup-approve/1",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201)
    except Exception:
        return False


# ── Signal detection ───────────────────────────────────────────────


CB_SYSTEM_NAMES = {"cb system", "ali muwwakkil ai", "colaberry library sync"}


def _classify_reply(comment_body: str) -> str:
    """Return 'approved' | 'rejected' | 'ambiguous'. Text is already stripped."""
    body = (comment_body or "").lower()
    has_approve = bool(_APPROVE_RE.search(body))
    has_reject = bool(_REJECT_RE.search(body))
    if has_approve and not has_reject:
        return "approved"
    if has_reject and not has_approve:
        return "rejected"
    if has_approve and has_reject:
        return "ambiguous"
    return "ambiguous"  # neither signal -- treat as needing clarification


def _find_approval(autopickup_id: str, autopickup_comment_id: int,
                                       comments: list[dict]) -> ApprovalDetection | None:
    """Walk the ticket's comments and find a human reply AFTER our
    autopickup comment. Returns the first matching detection, or None
    if no human reply has landed yet."""
    # Sort by created_at ascending so we walk in order
    sorted_comments = sorted(
        comments, key=lambda c: c.get("created_at", ""),
    )
    found_our_marker = False
    for c in sorted_comments:
        cid = c.get("id")
        if cid == autopickup_comment_id:
            found_our_marker = True
            continue
        if not found_our_marker:
            continue
        # This comment is AFTER our autopickup. Is it from a human?
        creator_name = (c.get("creator") or {}).get("name", "")
        if creator_name.strip().lower() in CB_SYSTEM_NAMES:
            continue   # ignore CB / AI personas; only humans count
        body_html = c.get("content") or ""
        body_text = bc_comments._strip_html(body_html).strip()
        if not body_text:
            continue
        status = _classify_reply(body_text)
        return ApprovalDetection(
            autopickup_id=autopickup_id,
            todo_id=0,  # caller fills these in
            bucket=0,
            status=status,
            signal_source="reply",
            signal_text=body_text[:200],
            signal_author=creator_name or "?",
            signal_comment_id=cid,
            detected_at=_now_iso(),
        )
    return None


# ── Public entrypoint ──────────────────────────────────────────────


def scan_for_user(user_email: str) -> dict:
    """Walk the recent autopickup audit, fetch each ticket's comments,
    classify the next-after-autopickup reply, log + (optionally) reply."""
    summary: dict[str, Any] = {
        "started_at": _now_iso(),
        "user_email": user_email,
        "autopickup_rows_scanned": 0,
        "still_pending": 0,
        "newly_approved": 0,
        "newly_rejected": 0,
        "newly_ambiguous": 0,
        "skipped_already_processed": 0,
        "errors": [],
    }

    token, src = tokens.get_user_token(user_email)
    if not token:
        summary["error"] = "no_token"
        summary["token_source"] = src
        return summary

    rows = _load_recent_autopickup_audit()
    summary["autopickup_rows_scanned"] = len(rows)

    processed = _processed()

    for row in rows:
        apid = row.get("autopickup_id") or ""
        if not apid:
            continue
        if apid in processed:
            summary["skipped_already_processed"] += 1
            continue
        bucket = int(row.get("bucket") or 0)
        todo_id = int(row.get("todo_id") or 0)
        cid = int(row.get("comment_id") or 0)
        if not bucket or not todo_id or not cid:
            continue

        comments = _fetch_comments(bucket, todo_id, token)
        if not comments:
            summary["still_pending"] += 1
            continue

        detection = _find_approval(apid, cid, comments)
        if not detection:
            summary["still_pending"] += 1
            continue
        detection.todo_id = todo_id
        detection.bucket = bucket

        # POST feedback comment if enabled and approval recorded
        if POST_FEEDBACK and detection.status == "approved":
            feedback_html = (
                f"<p>✅ <strong>Approval recorded.</strong> autopickup_id "
                f"{apid} marked approved. Phase 2 would now execute the "
                f"proposed action. (Phase 2 is not yet wired; this is "
                f"detection-only for now.)</p>"
                f"<p><em>Detected from: {detection.signal_author}'s "
                f"reply at {detection.detected_at}.</em></p>"
            )
            if _post_feedback_comment(bucket, todo_id, feedback_html, token):
                detection.feedback_posted = True

        _append_audit(detection)
        processed.add(apid)

        if detection.status == "approved":
            summary["newly_approved"] += 1
        elif detection.status == "rejected":
            summary["newly_rejected"] += 1
        else:
            summary["newly_ambiguous"] += 1

    _save_processed(processed)
    summary["finished_at"] = _now_iso()
    _write_heartbeat(summary)
    return summary


def scan_all_users() -> dict:
    """Cron entrypoint. No-op when OPS_AUTOPICKUP_ENABLED is false."""
    if not ENABLED:
        return {"status": "disabled",
                       "hint": "set OPS_AUTOPICKUP_ENABLED=true to enable"}
    # Phase 1 just uses Ali (same as the writer worker).
    phase1 = [
        e.strip()
        for e in (os.environ.get("OPS_AUTOPICKUP_USERS") or "ali@colaberry.com").split(",")
        if e.strip()
    ]
    all_summary = {"started_at": _now_iso(), "users": {}}
    for email in phase1:
        try:
            all_summary["users"][email] = scan_for_user(email)
        except Exception as e:
            logger.warning("autopickup_approve: scan failed for %s", email,
                                          exc_info=True)
            all_summary["users"][email] = {"error": f"{type(e).__name__}: {e}"}
    all_summary["finished_at"] = _now_iso()
    return all_summary
