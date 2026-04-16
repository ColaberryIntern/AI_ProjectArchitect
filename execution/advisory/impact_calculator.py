"""Financial impact calculator for AI Advisory sessions.

Deterministic engine that estimates cost savings, revenue lift,
efficiency gains, opportunity cost, and ROI based on capability maps,
maturity scores, and answer data.

Uses industry heuristic multipliers — no LLM required.
"""

import re
from datetime import datetime, timezone


# Average fully loaded cost per FTE (annual, USD) — fallback when no industry profile
_DEFAULT_FTE_COST = 75_000

# Automation potential -> FTE reduction multiplier
_AUTOMATION_MULTIPLIERS = {
    "high": 0.45,    # 45% of manual effort automated (was 60% — more realistic)
    "medium": 0.25,  # 25% of manual effort automated (was 35%)
    "low": 0.10,     # 10% of manual effort automated (was 15%)
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

# Departments that are NOT directly automated by AI systems
# (support roles — savings come indirectly, not from FTE automation)
_NON_AUTOMATABLE_DEPTS = {"management", "hr", "technology"}

# Department type -> loss prevention / cash flow improvement (% of revenue)
# NOT "revenue lift" — AI doesn't generate new revenue, it prevents leakage
_LOSS_PREVENTION_BY_DEPT = {
    "sales": 0.03,             # 3% from pipeline leakage prevention
    "marketing": 0.02,         # 2% from better targeting ROI
    "customer_support": 0.02,  # 2% from retention improvement
    "operations": 0.015,       # 1.5% from operational error reduction
    "billing": 0.015,          # 1.5% from billing accuracy
    "collections_ar": 0.008,   # 0.8% from DSO improvement
    "carrier_pay_ap": 0.010,   # 1.0% from avoided overpayments
    "finance": 0.005,          # 0.5% from margin visibility
    "compliance": 0.003,       # 0.3% from reduced audit/fine risk
}

# Legacy alias for backward compatibility
_REVENUE_LIFT_BY_DEPT = _LOSS_PREVENTION_BY_DEPT

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
    industry_profile: dict | None = None,
) -> dict:
    """Calculate full financial impact model.

    Args:
        capability_map: Business capability map with departments.
        maturity_score: Maturity assessment with overall and dimensions.
        answers: List of answer dicts from the session.
        business_idea: The original business idea text.
        industry_profile: Industry-specific benchmarks (FTE costs, revenue lift, etc.)

    Returns:
        ImpactModel dict with all financial projections.
    """
    budget = _extract_budget(answers)
    employee_count = _extract_employee_count(answers)
    departments = capability_map.get("departments", [])

    # Use industry-calibrated numbers if available
    if industry_profile:
        dept_struct = industry_profile.get("dept_structure", {})
        fte_overrides = {k: v["avg_fte_cost"] for k, v in dept_struct.items()}
        lift_overrides = industry_profile.get("revenue_lift_by_dept", {})
        rev_per_emp = industry_profile.get("revenue_per_employee", 200_000)
    else:
        fte_overrides = {}
        lift_overrides = {}
        rev_per_emp = 200_000

    dept_struct = industry_profile.get("dept_structure", {}) if industry_profile else None
    cost_savings = _estimate_cost_savings(
        departments, maturity_score, budget,
        employee_count=employee_count,
        fte_cost_overrides=fte_overrides if fte_overrides else None,
        dept_structure=dept_struct,
    )
    revenue_impact = _estimate_revenue_impact(
        departments, answers, budget,
        employee_count=employee_count,
        rev_per_employee=rev_per_emp,
        lift_overrides=lift_overrides if lift_overrides else None,
    )
    efficiency_gains = _estimate_efficiency_gains(departments)
    opportunity_cost = _estimate_opportunity_cost(maturity_score, cost_savings, revenue_impact)
    implementation_cost = _estimate_implementation_cost(
        departments, budget, employee_count=employee_count, rev_per_employee=rev_per_emp,
    )
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


def _extract_employee_count(answers: list[dict]) -> int:
    """Extract employee count from Q2 answer text."""
    for a in answers:
        if a.get("question_id") in ("q2_org_size", "q2_size"):
            text = a.get("answer_text", "").lower().replace(",", "")
            # Match ranges like "200-500", "51-200"
            m = re.search(r"(\d+)\s*[-–to]+\s*(\d+)", text)
            if m:
                return (int(m.group(1)) + int(m.group(2))) // 2
            # Match single numbers
            m = re.search(r"(\d+)", text)
            if m:
                val = int(m.group(1))
                if val > 0:
                    return val
    return 100  # Default assumption


def _estimate_cost_savings(
    departments: list[dict],
    maturity_score: dict,
    budget: float = 100_000,
    employee_count: int = 100,
    fte_cost_overrides: dict | None = None,
    dept_structure: dict | None = None,
) -> dict:
    """Estimate annual labor cost savings from AI automation.

    Uses industry-calibrated FTE costs and department sizing when available.
    """
    maturity = maturity_score.get("overall", 2.5)
    maturity_multiplier = max(0.5, (5 - maturity) / 3)

    breakdown = []
    total_labor = 0

    for dept in departments:
        dept_id = dept.get("id", "unknown")
        caps = dept.get("capabilities", [])

        if not caps:
            continue

        # Skip departments that aren't directly automated
        if dept_id in _NON_AUTOMATABLE_DEPTS:
            continue

        # Industry-calibrated FTE cost for this department
        fte_cost = _DEFAULT_FTE_COST
        if fte_cost_overrides:
            fte_cost = fte_cost_overrides.get(dept_id, _DEFAULT_FTE_COST)

        # Industry-calibrated department size
        if dept_structure and dept_id in dept_structure:
            dept_ftes = max(1, round(employee_count * dept_structure[dept_id].get("pct_of_headcount", 0.10)))
        else:
            base_ftes = _DEFAULT_DEPT_FTES.get(dept_id, 3)
            size_scale = min(budget / 200_000, 1.0)
            dept_ftes = max(1, base_ftes * size_scale)

        for cap in caps:
            potential = cap.get("automation_potential", "medium")
            auto_mult = _AUTOMATION_MULTIPLIERS.get(potential, 0.25)
            affected_ftes = dept_ftes / max(len(caps), 1)
            savings = affected_ftes * fte_cost * auto_mult * maturity_multiplier

            breakdown.append({
                "department": dept.get("name", dept_id),
                "capability": cap.get("name", "Unknown"),
                "automation_potential": potential,
                "annual_savings": round(savings),
            })
            total_labor += savings

    return {
        "annual_labor_savings": round(total_labor),
        "annual_tool_savings": round(total_labor * 0.1),
        "total_annual": round(total_labor * 1.1),
        "breakdown": breakdown,
    }


def _estimate_revenue_impact(
    departments: list[dict], answers: list[dict], budget: float,
    employee_count: int = 100, rev_per_employee: int = 200_000,
    lift_overrides: dict | None = None,
) -> dict:
    """Estimate loss prevention and cash flow improvement from AI.

    This is NOT new revenue generation. It represents:
    - Billing accuracy improvements (revenue recovered from errors)
    - Faster collection (DSO reduction, freed working capital)
    - Avoided overpayments and fraud losses
    - Reduced dispute resolution costs
    """
    estimated_revenue = max(budget * 20, employee_count * rev_per_employee)

    channels = []
    total_prevention = 0
    prevention_rates = lift_overrides or _LOSS_PREVENTION_BY_DEPT

    for dept in departments:
        dept_id = dept.get("id", "unknown")
        cap_count = len(dept.get("capabilities", []))

        # Only count departments with real capability investment
        if cap_count < 2:
            continue

        # Skip non-automatable departments
        if dept_id in _NON_AUTOMATABLE_DEPTS:
            continue

        prevention_pct = prevention_rates.get(dept_id, 0.005)

        # Scale by capability density (more caps = higher confidence)
        density_mult = min(cap_count / 3, 1.0)
        effective_pct = prevention_pct * density_mult
        prevention_amount = estimated_revenue * effective_pct

        if effective_pct > 0:
            channels.append({
                "department": dept.get("name", dept_id),
                "lift_percent": round(effective_pct * 100, 1),
                "estimated_annual_gain": round(prevention_amount),
            })
            total_prevention += prevention_amount

    overall_pct = (total_prevention / estimated_revenue * 100) if estimated_revenue > 0 else 0

    return {
        "revenue_lift_percent": round(overall_pct, 1),
        "estimated_annual_revenue_gain": round(total_prevention),
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


def _estimate_implementation_cost(
    departments: list[dict], budget: float,
    employee_count: int = 100, rev_per_employee: int = 200_000,
) -> float:
    """Estimate total AI implementation cost scaled to company size.

    Components:
    - Base platform cost: $150K (minimum viable deployment)
    - Per-agent customization: $35K per specialized agent
    - Integration cost: ~0.5% of estimated revenue, capped at $200K
    """
    total_capabilities = sum(len(d.get("capabilities", [])) for d in departments)

    base_cost = 150_000
    agent_cost = 35_000 * total_capabilities
    estimated_revenue = max(budget * 20, employee_count * rev_per_employee)
    integration_cost = min(estimated_revenue * 0.005, 200_000)

    estimated = base_cost + agent_cost + integration_cost

    # Floor at $200K, cap at stated budget * 3 or $2M
    return max(200_000, min(estimated, budget * 3, 2_000_000))


def _calculate_roi(cost_savings: dict, revenue_impact: dict, implementation_cost: float) -> dict:
    """Calculate ROI metrics including ongoing maintenance costs.

    Year 1: implementation cost (full)
    Years 2-3: annual maintenance at 20% of implementation
    """
    annual_benefit = (
        cost_savings.get("total_annual", 0)
        + revenue_impact.get("estimated_annual_revenue_gain", 0)
    )

    annual_maintenance = round(implementation_cost * 0.20)
    three_year_cost = implementation_cost + (annual_maintenance * 2)
    three_year_benefit = annual_benefit * 3

    if three_year_cost > 0:
        payback_months = round((implementation_cost / annual_benefit) * 12, 1) if annual_benefit > 0 else 36
        three_year_roi = round(((three_year_benefit - three_year_cost) / three_year_cost) * 100, 1)
    else:
        payback_months = 0
        three_year_roi = 0

    return {
        "implementation_cost": round(implementation_cost),
        "annual_maintenance": annual_maintenance,
        "three_year_total_cost": round(three_year_cost),
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
