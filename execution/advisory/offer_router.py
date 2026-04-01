"""Offer classification engine for the AI Advisory platform.

Routes leads into the best revenue path based on company profile,
complexity, and advisory results. Fully deterministic.

Offer tiers:
  - enterprise:    Large org, high ROI, multi-department → full engagement
  - custom_build:  Strong use case, moderate complexity → project-based
  - advisory:      Strategic interest, unclear path → paid advisory
  - accelerator:   Individual / small team → self-serve program
"""


OFFER_TIERS = {
    "enterprise": {
        "label": "Enterprise AI Transformation",
        "description": "Full-scale AI workforce deployment across multiple departments",
        "typical_value": "$100K+",
        "campaign_pipeline": "Enterprise Pipeline",
    },
    "custom_build": {
        "label": "Custom AI Build",
        "description": "Targeted AI solution for a specific business challenge",
        "typical_value": "$25K-$100K",
        "campaign_pipeline": "Custom Build Pipeline",
    },
    "advisory": {
        "label": "AI Strategy Advisory",
        "description": "Strategic planning and roadmap for AI adoption",
        "typical_value": "$5K-$25K",
        "campaign_pipeline": "AI Advisory Pipeline",
    },
    "accelerator": {
        "label": "AI Leadership Accelerator",
        "description": "Self-paced program for AI-ready leaders and small teams",
        "typical_value": "$1K-$5K",
        "campaign_pipeline": "Accelerator Pipeline",
    },
}


def classify_lead(lead: dict) -> dict:
    """Classify a lead into the best offer tier.

    Args:
        lead: Lead dict with metadata from advisory session.

    Returns:
        Dict with recommended_offer, confidence, reasoning, and tier details.
    """
    signals = _extract_signals(lead)
    offer, confidence, reasons = _apply_rules(signals)
    tier_info = OFFER_TIERS[offer]

    return {
        "recommended_offer": offer,
        "offer_label": tier_info["label"],
        "offer_description": tier_info["description"],
        "typical_value": tier_info["typical_value"],
        "campaign_pipeline": tier_info["campaign_pipeline"],
        "confidence": confidence,
        "reasoning": reasons,
        "signals": signals,
    }


def _extract_signals(lead: dict) -> dict:
    """Extract classification signals from lead data."""
    metadata = lead.get("metadata", {})

    company_size = _estimate_company_size(lead.get("company_size", ""))
    role_level = _classify_role(lead.get("role", ""))
    dept_count = len(metadata.get("key_departments", []))
    roi_3yr = metadata.get("estimated_roi_3yr", 0)
    annual_savings = metadata.get("estimated_annual_savings", 0)
    revenue_lift = metadata.get("estimated_revenue_lift", 0)
    maturity = metadata.get("maturity_score", 0)
    total_roles = metadata.get("total_ai_roles", 0)
    has_idea = bool(metadata.get("idea_input"))

    return {
        "company_size": company_size,
        "role_level": role_level,
        "dept_count": dept_count,
        "roi_3yr": roi_3yr,
        "annual_impact": annual_savings + revenue_lift,
        "maturity": maturity,
        "total_ai_roles": total_roles,
        "has_idea": has_idea,
    }


def _apply_rules(signals: dict) -> tuple[str, str, list[str]]:
    """Apply classification rules. Returns (offer, confidence, reasons)."""
    reasons = []

    # ── Enterprise: large company + high impact + multi-department ────
    enterprise_score = 0
    if signals["company_size"] >= 1000:
        enterprise_score += 3
        reasons.append("Large organization (1000+ employees)")
    elif signals["company_size"] >= 200:
        enterprise_score += 1
    if signals["roi_3yr"] >= 200:
        enterprise_score += 2
        reasons.append(f"High ROI potential ({signals['roi_3yr']}% 3-year)")
    if signals["dept_count"] >= 4:
        enterprise_score += 2
        reasons.append(f"Multi-department impact ({signals['dept_count']} departments)")
    if signals["role_level"] == "executive":
        enterprise_score += 1
        reasons.append("Executive-level decision maker")
    if signals["total_ai_roles"] >= 10:
        enterprise_score += 1

    if enterprise_score >= 5:
        confidence = "high" if enterprise_score >= 7 else "medium"
        return "enterprise", confidence, reasons

    # ── Custom Build: strong use case + moderate complexity ──────────
    reasons = []
    custom_score = 0
    if signals["has_idea"]:
        custom_score += 2
        reasons.append("Clear business use case identified")
    if signals["dept_count"] >= 2:
        custom_score += 1
        reasons.append(f"{signals['dept_count']} departments impacted")
    if signals["annual_impact"] >= 100_000:
        custom_score += 2
        reasons.append("Significant annual impact potential")
    if signals["maturity"] >= 2.0:
        custom_score += 1
        reasons.append("Sufficient AI readiness to execute")
    if signals["company_size"] >= 50:
        custom_score += 1

    if custom_score >= 4:
        confidence = "high" if custom_score >= 6 else "medium"
        return "custom_build", confidence, reasons

    # ── Advisory: strategic interest, unclear path ───────────────────
    reasons = []
    advisory_score = 0
    if signals["role_level"] in ("executive", "vp"):
        advisory_score += 2
        reasons.append("Senior leadership exploring AI strategy")
    if signals["maturity"] < 2.5:
        advisory_score += 1
        reasons.append("Early-stage AI maturity — needs guidance")
    if signals["has_idea"]:
        advisory_score += 1
        reasons.append("Has vision but needs strategic direction")
    if signals["company_size"] >= 20:
        advisory_score += 1

    if advisory_score >= 3:
        confidence = "medium"
        return "advisory", confidence, reasons

    # ── Accelerator: individual / small team ────────────────────────
    reasons = ["Individual or small team seeking AI capabilities"]
    if signals["role_level"] in ("manager", "individual"):
        reasons.append("Operational role — needs hands-on program")
    if signals["company_size"] < 50:
        reasons.append("Small organization — accelerator is best fit")

    return "accelerator", "medium", reasons


def _estimate_company_size(size_str: str) -> int:
    """Parse company size to an integer estimate."""
    import re
    if not size_str:
        return 0
    text = size_str.lower().replace(",", "").replace("+", "")
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))
    keywords = {
        "enterprise": 1500, "large": 1000, "mid": 300,
        "medium": 300, "small": 30, "startup": 5,
    }
    for kw, val in keywords.items():
        if kw in text:
            return val
    return 0


def _classify_role(role: str) -> str:
    """Classify role into a seniority level."""
    role = role.lower().strip()
    if not role:
        return "unknown"

    exec_keywords = {"ceo", "coo", "cfo", "cto", "cio", "cdo", "cmo",
                     "chief", "president", "founder", "owner"}
    vp_keywords = {"vp", "vice president", "svp", "evp", "head of", "director"}
    mgr_keywords = {"manager", "lead", "senior", "principal"}

    for kw in exec_keywords:
        if kw in role:
            return "executive"
    for kw in vp_keywords:
        if kw in role:
            return "vp"
    for kw in mgr_keywords:
        if kw in role:
            return "manager"

    return "individual"
