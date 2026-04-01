"""Revenue pipeline automation for the AI Advisory platform.

Orchestrates the full revenue intelligence flow:
score → classify → generate sales context → store → route campaign.

Called automatically when a lead is created/updated with advisory data.
"""

import logging

from execution.advisory.lead_manager import get_lead_by_email, update_revenue_intelligence
from execution.advisory.lead_scoring_engine import score_lead
from execution.advisory.offer_router import classify_lead
from execution.advisory.sales_intelligence import generate_sales_context

logger = logging.getLogger(__name__)


def run_revenue_pipeline(email: str) -> dict | None:
    """Execute the full revenue intelligence pipeline for a lead.

    1. Load the lead
    2. Score the lead (0-100)
    3. Classify into offer tier
    4. Generate sales intelligence
    5. Store all outputs on the lead
    6. Route to appropriate campaign pipeline

    Args:
        email: Lead email address.

    Returns:
        Dict with score, offer, and sales_context, or None if lead not found.
    """
    lead = get_lead_by_email(email)
    if not lead:
        logger.warning(f"Revenue pipeline: lead not found for {email}")
        return None

    # 1. Score
    score = score_lead(lead)

    # 2. Classify
    offer = classify_lead(lead)

    # 3. Generate sales intelligence
    sales_ctx = generate_sales_context(lead)

    # 4. Store on lead
    update_revenue_intelligence(email, score, offer, sales_ctx)

    # 5. Route to offer-specific campaign pipeline
    _route_to_pipeline(email, offer)

    logger.info(
        f"Revenue pipeline complete for {email}: "
        f"score={score['lead_score']}, offer={offer['recommended_offer']}, "
        f"tier={score['tier']}"
    )

    return {
        "score": score,
        "offer": offer,
        "sales_context": sales_ctx,
    }


def _route_to_pipeline(email: str, offer: dict) -> None:
    """Route lead to the appropriate campaign pipeline based on offer classification."""
    from execution.advisory.campaign_manager import advance_stage, ensure_advisory_campaign

    campaign = ensure_advisory_campaign()
    pipeline = offer.get("campaign_pipeline", "")

    if pipeline:
        advance_stage(email, campaign["campaign_id"], f"Routed: {pipeline}")
