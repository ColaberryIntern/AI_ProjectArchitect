"""@CB System mention polling worker.

Periodically scans recent BC events for tickets where CB System is
mentioned (`@CB System` in comments or the ticket body). For each new
mention, kicks off the Magic Input flow + posts a rubric-conformant
response as a BC comment on the originating ticket.

Idempotency: tracks seen mention IDs in
output/ops/_cb_mentions/seen.json so we never auto-respond twice to
the same comment. After processing, we mark the mention id as seen
regardless of whether the response succeeded (avoids tight retry loops
on persistent errors — they get re-tried only on the next sync after
manual cache clear).

Detection: BC's `/projects/recordings.json?type=Comment` returns
recent comments across the bucket. We pull last 50, filter to those
created in the last MENTION_WINDOW_MINUTES, and substring-match for
the trigger pattern.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import PROJECT_ROOT

from . import bc_comments, context_collector, plan_inference, tokens

logger = logging.getLogger(__name__)

SEEN_PATH = PROJECT_ROOT / "output" / "ops" / "_cb_mentions" / "seen.json"
MENTION_WINDOW_MINUTES = int(os.environ.get("OPS_CB_MENTION_WINDOW_MINUTES", "60"))
# Regex matches "@CB System", "@CB", or "@CBSystem" (case-insensitive).
TRIGGER_RE = re.compile(r"@CB[\s_-]*(System)?", re.IGNORECASE)


def _seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    try:
        return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_seen(s: set[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(sorted(s))[:200_000], encoding="utf-8")


def _strip_html(html: str) -> str:
    return bc_comments._strip_html(html) if html else ""


def _scan_bucket_for_mentions(bucket: int, token: str, cutoff: datetime) -> list[dict]:
    """Pull recent comments in a bucket; return ones mentioning @CB."""
    matches: list[dict] = []
    # BC recordings endpoint filtered to comments; sorted recent first.
    from .sync import _bc_get
    try:
        recs = _bc_get(
            f"/projects/recordings.json",
            token,
            {"type": "Comment", "bucket": str(bucket)},
        )
    except Exception:
        return matches
    if not isinstance(recs, list):
        return matches
    for r in recs[:50]:
        try:
            created_at = r.get("created_at") or ""
            cdt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if cdt.tzinfo is None:
                cdt = cdt.replace(tzinfo=timezone.utc)
            if cdt < cutoff:
                continue
        except (ValueError, TypeError):
            continue
        body = _strip_html(r.get("content") or "")
        if not TRIGGER_RE.search(body):
            continue
        # Find parent (the ticket/todo the comment is on)
        parent = r.get("parent") or {}
        parent_url = parent.get("app_url") or ""
        if not parent_url:
            continue
        matches.append({
            "comment_id": r.get("id"),
            "comment_url": r.get("app_url"),
            "comment_body": body,
            "created_at": created_at,
            "creator_name": (r.get("creator") or {}).get("name", "?"),
            "parent_url": parent_url,
            "parent_id": parent.get("id"),
            "bucket": bucket,
        })
    return matches


def _build_response_text(plan: dict) -> str:
    """Format a rubric-conformant BC comment from a plan_inference result."""
    if not plan:
        return (
            "<p>@CB System couldn't build a plan for this mention "
            "(LLM unavailable). Posting nothing — try again later.</p>"
        )
    parts = [
        "<p><strong>CB System · automated response</strong></p>",
        f"<p><strong>Anticipated goal:</strong> {plan.get('anticipated_goal', '?')}</p>",
    ]
    if plan.get("summary_paragraph"):
        parts.append(f"<p>{plan['summary_paragraph']}</p>")
    if plan.get("execution_plan"):
        parts.append("<p><strong>Proposed plan:</strong></p><ol>")
        for s in plan["execution_plan"]:
            est = f" · ~{s.get('estimated_minutes')}m" if s.get("estimated_minutes") else ""
            parts.append(f"<li>{s.get('action','?')}{est}</li>")
        parts.append("</ol>")
    if plan.get("missing_information"):
        parts.append("<p><strong>Missing info (drops confidence):</strong></p><ul>")
        for m in plan["missing_information"]:
            parts.append(f"<li>{m}</li>")
        parts.append("</ul>")
    parts.append(
        f"<p>Confidence: <strong>{plan.get('confidence_pct', 0)}%</strong>. "
        f"Output type: <code>{plan.get('inferred_output_type', '?')}</code>. "
        f"Approve to execute, or reply with corrections.</p>"
    )
    return "".join(parts)


def _parent_is_closed(bucket: int, todo_id: int, token: str) -> bool:
    """Return True if the parent todo is already completed in BC.

    Closed tickets should NOT receive new auto-responses — user feedback:
    'If your queue is re-firing the prompt for tickets already closed,
    it's worth flagging that to whatever is queueing — closed tickets
    shouldn't generate new prompts.'
    """
    from .sync import _bc_get
    try:
        todo = _bc_get(f"/buckets/{bucket}/todos/{todo_id}.json", token)
    except Exception:
        return False
    if not todo:
        return False
    return bool(todo.get("completed"))


def _post_comment(bucket: int, recording_id: int, html_content: str, token: str) -> bool:
    """POST a comment as the AI clone identity."""
    url = (
        f"https://3.basecampapi.com/3945211/buckets/{bucket}/"
        f"recordings/{recording_id}/comments.json"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps({"content": html_content}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Advisor CB System (auto-response)",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201)
    except urllib.error.HTTPError as e:
        logger.warning("CB-mention post failed: HTTP %s", e.code)
        return False
    except Exception:
        logger.warning("CB-mention post failed", exc_info=True)
        return False


def scan_for_user(user_id: str, max_buckets: int = 50) -> dict:
    """Scan all buckets visible to the user's token; auto-respond to new
    @CB mentions. Returns a summary dict.
    """
    token, src = tokens.get_user_token(user_id)
    if not token:
        return {"status": "no_token", "checked_buckets": 0, "mentions": 0, "responded": 0}

    from .sync import discover_projects
    projects = discover_projects(token)[:max_buckets]
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=MENTION_WINDOW_MINUTES)
    seen = _seen()
    found = 0
    responded = 0
    skipped_seen = 0
    failed = 0

    skipped_closed = 0

    for proj in projects:
        bucket = proj.get("id")
        if not bucket:
            continue
        mentions = _scan_bucket_for_mentions(bucket, token, cutoff)
        for m in mentions:
            found += 1
            key = f"comment:{m['comment_id']}"
            if key in seen:
                skipped_seen += 1
                continue
            # Skip closed tickets BEFORE we burn an LLM call + post a comment
            # the user already considered resolved. Mark as seen anyway so
            # we don't re-check on every scan.
            parent_id = m.get("parent_id")
            if parent_id and _parent_is_closed(bucket, parent_id, token):
                logger.info(
                    "cb-mention: parent todo %s is completed; skipping auto-response",
                    parent_id,
                )
                seen.add(key)
                skipped_closed += 1
                continue
            seen.add(key)
            # Crawl the parent ticket so the plan has real context
            try:
                bundle = context_collector.collect(m["parent_url"], token)
                # Use the comment's body as the user-facing "what I need"
                # (caller will often have written something like:
                # "@CB System turn this into a PowerPoint")
                user_feedback = m["comment_body"]
                plan = plan_inference.infer(
                    user_feedback=user_feedback,
                    basecamp_url=m["parent_url"],
                    output_type="",
                    success_criteria="",
                    context_bundle=bundle,
                )
                html = _build_response_text(plan)
                ok = _post_comment(bucket, m["parent_id"], html, token)
                if ok:
                    responded += 1
                else:
                    failed += 1
            except Exception:
                logger.warning("CB mention handling failed for %s", m["parent_url"], exc_info=True)
                failed += 1

    _save_seen(seen)
    return {
        "status": "ok",
        "checked_buckets": len(projects),
        "mentions_found": found,
        "responded": responded,
        "skipped_already_seen": skipped_seen,
        "skipped_closed_parent": skipped_closed,
        "failed": failed,
        "token_source": src,
    }


def scan_all_users() -> None:
    """Top-level entry called by the scheduler. Walks every user with a
    vault token (same set the sync scheduler hits).
    """
    from execution.products.library import tenancy, vault
    try:
        users = tenancy.list_users(active_only=True)
    except Exception:
        return
    for u in users:
        try:
            has_token = any(
                c.tool_name == "basecamp_ai_clone"
                for c in vault.list_for_user(u.user_id, caller_id="cb_mention_cron")
            )
        except Exception:
            has_token = False
        if not has_token:
            continue
        try:
            r = scan_for_user(u.email)
            logger.info("cb_mentions for %s: %s", u.email, r)
        except Exception:
            logger.warning("cb_mentions failed for %s", u.email, exc_info=True)
