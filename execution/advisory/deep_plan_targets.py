"""Pluggable publish targets for a story-driven deep plan.

The generator (``deep_plan``) is target-agnostic and stores
``output/{slug}/deep_plan.json`` before any publish (store-first). This module
routes a generated plan to a named publish target so the generator/orchestrator
never has to know the destination:

  - ``"basecamp"``    -> ``deep_plan_publisher.publish_deep_plan`` (employees;
                         one to-do list, release groups, assigned/due-dated
                         story todos + the docs). Behaviour unchanged.
  - ``"accelerator"`` -> the Colaberry student platform (enterprise.colaberry.ai),
                         via the SAME HMAC-signed webhook service-auth that
                         ``enterprise_sync`` already uses. The platform's webhook
                         receiver verifies the signature and ingests the plan as
                         native student sprints/tasks/requirements.

Add a new destination by registering a function in ``PUBLISHERS`` — the
orchestrator selects it by name, so nothing upstream changes.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# The student-platform webhook that receives a generated build plan. Same host +
# HMAC secret family as enterprise_sync's advisory webhook.
ACCELERATOR_BUILD_PLAN_URL = os.getenv(
    "ACCELERATOR_BUILD_PLAN_WEBHOOK_URL",
    "https://enterprise.colaberry.ai/api/webhooks/build-plan",
)


def publish_to_basecamp(plan, *, user, bc_project_id, anchor_monday, list_name, project_name="", **_):
    """Employee target — unchanged Basecamp publish."""
    from execution.advisory import deep_plan_publisher
    return deep_plan_publisher.publish_deep_plan(
        plan, user, bc_project_id, anchor_monday, list_name, project_name=project_name,
    )


def publish_to_accelerator(plan, *, operator_email, project_ref=None,
                           webhook_url=None, secret=None, timeout=30, **_):
    """Student target — POST the plan to the Colaberry student platform via the
    signed enterprise webhook (same HMAC service-auth as ``enterprise_sync``).

    The receiver resolves the student project (by ``operator_email`` /
    ``project_ref``) and ingests the plan idempotently. Fire-and-report:
    returns ``{"ok": bool, "status": int|None, "response": .., "error": ..}`` and
    never raises (the caller decides how to surface failure).
    """
    try:
        import httpx
    except ImportError:
        logger.warning("[deep_plan_targets] httpx not installed; cannot publish to accelerator")
        return {"ok": False, "error": "httpx not installed"}

    # Reuse the proven signer + secret from the existing enterprise webhook path.
    from execution.advisory.enterprise_sync import _sign_payload
    secret = secret or os.getenv("ENTERPRISE_WEBHOOK_SECRET")
    if not secret:
        logger.warning(
            "[deep_plan_targets] ENTERPRISE_WEBHOOK_SECRET not set; refusing to publish "
            "the plan unsigned to the student platform.")
        return {"ok": False, "error": "missing ENTERPRISE_WEBHOOK_SECRET"}

    url = webhook_url or ACCELERATOR_BUILD_PLAN_URL
    payload = json.dumps({
        "event": "build_plan.published",
        "data": {"operator_email": operator_email, "project_ref": project_ref, "plan": plan},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    signature = _sign_payload(payload, secret)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, content=payload, headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": signature,
                "X-Webhook-Event": "build_plan.published",
            })
        ok = resp.status_code == 200
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:300]
        if not ok:
            logger.warning("[deep_plan_targets] accelerator publish failed: %s %s",
                           resp.status_code, str(body)[:200])
        return {"ok": ok, "status": resp.status_code, "response": body}
    except Exception as e:  # noqa: BLE001 — report, don't crash the build thread
        logger.error("[deep_plan_targets] accelerator publish error: %s", e)
        return {"ok": False, "error": str(e)}


# name -> publisher. Extend here to add a destination.
PUBLISHERS = {
    "basecamp": publish_to_basecamp,
    "accelerator": publish_to_accelerator,
}


def publish_plan(target, plan, **kwargs):
    """Dispatch a generated plan to a named publish target."""
    fn = PUBLISHERS.get(target)
    if fn is None:
        raise ValueError(f"unknown publish target {target!r}; known: {sorted(PUBLISHERS)}")
    return fn(plan, **kwargs)
