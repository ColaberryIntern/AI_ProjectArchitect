"""Industry-specific demo scenarios for the guided walkthrough.

Each scenario contains all the data needed for a self-contained,
zero-API-call demo of the AI Workforce Designer.
"""

SCENARIOS = {

    # ─── 1. LOGISTICS ──────────────────────────────────────────
    "logistics": {
        "id": "logistics",
        "industry": "Logistics & Supply Chain",
        "company": {
            "name": "SwiftLogistics",
            "idea": "We are a regional logistics company with 200+ drivers and 15 warehouses. Our biggest challenges are manual route planning that takes 3+ hours daily, no real-time shipment tracking for customers, and slow dispatch processes. We want to automate operations, optimize delivery routes, and provide instant tracking updates.",
            "size": "200 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "We're a regional logistics and freight company specializing in last-mile delivery for e-commerce across the Southeast US. We handle 50,000+ packages per month.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "201-1000", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "Dispatchers spend 3 hours each morning manually assigning routes to 200+ drivers using spreadsheets. Customers call constantly asking where their packages are.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Sales", "Operations", "Customer Support", "Finance", "Logistics"], "a": "Operations, Customer Support, Logistics", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "costs", "label": "Reduce Costs", "icon": "bi-piggy-bank", "sel": True},
                {"id": "scale", "label": "Scale Operations", "icon": "bi-graph-up-arrow", "sel": True},
                {"id": "cx", "label": "Improve CX", "icon": "bi-people", "sel": True},
                {"id": "rev", "label": "Increase Revenue", "icon": "bi-currency-dollar", "sel": False}
            ],
            "systems": [
                {"id": "ops", "label": "Operations Engine", "icon": "bi-gear-wide-connected", "color": "warning", "sel": True},
                {"id": "cust", "label": "Customer Engine", "icon": "bi-people", "color": "info", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "rev", "label": "Revenue Engine", "icon": "bi-currency-dollar", "color": "primary", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Route Optimizer", "dept": "Operations", "primary": True},
            {"name": "Dispatch Agent", "dept": "Operations"},
            {"name": "Warehouse Monitor", "dept": "Operations"},
            {"name": "Tracking Bot", "dept": "Customer Support", "primary": True},
            {"name": "Complaint Resolver", "dept": "Customer Support"},
            {"name": "Lead Qualifier", "dept": "Sales"},
            {"name": "Invoice Processor", "dept": "Finance"},
            {"name": "Cost Analyzer", "dept": "Finance"},
            {"name": "Report Generator", "dept": "Executive"}
        ],
        "kpis": {"savings": 485, "savings_suf": "K", "revenue": 1.2, "revenue_suf": "M", "roi": 340, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Morning scan: 847 deliveries, 3 risk zones flagged", "narr": "The AI Control Tower scans all deliveries and flags risks before drivers leave."},
            {"agent": "Route Optimizer", "action": "Optimized 212 routes, saving 1,840 miles", "narr": "Routes recalculated using real-time traffic and weather data."},
            {"agent": "Dispatch Agent", "action": "Auto-assigned 212 drivers in 8 seconds", "narr": "What took 3 hours now happens in seconds."},
            {"agent": "Tracking Bot", "action": "Answered 34 tracking inquiries simultaneously", "narr": "Customers get instant updates without waiting."},
            {"agent": "AI Control Tower", "action": "Rerouting 8 deliveries around weather zone", "narr": "The Control Tower connects dots across departments."},
            {"agent": "Cost Analyzer", "action": "Weekly savings: $12,400 from route optimization", "narr": "Finance AI tracks exactly how much every optimization saves."}
        ],
        "narr": {
            "idea": "Imagine you run a logistics company with 200 drivers...",
            "questions": "The AI advisor asks a few targeted questions...",
            "design": "The system recommends objectives and AI engines...",
            "results": "Your AI organization: 10 agents, $485K savings, 340% ROI.",
            "sim": "Watch your AI workforce operate autonomously."
        }
    },

    # ─── 2. HEALTHCARE STAFFING ────────────────────────────────
    "healthcare": {
        "id": "healthcare",
        "industry": "Healthcare Staffing",
        "company": {
            "name": "CareStaff Pro",
            "idea": "We are a healthcare staffing agency placing nurses, CNAs, and allied health professionals across 45 facilities in 3 states. Our biggest pain points are manual credential verification taking days, last-minute shift coverage scrambles, and compliance documentation that buries our team in paperwork. We need to automate placement, credentialing, and compliance tracking.",
            "size": "120 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "We're a healthcare staffing agency that places nurses, CNAs, and allied health professionals across 45 facilities in 3 states. We manage over 2,000 active healthcare workers.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "51-200", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "Credential verification takes 3-5 days per candidate. When a facility needs emergency coverage, we're manually calling through lists. Compliance paperwork consumes 40% of our back-office time.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Sales", "Operations", "Customer Support", "HR", "Finance", "Compliance"], "a": "Operations, HR, Compliance", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "speed", "label": "Faster Placements", "icon": "bi-lightning", "sel": True},
                {"id": "compliance", "label": "Automate Compliance", "icon": "bi-shield-check", "sel": True},
                {"id": "scale", "label": "Scale Operations", "icon": "bi-graph-up-arrow", "sel": True},
                {"id": "rev", "label": "Increase Revenue", "icon": "bi-currency-dollar", "sel": False}
            ],
            "systems": [
                {"id": "ops", "label": "Staffing Engine", "icon": "bi-people", "color": "primary", "sel": True},
                {"id": "compliance", "label": "Compliance Engine", "icon": "bi-shield-check", "color": "success", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "comms", "label": "Communication Engine", "icon": "bi-chat-square-text", "color": "info", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Credential Verifier", "dept": "Operations", "primary": True},
            {"name": "Shift Matcher", "dept": "Operations", "primary": True},
            {"name": "Availability Scanner", "dept": "Operations"},
            {"name": "Compliance Monitor", "dept": "Compliance"},
            {"name": "License Tracker", "dept": "Compliance"},
            {"name": "Candidate Screener", "dept": "HR"},
            {"name": "Onboarding Guide", "dept": "HR"},
            {"name": "Invoice Generator", "dept": "Finance"},
            {"name": "Facility Liaison", "dept": "Sales"}
        ],
        "kpis": {"savings": 320, "savings_suf": "K", "revenue": 890, "revenue_suf": "K", "roi": 280, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Morning scan: 12 open shifts across 6 facilities", "narr": "The AI Control Tower identifies all staffing gaps before they become emergencies."},
            {"agent": "Shift Matcher", "action": "Matched 9 of 12 open shifts in 14 seconds", "narr": "AI matches worker availability, credentials, and facility preferences instantly."},
            {"agent": "Credential Verifier", "action": "Verified 3 nurse licenses in 45 seconds (was 3 days)", "narr": "Automated credential checks against state databases in real-time."},
            {"agent": "Compliance Monitor", "action": "Flagged 2 expiring certifications due next week", "narr": "Proactive compliance alerts prevent costly violations."},
            {"agent": "AI Control Tower", "action": "Cross-facility insight: rebalancing 4 nurses for optimal coverage", "narr": "The Control Tower optimizes staffing across all 45 facilities simultaneously."},
            {"agent": "Invoice Generator", "action": "Generated 23 facility invoices with zero errors", "narr": "Billing automated with correct rates, hours, and compliance documentation."}
        ],
        "narr": {
            "idea": "Imagine you run a healthcare staffing agency across 3 states...",
            "questions": "The AI advisor learns about your staffing operations...",
            "design": "AI recommends engines for staffing, compliance, and control...",
            "results": "Your AI organization: 10 agents, $320K savings, 280% ROI.",
            "sim": "Watch your AI workforce handle staffing and compliance."
        }
    },

    # ─── 3. B2B SAAS ──────────────────────────────────────────
    "saas": {
        "id": "saas",
        "industry": "B2B SaaS",
        "company": {
            "name": "ProjectFlow",
            "idea": "We are a B2B SaaS platform for project management with 2,400 customers and $8M ARR. Our sales team can't keep up with inbound leads, our churn rate is climbing because we don't catch at-risk accounts early enough, and our support team spends hours on repetitive tickets. We want AI to qualify leads faster, predict churn, and automate tier-1 support.",
            "size": "85 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "We're a B2B SaaS platform for project management with 2,400 paying customers and $8M ARR. We serve mid-market companies with 50-500 employees.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "51-200", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "Inbound leads sit for 6+ hours before first contact. We've lost deals to competitors who respond faster. Our churn rate hit 4.2% last month and we can't identify at-risk accounts until they cancel.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Sales", "Operations", "Customer Support", "Marketing", "Finance", "Engineering"], "a": "Sales, Customer Support, Marketing", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "rev", "label": "Grow Revenue", "icon": "bi-currency-dollar", "sel": True},
                {"id": "churn", "label": "Reduce Churn", "icon": "bi-arrow-down-circle", "sel": True},
                {"id": "cx", "label": "Improve CX", "icon": "bi-people", "sel": True},
                {"id": "costs", "label": "Reduce Costs", "icon": "bi-piggy-bank", "sel": False}
            ],
            "systems": [
                {"id": "rev", "label": "Revenue Engine", "icon": "bi-currency-dollar", "color": "primary", "sel": True},
                {"id": "cust", "label": "Customer Engine", "icon": "bi-people", "color": "info", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "mktg", "label": "Marketing Engine", "icon": "bi-megaphone", "color": "success", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Lead Scorer", "dept": "Sales", "primary": True},
            {"name": "Pipeline Manager", "dept": "Sales"},
            {"name": "Outreach Agent", "dept": "Sales"},
            {"name": "Churn Predictor", "dept": "Customer Support", "primary": True},
            {"name": "Support Bot", "dept": "Customer Support"},
            {"name": "Onboarding Guide", "dept": "Customer Support"},
            {"name": "Campaign Optimizer", "dept": "Marketing"},
            {"name": "Revenue Forecaster", "dept": "Finance"},
            {"name": "Usage Analyzer", "dept": "Engineering"}
        ],
        "kpis": {"savings": 290, "savings_suf": "K", "revenue": 1.8, "revenue_suf": "M", "roi": 420, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Daily pipeline scan: 47 new leads, 3 at-risk accounts", "narr": "The AI Control Tower monitors your entire revenue pipeline in real-time."},
            {"agent": "Lead Scorer", "action": "Scored 47 leads in 3 seconds. 8 marked as high-intent.", "narr": "AI scores every lead instantly so sales focuses on the best opportunities."},
            {"agent": "Outreach Agent", "action": "Sent personalized emails to 8 high-intent leads", "narr": "First touch in under 30 seconds. Competitors can't match that speed."},
            {"agent": "Churn Predictor", "action": "Flagged 3 accounts showing disengagement patterns", "narr": "AI catches churn signals weeks before the customer thinks about leaving."},
            {"agent": "Support Bot", "action": "Resolved 23 tier-1 tickets autonomously", "narr": "Repetitive support questions handled instantly without human involvement."},
            {"agent": "AI Control Tower", "action": "Revenue insight: trial-to-paid conversion up 18% this week", "narr": "The Control Tower connects marketing, sales, and support data for executive insights."}
        ],
        "narr": {
            "idea": "Imagine you run a B2B SaaS platform with 2,400 customers...",
            "questions": "The AI advisor understands your SaaS business model...",
            "design": "AI recommends revenue, customer, and intelligence engines...",
            "results": "Your AI organization: 10 agents, $1.8M revenue impact, 420% ROI.",
            "sim": "Watch your AI workforce drive revenue and reduce churn."
        }
    },

    # ─── 4. E-COMMERCE / RETAIL ────────────────────────────────
    "ecommerce": {
        "id": "ecommerce",
        "industry": "E-Commerce & Retail",
        "company": {
            "name": "UrbanThreads",
            "idea": "We are an online fashion retailer doing $12M in annual revenue with 180,000 customers. Our biggest problems are abandoned carts (68% rate), slow customer service responses on weekends, inventory that runs out of trending items too late, and marketing campaigns that aren't personalized. We want AI to recover carts, predict inventory needs, personalize marketing, and handle support 24/7.",
            "size": "45 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "We're an online fashion retailer doing $12M annually with 180,000 customers. We sell direct-to-consumer through our website and social channels.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "11-50", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "68% cart abandonment rate is killing revenue. Our 3-person support team can't handle weekend volume. We keep running out of trending items because we can't predict demand fast enough.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Sales", "Operations", "Customer Support", "Marketing", "Finance", "Logistics"], "a": "Marketing, Customer Support, Operations", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "rev", "label": "Recover Revenue", "icon": "bi-cart-check", "sel": True},
                {"id": "cx", "label": "24/7 Customer Support", "icon": "bi-headset", "sel": True},
                {"id": "inventory", "label": "Optimize Inventory", "icon": "bi-box-seam", "sel": True},
                {"id": "costs", "label": "Reduce Costs", "icon": "bi-piggy-bank", "sel": False}
            ],
            "systems": [
                {"id": "rev", "label": "Revenue Engine", "icon": "bi-currency-dollar", "color": "primary", "sel": True},
                {"id": "cust", "label": "Customer Engine", "icon": "bi-people", "color": "info", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "ops", "label": "Operations Engine", "icon": "bi-gear-wide-connected", "color": "warning", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Cart Recovery Agent", "dept": "Marketing", "primary": True},
            {"name": "Personalization Engine", "dept": "Marketing"},
            {"name": "Campaign Optimizer", "dept": "Marketing"},
            {"name": "Shopping Assistant", "dept": "Customer Support", "primary": True},
            {"name": "Returns Handler", "dept": "Customer Support"},
            {"name": "Demand Forecaster", "dept": "Operations"},
            {"name": "Inventory Monitor", "dept": "Operations"},
            {"name": "Fraud Detector", "dept": "Finance"},
            {"name": "Revenue Tracker", "dept": "Finance"}
        ],
        "kpis": {"savings": 180, "savings_suf": "K", "revenue": 2.1, "revenue_suf": "M", "roi": 510, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Real-time scan: 342 active shoppers, 28 abandoned carts in last hour", "narr": "The AI Control Tower monitors your entire store in real-time."},
            {"agent": "Cart Recovery Agent", "action": "Sent 28 personalized recovery emails with dynamic discounts", "narr": "AI recovers abandoned carts within minutes with personalized incentives."},
            {"agent": "Personalization Engine", "action": "Updated recommendations for 12,000 returning visitors", "narr": "Every customer sees products matched to their browsing and purchase history."},
            {"agent": "Shopping Assistant", "action": "Handled 47 customer inquiries simultaneously (sizing, shipping, returns)", "narr": "24/7 support without weekend staffing issues."},
            {"agent": "Demand Forecaster", "action": "Alert: 'Oversized Blazer' trending on TikTok, projected +400% demand", "narr": "AI spots trends before they hit your store so you never run out of stock."},
            {"agent": "AI Control Tower", "action": "Revenue insight: cart recovery campaign driving $8,200/day in recovered sales", "narr": "The Control Tower connects marketing, support, and operations into one revenue picture."}
        ],
        "narr": {
            "idea": "Imagine you run a $12M online fashion retailer...",
            "questions": "The AI advisor learns about your e-commerce challenges...",
            "design": "AI recommends revenue, customer, and intelligence engines...",
            "results": "Your AI organization: 10 agents, $2.1M revenue impact, 510% ROI.",
            "sim": "Watch your AI workforce recover carts and boost sales."
        }
    },

    # ─── 5. PROFESSIONAL SERVICES / CONSULTING ─────────────────
    "consulting": {
        "id": "consulting",
        "industry": "Professional Services",
        "company": {
            "name": "Meridian Consulting Group",
            "idea": "We are a management consulting firm with 150 consultants across 4 offices. Our biggest challenges are proposal writing taking 2-3 weeks per RFP, knowledge trapped in individual consultants' heads rather than shared across the firm, utilization tracking that's always 2 weeks behind reality, and client reporting that takes consultants away from billable work. We want AI to accelerate proposals, centralize knowledge, and automate reporting.",
            "size": "150 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "We're a management consulting firm with 150 consultants across 4 offices, serving Fortune 500 clients in digital transformation, operations, and strategy engagements.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "51-200", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "Proposals take 2-3 weeks to write. Our best knowledge is locked in senior partners' heads. Utilization reports are always 2 weeks behind, so we can't make staffing decisions fast enough.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Sales", "Operations", "Customer Support", "Marketing", "Finance", "HR"], "a": "Sales, Operations, Finance", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "speed", "label": "Faster Proposals", "icon": "bi-lightning", "sel": True},
                {"id": "knowledge", "label": "Centralize Knowledge", "icon": "bi-book", "sel": True},
                {"id": "util", "label": "Optimize Utilization", "icon": "bi-speedometer2", "sel": True},
                {"id": "rev", "label": "Grow Revenue", "icon": "bi-currency-dollar", "sel": False}
            ],
            "systems": [
                {"id": "rev", "label": "Revenue Engine", "icon": "bi-currency-dollar", "color": "primary", "sel": True},
                {"id": "ops", "label": "Operations Engine", "icon": "bi-gear-wide-connected", "color": "warning", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "knowledge", "label": "Knowledge Engine", "icon": "bi-book", "color": "success", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Proposal Writer", "dept": "Sales", "primary": True},
            {"name": "RFP Analyzer", "dept": "Sales"},
            {"name": "Knowledge Search", "dept": "Operations", "primary": True},
            {"name": "Utilization Tracker", "dept": "Operations"},
            {"name": "Staffing Optimizer", "dept": "Operations"},
            {"name": "Client Reporter", "dept": "Finance"},
            {"name": "Time Entry Agent", "dept": "Finance"},
            {"name": "Talent Matcher", "dept": "HR"},
            {"name": "Meeting Summarizer", "dept": "Executive"}
        ],
        "kpis": {"savings": 410, "savings_suf": "K", "revenue": 1.5, "revenue_suf": "M", "roi": 360, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Weekly scan: 6 active proposals, 3 understaffed projects, 12 upcoming deadlines", "narr": "The AI Control Tower gives partners a real-time view across all engagements."},
            {"agent": "RFP Analyzer", "action": "Analyzed incoming RFP: 80% match to previous healthcare transformation win", "narr": "AI matches new RFPs to past wins, identifying your competitive advantage instantly."},
            {"agent": "Proposal Writer", "action": "Generated first draft in 4 hours (was 2 weeks)", "narr": "AI drafts proposals using your firm's best past work and tailored to the RFP requirements."},
            {"agent": "Knowledge Search", "action": "Found 12 relevant case studies across 3 practice areas", "narr": "No more knowledge trapped in individual consultants' heads."},
            {"agent": "Utilization Tracker", "action": "Real-time utilization: 78%. Flagged 4 consultants below target.", "narr": "Live utilization data so partners can make staffing decisions today, not in 2 weeks."},
            {"agent": "AI Control Tower", "action": "Revenue insight: proposal win rate up 23% since AI-assisted drafting began", "narr": "The Control Tower connects sales, operations, and finance for executive decision-making."}
        ],
        "narr": {
            "idea": "Imagine you run a consulting firm with 150 consultants...",
            "questions": "The AI advisor understands your professional services model...",
            "design": "AI recommends revenue, operations, and intelligence engines...",
            "results": "Your AI organization: 10 agents, $1.5M revenue impact, 360% ROI.",
            "sim": "Watch your AI workforce accelerate proposals and optimize utilization."
        }
    },
}


def get_scenario(scenario_id: str) -> dict:
    """Return a demo scenario by ID, defaulting to logistics."""
    return SCENARIOS.get(scenario_id, SCENARIOS["logistics"])


def list_scenarios() -> list[dict]:
    """Return summary list of all available scenarios."""
    return [
        {"id": s["id"], "industry": s["industry"], "company": s["company"]["name"]}
        for s in SCENARIOS.values()
    ]
