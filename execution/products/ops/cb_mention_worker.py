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
HEARTBEAT_PATH = PROJECT_ROOT / "output" / "ops" / "_cb_mentions" / "heartbeat.json"
CURSOR_PATH = PROJECT_ROOT / "output" / "ops" / "_cb_mentions" / "cursor.json"
# Wall-clock fallback when a bucket has no cursor yet (first scan, or
# corrupted cursor file). Existing operators may have set this to 60;
# bump in prod once cursors are populated.
MENTION_WINDOW_MINUTES = int(os.environ.get("OPS_CB_MENTION_WINDOW_MINUTES", "60"))
# Hard ceiling on lookback even when the cursor is much older. Prevents
# replaying weeks of BC history if the scheduler was down for a long
# stretch. 7 days = 10080 minutes by default.
MAX_LOOKBACK_MINUTES = int(os.environ.get("OPS_CB_MENTION_MAX_LOOKBACK_MINUTES", "10080"))
# Default raised from 50 → 100. Buckets are sorted by `updated_at` so the
# most active 100 always get scanned; cold buckets rotate out and are
# reported in the heartbeat. With per-bucket cursors, rotated-out buckets
# retain their cursor and catch up on the next tick they're scanned — no
# mentions permanently lost, just higher latency for cold buckets.
MAX_BUCKETS = int(os.environ.get("OPS_CB_MENTION_MAX_BUCKETS", "100"))


def _polling_enabled() -> bool:
    """Read the polling flag at call time (not module-import time) so
    tests can flip via monkeypatch.setenv. Mirrors scheduler.POLLING_ENABLED
    semantics: default ON; only "false" (case-insensitive) disables.
    """
    return os.environ.get(
        "OPS_CB_MENTION_POLLING_ENABLED", "true",
    ).strip().lower() != "false"
# Regex matches "@CB System", "@CB", or "@CBSystem" (case-insensitive).
TRIGGER_RE = re.compile(r"@CB[\s_-]*(System)?", re.IGNORECASE)
# BC API requires the User-Agent to include contact info (an email or URL);
# without it BC can return 403. Used for BOTH GET and POST so detection and
# the auto-response use the same compliant identity.
USER_AGENT = "Advisor CB System auto-response (ali@colaberry.com)"


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


def _load_cursors() -> dict:
    """Read the per-(user, bucket) cursor map.

    Shape: `{"per_user": {"<email>": {"<bucket_id>": "<iso_ts>"}}}`.
    Returns the per_user dict (or empty) — callers don't need the wrapper.
    """
    if not CURSOR_PATH.exists():
        return {}
    try:
        raw = json.loads(CURSOR_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    pu = raw.get("per_user")
    return pu if isinstance(pu, dict) else {}


def _save_cursors(per_user: dict) -> None:
    """Persist cursors atomically(-ish). Best-effort — disk failures must
    not break the cron; the next scan will just fall back to the window."""
    try:
        CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
        CURSOR_PATH.write_text(
            json.dumps({"per_user": per_user}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("cb_mentions: failed to write cursor", exc_info=True)


def _cutoff_for_bucket(
    cursors_for_user: dict, bucket_id: int, now: datetime,
) -> tuple[datetime, str]:
    """Pick the lookback cutoff for one bucket.

    Returns (cutoff_datetime, source_tag) where source_tag is one of:
        'cursor'        — cursor exists and is within MAX_LOOKBACK
        'cursor_capped' — cursor exists but clamped to MAX_LOOKBACK
        'first_scan'    — no cursor, use MENTION_WINDOW_MINUTES window

    The source_tag is just for heartbeat visibility — callers don't
    branch on it.
    """
    max_age = now - timedelta(minutes=MAX_LOOKBACK_MINUTES)
    raw = cursors_for_user.get(str(bucket_id))
    if not raw:
        # First scan for this bucket — fall back to the wall-clock window.
        # On a fresh deploy, MENTION_WINDOW_MINUTES is what catches the
        # mentions newer than X minutes; subsequent scans use the cursor.
        return now - timedelta(minutes=MENTION_WINDOW_MINUTES), "first_scan"
    try:
        cdt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if cdt.tzinfo is None:
            cdt = cdt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return now - timedelta(minutes=MENTION_WINDOW_MINUTES), "first_scan"
    if cdt < max_age:
        return max_age, "cursor_capped"
    return cdt, "cursor"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_heartbeat(summary: dict) -> None:
    """Persist the result of `scan_all_users()` so an admin endpoint can
    answer 'is CB-mention polling alive, did it fail silently?' without
    grepping container logs.

    Heartbeat is best-effort — disk failure here must not break the cron.
    """
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8",
        )
    except OSError:
        logger.warning("cb_mentions: failed to write heartbeat", exc_info=True)


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
    """Format a rubric-conformant BC comment from a plan_inference result.

    Same prompt-embed pattern as autopickup_worker._render_comment: when
    plan carries a claude_code_prompt, embed it in a <details> block at
    the bottom so the reader can copy + paste into Claude Code without
    leaving BC. Wrapping in <details> keeps the default comment view
    short.
    """
    if not plan:
        return (
            "<p>@CB System couldn't build a plan for this mention "
            "(LLM unavailable). Posting nothing. Try again later.</p>"
        )
    parts = [
        "<p><strong>CB System: automated response</strong></p>",
        f"<p><strong>Anticipated goal:</strong> {plan.get('anticipated_goal', '?')}</p>",
    ]
    if plan.get("summary_paragraph"):
        parts.append(f"<p>{plan['summary_paragraph']}</p>")
    if plan.get("execution_plan"):
        parts.append("<p><strong>Proposed plan:</strong></p><ol>")
        for s in plan["execution_plan"]:
            est = f" : ~{s.get('estimated_minutes')}m" if s.get("estimated_minutes") else ""
            parts.append(f"<li>{s.get('action','?')}{est}</li>")
        parts.append("</ol>")
    if plan.get("missing_information"):
        parts.append("<p><strong>Missing info (drops confidence):</strong></p><ul>")
        for m in plan["missing_information"]:
            parts.append(f"<li>{m}</li>")
        parts.append("</ul>")

    # Embed the paste-ready Claude Code prompt, if plan_inference produced
    # one. <details> keeps the comment short by default; reader expands
    # when they want the prompt.
    cc_prompt = (plan.get("claude_code_prompt") or "").strip()
    if cc_prompt:
        import html as _htmllib
        escaped = _htmllib.escape(cc_prompt)
        parts.append(
            "<details><summary><strong>Claude Code prompt</strong> "
            "(click to expand and copy)</summary>"
            f"<pre style='white-space: pre-wrap; word-break: break-word; "
            f"background: #f6f8fa; padding: 10px; border-radius: 6px; "
            f"font-size: 12px;'>{escaped}</pre></details>"
        )

    parts.append(
        f"<p>Confidence: <strong>{plan.get('confidence_pct', 0)}%</strong>. "
        f"Output type: <code>{plan.get('inferred_output_type', '?')}</code>. "
        f"Approve to execute, reply with corrections, "
        f"or copy the prompt above into Claude Code to act now.</p>"
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


SENTINEL_HTML = (
    "<p><em>CB System saw this @-mention but couldn't post a full reply "
    "(token, validation, or BC rate-limit). Paging Ali — check "
    "<code>/admin/cb-mentions.json</code> for the heartbeat.</em></p>"
)


def _post_comment(
    bucket: int,
    recording_id: int,
    html_content: str,
    token: str,
) -> tuple[bool, str]:
    """POST a comment via the user's BC OAuth token.

    Returns (ok, detail). `detail` is a short tag for the heartbeat:
        "ok"          — 200/201 from BC
        "http_<code>" — BC returned a non-2xx
        "error_<type>"— network/other exception
    """
    url = (
        f"https://3.basecampapi.com/3945211/buckets/{bucket}/"
        f"recordings/{recording_id}/comments.json"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps({"content": html_content}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status in (200, 201):
                return True, "ok"
            return False, f"http_{r.status}"
    except urllib.error.HTTPError as e:
        logger.warning("CB-mention post failed: HTTP %s on bucket=%s rec=%s",
                       e.code, bucket, recording_id)
        return False, f"http_{e.code}"
    except Exception as e:
        logger.warning("CB-mention post failed: %s", type(e).__name__, exc_info=True)
        return False, f"error_{type(e).__name__}"


def scan_for_user(user_id: str, max_buckets: int | None = None) -> dict:
    """Scan all buckets visible to the user's token; auto-respond to new
    @CB mentions. Returns a summary dict.
    """
    if max_buckets is None:
        max_buckets = MAX_BUCKETS
    token, src = tokens.get_user_token(user_id)
    if not token:
        # WARNING (not INFO) so uvicorn's default config surfaces this in
        # container logs — silent no_token was the dominant failure mode.
        logger.warning("cb_mentions: no BC token for user=%s; skipping scan", user_id)
        return {
            "status": "no_token", "checked_buckets": 0, "mentions_found": 0,
            "responded": 0, "failed": 0, "skipped_already_seen": 0,
            "skipped_closed_parent": 0, "token_source": src, "errors": [],
        }

    from .sync import discover_projects
    all_projects = discover_projects(token)
    # Sort by `updated_at` descending so the most-active buckets always
    # rank first. With cursors in place (see cb_mention_worker), rotated-
    # out buckets keep their cursor and catch up next time they're scanned;
    # they don't permanently lose mentions, just see higher latency.
    # Missing updated_at sorts to the bottom — projects with no signal
    # haven't seen activity in a long time.
    all_projects = sorted(
        all_projects,
        key=lambda p: p.get("updated_at") or "",
        reverse=True,
    )
    truncated = max(0, len(all_projects) - max_buckets)
    rotated_out_buckets: list[dict] = []
    if truncated:
        # Visibility into the cap — when this is non-zero, a mention in a
        # rotated-out bucket has latency = (#scan ticks until it rotates
        # back into the top max_buckets).
        rotated = all_projects[max_buckets:]
        rotated_out_buckets = [
            {"id": p.get("id"), "name": p.get("name", "?"),
             "updated_at": p.get("updated_at", "")}
            for p in rotated[:25]  # cap log payload size
        ]
        logger.warning(
            "cb_mentions: user=%s has %d buckets > cap=%d; rotated out "
            "(oldest activity first): %s",
            user_id, len(all_projects), max_buckets,
            ", ".join(f"{b['id']}({b['updated_at'][:10]})"
                      for b in rotated_out_buckets[:5]),
        )
    projects = all_projects[:max_buckets]
    # Per-bucket cursor. The scan-start time becomes the new cursor for
    # every bucket we successfully scan; on the NEXT tick we look back
    # only as far as this scan started, so a mention created during
    # this scan still gets picked up next time.
    scan_started_at = datetime.now(timezone.utc)
    cursors = _load_cursors()
    cursors_for_user = dict(cursors.get(user_id) or {})
    cutoff_sources: dict[str, int] = {"cursor": 0, "cursor_capped": 0,
                                       "first_scan": 0}
    seen = _seen()
    found = 0
    responded = 0
    skipped_seen = 0
    failed = 0
    skipped_closed = 0
    errors: list[dict] = []

    for proj in projects:
        bucket = proj.get("id")
        if not bucket:
            continue
        cutoff, source = _cutoff_for_bucket(cursors_for_user, bucket,
                                             scan_started_at)
        cutoff_sources[source] = cutoff_sources.get(source, 0) + 1
        mentions = _scan_bucket_for_mentions(bucket, token, cutoff)
        # Advance the cursor only if the scan didn't blow up. If
        # _scan_bucket_for_mentions raises (caught internally to return
        # []), we'd miss the chance to retry — so we conservatively
        # advance on []-or-list returns.
        cursors_for_user[str(bucket)] = scan_started_at.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
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
                user_feedback = m["comment_body"]
                plan = plan_inference.infer(
                    user_feedback=user_feedback,
                    basecamp_url=m["parent_url"],
                    output_type="",
                    success_criteria="",
                    context_bundle=bundle,
                )
                html = _build_response_text(plan)
                ok, detail = _post_comment(bucket, m["parent_id"], html, token)
                if ok:
                    responded += 1
                else:
                    failed += 1
                    errors.append({
                        "parent_url": m["parent_url"], "comment_id": m["comment_id"],
                        "stage": "post_comment", "detail": detail,
                    })
                    # Sentinel footprint so the human asker sees CB tried
                    # even if the rich response failed. Same token, same
                    # endpoint, simpler body — covers body-validation
                    # failures while staying inert under auth failures.
                    sentinel_ok, sentinel_detail = _post_comment(
                        bucket, m["parent_id"], SENTINEL_HTML, token,
                    )
                    if not sentinel_ok:
                        errors.append({
                            "parent_url": m["parent_url"],
                            "comment_id": m["comment_id"],
                            "stage": "sentinel_comment", "detail": sentinel_detail,
                        })
            except Exception as e:
                logger.warning("CB mention handling failed for %s: %s",
                               m["parent_url"], type(e).__name__, exc_info=True)
                failed += 1
                errors.append({
                    "parent_url": m["parent_url"], "comment_id": m["comment_id"],
                    "stage": "plan_inference_or_collect",
                    "detail": f"error_{type(e).__name__}",
                })

    _save_seen(seen)
    # Persist this user's cursor map back to disk. Other users' cursors
    # are untouched (we copied them into `cursors_for_user` then write a
    # fresh top-level dict so concurrent writes from another scan_for_user
    # call don't clobber each other under the GIL).
    cursors[user_id] = cursors_for_user
    _save_cursors(cursors)
    return {
        "status": "ok",
        "checked_buckets": len(projects),
        "buckets_truncated": truncated,
        "buckets_rotated_out": rotated_out_buckets,
        "mentions_found": found,
        "responded": responded,
        "skipped_already_seen": skipped_seen,
        "skipped_closed_parent": skipped_closed,
        "failed": failed,
        "token_source": src,
        "errors": errors,
        "cutoff_sources": cutoff_sources,
    }


def scan_all_users() -> dict:
    """Top-level entry called by the scheduler. Walks every user with a
    vault token (same set the sync scheduler hits) and writes a heartbeat
    summary so `/admin/cb-mentions.json` can answer 'is CB alive?'.

    Returns the heartbeat dict (also persisted to HEARTBEAT_PATH).
    """
    if not _polling_enabled():
        summary = {
            "started_at": _now_iso(),
            "finished_at": _now_iso(),
            "skipped": True,
            "reason": "polling_disabled",
            "users_with_token": 0, "total_mentions_found": 0,
            "total_responded": 0, "total_failed": 0,
            "fatal_error": None, "per_user": [],
        }
        _write_heartbeat(summary)
        return summary

    from execution.products.library import tenancy, vault

    started_at = _now_iso()
    per_user: list[dict] = []
    fatal_error: str | None = None

    try:
        users = tenancy.list_users(active_only=True)
    except Exception as e:
        fatal_error = f"list_users_failed:{type(e).__name__}"
        users = []
        logger.warning("cb_mentions: tenancy.list_users failed", exc_info=True)

    for u in users:
        try:
            has_token = any(
                c.tool_name == "basecamp_ai_clone"
                for c in vault.list_for_user(u.user_id, caller_id="cb_mention_cron")
            )
        except Exception:
            has_token = False
            logger.warning("cb_mentions: vault.list_for_user failed for %s",
                           u.email, exc_info=True)
        if not has_token:
            continue
        try:
            r = scan_for_user(u.email)
            r_record = {"user_id": u.user_id, "email": u.email, **r}
            per_user.append(r_record)
            logger.info("cb_mentions for %s: %s", u.email, r)
        except Exception as e:
            per_user.append({
                "user_id": u.user_id, "email": u.email,
                "status": "exception", "error": f"{type(e).__name__}",
            })
            logger.warning("cb_mentions failed for %s", u.email, exc_info=True)

    finished_at = _now_iso()
    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "users_with_token": len(per_user),
        "total_mentions_found": sum(p.get("mentions_found", 0) for p in per_user),
        "total_responded": sum(p.get("responded", 0) for p in per_user),
        "total_failed": sum(p.get("failed", 0) for p in per_user),
        "fatal_error": fatal_error,
        "per_user": per_user,
    }
    _write_heartbeat(summary)
    return summary
