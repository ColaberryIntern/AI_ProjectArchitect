"""Maps advisory sessions to structured leads and campaign enrollments.

This is the integration bridge between the advisory wizard and the
lead/campaign management system. It extracts business intelligence
from session data and stores it on the lead record.
"""

import logging

from execution.advisory.campaign_manager import (
    advance_stage,
    ensure_advisory_campaign,
    enroll_lead,
)
from execution.advisory.lead_manager import (
    add_lead_metadata,
    attach_pdf,
    link_advisory_session,
    upsert_lead,
)

logger = logging.getLogger(__name__)


def map_advisory_to_lead(session: dict) -> dict | None:
    """Convert an advisory session into a lead record with campaign enrollment.

    This function:
    1. Upserts a lead by email
    2. Links the advisory session
    3. Stores business intelligence as metadata
    4. Enrolls in the advisory campaign
    5. Advances campaign stage based on session status
    6. Attaches PDF if generated

    Args:
        session: The advisory session dict.

    Returns:
        The lead dict, or None if no email is available.
    """
    lead_info = session.get("lead") or {}
    email = lead_info.get("email") or session.get("email", "")

    if not email:
        return None

    # Extract fields from session answers
    industry = _extract_from_answers(session, "q1_business_overview")
    company_size = ""  # Could be extracted from answers if question added

    # 1. Upsert lead
    lead = upsert_lead(
        email=email,
        name=lead_info.get("name", ""),
        company=lead_info.get("company", ""),
        role=lead_info.get("role", ""),
        industry=industry,
        company_size=company_size,
        source="advisory",
    )

    # 2. Link advisory session
    link_advisory_session(email, session["session_id"])

    # 3. Store business intelligence metadata
    metadata = _extract_metadata(session)
    if metadata:
        add_lead_metadata(email, metadata)

    # 4. Ensure campaign exists and enroll lead
    campaign = ensure_advisory_campaign()
    enroll_lead(email, campaign["campaign_id"])

    # 5. Advance campaign stage based on session status
    stage = _map_status_to_stage(session.get("status", ""))
    if stage:
        advance_stage(email, campaign["campaign_id"], stage)

    # 6. Attach PDF if generated
    pdf_path = session.get("pdf_path")
    if pdf_path:
        attach_pdf(email, pdf_path)

    # 7. Run revenue intelligence pipeline (score, classify, sales intel)
    try:
        from execution.advisory.revenue_pipeline import run_revenue_pipeline
        run_revenue_pipeline(email)
    except Exception:
        logger.warning("Revenue pipeline failed for %s", email, exc_info=True)

    # 8. Sync to enterprise platform (fire-and-forget)
    try:
        import asyncio
        from execution.advisory.enterprise_sync import build_lead_payload, send_enterprise_event
        payload = build_lead_payload(session)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(send_enterprise_event("recommendation.created", payload))
        except RuntimeError:
            # No event loop running (e.g., in tests) — skip
            pass
    except Exception:
        logger.warning("Enterprise sync failed (non-blocking)", exc_info=True)

    return lead


def advance_campaign_for_session(session: dict, stage: str) -> None:
    """Advance a session's lead to a specific campaign stage.

    Used by route handlers when specific events occur (e.g., booking).
    """
    lead_info = session.get("lead") or {}
    email = lead_info.get("email") or session.get("email", "")
    if not email:
        return

    campaign = ensure_advisory_campaign()
    advance_stage(email, campaign["campaign_id"], stage)


def _extract_from_answers(session: dict, question_id: str) -> str:
    """Extract an answer value from session answers."""
    for answer in session.get("answers", []):
        if answer.get("question_id") == question_id:
            return answer.get("answer_text", "")[:200]
    return ""


def _extract_metadata(session: dict) -> dict:
    """Extract sales-relevant metadata from the session."""
    metadata = {}

    # Idea input
    idea = session.get("business_idea", "")
    if idea:
        metadata["idea_input"] = idea[:500]

    # Maturity score
    maturity = session.get("maturity_score")
    if maturity:
        metadata["maturity_score"] = maturity.get("overall")
        metadata["maturity_dimensions"] = maturity.get("dimensions", {})

    # Impact model summary
    impact = session.get("impact_model")
    if impact:
        cost = impact.get("cost_savings", {})
        revenue = impact.get("revenue_impact", {})
        roi = impact.get("roi_summary", {})
        metadata["estimated_annual_savings"] = cost.get("total_annual", 0)
        metadata["estimated_revenue_lift"] = revenue.get("estimated_annual_revenue_gain", 0)
        metadata["estimated_roi_3yr"] = roi.get("three_year_roi_percent", 0)
        metadata["payback_months"] = roi.get("payback_period_months", 0)

    # Key departments
    cap_map = session.get("capability_map")
    if cap_map:
        departments = [d.get("name", "") for d in cap_map.get("departments", [])]
        metadata["key_departments"] = departments

    # Org summary
    org = session.get("org_structure")
    if org:
        metadata["total_ai_roles"] = len(org)
        metadata["total_fte_equivalent"] = sum(
            n.get("estimated_fte_equivalent", 0) for n in org
        )

    return metadata


def _map_status_to_stage(status: str) -> str | None:
    """Map an advisory session status to a campaign stage."""
    status_to_stage = {
        "questioning": "Started Advisory",
        "generating": "Completed Questions",
        "complete": "Generated Results",
        "gated": "Captured Lead",
    }
    return status_to_stage.get(status)
