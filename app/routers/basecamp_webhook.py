"""HTTP endpoint that Basecamp POSTs to when a Comment event fires.

Subscribed per bucket by `cb_webhooks.subscribe_user_buckets()`. BC
doesn't sign payloads, so we authenticate via a shared-secret path
segment — only the BC subscription that knows the secret can hit this
URL. Rotate by re-running subscribe_user_buckets with a new
OPS_CB_WEBHOOK_SECRET.

Path: `POST /webhooks/basecamp/{secret}` — secret is the URL token, not a
header. (BC's webhook config only lets you set the payload URL.)
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request

from execution.products.ops import cb_webhooks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks")


@router.post("/basecamp/{secret}")
async def basecamp_webhook(secret: str, request: Request):
    """Receive a BC Comment event and dispatch to cb_webhooks.handle_event.

    Returns 200 on every authenticated request, even if the event was a
    no-op (skipped due to no_trigger, parent_closed, already_seen, etc.).
    BC retries 5xx — we don't want spurious retries when the event simply
    didn't match the trigger regex.

    Returns 503 when OPS_CB_WEBHOOK_SECRET is unset — the operator has
    disabled webhooks but BC is still posting (probably a stale
    subscription). 401 when the URL secret doesn't match the configured
    one (also covers the case where the secret was rotated).
    """
    configured = cb_webhooks.webhook_secret()
    if not configured:
        raise HTTPException(503, "OPS_CB_WEBHOOK_SECRET is not configured")
    if secret != configured:
        # Don't leak how close the supplied secret is — generic 401.
        raise HTTPException(401, "invalid webhook secret")

    try:
        payload = await request.json()
    except Exception:
        # Malformed JSON: still ack 200 so BC doesn't retry, but log.
        logger.warning("cb_webhooks: malformed webhook payload (not JSON)")
        return {"ok": False, "skipped": "malformed_json"}

    # Per-PR: events route to the system Ali account. Multi-tenant
    # delivery (one subscription per user, payload includes user hint)
    # is a follow-up.
    user_email = os.environ.get(
        "OPS_CB_WEBHOOK_DEFAULT_USER", "ali@colaberry.com",
    )
    summary = cb_webhooks.handle_event(payload, user_email=user_email)
    return {"ok": True, "summary": summary}
