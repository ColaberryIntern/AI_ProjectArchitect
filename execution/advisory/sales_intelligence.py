"""Sales intelligence generator for the AI Advisory platform.

Produces actionable sales context for each lead: summary, pain points,
recommended approach, likely objections, and close strategy.
Fully deterministic — uses pattern matching on lead data.
"""


def generate_sales_context(lead: dict) -> dict:
    """Generate complete sales intelligence for a lead.

    Args:
        lead: Lead dict with metadata from advisory session.

    Returns:
        Dict with summary, pain_points, recommended_angle,
        likely_objections, and close_strategy.
    """
    metadata = lead.get("metadata", {})

    summary = _generate_summary(lead, metadata)
    pain_points = _identify_pain_points(metadata)
    angle = _recommend_angle(lead, metadata)
    objections = _predict_objections(lead, metadata)
    close = _suggest_close_strategy(lead, metadata)

    return {
        "summary": summary,
        "pain_points": pain_points,
        "recommended_angle": angle,
        "likely_objections": objections,
        "close_strategy": close,
    }


def _generate_summary(lead: dict, metadata: dict) -> str:
    """Generate a 2-3 sentence executive summary for the sales team."""
    parts = []

    name = lead.get("name", "Lead")
    company = lead.get("company", "their organization")
    role = lead.get("role", "")
    industry = lead.get("industry", "")

    # Opening
    if role and company:
        parts.append(f"{name} is {role} at {company}")
    elif company:
        parts.append(f"{name} represents {company}")
    else:
        parts.append(f"{name} is exploring AI transformation")

    if industry:
        parts[-1] += f" in the {industry[:100]} space"

    parts[-1] += "."

    # Impact
    savings = metadata.get("estimated_annual_savings", 0)
    revenue = metadata.get("estimated_revenue_lift", 0)
    if savings or revenue:
        from execution.advisory.impact_calculator import format_currency
        impact_parts = []
        if savings:
            impact_parts.append(f"{format_currency(savings)} in annual cost savings")
        if revenue:
            impact_parts.append(f"{format_currency(revenue)} in revenue lift")
        parts.append(f"Our analysis identified {' and '.join(impact_parts)}.")

    # Maturity
    maturity = metadata.get("maturity_score", 0)
    if maturity:
        if maturity >= 3.5:
            parts.append("They have strong AI readiness and are positioned for rapid deployment.")
        elif maturity >= 2.0:
            parts.append("They have moderate AI readiness — ideal for a structured implementation program.")
        else:
            parts.append("They are early in their AI journey and need guided strategy before execution.")

    return " ".join(parts)


def _identify_pain_points(metadata: dict) -> list[str]:
    """Identify key pain points from advisory data."""
    points = []

    idea = metadata.get("idea_input", "").lower()

    # Pattern-based pain point detection
    pain_patterns = {
        "manual": "Heavy reliance on manual processes consuming employee time",
        "slow": "Slow response times and operational bottlenecks",
        "cost": "Rising operational costs without corresponding efficiency gains",
        "scale": "Difficulty scaling operations to meet demand",
        "data": "Underutilized data assets — decisions made without analytics",
        "customer": "Customer experience gaps — inconsistent or slow support",
        "compet": "Competitive pressure from AI-adopting competitors",
        "staff": "Staffing constraints limiting growth capacity",
        "error": "Error-prone processes creating quality and compliance risk",
        "lead": "Lead management and conversion inefficiencies",
    }

    for keyword, description in pain_patterns.items():
        if keyword in idea:
            points.append(description)

    # Department-based pain points
    departments = metadata.get("key_departments", [])
    dept_pains = {
        "Operations": "Operational inefficiency in core business processes",
        "Sales": "Sales pipeline bottlenecks and lost revenue opportunities",
        "Customer Support": "High support costs and inconsistent customer experience",
        "Marketing": "Marketing ROI uncertainty and campaign optimization gaps",
        "Finance": "Financial reporting delays and forecasting inaccuracy",
        "HR": "Talent acquisition and retention challenges",
    }
    for dept in departments:
        pain = dept_pains.get(dept)
        if pain and pain not in points:
            points.append(pain)

    return points[:5] if points else ["General operational efficiency opportunity"]


def _recommend_angle(lead: dict, metadata: dict) -> str:
    """Recommend the best sales angle for this lead."""
    role = (lead.get("role") or "").lower()
    maturity = metadata.get("maturity_score", 0)
    savings = metadata.get("estimated_annual_savings", 0)
    revenue = metadata.get("estimated_revenue_lift", 0)
    departments = metadata.get("key_departments", [])

    # C-suite: lead with strategic vision and ROI
    if any(kw in role for kw in ("ceo", "coo", "president", "founder", "owner")):
        return (
            "Lead with strategic vision and competitive positioning. "
            "Frame AI as a force multiplier for the entire organization, "
            "not just a cost-cutting tool. Emphasize market leadership."
        )

    # CFO/Finance: lead with numbers
    if any(kw in role for kw in ("cfo", "finance", "controller")):
        from execution.advisory.impact_calculator import format_currency
        return (
            f"Lead with hard numbers: {format_currency(savings)} annual savings, "
            f"{format_currency(revenue)} revenue lift. "
            "Focus on payback period and 3-year ROI. "
            "Present implementation as a phased investment, not a cost."
        )

    # CTO/CIO/Tech: lead with architecture and integration
    if any(kw in role for kw in ("cto", "cio", "tech", "engineer", "developer")):
        return (
            "Lead with technical credibility and integration simplicity. "
            "Show the architecture, not just outcomes. "
            "Emphasize API-first design, existing system compatibility, and phased rollout."
        )

    # VP/Director: lead with departmental impact
    if any(kw in role for kw in ("vp", "director", "head")):
        dept_focus = departments[0] if departments else "their department"
        return (
            f"Lead with specific impact on {dept_focus}. "
            "Show how AI directly solves their team's bottlenecks. "
            "Provide concrete before/after scenarios they can champion internally."
        )

    # Default: operational efficiency
    if savings > revenue:
        return (
            "Focus on operational efficiency and cost reduction. "
            "Emphasize fast deployment, measurable ROI, and minimal disruption to current workflows."
        )
    else:
        return (
            "Focus on revenue growth and competitive advantage. "
            "Position AI as a revenue driver, not just a cost center. "
            "Emphasize speed to value and quick wins."
        )


def _predict_objections(lead: dict, metadata: dict) -> list[dict]:
    """Predict likely objections and prepare responses."""
    objections = []
    maturity = metadata.get("maturity_score", 0)
    payback = metadata.get("payback_months", 0)

    # Budget objection (almost always)
    objections.append({
        "objection": "The budget isn't there right now",
        "response": (
            "Frame as cost of inaction — every month of delay costs the organization "
            "in lost efficiency and competitive ground. Offer phased implementation "
            "starting with highest-ROI department."
        ),
    })

    # Technical readiness
    if maturity and maturity < 2.5:
        objections.append({
            "objection": "We're not technically ready for AI",
            "response": (
                "That's exactly why a structured program exists — to build readiness "
                "systematically. Our approach starts with quick wins that build confidence "
                "before tackling complex integrations."
            ),
        })

    # Trust / AI skepticism
    objections.append({
        "objection": "We've tried AI tools before and they didn't work",
        "response": (
            "Generic AI tools fail because they're not built for your specific workflows. "
            "This is a custom AI workforce designed around your exact operations, departments, "
            "and pain points — not a one-size-fits-all chatbot."
        ),
    })

    # Timeline
    if payback and payback > 12:
        objections.append({
            "objection": "The ROI timeline is too long",
            "response": (
                "The full ROI materializes over time, but quick wins appear in 30-60 days. "
                "We start with the highest-impact, lowest-complexity automations to deliver "
                "early value while building toward the full vision."
            ),
        })

    # Internal resistance
    objections.append({
        "objection": "Our team might resist this change",
        "response": (
            "AI augments your team — it doesn't replace them. Position it as removing "
            "tedious tasks so your people can focus on high-value work. "
            "Include change management in the rollout plan."
        ),
    })

    return objections[:4]


def _suggest_close_strategy(lead: dict, metadata: dict) -> str:
    """Suggest the best closing strategy for this lead."""
    savings = metadata.get("estimated_annual_savings", 0)
    revenue = metadata.get("estimated_revenue_lift", 0)
    maturity = metadata.get("maturity_score", 0)
    departments = metadata.get("key_departments", [])

    # High-value, ready to act
    if (savings + revenue) >= 300_000 and maturity >= 2.5:
        return (
            "This is a high-value, execution-ready lead. Push for a strategy session "
            "within the week. Come prepared with a 90-day deployment roadmap for their "
            "highest-impact department. Offer a pilot program as the entry point — "
            "low commitment, fast proof of value."
        )

    # High-value, needs convincing
    if (savings + revenue) >= 200_000:
        return (
            "High potential but may need social proof. Share a relevant case study. "
            "Propose a paid discovery workshop (2-3 days) to build confidence before "
            "committing to full implementation. Anchor on the cost-of-inaction narrative."
        )

    # Moderate value
    if departments and len(departments) >= 2:
        dept = departments[0]
        return (
            f"Start with a focused pilot in {dept} — the department with clearest ROI. "
            "Use pilot success to expand to other departments. "
            "Propose a 30-day proof of concept with defined success metrics."
        )

    # Early stage
    return (
        "This lead needs nurturing. Invite to an AI strategy webinar or offer a "
        "free extended advisory session. Focus on education and building trust "
        "before pushing for a paid engagement."
    )
