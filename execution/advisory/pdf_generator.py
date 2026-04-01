"""Executive AI Transformation Blueprint generator.

Generates a comprehensive, consulting-grade PDF report using reportlab.
Structured as a 10-section executive strategy document designed to
justify investment, show transformation, and help close deals.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from config.settings import ADVISORY_OUTPUT_DIR

logger = logging.getLogger(__name__)


def generate_pdf(session: dict) -> str:
    """Generate the Executive AI Transformation Blueprint PDF.

    Args:
        session: The advisory session dict with all generated data.

    Returns:
        Path string to the generated PDF file.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        logger.warning("reportlab not installed - generating text fallback")
        return _generate_text_fallback(session)

    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    session_id = session["session_id"]
    output_dir = ADVISORY_OUTPUT_DIR / session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = str(output_dir / "AI_Advisory_Report.pdf")

    lead = session.get("lead") or {}
    company = lead.get("company") or "Your Organization"
    contact_name = lead.get("name") or ""

    # Header/footer callback
    def _header_footer(canvas, doc_obj):
        canvas.saveState()
        w, h = letter
        # Header line
        canvas.setStrokeColor(colors.HexColor("#4361ee"))
        canvas.setLineWidth(1)
        canvas.line(60, h - 45, w - 60, h - 45)
        # Header text
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(colors.HexColor("#1a1a2e"))
        canvas.drawString(60, h - 40, "Colaberry AI Workforce Designer")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#6c757d"))
        canvas.drawRightString(w - 60, h - 40, f"Prepared for {company}")
        # Footer line
        canvas.setStrokeColor(colors.HexColor("#dee2e6"))
        canvas.line(60, 45, w - 60, 45)
        # Footer text
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#6c757d"))
        canvas.drawString(60, 32, f"Confidential | {company}")
        if contact_name:
            canvas.drawString(60, 22, f"Prepared for {contact_name}")
        canvas.drawRightString(w - 60, 32, f"Page {doc_obj.page}")
        canvas.drawRightString(w - 60, 22, "colaberry.com")
        canvas.restoreState()

    def _cover_page(canvas, doc_obj):
        """Cover page has no header/footer."""
        pass

    # Build doc with page templates
    frame = Frame(60, 60, letter[0] - 120, letter[1] - 120, id='normal')
    cover_frame = Frame(60, 60, letter[0] - 120, letter[1] - 120, id='cover')

    doc = BaseDocTemplate(pdf_path, pagesize=letter)
    doc.addPageTemplates([
        PageTemplate(id='cover', frames=[cover_frame], onPage=_cover_page),
        PageTemplate(id='content', frames=[frame], onPage=_header_footer),
    ])

    styles = getSampleStyleSheet()
    # Custom styles
    S = {
        "title": ParagraphStyle("RTitle", parent=styles["Title"], fontSize=28, spaceAfter=6, textColor=colors.HexColor("#1a1a2e")),
        "subtitle": ParagraphStyle("RSub", parent=styles["Normal"], fontSize=13, textColor=colors.HexColor("#6c757d"), spaceAfter=20),
        "h1": ParagraphStyle("RH1", parent=styles["Heading1"], fontSize=16, spaceAfter=10, spaceBefore=24, textColor=colors.HexColor("#1a1a2e")),
        "h2": ParagraphStyle("RH2", parent=styles["Heading2"], fontSize=13, spaceAfter=8, spaceBefore=16, textColor=colors.HexColor("#4361ee")),
        "body": styles["BodyText"],
        "bold": ParagraphStyle("RBold", parent=styles["BodyText"], fontName="Helvetica-Bold"),
        "small": ParagraphStyle("RSmall", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#6c757d")),
        "quote": ParagraphStyle("RQuote", parent=styles["Normal"], fontSize=11, leftIndent=20, rightIndent=20, textColor=colors.HexColor("#4361ee"), fontName="Helvetica-Oblique", spaceBefore=10, spaceAfter=10),
        "bullet": ParagraphStyle("RBullet", parent=styles["BodyText"], leftIndent=20, bulletIndent=10),
    }

    # Helper
    from execution.advisory.impact_calculator import format_currency
    from reportlab.platypus import NextPageTemplate

    idea = session.get("business_idea", "")
    impact = session.get("impact_model") or {}
    maturity = session.get("maturity_score") or {}
    agents = session.get("agents") or []
    problem = session.get("problem_analysis") or {}
    cost_savings = impact.get("cost_savings", {})
    revenue = impact.get("revenue_impact", {})
    efficiency = impact.get("efficiency_gains", {})
    roi = impact.get("roi_summary", {})
    opp = impact.get("opportunity_cost", {})

    story = []

    # ═══════════════════════════════════════════════════════════════
    # COVER PAGE (no header/footer)
    # ═══════════════════════════════════════════════════════════════
    # Blue accent bar at top
    cover_bar = ParagraphStyle("CoverBar", parent=styles["Normal"], fontSize=10,
                               textColor=colors.HexColor("#4361ee"), fontName="Helvetica-Bold")

    story.append(Spacer(1, 0.5 * inch))
    story.append(Paragraph("COLABERRY", cover_bar))
    story.append(Paragraph("AI Workforce Designer", S["small"]))
    story.append(Spacer(1, 1.5 * inch))
    story.append(Paragraph("AI Transformation Blueprint", S["title"]))
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(f"Prepared exclusively for <b>{company}</b>", S["subtitle"]))
    if contact_name:
        story.append(Paragraph(f"Attention: {contact_name}", S["body"]))
    story.append(Spacer(1, 0.5 * inch))
    story.append(Paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%B %d, %Y')}", S["small"]))
    if lead.get("email"):
        story.append(Paragraph(f"Contact: {lead.get('email', '')}", S["small"]))
    story.append(Spacer(1, 1.5 * inch))
    story.append(Paragraph("Colaberry AI Workforce Designer", S["small"]))
    story.append(Paragraph("Enterprise AI Systems for Business Transformation", S["small"]))
    story.append(Paragraph("colaberry.com", S["small"]))
    # Switch to content template (with headers/footers) for remaining pages
    story.append(NextPageTemplate('content'))
    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════
    # SECTION 1: EXECUTIVE SUMMARY
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("1. Executive Summary", S["h1"]))

    total_annual = cost_savings.get("total_annual", 0) + revenue.get("estimated_annual_revenue_gain", 0)
    agent_count = len([a for a in agents if not a.get("is_cory")])
    dept_count = len(set(a.get("department", "") for a in agents if not a.get("is_cory")))

    story.append(Paragraph(
        f"This document presents a comprehensive AI transformation strategy for {company}. "
        f"Based on a detailed analysis of your business operations, challenges, and goals, "
        f"we have designed an AI Operating System consisting of <b>{agent_count} specialized AI agents</b> "
        f"across <b>{dept_count} departments</b>, coordinated by a central AI Control Tower.",
        S["body"],
    ))
    story.append(Paragraph(
        f"The projected annual impact is <b>{format_currency(total_annual)}</b> in combined cost savings "
        f"and revenue uplift, with a payback period of <b>{roi.get('payback_period_months', 0)} months</b> "
        f"and a three-year ROI of <b>{roi.get('three_year_roi_percent', 0)}%</b>.",
        S["body"],
    ))
    story.append(Spacer(1, 0.2 * inch))

    # Key metrics table
    summary_data = [
        ["Metric", "Value"],
        ["Total AI Agents", str(agent_count)],
        ["Annual Cost Savings", format_currency(cost_savings.get("total_annual", 0))],
        ["Annual Revenue Lift", format_currency(revenue.get("estimated_annual_revenue_gain", 0))],
        ["Combined Annual Impact", format_currency(total_annual)],
        ["Implementation Cost", format_currency(roi.get("implementation_cost", 0))],
        ["Payback Period", f"{roi.get('payback_period_months', 0)} months"],
        ["3-Year ROI", f"{roi.get('three_year_roi_percent', 0)}%"],
    ]
    story.append(_make_table(summary_data, colors, Table, TableStyle, inch, header_color="#1a1a2e"))
    story.append(Spacer(1, 0.3 * inch))

    # ═══════════════════════════════════════════════════════════════
    # SECTION 2: WHAT THIS MEANS FOR YOUR BUSINESS
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("2. What This Means for Your Business", S["h1"]))

    primary_label = problem.get("primary_label", "operational efficiency")
    story.append(Paragraph(
        f"Your organization faces significant challenges in <b>{primary_label.lower()}</b>. "
        f"Current processes rely heavily on manual effort, creating bottlenecks that limit growth, "
        f"increase costs, and reduce your ability to respond quickly to market demands.",
        S["body"],
    ))
    story.append(Paragraph(
        f"The AI system we've designed addresses these challenges directly. Rather than incremental improvements, "
        f"this represents a fundamental shift in how your organization operates. AI agents handle routine decisions, "
        f"automate repetitive workflows, and surface insights that would otherwise go undetected.",
        S["body"],
    ))
    story.append(Paragraph(
        f"The result: your team focuses on high-value strategic work while AI handles the operational load. "
        f"This isn't about replacing people. It's about giving your existing team superpowers.",
        S["body"],
    ))
    story.append(Spacer(1, 0.2 * inch))

    # ═══════════════════════════════════════════════════════════════
    # SECTION 3: CURRENT VS FUTURE STATE
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("3. Current State vs. AI-Enabled Future", S["h1"]))

    comparison_data = [
        ["Area", "Current State", "With AI Workforce"],
        ["Lead Handling", "Manual follow-ups, inconsistent timing", "AI scores and contacts leads in under 30 seconds"],
        ["Customer Support", "Slow response, high ticket backlog", "24/7 AI support with instant triage and resolution"],
        ["Operations", "Manual coordination, reactive management", "Automated workflows with predictive optimization"],
        ["Decision Making", "Gut feel, delayed reporting", "Real-time dashboards with AI-driven insights"],
        ["Scalability", "Growth requires proportional hiring", "Scale operations without proportional headcount"],
    ]
    story.append(_make_table(comparison_data, colors, Table, TableStyle, inch, header_color="#198754", col_widths=[1.3, 2.2, 2.5]))
    story.append(Spacer(1, 0.3 * inch))

    # ═══════════════════════════════════════════════════════════════
    # SECTION 4: HOW YOUR AI ORGANIZATION OPERATES
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("4. How Your AI Organization Operates", S["h1"]))
    story.append(Paragraph(
        f"Your AI Operating System works as an interconnected network of specialized agents, each responsible "
        f"for a specific business function. Data flows between agents automatically. When a customer submits "
        f"a request, the support agent responds instantly. When a lead enters the pipeline, the sales agent "
        f"scores and routes it. When an anomaly is detected, the relevant agent triggers a corrective action.",
        S["body"],
    ))
    story.append(Paragraph(
        f"At the center sits the <b>AI Control Tower</b>, a central intelligence layer that monitors "
        f"all systems simultaneously. It detects cross-department patterns, identifies optimization opportunities, "
        f"and triggers proactive actions before problems escalate. Think of it as an always-on executive "
        f"that never sleeps, never misses a signal, and continuously improves.",
        S["body"],
    ))
    story.append(Spacer(1, 0.2 * inch))

    # ═══════════════════════════════════════════════════════════════
    # SECTION 5: AI WORKFORCE STRUCTURE
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("5. AI Workforce Structure", S["h1"]))

    # Group agents by department
    dept_agents = {}
    for a in agents:
        if a.get("is_cory"):
            continue
        dept = a.get("department", "Other")
        dept_agents.setdefault(dept, []).append(a)

    for dept, dagents in sorted(dept_agents.items()):
        story.append(Paragraph(f"{dept}", S["h2"]))
        agent_data = [["Agent", "Role", "Trigger"]]
        for a in dagents:
            agent_data.append([
                a.get("name", ""),
                a.get("role", "")[:60] + ("..." if len(a.get("role", "")) > 60 else ""),
                a.get("trigger_type", ""),
            ])
        story.append(_make_table(agent_data, colors, Table, TableStyle, inch,
                                 header_color="#4361ee", col_widths=[2.0, 2.8, 1.2]))
        story.append(Spacer(1, 0.1 * inch))

    # AI COO
    coo = next((a for a in agents if a.get("is_cory")), None)
    if coo:
        story.append(Paragraph("AI Control Tower", S["h2"]))
        story.append(Paragraph(coo.get("role", "Central intelligence monitoring all systems."), S["body"]))
    story.append(Spacer(1, 0.2 * inch))

    # ═══════════════════════════════════════════════════════════════
    # SECTION 6: FINANCIAL MODEL
    # ═══════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("6. Financial Impact Model", S["h1"]))

    # Cost savings breakdown
    if cost_savings.get("breakdown"):
        story.append(Paragraph("Cost Savings Breakdown", S["h2"]))
        cs_data = [["Department", "Capability", "Annual Savings"]]
        for item in cost_savings["breakdown"][:8]:
            cs_data.append([
                item.get("department", ""),
                item.get("capability", ""),
                format_currency(item.get("annual_savings", 0)),
            ])
        cs_data.append(["", "TOTAL", format_currency(cost_savings.get("total_annual", 0))])
        story.append(_make_table(cs_data, colors, Table, TableStyle, inch, header_color="#198754", col_widths=[1.5, 2.5, 2.0]))
        story.append(Spacer(1, 0.15 * inch))

    # Revenue lift breakdown
    if revenue.get("channels"):
        story.append(Paragraph("Revenue Lift Breakdown", S["h2"]))
        rv_data = [["Department", "Lift %", "Annual Gain"]]
        for ch in revenue["channels"]:
            rv_data.append([ch.get("department", ""), f"+{ch.get('lift_percent', 0)}%", format_currency(ch.get("estimated_annual_gain", 0))])
        story.append(_make_table(rv_data, colors, Table, TableStyle, inch, header_color="#4361ee", col_widths=[2.0, 1.5, 2.5]))
        story.append(Spacer(1, 0.15 * inch))

    # ROI explanation
    story.append(Paragraph("Return on Investment", S["h2"]))
    story.append(Paragraph(
        f"The total implementation cost of <b>{format_currency(roi.get('implementation_cost', 0))}</b> "
        f"delivers <b>{format_currency(roi.get('annual_benefit', 0))}</b> in annual benefit, "
        f"resulting in a payback period of just <b>{roi.get('payback_period_months', 0)} months</b>. "
        f"Over three years, the projected return is <b>{roi.get('three_year_roi_percent', 0)}%</b> "
        f"on the initial investment.",
        S["body"],
    ))
    story.append(Spacer(1, 0.2 * inch))

    # ═══════════════════════════════════════════════════════════════
    # SECTION 7: COST OF INACTION
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("7. The Cost of Waiting", S["h1"]))
    monthly_loss = opp.get("monthly_cost_of_inaction", 0)
    annual_loss = opp.get("annual_cost_of_inaction", monthly_loss * 12)
    story.append(Paragraph(
        f"Every month without AI automation costs your organization an estimated "
        f"<b>{format_currency(monthly_loss)}</b> in lost efficiency and missed revenue opportunities.",
        S["body"],
    ))
    story.append(Paragraph(
        f"Over 12 months, maintaining current operations could result in over "
        f"<b>{format_currency(annual_loss)}</b> in unrealized value. This compounds as competitors "
        f"adopt AI-driven operations and capture market share.",
        S["body"],
    ))
    risk = opp.get("competitive_risk", "medium")
    story.append(Paragraph(
        f"<b>Competitive Risk Level: {risk.upper()}</b>",
        S["bold"],
    ))
    story.append(Spacer(1, 0.2 * inch))

    # ═══════════════════════════════════════════════════════════════
    # SECTION 8: AI READINESS
    # ═══════════════════════════════════════════════════════════════
    if maturity:
        story.append(Paragraph("8. AI Readiness Assessment", S["h1"]))
        from execution.advisory.maturity_scorer import get_dimension_label, get_maturity_label
        overall = maturity.get("overall", 0)
        story.append(Paragraph(
            f"Your organization scored <b>{overall}/5 ({get_maturity_label(overall)})</b> on AI readiness.",
            S["body"],
        ))
        dims = maturity.get("dimensions", {})
        if dims:
            dim_data = [["Dimension", "Score", "Level"]]
            for dim, score in dims.items():
                dim_data.append([get_dimension_label(dim), f"{score}/5", get_maturity_label(score)])
            story.append(_make_table(dim_data, colors, Table, TableStyle, inch, header_color="#4361ee"))
        story.append(Spacer(1, 0.2 * inch))

    # ═══════════════════════════════════════════════════════════════
    # SECTION 9: 90-DAY IMPLEMENTATION ROADMAP
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("9. 90-Day Implementation Roadmap", S["h1"]))

    story.append(Paragraph("<b>Phase 1: Foundation (Days 1-30)</b>", S["bold"]))
    story.append(Paragraph("- Connect core data sources and configure primary AI agents", S["bullet"]))
    story.append(Paragraph("- Deploy highest-impact automation in primary focus area", S["bullet"]))
    story.append(Paragraph("- Begin seeing measurable results within 2-3 weeks", S["bullet"]))
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("<b>Phase 2: Expansion (Days 30-60)</b>", S["bold"]))
    story.append(Paragraph("- Roll out AI agents to additional departments", S["bullet"]))
    story.append(Paragraph("- Activate cross-department intelligence (AI Control Tower)", S["bullet"]))
    story.append(Paragraph("- Train team on AI collaboration workflows", S["bullet"]))
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("<b>Phase 3: Optimization (Days 60-90)</b>", S["bold"]))
    story.append(Paragraph("- All agents operational across all departments", S["bullet"]))
    story.append(Paragraph("- AI Control Tower detecting patterns and triggering proactive actions", S["bullet"]))
    story.append(Paragraph("- Full ROI realization begins, continuous improvement cycle active", S["bullet"]))
    story.append(Spacer(1, 0.3 * inch))

    # ═══════════════════════════════════════════════════════════════
    # SECTION 10: WHAT THIS ENABLES
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("10. What This Enables", S["h1"]))
    story.append(Paragraph(
        f"This AI transformation positions {company} for sustainable competitive advantage:",
        S["body"],
    ))
    story.append(Paragraph("- <b>Scale without limits:</b> Grow operations without proportional headcount increases", S["bullet"]))
    story.append(Paragraph("- <b>Real-time intelligence:</b> Make decisions based on live data, not monthly reports", S["bullet"]))
    story.append(Paragraph("- <b>Continuous optimization:</b> AI learns and improves every day, compounding gains", S["bullet"]))
    story.append(Paragraph("- <b>Cross-department coordination:</b> Break silos with unified AI orchestration", S["bullet"]))
    story.append(Paragraph("- <b>Proactive management:</b> Detect and resolve issues before they impact the business", S["bullet"]))
    story.append(Spacer(1, 0.5 * inch))

    # Closing
    story.append(Paragraph(
        f"<i>Ready to transform {company}? Schedule a strategy session to discuss "
        f"your personalized AI deployment plan.</i>",
        S["quote"],
    ))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("Colaberry AI Workforce Designer", S["small"]))

    doc.build(story)
    return pdf_path


def _make_table(data, colors, Table, TableStyle, inch, header_color="#4361ee", col_widths=None):
    """Build a styled reportlab table."""
    if col_widths:
        widths = [w * inch for w in col_widths]
    else:
        ncols = len(data[0]) if data else 3
        widths = [(6.0 / ncols) * inch] * ncols

    table = Table(data, colWidths=widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_color)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8f9fc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f2f5")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dee2e6")),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
    ]))
    return table


def _generate_text_fallback(session: dict) -> str:
    """Generate a plain text report when reportlab is unavailable."""
    session_id = session["session_id"]
    output_dir = ADVISORY_OUTPUT_DIR / session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    txt_path = output_dir / "AI_Advisory_Report.txt"

    from execution.advisory.impact_calculator import format_currency
    lead = session.get("lead") or {}
    company = lead.get("company", "Your Organization")
    impact = session.get("impact_model") or {}
    agents = session.get("agents") or []
    cost_savings = impact.get("cost_savings", {})
    revenue = impact.get("revenue_impact", {})
    roi = impact.get("roi_summary", {})

    lines = [
        "AI TRANSFORMATION BLUEPRINT",
        f"Prepared for: {company}",
        f"Generated: {datetime.now(timezone.utc).strftime('%B %d, %Y')}",
        "=" * 50,
        "",
        "EXECUTIVE SUMMARY",
        f"AI Agents: {len([a for a in agents if not a.get('is_cory')])}",
        f"Annual Cost Savings: {format_currency(cost_savings.get('total_annual', 0))}",
        f"Annual Revenue Lift: {format_currency(revenue.get('estimated_annual_revenue_gain', 0))}",
        f"Payback Period: {roi.get('payback_period_months', 0)} months",
        f"3-Year ROI: {roi.get('three_year_roi_percent', 0)}%",
        "",
        "AI WORKFORCE",
    ]
    for a in agents:
        if not a.get("is_cory"):
            lines.append(f"  {a.get('name', '')} ({a.get('department', '')})")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return str(txt_path)
