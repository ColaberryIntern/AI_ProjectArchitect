"""Nightly smoke test for the @CB mention auto-response pipeline.

Closes the visibility gap PR #2 left open: heartbeat + sentinel + WARNING
logs surface failures *if* the mention is detected, but say nothing about
classes of bug we haven't yet thought of — the trigger regex regressing
after a refactor, BC shipping a breaking API change, the user's OAuth
refresh token rotating without notice.

This module:
  1. Posts a known `@CB smoke ping <marker>` comment on a fixed BC todo.
  2. Sleeps `OPS_CB_SMOKE_TIMEOUT_MINUTES` (default 15).
  3. Checks the same todo for any reply created after the ping.
  4. If no reply, pages via the configured ops_platform notification
     channel — or, lacking one, logs at WARNING so container logs still
     surface the failure.

Configuration (all env-driven so non-prod environments don't run it):
  OPS_CB_SMOKE_BUCKET_ID       — int, BC bucket the smoke-test todo lives in
  OPS_CB_SMOKE_TODO_ID         — int, BC todo / recording id to ping
  OPS_CB_SMOKE_USER_EMAIL      — defaults to ali@colaberry.com (whose
                                 BC OAuth token is used for ping + verify)
  OPS_CB_SMOKE_TIMEOUT_MINUTES — defaults to 15
  OPS_CB_SMOKE_ALERT_CHANNEL   — ops_platform.notifications channel_id
                                 (optional; falls back to WARNING log)

`is_configured()` is the gate — when the three required env vars aren't
set, `run()` is a no-op. This is deliberate: shipping the code with no
hardcoded BC IDs lets us land it before the fixture todo exists in BC.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import PROJECT_ROOT

from . import cb_mention_worker, tokens

logger = logging.getLogger(__name__)

STATE_PATH = PROJECT_ROOT / "output" / "ops" / "_cb_mentions" / "smoke_history.jsonl"
DEFAULT_TIMEOUT_MINUTES = 15
DEFAULT_USER_EMAIL = "ali@colaberry.com"


# ── configuration ──────────────────────────────────────────────────


def _bucket_id() -> Optional[int]:
    v = os.environ.get("OPS_CB_SMOKE_BUCKET_ID", "").strip()
    try:
        return int(v) if v else None
    except ValueError:
        return None


def _todo_id() -> Optional[int]:
    v = os.environ.get("OPS_CB_SMOKE_TODO_ID", "").strip()
    try:
        return int(v) if v else None
    except ValueError:
        return None


def _user_email() -> str:
    return os.environ.get("OPS_CB_SMOKE_USER_EMAIL", DEFAULT_USER_EMAIL).strip() \
        or DEFAULT_USER_EMAIL


def _timeout_minutes() -> int:
    try:
        return int(os.environ.get("OPS_CB_SMOKE_TIMEOUT_MINUTES",
                                  str(DEFAULT_TIMEOUT_MINUTES)))
    except ValueError:
        return DEFAULT_TIMEOUT_MINUTES


def _alert_channel() -> Optional[str]:
    v = os.environ.get("OPS_CB_SMOKE_ALERT_CHANNEL", "").strip()
    return v or None


def is_configured() -> bool:
    """True iff we have enough config to run a smoke check."""
    return _bucket_id() is not None and _todo_id() is not None


# ── core operations ────────────────────────────────────────────────


def _new_marker() -> str:
    """Short unique marker so verify() can prove the reply is *for this run*,
    not a stale reply from a previous run that happened to land late.
    """
    return f"smoke-{uuid.uuid4().hex[:10]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(event: dict) -> None:
    """Append an event to the smoke history log so we can chart pass/fail
    over time. Best-effort — disk error must not break the cron."""
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STATE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        logger.warning("cb_smoke: failed to append history", exc_info=True)


def ping(marker: Optional[str] = None) -> dict:
    """Post a `@CB smoke ping <marker>` comment on the configured smoke todo.

    Returns `{ok, marker, posted_at, detail, bucket_id, todo_id}`. `detail`
    is the per-`_post_comment` short tag (`ok`, `http_<n>`, `error_<type>`).
    """
    bucket = _bucket_id()
    todo = _todo_id()
    if bucket is None or todo is None:
        return {"ok": False, "detail": "not_configured",
                "marker": None, "posted_at": None,
                "bucket_id": bucket, "todo_id": todo}

    user_email = _user_email()
    token, src = tokens.get_user_token(user_email)
    if not token:
        return {"ok": False, "detail": f"no_token:{src}",
                "marker": None, "posted_at": None,
                "bucket_id": bucket, "todo_id": todo,
                "user_email": user_email}

    marker = marker or _new_marker()
    posted_at = _now_iso()
    html = (
        f"<p>@CB smoke ping <code>{marker}</code> at "
        f"<time datetime=\"{posted_at}\">{posted_at}</time>. "
        f"Auto-issued by cb_smoke; ignore unless you're debugging.</p>"
    )
    ok, detail = cb_mention_worker._post_comment(bucket, todo, html, token)
    return {
        "ok": ok, "detail": detail, "marker": marker, "posted_at": posted_at,
        "bucket_id": bucket, "todo_id": todo, "user_email": user_email,
        "token_source": src,
    }


def verify(after_iso: str, marker: str, *,
           ping_user_email: Optional[str] = None) -> dict:
    """Look for any comment on the smoke todo created strictly after
    `after_iso` whose body does NOT come from the ping author. Matches
    the marker in body for paranoia — a CB reply that's actually for a
    different ping (latency >24h) doesn't count.

    Returns `{ok, found_reply, latency_seconds, replier, reply_excerpt}`.
    """
    bucket = _bucket_id()
    todo = _todo_id()
    if bucket is None or todo is None:
        return {"ok": False, "found_reply": False, "detail": "not_configured"}

    user_email = ping_user_email or _user_email()
    token, src = tokens.get_user_token(user_email)
    if not token:
        return {"ok": False, "found_reply": False,
                "detail": f"no_token:{src}"}

    url = (
        f"https://3.basecampapi.com/3945211/buckets/{bucket}/"
        f"recordings/{todo}/comments.json"
    )
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}",
                 "User-Agent": cb_mention_worker.USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            comments = json.load(r)
    except urllib.error.HTTPError as e:
        return {"ok": False, "found_reply": False,
                "detail": f"http_{e.code}"}
    except Exception as e:
        return {"ok": False, "found_reply": False,
                "detail": f"error_{type(e).__name__}"}

    if not isinstance(comments, list):
        return {"ok": False, "found_reply": False, "detail": "bad_response"}

    try:
        ping_dt = datetime.fromisoformat(after_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return {"ok": False, "found_reply": False,
                "detail": "bad_after_iso"}

    for c in comments:
        try:
            created = c.get("created_at") or ""
            cdt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if cdt <= ping_dt:
                continue
        except (ValueError, TypeError):
            continue
        body = cb_mention_worker._strip_html(c.get("content") or "")
        # The marker is in the *ping* body, not the reply — so a CB reply
        # that quotes the marker is a strong signal. We also accept any
        # reply from a different author (the ping is from `user_email`'s
        # token, replies come from CB System or a human).
        creator = (c.get("creator") or {})
        creator_email = (creator.get("email_address") or "").lower()
        is_ping_author = creator_email == user_email.lower()
        if is_ping_author and marker not in body:
            continue
        if is_ping_author:
            # This IS the ping comment we just posted — skip it.
            continue
        latency = (cdt - ping_dt).total_seconds()
        return {
            "ok": True, "found_reply": True, "latency_seconds": latency,
            "replier": creator.get("name") or "?",
            "replier_email": creator_email,
            "reply_excerpt": body[:200],
            "marker_in_reply": marker in body,
            "detail": "ok",
        }

    return {"ok": False, "found_reply": False, "detail": "timeout"}


def _alert(title: str, body: str) -> None:
    """Send a high-visibility alert. Uses ops_platform.notifications if a
    channel is configured; otherwise emits a WARNING that container logs
    will surface."""
    ch = _alert_channel()
    if not ch:
        logger.warning("cb_smoke ALERT: %s — %s", title, body)
        return
    try:
        from execution.ops_platform import notifications as ops_notif
        ops_notif.send(ch, title=title, body=body, correlation_id="cb_smoke")
    except Exception:
        logger.warning("cb_smoke: alert via channel %s failed", ch, exc_info=True)
        logger.warning("cb_smoke ALERT (fallback): %s — %s", title, body)


def run() -> dict:
    """Scheduler entry point — ping → wait → verify → page on failure.

    Returns a summary dict that's also appended to `smoke_history.jsonl`.
    """
    started_at = _now_iso()
    if not is_configured():
        summary = {"started_at": started_at, "ok": False,
                   "skipped": True, "reason": "not_configured"}
        logger.info("cb_smoke: skipped — bucket/todo env vars unset")
        return summary

    p = ping()
    if not p.get("ok"):
        summary = {"started_at": started_at, "stage": "ping",
                   "ok": False, "ping": p, "verify": None}
        _alert(
            "CB smoke: PING FAILED",
            f"Couldn't post the smoke-test ping to BC. detail={p.get('detail')}. "
            f"This means CB's outbound posts are broken, not the trigger regex. "
            f"Check /admin/cb-mentions.json for the heartbeat.",
        )
        _record(summary)
        return summary

    timeout_min = _timeout_minutes()
    logger.info("cb_smoke: ping posted (marker=%s); waiting %d min for reply",
                p["marker"], timeout_min)
    time.sleep(timeout_min * 60)

    v = verify(p["posted_at"], p["marker"], ping_user_email=p.get("user_email"))
    summary = {
        "started_at": started_at,
        "finished_at": _now_iso(),
        "timeout_minutes": timeout_min,
        "ping": p,
        "verify": v,
        "ok": bool(v.get("found_reply")),
    }
    _record(summary)

    if not v.get("found_reply"):
        _alert(
            "CB smoke: NO REPLY",
            f"CB did not respond to a smoke ping within {timeout_min} minutes "
            f"(marker={p['marker']}, posted_at={p['posted_at']}, "
            f"verify_detail={v.get('detail')}). "
            f"Check /admin/cb-mentions.json — the pipeline may be silently "
            f"broken in a way the heartbeat alone can't detect.",
        )
    return summary
