"""Basecamp webhook subscription + event handling.

Eliminates the 10-minute polling latency floor of cb_mention_worker by
subscribing to BC's `comment.created` event per bucket. When a comment
lands on any subscribed bucket, BC POSTs to our webhook endpoint
within seconds — no waiting for the next scheduler tick.

This module covers the *outbound* side (subscribing) and the *inbound*
side's pure-Python core (handle_event). The HTTP endpoint that BC posts
to lives in app/routers/basecamp_webhook.py — it calls handle_event()
after verifying the shared-secret path segment.

Strategy: poll AND webhook run in parallel during rollout. Both paths
share seen.json so a webhook-handled mention won't be re-handled by the
next polling tick. Once heartbeat shows webhook events landing reliably,
we can flip OPS_CB_MENTION_INTERVAL_MINUTES way up (or to 0) to cut the
polling cost.

Auth: Basecamp does NOT sign webhook payloads (no HMAC). We use a
shared-secret in the URL path (`/webhooks/basecamp/<secret>`) — only
the BC subscription with that secret can hit our endpoint. Rotate by
re-running subscribe_user_buckets() with a new OPS_CB_WEBHOOK_SECRET
(which re-creates subscriptions with the new URL); old subscriptions
become inert.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from config.settings import PROJECT_ROOT

from . import cb_mention_worker, tokens

logger = logging.getLogger(__name__)

SUBS_PATH = PROJECT_ROOT / "output" / "ops" / "_cb_mentions" / "webhook_subs.json"
EVENT_LOG_PATH = PROJECT_ROOT / "output" / "ops" / "_cb_mentions" / "webhook_events.jsonl"
BC_API_BASE = "https://3.basecampapi.com/3945211"


# ── configuration ──────────────────────────────────────────────────


def webhook_secret() -> Optional[str]:
    """The shared-secret path segment. Required — if unset, the HTTP
    endpoint refuses all events and subscription is a no-op. We do NOT
    fall back to an empty string; an unauthenticated webhook URL would
    let anyone trigger plan_inference calls on any BC todo."""
    v = os.environ.get("OPS_CB_WEBHOOK_SECRET", "").strip()
    return v or None


def webhook_base_url() -> str:
    return os.environ.get("OPS_CB_WEBHOOK_BASE_URL",
                          "https://advisor.colaberry.ai").rstrip("/")


def payload_url() -> Optional[str]:
    s = webhook_secret()
    if not s:
        return None
    return f"{webhook_base_url()}/webhooks/basecamp/{s}"


# ── subscription state ────────────────────────────────────────────


def _load_subs() -> dict:
    """`{"<user_email>": {"<bucket_id>": <webhook_id>}}`"""
    if not SUBS_PATH.exists():
        return {}
    try:
        raw = json.loads(SUBS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_subs(subs: dict) -> None:
    try:
        SUBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUBS_PATH.write_text(json.dumps(subs, indent=2, sort_keys=True),
                             encoding="utf-8")
    except OSError:
        logger.warning("cb_webhooks: failed to save subs", exc_info=True)


# ── outbound: subscribe ────────────────────────────────────────────


def _create_webhook(bucket: int, url: str, token: str) -> tuple[Optional[int], str]:
    """POST a webhook subscription to BC. Returns (webhook_id, detail).

    `types: ["Comment"]` means we only get notified on comment events,
    not every todo/message/checkin. This is what we want — CB only acts
    on comments anyway.
    """
    api = f"{BC_API_BASE}/buckets/{bucket}/webhooks.json"
    payload = json.dumps({"payload_url": url, "types": ["Comment"]}).encode("utf-8")
    req = urllib.request.Request(
        api, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": cb_mention_worker.USER_AGENT,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read()
            obj = json.loads(body) if body else {}
            wid = obj.get("id")
            if wid:
                return int(wid), "ok"
            return None, "no_id_in_response"
    except urllib.error.HTTPError as e:
        return None, f"http_{e.code}"
    except Exception as e:
        return None, f"error_{type(e).__name__}"


def subscribe_user_buckets(user_email: str,
                           max_buckets: Optional[int] = None) -> dict:
    """Idempotently subscribe webhooks for every bucket the user can see.

    Buckets already in `webhook_subs.json` are skipped (BC doesn't dedupe
    subscriptions — we'd get duplicate POSTs per comment if we re-subscribed).
    Returns `{users, buckets_existing, buckets_added, failed, errors}`.

    No-op when webhook_secret() is unset — refuses to create subscriptions
    that point at an unauthenticated URL.
    """
    if not webhook_secret():
        logger.warning("cb_webhooks: OPS_CB_WEBHOOK_SECRET unset — "
                       "refusing to subscribe (would create unauthenticated URL)")
        return {"status": "no_secret", "buckets_added": 0,
                "buckets_existing": 0, "failed": 0, "errors": []}

    token, src = tokens.get_user_token(user_email)
    if not token:
        return {"status": f"no_token:{src}", "buckets_added": 0,
                "buckets_existing": 0, "failed": 0, "errors": []}

    from .sync import discover_projects
    projects = discover_projects(token)
    if max_buckets is not None:
        projects = projects[:max_buckets]

    subs = _load_subs()
    user_subs = dict(subs.get(user_email) or {})

    added = 0
    existing = 0
    failed = 0
    errors: list[dict] = []
    url = payload_url()
    assert url, "webhook_secret() was non-empty above"

    for proj in projects:
        bucket = proj.get("id")
        if not bucket:
            continue
        bucket_key = str(bucket)
        if bucket_key in user_subs:
            existing += 1
            continue
        wid, detail = _create_webhook(bucket, url, token)
        if wid is not None:
            user_subs[bucket_key] = wid
            added += 1
        else:
            failed += 1
            errors.append({"bucket": bucket, "detail": detail})

    subs[user_email] = user_subs
    _save_subs(subs)
    return {
        "status": "ok", "user_email": user_email,
        "token_source": src,
        "buckets_added": added,
        "buckets_existing": existing,
        "failed": failed,
        "errors": errors,
    }


# ── inbound: handle a BC webhook event ────────────────────────────


def _log_event(record: dict) -> None:
    """Append-only event log for ops visibility. Caps line length to keep
    a misbehaving BC payload from blowing up the file."""
    try:
        EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record)[:8000]
        with EVENT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        logger.warning("cb_webhooks: failed to append event log", exc_info=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def handle_event(payload: dict, *, user_email: str) -> dict:
    """Process a BC webhook event. Returns a summary dict for telemetry.

    BC's payload shape (https://github.com/basecamp/bc3-api/blob/master/sections/webhooks.md):
        {
          "id": <event_id>,
          "kind": "comment_created" | ...,
          "recording": {
            "id": <comment_id>, "type": "Comment", "content": "...",
            "parent": {"id": ..., "type": "Todo", "app_url": "..."},
            "bucket": {"id": ...}, ...
          }
        }

    We only act on Comment recordings whose content matches TRIGGER_RE
    and whose parent isn't already closed. seen.json is shared with the
    polling path so dual-running doesn't double-respond.
    """
    received_at = _now_iso()
    rec = payload.get("recording") or {}
    rec_type = rec.get("type") or ""

    if rec_type != "Comment":
        result = {"received_at": received_at, "skipped": "non_comment",
                  "recording_type": rec_type}
        _log_event(result)
        return result

    comment_id = rec.get("id")
    body = cb_mention_worker._strip_html(rec.get("content") or "")
    if not cb_mention_worker.TRIGGER_RE.search(body):
        result = {"received_at": received_at, "skipped": "no_trigger",
                  "comment_id": comment_id}
        _log_event(result)
        return result

    parent = rec.get("parent") or {}
    parent_id = parent.get("id")
    parent_url = parent.get("app_url") or ""
    bucket_id = (rec.get("bucket") or {}).get("id")
    if not (parent_id and parent_url and bucket_id):
        result = {"received_at": received_at, "skipped": "missing_parent_or_bucket",
                  "comment_id": comment_id}
        _log_event(result)
        return result

    # Idempotency vs the polling path
    seen = cb_mention_worker._seen()
    key = f"comment:{comment_id}"
    if key in seen:
        result = {"received_at": received_at, "skipped": "already_seen",
                  "comment_id": comment_id}
        _log_event(result)
        return result

    token, src = tokens.get_user_token(user_email)
    if not token:
        result = {"received_at": received_at, "skipped": f"no_token:{src}",
                  "comment_id": comment_id}
        _log_event(result)
        # Don't mark as seen — when the token comes back the polling path
        # should still pick this up.
        return result

    if cb_mention_worker._parent_is_closed(bucket_id, parent_id, token):
        seen.add(key)
        cb_mention_worker._save_seen(seen)
        result = {"received_at": received_at, "skipped": "parent_closed",
                  "comment_id": comment_id, "parent_id": parent_id}
        _log_event(result)
        return result

    seen.add(key)
    cb_mention_worker._save_seen(seen)

    # Same flow as scan_for_user's inner loop
    from . import context_collector, plan_inference
    try:
        bundle = context_collector.collect(parent_url, token)
        plan = plan_inference.infer(
            user_feedback=body,
            basecamp_url=parent_url,
            output_type="",
            success_criteria="",
            context_bundle=bundle,
        )
        html = cb_mention_worker._build_response_text(plan)
        ok, detail = cb_mention_worker._post_comment(bucket_id, parent_id, html, token)
        if not ok:
            sentinel_ok, sentinel_detail = cb_mention_worker._post_comment(
                bucket_id, parent_id, cb_mention_worker.SENTINEL_HTML, token,
            )
            result = {
                "received_at": received_at,
                "responded": False, "post_detail": detail,
                "sentinel_posted": sentinel_ok, "sentinel_detail": sentinel_detail,
                "comment_id": comment_id, "bucket_id": bucket_id,
                "parent_id": parent_id,
            }
            _log_event(result)
            return result
        result = {
            "received_at": received_at, "responded": True,
            "comment_id": comment_id, "bucket_id": bucket_id,
            "parent_id": parent_id,
        }
        _log_event(result)
        return result
    except Exception as e:
        logger.warning("cb_webhooks: handle_event failed for comment=%s: %s",
                       comment_id, type(e).__name__, exc_info=True)
        result = {"received_at": received_at, "responded": False,
                  "stage": "plan_inference_or_collect",
                  "detail": f"error_{type(e).__name__}",
                  "comment_id": comment_id}
        _log_event(result)
        return result
