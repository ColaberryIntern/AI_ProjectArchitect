"""Sync advisory events to Colaberry Enterprise AI platform.

Sends webhooks to enterprise.colaberry.ai/api/webhooks/advisory
when advisory sessions complete, leads are captured, or bookings happen.

All calls are fire-and-forget. Failures are logged but never block the user flow.
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ENTERPRISE_WEBHOOK_URL = os.getenv(
    "ENTERPRISE_WEBHOOK_URL",
    "https://enterprise.colaberry.ai/api/webhooks/advisory",
)
ENTERPRISE_WEBHOOK_SECRET = os.getenv(
    "ENTERPRISE_WEBHOOK_SECRET",
    "colaberry-advisory-sync-2026",
)


def _sign_payload(payload: str, secret: str) -> str:
    """HMAC-SHA256 signature matching enterprise platform verification."""
    return "sha256=" + hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()


async def send_enterprise_event(event_type: str, data: dict) -> bool:
    """Fire webhook to enterprise platform. Non-blocking, fire-and-forget.

    Args:
        event_type: One of recommendation.created, recommendation.accepted,
                    report.completed, strategy_call.booked
        data: Payload dict matching enterprise webhook schema.

    Returns:
        True if sent successfully, False otherwise.
    """
    try:
        import httpx
    except ImportError:
        logger.warning("[EnterpriseSync] httpx not installed, skipping webhook")
        return False

    try:
        payload = json.dumps({
            "event": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        signature = _sign_payload(payload, ENTERPRISE_WEBHOOK_SECRET)

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                ENTERPRISE_WEBHOOK_URL,
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": signature,
                    "X-Webhook-Event": event_type,
                },
            )
            if resp.status_code == 200:
                logger.info(f"[EnterpriseSync] Sent {event_type}: {resp.status_code}")
                return True
            else:
                logger.warning(
                    f"[EnterpriseSync] {event_type} failed: {resp.status_code} {resp.text[:200]}"
                )
                return False
    except Exception as e:
        logger.error(f"[EnterpriseSync] {event_type} error: {e}")
        return False


def build_lead_payload(session: dict) -> dict:
    """Build the payload the enterprise platform expects from session data.

    Extracts user info, company details, and recommendation metadata
    from the advisory session and its nested lead/impact data.
    """
    lead = session.get("lead") or {}
    impact = session.get("impact_model") or {}
    maturity = session.get("maturity_score") or {}
    cap_map = session.get("capability_map") or {}
    problem = session.get("problem_analysis") or {}

    email = lead.get("email", "") or session.get("email", "")
    name = lead.get("name", "")
    company = lead.get("company", "")
    role = lead.get("role", "")

    # Extract departments from capability map
    departments = [d.get("name", "") for d in cap_map.get("departments", [])]

    # Extract systems from session
    systems = session.get("selected_systems", [])

    # ROI
    roi = impact.get("roi_summary", {})
    cost_savings = impact.get("cost_savings", {})
    estimated_roi = cost_savings.get("total_annual", 0) + impact.get(
        "revenue_impact", {}
    ).get("estimated_annual_revenue_gain", 0)

    # Confidence from maturity
    maturity_overall = maturity.get("overall", 0) if isinstance(maturity, dict) else 0
    confidence = min(maturity_overall / 5.0, 1.0) if maturity_overall else 0.5

    # Severity from ROI
    severity = "high" if estimated_roi > 100000 else "medium" if estimated_roi > 25000 else "low"

    return {
        "id": session.get("session_id", ""),
        "userId": session.get("session_id", ""),
        "user": {
            "email": email,
            "name": name,
            "role": role,
        },
        "recommendation": {
            "type": "ai_workforce_design",
            "title": f"AI Workforce Design for {company or 'Organization'}",
            "description": (session.get("business_idea", "") or "")[:500],
            "confidence": round(confidence, 2),
            "severity": severity,
            "status": session.get("status", "complete"),
            "metadata": {
                "maturity_score": maturity_overall,
                "primary_problem": problem.get("primary_problem", ""),
                "agent_count": len(session.get("agents", [])),
                "capabilities_selected": len(session.get("selected_capabilities", [])),
                "questions_and_answers": [
                    {"question": a.get("question_text", ""), "answer": a.get("answer_text", "")[:500]}
                    for a in session.get("answers", [])
                ],
                "session_summary": (session.get("business_idea", "") or "")[:1000],
                "maturity_assessment": f"{maturity_overall}/5" if maturity_overall else "",
                "selected_outcomes": session.get("selected_outcomes", []),
                "selected_ai_systems": session.get("selected_ai_systems", []),
            },
        },
        "metadata": {
            "company": company,
            "industry": lead.get("industry", "") or _extract_industry(session),
            "company_size": _parse_int(lead.get("company_size", 0)),
            "estimated_roi": round(estimated_roi),
            "departments": departments,
            "systems": systems,
        },
    }


def _extract_industry(session: dict) -> str:
    """Try to extract industry from Q1 answer."""
    for a in session.get("answers", []):
        if a.get("question_id") == "q1_business_overview":
            return a.get("answer_text", "")[:100]
    return ""


def _parse_int(val) -> int:
    """Safely parse an integer from various input types."""
    if isinstance(val, int):
        return val
    try:
        import re
        m = re.search(r"(\d+)", str(val))
        return int(m.group(1)) if m else 0
    except Exception:
        return 0
