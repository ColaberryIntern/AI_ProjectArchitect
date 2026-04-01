"""Lead scoring engine for the AI Advisory platform.

Scores leads 0-100 based on company profile, role seniority,
advisory results (maturity, ROI, departments), and engagement signals.
Fully deterministic — no LLM required.
"""

# ── Role seniority tiers ────────────────────────────────────────────

_EXECUTIVE_ROLES = {
    "ceo", "coo", "cfo", "cto", "cio", "cdo", "cmo",
    "chief", "president", "founder", "co-founder", "owner",
    "managing director", "general manager",
}
_VP_ROLES = {
    "vp", "vice president", "svp", "evp", "avp",
    "head of", "director",
}
_MANAGER_ROLES = {
    "manager", "lead", "senior", "principal", "team lead",
}

# ── Company size tiers ──────────────────────────────────────────────

_SIZE_SCORES = {
    "enterprise": 25,    # 1000+
    "mid_market": 20,    # 200-999
    "smb": 12,           # 50-199
    "small": 6,          # 10-49
    "startup": 3,        # <10
}


def score_lead(lead: dict) -> dict:
    """Score a lead on a 0-100 scale.

    Args:
        lead: Lead dict with fields like role, company_size, metadata.

    Returns:
        Dict with total score, breakdown by category, and tier label.
    """
    breakdown = {
        "role_score": _score_role(lead),
        "company_score": _score_company_size(lead),
        "roi_score": _score_roi(lead),
        "department_score": _score_departments(lead),
        "maturity_score": _score_maturity(lead),
        "engagement_score": _score_engagement(lead),
    }

    total = min(sum(breakdown.values()), 100)
    tier = _score_to_tier(total)

    return {
        "lead_score": total,
        "breakdown": breakdown,
        "tier": tier,
    }


def _score_role(lead: dict) -> int:
    """Score based on role seniority (0-25)."""
    role = (lead.get("role") or "").lower().strip()
    if not role:
        return 5  # Unknown role gets minimal score

    for keyword in _EXECUTIVE_ROLES:
        if keyword in role:
            return 25

    for keyword in _VP_ROLES:
        if keyword in role:
            return 20

    for keyword in _MANAGER_ROLES:
        if keyword in role:
            return 12

    return 5  # Individual contributor / other


def _score_company_size(lead: dict) -> int:
    """Score based on company size (0-25)."""
    size_str = (lead.get("company_size") or "").lower().strip()

    if not size_str:
        # Try to infer from metadata or default
        return 8  # Unknown defaults to moderate

    size_num = _parse_company_size(size_str)

    if size_num >= 1000:
        return _SIZE_SCORES["enterprise"]
    elif size_num >= 200:
        return _SIZE_SCORES["mid_market"]
    elif size_num >= 50:
        return _SIZE_SCORES["smb"]
    elif size_num >= 10:
        return _SIZE_SCORES["small"]
    else:
        return _SIZE_SCORES["startup"]


def _parse_company_size(text: str) -> int:
    """Parse a company size from free text."""
    import re

    text = text.lower().replace(",", "").replace("+", "")

    # Match patterns: "5000", "1000-5000", "500 employees"
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))

    # Keyword matching
    keywords = {
        "enterprise": 1500, "large": 1000,
        "mid": 300, "medium": 300,
        "small": 30, "startup": 5, "solo": 1,
    }
    for kw, val in keywords.items():
        if kw in text:
            return val

    return 0


def _score_roi(lead: dict) -> int:
    """Score based on estimated ROI from advisory (0-20)."""
    metadata = lead.get("metadata", {})

    roi_3yr = metadata.get("estimated_roi_3yr", 0)
    annual_savings = metadata.get("estimated_annual_savings", 0)
    revenue_lift = metadata.get("estimated_revenue_lift", 0)

    total_annual_impact = annual_savings + revenue_lift

    roi_points = 0

    # ROI percentage scoring
    if roi_3yr >= 500:
        roi_points += 10
    elif roi_3yr >= 200:
        roi_points += 7
    elif roi_3yr >= 100:
        roi_points += 4
    elif roi_3yr > 0:
        roi_points += 2

    # Absolute impact scoring
    if total_annual_impact >= 500_000:
        roi_points += 10
    elif total_annual_impact >= 200_000:
        roi_points += 7
    elif total_annual_impact >= 50_000:
        roi_points += 4
    elif total_annual_impact > 0:
        roi_points += 2

    return min(roi_points, 20)


def _score_departments(lead: dict) -> int:
    """Score based on number of departments impacted (0-15)."""
    metadata = lead.get("metadata", {})
    departments = metadata.get("key_departments", [])
    count = len(departments)

    if count >= 5:
        return 15
    elif count >= 3:
        return 10
    elif count >= 2:
        return 7
    elif count >= 1:
        return 4
    return 0


def _score_maturity(lead: dict) -> int:
    """Score based on AI maturity (0-10).

    Mid-range maturity scores highest (ready to act but not already done).
    """
    metadata = lead.get("metadata", {})
    maturity = metadata.get("maturity_score", 0)

    if not maturity:
        return 0

    # Sweet spot: maturity 2-3.5 (ready to invest, not already advanced)
    if 2.0 <= maturity <= 3.5:
        return 10
    elif 1.5 <= maturity < 2.0 or 3.5 < maturity <= 4.0:
        return 7
    elif maturity > 4.0:
        return 4  # Already advanced, less need
    else:
        return 3  # Very low, may not be ready


def _score_engagement(lead: dict) -> int:
    """Score based on engagement signals (0-5)."""
    score = 0
    if lead.get("advisory_session_ids"):
        score += 2
    if lead.get("pdf_paths"):
        score += 2
    if lead.get("metadata", {}).get("idea_input"):
        score += 1
    return min(score, 5)


def _score_to_tier(score: int) -> str:
    """Convert numeric score to tier label."""
    if score >= 80:
        return "hot"
    elif score >= 60:
        return "warm"
    elif score >= 40:
        return "qualified"
    elif score >= 20:
        return "nurture"
    else:
        return "cold"
