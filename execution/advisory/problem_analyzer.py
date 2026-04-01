"""Problem analysis engine for AI Advisory.

Extracts the user's primary and secondary business problems from their
answers, classifies them into domains, and produces confidence-scored
problem weights that drive the entire architecture generation.
"""

import re

# ── Problem Domain Definitions ──────────────────────────────────────

PROBLEM_DOMAINS = {
    "operations": {
        "label": "Operations & Logistics",
        "keywords": [
            "routing", "dispatch", "schedule", "scheduling", "delivery",
            "warehouse", "inventory", "logistics", "shipping", "fleet",
            "manufacturing", "supply chain", "coordination", "allocation",
            "workflow", "process", "bottleneck", "delay", "capacity",
            "production", "fulfillment", "tracking", "optimization",
        ],
    },
    "sales": {
        "label": "Sales & Revenue",
        "keywords": [
            "lead", "leads", "conversion", "pipeline", "sales",
            "prospect", "outreach", "follow-up", "follow up", "close",
            "deal", "revenue", "quota", "crm", "upsell", "proposal",
            "pricing", "demo", "meeting", "cold call",
        ],
    },
    "support": {
        "label": "Customer Support",
        "keywords": [
            "support", "ticket", "customer service", "response time",
            "complaint", "help desk", "chat", "call center", "escalation",
            "satisfaction", "nps", "churn", "retention", "resolution",
            "sla", "wait time",
        ],
    },
    "marketing": {
        "label": "Marketing & Growth",
        "keywords": [
            "marketing", "campaign", "content", "social media", "seo",
            "advertising", "brand", "email marketing", "newsletter",
            "engagement", "traffic", "audience", "funnel", "awareness",
        ],
    },
    "finance": {
        "label": "Finance & Accounting",
        "keywords": [
            "invoice", "invoicing", "billing", "payment", "accounting",
            "expense", "budget", "forecast", "reconciliation", "audit",
            "cash flow", "financial", "cost", "profitability",
        ],
    },
    "data": {
        "label": "Data & Intelligence",
        "keywords": [
            "data", "report", "reporting", "dashboard", "analytics",
            "insight", "visibility", "decision", "metric", "kpi",
            "tracking", "monitoring", "intelligence", "prediction",
        ],
    },
    "hr": {
        "label": "People & HR",
        "keywords": [
            "hiring", "recruit", "onboarding", "training", "employee",
            "hr", "talent", "performance review", "payroll", "retention",
            "staffing", "headcount",
        ],
    },
    "communication": {
        "label": "Communication & Collaboration",
        "keywords": [
            "email", "meeting", "communication", "collaboration",
            "document", "knowledge", "notification", "messaging",
        ],
    },
}


def analyze_problems(session: dict) -> dict:
    """Analyze the user's business problems from session data.

    Scans the business idea and all answers, scores each problem domain,
    and returns a weighted classification.

    Returns:
        Dict with primary_problem, secondary_problems, domain_weights,
        detected_keywords, and confidence.
    """
    idea = session.get("business_idea", "")
    answers = session.get("answers", [])

    # Build text corpus with weighted sections
    # Idea and bottleneck answers get higher weight
    texts = {"idea": idea}
    for a in answers:
        qid = a.get("question_id", "")
        text = a.get("answer_text", "")
        texts[qid] = text

    # Score each domain
    domain_scores = {}
    detected_keywords = {}

    for domain_id, domain in PROBLEM_DOMAINS.items():
        score = 0
        found_keywords = []

        for source_id, text in texts.items():
            text_lower = text.lower()
            # Weight multiplier: idea and bottleneck answers count more
            weight = 1.0
            if source_id == "idea":
                weight = 3.0
            elif source_id in ("q4_bottlenecks", "q8_manual_processes"):
                weight = 2.0
            elif source_id in ("q5_customer_journey", "q3_departments"):
                weight = 1.5

            for keyword in domain["keywords"]:
                count = len(re.findall(r'\b' + re.escape(keyword) + r'\b', text_lower))
                if count > 0:
                    score += count * weight
                    if keyword not in found_keywords:
                        found_keywords.append(keyword)

        domain_scores[domain_id] = round(score, 1)
        if found_keywords:
            detected_keywords[domain_id] = found_keywords

    # Normalize to weights (0-1, summing to 1)
    total = sum(domain_scores.values())
    if total == 0:
        # No signals — distribute evenly
        n = len(PROBLEM_DOMAINS)
        domain_weights = {d: round(1.0 / n, 3) for d in PROBLEM_DOMAINS}
        return {
            "primary_problem": None,
            "secondary_problems": [],
            "domain_weights": domain_weights,
            "detected_keywords": {},
            "confidence": 0.0,
        }

    domain_weights = {
        d: round(score / total, 3) for d, score in domain_scores.items()
    }

    # Sort by weight descending
    sorted_domains = sorted(domain_weights.items(), key=lambda x: x[1], reverse=True)

    primary = sorted_domains[0]
    primary_id = primary[0]
    primary_weight = primary[1]

    # Secondary: anything with weight > 0.1
    secondary = [d for d, w in sorted_domains[1:] if w >= 0.1]

    # Confidence: how dominant is the primary problem?
    confidence = min(primary_weight * 2, 1.0)  # 0.5 weight = 1.0 confidence

    return {
        "primary_problem": primary_id,
        "primary_label": PROBLEM_DOMAINS[primary_id]["label"],
        "primary_weight": primary_weight,
        "secondary_problems": secondary,
        "domain_weights": domain_weights,
        "detected_keywords": detected_keywords,
        "confidence": round(confidence, 2),
    }


def get_domain_label(domain_id: str) -> str:
    """Get human-readable label for a domain."""
    domain = PROBLEM_DOMAINS.get(domain_id)
    return domain["label"] if domain else domain_id.replace("_", " ").title()
