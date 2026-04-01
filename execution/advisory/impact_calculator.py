"""Financial impact calculator for AI Advisory sessions.

Deterministic engine that estimates cost savings, revenue lift,
efficiency gains, opportunity cost, and ROI based on capability maps,
maturity scores, and answer data.

Uses industry heuristic multipliers — no LLM required.
"""

import re
from datetime import datetime, timezone


# Average fully loaded cost per FTE (annual, USD)
_DEFAULT_FTE_COST = 75_000

# Automation potential -> FTE reduction multiplier
_AUTOMATION_MULTIPLIERS = {
    "high": 0.60,    # 60% of manual effort automated
    "medium": 0.35,  # 35% of manual effort automated
    "low": 0.15,     # 15% of manual effort automated
}

# Department -> estimated FTEs affected by AI (default if no sizing info)
_DEFAULT_DEPT_FTES = {
    "operations": 8,
    "sales": 5,
    "marketing": 4,
    "customer_support": 6,
    "finance": 3,
    "hr": 3,
    "technology": 4,
}

# Department type -> revenue lift multiplier (% of revenue)
_REVENUE_LIFT_BY_DEPT = {
    "sales": 0.08,             # 8% revenue lift from AI-optimized sales
    "marketing": 0.05,         # 5% from better targeting
    "customer_support": 0.03,  # 3% from better retention
    "operations": 0.02,        # 2% from operational efficiency
}

# Hours saved per automated capability per week
_HOURS_PER_CAPABILITY = {
    "high": 15,
    "medium": 8,
    "low": 3,
}


def calculate_impact(
    capability_map: dict,
    maturity_score: dict,
    answers: list[dict],
    business_idea: str = "",
) -> dict:
    """Calculate full financial impact model.

    Args:
        capability_map: Business capability map with departments.
        maturity_score: Maturity assessment with overall and dimensions.
        answers: List of answer dicts from the session.
        business_idea: The original business idea text.

    Returns:
        ImpactModel dict with all financial projections.
    """
    budget = _extract_budget(answers)
    departments = capability_map.get("departments", [])

    cost_savings = _estimate_cost_savings(departments, maturity_score, budget)
    revenue_impact = _estimate_revenue_impact(departments, answers, budget)
    efficiency_gains = _estimate_efficiency_gains(departments)
    opportunity_cost = _estimate_opportunity_cost(maturity_score, cost_savings, revenue_impact)
    implementation_cost = _estimate_implementation_cost(departments, budget)
    roi_summary = _calculate_roi(cost_savings, revenue_impact, implementation_cost)

    return {
        "cost_savings": cost_savings,
        "revenue_impact": revenue_impact,
        "efficiency_gains": efficiency_gains,
        "opportunity_cost": opportunity_cost,
        "roi_summary": roi_summary,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }


def _extract_budget(answers: list[dict]) -> float:
    """Extract approximate budget from Q7 answer text."""
    for a in answers:
        if a.get("question_id") in ("q7_budget", "q9_budget_timeline"):
            text = a.get("answer_text", "")
            return _parse_budget_text(text)
    return 100_000  # Default assumption


def _parse_budget_text(text: str) -> float:
    """Parse a budget amount from free-text answer."""
    text = text.lower().replace(",", "").replace("$", "")

    # Match "1.5m", "1.5 million" (but NOT "3 months")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:million|mil\b|m\b(?!onth))", text)
    if m:
        return float(m.group(1)) * 1_000_000

    # Match "500k", "50k"
    m = re.search(r"(\d+(?:\.\d+)?)\s*k\b", text)
    if m:
        return float(m.group(1)) * 1_000

    # Match bare numbers like "200000"
    m = re.search(r"(\d{4,})", text)  # 4+ digits = likely a dollar amount
    if m:
        return float(m.group(1))

    # Small bare number — assume thousands
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if m:
        val = float(m.group(1))
        if val >= 10:
            return val * 1_000
        return 100_000  # Very small number, default

    return 100_000  # Default


def _estimate_cost_savings(departments: list[dict], maturity_score: dict, budget: float = 100_000) -> dict:
    """Estimate annual labor cost savings from AI automation."""
    maturity = maturity_score.get("overall", 2.5)
    maturity_multiplier = max(0.5, (5 - maturity) / 3)

    # Scale FTE assumptions by company size (budget as proxy)
    size_scale = min(budget / 200_000, 1.0)  # <200K = smaller, cap at 1.0

    breakdown = []
    total_labor = 0

    for dept in departments:
        dept_id = dept.get("id", "unknown")
        base_ftes = _DEFAULT_DEPT_FTES.get(dept_id, 3)
        dept_ftes = max(1, base_ftes * size_scale)

        for cap in dept.get("capabilities", []):
            potential = cap.get("automation_potential", "medium")
            auto_mult = _AUTOMATION_MULTIPLIERS.get(potential, 0.35)
            affected_ftes = dept_ftes / max(len(dept.get("capabilities", [1])), 1)
            savings = affected_ftes * _DEFAULT_FTE_COST * auto_mult * maturity_multiplier

            breakdown.append({
                "department": dept.get("name", dept_id),
                "capability": cap.get("name", "Unknown"),
                "automation_potential": potential,
                "annual_savings": round(savings),
            })
            total_labor += savings

    return {
        "annual_labor_savings": round(total_labor),
        "annual_tool_savings": round(total_labor * 0.1),  # 10% additional tool savings
        "total_annual": round(total_labor * 1.1),
        "breakdown": breakdown,
    }


def _estimate_revenue_impact(
    departments: list[dict], answers: list[dict], budget: float
) -> dict:
    """Estimate revenue lift from AI-enhanced departments."""
    # Use budget as a rough proxy for company size/revenue
    estimated_revenue = budget * 20  # Assume tech budget is ~5% of revenue

    channels = []
    total_lift = 0

    for dept in departments:
        dept_id = dept.get("id", "unknown")
        lift_pct = _REVENUE_LIFT_BY_DEPT.get(dept_id, 0.01)
        lift_amount = estimated_revenue * lift_pct

        if lift_pct > 0:
            channels.append({
                "department": dept.get("name", dept_id),
                "lift_percent": round(lift_pct * 100, 1),
                "estimated_annual_gain": round(lift_amount),
            })
            total_lift += lift_amount

    overall_pct = (total_lift / estimated_revenue * 100) if estimated_revenue > 0 else 0

    return {
        "revenue_lift_percent": round(overall_pct, 1),
        "estimated_annual_revenue_gain": round(total_lift),
        "estimated_company_revenue": round(estimated_revenue),
        "channels": channels,
    }


def _estimate_efficiency_gains(departments: list[dict]) -> dict:
    """Estimate time and process efficiency improvements."""
    total_hours_per_week = 0
    processes_automated = 0

    for dept in departments:
        for cap in dept.get("capabilities", []):
            potential = cap.get("automation_potential", "medium")
            total_hours_per_week += _HOURS_PER_CAPABILITY.get(potential, 8)
            processes_automated += 1

    return {
        "time_saved_hours_per_week": total_hours_per_week,
        "time_saved_hours_per_year": total_hours_per_week * 50,
        "processes_automated": processes_automated,
        "error_reduction_percent": min(processes_automated * 8, 65),
    }


def _estimate_opportunity_cost(
    maturity_score: dict, cost_savings: dict, revenue_impact: dict
) -> dict:
    """Estimate cost of NOT implementing AI."""
    monthly_savings_lost = cost_savings.get("total_annual", 0) / 12
    monthly_revenue_lost = revenue_impact.get("estimated_annual_revenue_gain", 0) / 12
    monthly_total = monthly_savings_lost + monthly_revenue_lost

    maturity = maturity_score.get("overall", 2.5)
    competitive_risk = "high" if maturity < 2.5 else "medium" if maturity < 3.5 else "low"

    return {
        "monthly_cost_of_inaction": round(monthly_total),
        "annual_cost_of_inaction": round(monthly_total * 12),
        "competitive_risk": competitive_risk,
    }


def _estimate_implementation_cost(departments: list[dict], budget: float) -> float:
    """Estimate total AI implementation cost."""
    # Base cost per department with AI capabilities
    num_depts = len(departments)
    total_capabilities = sum(len(d.get("capabilities", [])) for d in departments)

    # Cost scales with complexity
    base_cost = 25_000 * num_depts
    capability_cost = 10_000 * total_capabilities

    estimated = base_cost + capability_cost
    # Cap at 2x the stated budget
    return min(estimated, budget * 2)


def _calculate_roi(cost_savings: dict, revenue_impact: dict, implementation_cost: float) -> dict:
    """Calculate ROI metrics."""
    annual_benefit = (
        cost_savings.get("total_annual", 0)
        + revenue_impact.get("estimated_annual_revenue_gain", 0)
    )

    if implementation_cost > 0:
        payback_months = round((implementation_cost / annual_benefit) * 12, 1) if annual_benefit > 0 else 36
        three_year_roi = round(((annual_benefit * 3 - implementation_cost) / implementation_cost) * 100, 1)
    else:
        payback_months = 0
        three_year_roi = 0

    return {
        "implementation_cost": round(implementation_cost),
        "annual_benefit": round(annual_benefit),
        "payback_period_months": min(payback_months, 36),
        "three_year_roi_percent": max(three_year_roi, 0),
    }


def format_currency(amount: float) -> str:
    """Format a number as USD currency string."""
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    else:
        return f"${amount:.0f}"
