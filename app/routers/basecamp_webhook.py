"""HTTP endpoint that Basecamp POSTs to when a Comment event fires.

Subscribed per bucket by `cb_webhooks.subscribe_user_buckets()`. BC
doesn't sign payloads, so we authenticate via a shared-secret path
segment plus a per-user token segment that also tells the inbound side
which operator's credentials to use. Rotate by re-running
subscribe_user_buckets with a new OPS_CB_WEBHOOK_SECRET.

Path: `POST /webhooks/basecamp/{secret}/{user_token}`
   secret     -- OPS_CB_WEBHOOK_SECRET shared between BC subs and us
   user_token -- sha256("{email}|{secret}")[:24], resolves to a user

Legacy path: `POST /webhooks/basecamp/{secret}` is still registered for
the migration window. It logs a WARNING and routes events to
OPS_CB_WEBHOOK_LEGACY_DEFAULT_USER (falling back to
OPS_CB_WEBHOOK_DEFAULT_USER for back-compat) so existing BC
subscriptions don't drop events while operators re-run subscribe to
get the new per-user URL.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request

from execution.products.ops import cb_webhooks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks")


@router.post("/basecamp/{secret}/{user_token}")
async def basecamp_webhook(secret: str, user_token: str, request: Request):
    """Receive a BC Comment event and dispatch to cb_webhooks.handle_event.

    Returns 200 on every authenticated request, even if the event was a
    no-op (skipped due to no_trigger, parent_closed, already_seen, etc.).
    BC retries 5xx — we don't want spurious retries when the event simply
    didn't match the trigger regex.

    Returns 503 when OPS_CB_WEBHOOK_SECRET is unset — the operator has
    disabled webhooks but BC is still posting (probably a stale
    subscription). 401 when the URL secret doesn't match the configured
    one, or when user_token doesn't resolve to any active user.
    """
    configured = cb_webhooks.webhook_secret()
    if not configured:
        raise HTTPException(503, "OPS_CB_WEBHOOK_SECRET is not configured")
    if secret != configured:
        # Don't leak how close the supplied secret is — generic 401.
        raise HTTPException(401, "invalid webhook secret")

    resolved_email = cb_webhooks.resolve_user_email_from_token(user_token)
    if not resolved_email:
        raise HTTPException(401, "invalid user token")

    try:
        payload = await request.json()
    except Exception:
        # Malformed JSON: still ack 200 so BC doesn't retry, but log.
        logger.warning("cb_webhooks: malformed webhook payload (not JSON)")
        return {"ok": False, "skipped": "malformed_json"}

    summary = cb_webhooks.handle_event(payload, user_email=resolved_email)
    return {"ok": True, "summary": summary}


@router.post("/basecamp/{secret}")
async def basecamp_webhook_legacy(secret: str, request: Request):
    """Legacy single-segment route from PR #10.

    Existing BC subscriptions point here. We keep it alive during the
    migration window so events don't get dropped, but log a WARNING on
    every hit so the operator sees stale subscriptions in the log and
    re-runs /admin/cb-webhooks/subscribe (which produces the new
    per-user URL).

    Routes to OPS_CB_WEBHOOK_LEGACY_DEFAULT_USER if set, else falls back
    to OPS_CB_WEBHOOK_DEFAULT_USER (the var PR #10 used). When neither
    is set, the route still acks 200 with a skipped marker — BC
    shouldn't retry just because the operator hasn't picked a default.
    """
    configured = cb_webhooks.webhook_secret()
    if not configured:
        raise HTTPException(503, "OPS_CB_WEBHOOK_SECRET is not configured")
    if secret != configured:
        raise HTTPException(401, "invalid webhook secret")

    legacy_user = (
        os.environ.get("OPS_CB_WEBHOOK_LEGACY_DEFAULT_USER", "").strip()
        or os.environ.get("OPS_CB_WEBHOOK_DEFAULT_USER", "").strip()
    )
    logger.warning(
        "cb_webhooks: legacy single-segment URL hit — operator should "
        "re-run /admin/cb-webhooks/subscribe to migrate to per-user URLs "
        "(routing to user=%s)", legacy_user or "<unset>",
    )

    try:
        payload = await request.json()
    except Exception:
        logger.warning("cb_webhooks: malformed legacy webhook payload (not JSON)")
        return {"ok": False, "skipped": "malformed_json", "legacy": True}

    if not legacy_user:
        return {"ok": False, "skipped": "legacy_no_default_user", "legacy": True}

    summary = cb_webhooks.handle_event(payload, user_email=legacy_user)
    return {"ok": True, "summary": summary, "legacy": True}
