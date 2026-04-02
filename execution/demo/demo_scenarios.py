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

    # ─── 6. UTILITY / ENERGY ──────────────────────────────────
    "utility": {
        "id": "utility",
        "industry": "Utility & Energy",
        "company": {
            "name": "PeakGrid Energy",
            "idea": "We are a regional electric utility serving 380,000 residential and commercial customers across 4 counties. Our biggest challenges are predicting outages before they happen, managing a flood of customer calls during storms, dispatching field crews efficiently to restore power, and keeping up with regulatory compliance reporting. We want AI to predict failures, automate dispatch, handle customer communication, and streamline compliance.",
            "size": "850 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "Regional electric utility serving 380,000 customers across 4 counties. We manage generation, transmission, distribution, and customer service.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "201-1000", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "During storms we get 15,000+ calls in hours. Field crew dispatch is manual and inefficient. We can't predict equipment failures before they cause outages. Compliance reports take a full-time employee.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Operations", "Customer Support", "Field Services", "Finance", "Compliance", "Engineering"], "a": "Operations, Customer Support, Field Services, Compliance", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "reliability", "label": "Improve Grid Reliability", "icon": "bi-lightning-charge", "sel": True},
                {"id": "cx", "label": "Reduce Call Volume", "icon": "bi-headset", "sel": True},
                {"id": "costs", "label": "Reduce Costs", "icon": "bi-piggy-bank", "sel": True},
                {"id": "compliance", "label": "Automate Compliance", "icon": "bi-shield-check", "sel": False}
            ],
            "systems": [
                {"id": "ops", "label": "Grid Operations Engine", "icon": "bi-lightning-charge", "color": "warning", "sel": True},
                {"id": "cust", "label": "Customer Engine", "icon": "bi-people", "color": "info", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "field", "label": "Field Services Engine", "icon": "bi-truck", "color": "success", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Outage Predictor", "dept": "Operations", "primary": True},
            {"name": "Grid Monitor", "dept": "Operations"},
            {"name": "Load Balancer", "dept": "Operations"},
            {"name": "Storm Response Bot", "dept": "Customer Support", "primary": True},
            {"name": "Outage Communicator", "dept": "Customer Support"},
            {"name": "Crew Dispatcher", "dept": "Field Services"},
            {"name": "Asset Inspector", "dept": "Field Services"},
            {"name": "Compliance Reporter", "dept": "Compliance"},
            {"name": "Rate Analyzer", "dept": "Finance"}
        ],
        "kpis": {"savings": 620, "savings_suf": "K", "revenue": 1.8, "revenue_suf": "M", "roi": 290, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Grid scan: 380,000 meters monitored, 7 anomalies detected", "narr": "The AI Control Tower monitors the entire grid in real-time, catching problems before customers notice."},
            {"agent": "Outage Predictor", "action": "Transformer T-4821 showing thermal stress pattern, failure predicted in 48 hours", "narr": "AI predicts equipment failures days before they happen, preventing outages."},
            {"agent": "Crew Dispatcher", "action": "Dispatched maintenance crew to T-4821, ETA 2 hours, zero customer impact", "narr": "Preventive dispatch means fixing problems before anyone loses power."},
            {"agent": "Storm Response Bot", "action": "Incoming storm: auto-sent prep alerts to 42,000 customers in affected zone", "narr": "Proactive communication reduces inbound calls by 60% during storms."},
            {"agent": "Grid Monitor", "action": "Rerouting power through alternate feeders to reduce storm exposure", "narr": "AI reroutes the grid in real-time to minimize storm impact."},
            {"agent": "AI Control Tower", "action": "Storm response: 94% fewer inbound calls vs last comparable event", "narr": "The Control Tower coordinates prediction, dispatch, and communication into one seamless response."}
        ],
        "narr": {
            "idea": "Imagine you run a utility serving 380,000 customers...",
            "questions": "The AI advisor learns about your grid operations...",
            "design": "AI recommends grid operations, customer, and control engines...",
            "results": "Your AI organization: 10 agents, $620K savings, 290% ROI.",
            "sim": "Watch your AI workforce predict outages and coordinate storm response."
        }
    },

    # ─── 7. MANUFACTURING ─────────────────────────────────────
    "manufacturing": {
        "id": "manufacturing",
        "industry": "Manufacturing",
        "company": {
            "name": "Precision Parts Inc",
            "idea": "We are a precision manufacturing company producing automotive and aerospace components with 3 production lines running 24/7. Our biggest issues are unplanned downtime costing us $50K per hour, quality defects caught too late in the process, manual production scheduling, and supply chain disruptions we don't see coming until it's too late.",
            "size": "320 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "Precision manufacturing for automotive and aerospace. 3 production lines running 24/7, producing 50,000 components per week.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "201-1000", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "Unplanned downtime costs $50K/hour. Quality defects caught at final inspection instead of in-process. Production scheduling is a 4-hour manual exercise every morning.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Production", "Quality", "Supply Chain", "Maintenance", "Finance", "Engineering"], "a": "Production, Quality, Maintenance", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "uptime", "label": "Maximize Uptime", "icon": "bi-gear-wide-connected", "sel": True},
                {"id": "quality", "label": "Zero-Defect Quality", "icon": "bi-check-circle", "sel": True},
                {"id": "costs", "label": "Reduce Costs", "icon": "bi-piggy-bank", "sel": True},
                {"id": "supply", "label": "Supply Chain Visibility", "icon": "bi-truck", "sel": False}
            ],
            "systems": [
                {"id": "prod", "label": "Production Engine", "icon": "bi-gear-wide-connected", "color": "warning", "sel": True},
                {"id": "quality", "label": "Quality Engine", "icon": "bi-check-circle", "color": "success", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "supply", "label": "Supply Chain Engine", "icon": "bi-truck", "color": "info", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Predictive Maintenance", "dept": "Production", "primary": True},
            {"name": "Production Scheduler", "dept": "Production"},
            {"name": "OEE Monitor", "dept": "Production"},
            {"name": "Defect Detector", "dept": "Quality", "primary": True},
            {"name": "SPC Analyzer", "dept": "Quality"},
            {"name": "Supplier Monitor", "dept": "Supply Chain"},
            {"name": "Inventory Optimizer", "dept": "Supply Chain"},
            {"name": "Energy Manager", "dept": "Operations"},
            {"name": "Cost Tracker", "dept": "Finance"}
        ],
        "kpis": {"savings": 750, "savings_suf": "K", "revenue": 2.3, "revenue_suf": "M", "roi": 380, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Plant scan: 3 lines running, OEE at 82%, 1 vibration anomaly flagged", "narr": "The AI Control Tower monitors every machine, every second, across all production lines."},
            {"agent": "Predictive Maintenance", "action": "Spindle bearing on Line 2 showing wear pattern, failure predicted in 72 hours", "narr": "AI predicts failures days ahead, turning unplanned downtime into scheduled maintenance."},
            {"agent": "Production Scheduler", "action": "Optimized tomorrow's schedule: rearranged 12 jobs to maximize Line 2 output before maintenance window", "narr": "What took 4 hours of manual scheduling happens in seconds."},
            {"agent": "Defect Detector", "action": "Caught surface micro-crack on part #8847 at Station 3, rejected before next operation", "narr": "AI catches defects in-process, not at final inspection when it's too late."},
            {"agent": "SPC Analyzer", "action": "Process drift detected on Line 1, auto-adjusted parameters to center tolerance", "narr": "Statistical process control runs continuously, not just during audits."},
            {"agent": "AI Control Tower", "action": "Weekly impact: prevented 2 unplanned stops, saving $100K in downtime costs", "narr": "The Control Tower connects maintenance, quality, and scheduling into one intelligent system."}
        ],
        "narr": {
            "idea": "Imagine you run a precision manufacturing plant with 3 production lines...",
            "questions": "The AI advisor learns about your manufacturing operations...",
            "design": "AI recommends production, quality, and control engines...",
            "results": "Your AI organization: 10 agents, $750K savings, 380% ROI.",
            "sim": "Watch your AI workforce prevent downtime and eliminate defects."
        }
    },

    # ─── 8. REAL ESTATE ───────────────────────────────────────
    "realestate": {
        "id": "realestate",
        "industry": "Real Estate",
        "company": {
            "name": "Apex Property Group",
            "idea": "We are a commercial real estate firm managing 45 properties and 2.1 million square feet across 3 metro areas. Our challenges are tenant communication bottlenecks, maintenance requests piling up, lease renewal tracking falling through the cracks, and no visibility into which properties are underperforming until quarterly reviews.",
            "size": "65 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "Commercial real estate firm managing 45 properties, 2.1M sq ft across 3 metro areas. Mixed portfolio: office, retail, and light industrial.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "51-200", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "Maintenance requests take 48 hours to triage. We missed 6 lease renewals last quarter. Tenant satisfaction surveys show communication as the #1 complaint. Portfolio performance is only reviewed quarterly.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Property Management", "Leasing", "Maintenance", "Finance", "Marketing", "Tenant Relations"], "a": "Property Management, Maintenance, Leasing", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "retention", "label": "Improve Tenant Retention", "icon": "bi-people", "sel": True},
                {"id": "ops", "label": "Streamline Operations", "icon": "bi-gear", "sel": True},
                {"id": "revenue", "label": "Maximize Revenue", "icon": "bi-graph-up-arrow", "sel": True},
                {"id": "costs", "label": "Reduce Costs", "icon": "bi-piggy-bank", "sel": False}
            ],
            "systems": [
                {"id": "prop", "label": "Property Engine", "icon": "bi-building", "color": "primary", "sel": True},
                {"id": "tenant", "label": "Tenant Engine", "icon": "bi-people", "color": "info", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "fin", "label": "Finance Engine", "icon": "bi-cash-stack", "color": "danger", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Maintenance Router", "dept": "Property Management", "primary": True},
            {"name": "Property Monitor", "dept": "Property Management"},
            {"name": "Lease Tracker", "dept": "Leasing", "primary": True},
            {"name": "Renewal Negotiator", "dept": "Leasing"},
            {"name": "Tenant Communicator", "dept": "Tenant Relations"},
            {"name": "Satisfaction Monitor", "dept": "Tenant Relations"},
            {"name": "Vendor Manager", "dept": "Maintenance"},
            {"name": "NOI Analyzer", "dept": "Finance"},
            {"name": "Market Comparator", "dept": "Leasing"}
        ],
        "kpis": {"savings": 340, "savings_suf": "K", "revenue": 1.4, "revenue_suf": "M", "roi": 310, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Portfolio scan: 45 properties, 3 maintenance backlogs, 2 leases expiring in 30 days", "narr": "The AI Control Tower monitors your entire portfolio in real-time."},
            {"agent": "Maintenance Router", "action": "Triaged 18 maintenance requests in 12 seconds, dispatched 3 urgent to vendors", "narr": "What took 48 hours of triage now happens instantly."},
            {"agent": "Lease Tracker", "action": "Alert: 2 high-value leases expiring in 30 days, renewal conversations not started", "narr": "AI catches every lease renewal so nothing falls through the cracks."},
            {"agent": "Tenant Communicator", "action": "Sent proactive updates to 12 tenants about upcoming building maintenance", "narr": "Proactive communication turns the #1 complaint into a strength."},
            {"agent": "NOI Analyzer", "action": "Property #23 NOI trending 8% below target, flagged for review", "narr": "Real-time portfolio analytics instead of waiting for quarterly reviews."},
            {"agent": "AI Control Tower", "action": "Impact: tenant retention up 12%, maintenance resolution down from 48h to 4h", "narr": "The Control Tower connects leasing, maintenance, and finance into one operating view."}
        ],
        "narr": {
            "idea": "Imagine you manage 45 commercial properties across 3 cities...",
            "questions": "The AI advisor learns about your property portfolio...",
            "design": "AI recommends property, tenant, and control engines...",
            "results": "Your AI organization: 10 agents, $1.4M revenue impact, 310% ROI.",
            "sim": "Watch your AI workforce manage properties and retain tenants."
        }
    },

    # ─── 9. INSURANCE ─────────────────────────────────────────
    "insurance": {
        "id": "insurance",
        "industry": "Insurance",
        "company": {
            "name": "ShieldPoint Insurance",
            "idea": "We are a mid-size insurance company writing property and casualty policies with 120,000 policyholders. Claims processing takes 14 days on average, underwriting is mostly manual with inconsistent risk assessment, fraud costs us $4M annually, and policy renewals have a 23% lapse rate because we don't engage customers early enough.",
            "size": "280 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "Mid-size P&C insurance company with 120,000 policyholders. We write homeowners, auto, and commercial lines across 8 states.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "201-1000", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "Claims take 14 days average. Underwriting is manual and inconsistent. Fraud costs $4M/year. 23% policy lapse rate because we engage too late on renewals.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Claims", "Underwriting", "Sales", "Customer Service", "Compliance", "Finance"], "a": "Claims, Underwriting, Sales", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "claims", "label": "Accelerate Claims", "icon": "bi-lightning", "sel": True},
                {"id": "fraud", "label": "Detect Fraud", "icon": "bi-shield-exclamation", "sel": True},
                {"id": "retention", "label": "Improve Retention", "icon": "bi-people", "sel": True},
                {"id": "costs", "label": "Reduce Costs", "icon": "bi-piggy-bank", "sel": False}
            ],
            "systems": [
                {"id": "claims", "label": "Claims Engine", "icon": "bi-file-earmark-check", "color": "primary", "sel": True},
                {"id": "underwriting", "label": "Underwriting Engine", "icon": "bi-clipboard-data", "color": "warning", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "cust", "label": "Customer Engine", "icon": "bi-people", "color": "info", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Claims Processor", "dept": "Claims", "primary": True},
            {"name": "Fraud Detector", "dept": "Claims"},
            {"name": "Damage Assessor", "dept": "Claims"},
            {"name": "Risk Scorer", "dept": "Underwriting", "primary": True},
            {"name": "Policy Pricer", "dept": "Underwriting"},
            {"name": "Renewal Agent", "dept": "Sales"},
            {"name": "Policyholder Bot", "dept": "Customer Service"},
            {"name": "Compliance Checker", "dept": "Compliance"},
            {"name": "Loss Ratio Analyst", "dept": "Finance"}
        ],
        "kpis": {"savings": 580, "savings_suf": "K", "revenue": 3.2, "revenue_suf": "M", "roi": 440, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Daily scan: 847 open claims, 12 flagged suspicious, 340 renewals due in 30 days", "narr": "The AI Control Tower monitors claims, fraud, and retention risk across your entire book."},
            {"agent": "Claims Processor", "action": "Auto-processed 23 straightforward claims in 90 seconds (was 14 days)", "narr": "Simple claims resolved instantly. Complex claims fast-tracked with full documentation."},
            {"agent": "Fraud Detector", "action": "Flagged claim #C-48291: inconsistent damage photos + prior claim pattern, confidence 91%", "narr": "AI catches fraud patterns humans miss, saving millions annually."},
            {"agent": "Risk Scorer", "action": "Scored 15 new applications with consistent risk assessment in 8 seconds", "narr": "Underwriting decisions are consistent, fast, and data-driven."},
            {"agent": "Renewal Agent", "action": "Sent personalized renewal offers to 34 at-risk policyholders", "narr": "AI engages customers before they lapse, not after."},
            {"agent": "AI Control Tower", "action": "Impact: claims cycle down 73%, fraud savings $1.2M projected, lapse rate down to 15%", "narr": "The Control Tower connects claims, underwriting, and sales into one intelligent system."}
        ],
        "narr": {
            "idea": "Imagine you run an insurance company with 120,000 policyholders...",
            "questions": "The AI advisor understands your insurance operations...",
            "design": "AI recommends claims, underwriting, and control engines...",
            "results": "Your AI organization: 10 agents, $3.2M revenue impact, 440% ROI.",
            "sim": "Watch your AI workforce process claims and detect fraud."
        }
    },

    # ─── 10. EDUCATION / TRAINING ─────────────────────────────
    "education": {
        "id": "education",
        "industry": "Education & Training",
        "company": {
            "name": "SkillBridge Academy",
            "idea": "We are a workforce training company that delivers corporate upskilling programs to 200+ enterprise clients. Our challenges are creating personalized learning paths for thousands of learners, tracking completion and certification compliance, matching trainers to programs, and demonstrating ROI to corporate clients who are evaluating whether to renew contracts.",
            "size": "95 employees"
        },
        "questions": [
            {"q": "What does your business do?", "a": "Workforce training company delivering corporate upskilling programs to 200+ enterprise clients. We train 15,000+ learners annually across tech, leadership, and compliance.", "method": "type"},
            {"q": "How large is your organization?", "chips": ["1-10", "11-50", "51-200", "201-1000", "1000+"], "a": "51-200", "method": "chip"},
            {"q": "What's your biggest bottleneck?", "a": "Personalized learning paths are created manually for each cohort. Certification tracking is a spreadsheet nightmare. We can't prove ROI to clients at renewal time. Trainer scheduling takes days.", "method": "type"},
            {"q": "Which departments need AI most?", "chips": ["Curriculum", "Operations", "Sales", "Student Support", "Finance", "HR"], "a": "Curriculum, Operations, Sales", "method": "chip", "multi": True}
        ],
        "design": {
            "outcomes": [
                {"id": "personalize", "label": "Personalize Learning", "icon": "bi-mortarboard", "sel": True},
                {"id": "retention", "label": "Improve Client Retention", "icon": "bi-arrow-repeat", "sel": True},
                {"id": "scale", "label": "Scale Operations", "icon": "bi-graph-up-arrow", "sel": True},
                {"id": "costs", "label": "Reduce Costs", "icon": "bi-piggy-bank", "sel": False}
            ],
            "systems": [
                {"id": "learn", "label": "Learning Engine", "icon": "bi-mortarboard", "color": "primary", "sel": True},
                {"id": "ops", "label": "Operations Engine", "icon": "bi-gear-wide-connected", "color": "warning", "sel": True},
                {"id": "tower", "label": "AI Control Tower", "icon": "bi-cpu", "color": "dark", "sel": True},
                {"id": "rev", "label": "Revenue Engine", "icon": "bi-currency-dollar", "color": "success", "sel": False}
            ]
        },
        "agents": [
            {"name": "AI Control Tower", "dept": "Executive", "cory": True},
            {"name": "Path Designer", "dept": "Curriculum", "primary": True},
            {"name": "Content Recommender", "dept": "Curriculum"},
            {"name": "Certification Tracker", "dept": "Operations", "primary": True},
            {"name": "Trainer Scheduler", "dept": "Operations"},
            {"name": "Learner Support Bot", "dept": "Student Support"},
            {"name": "Progress Monitor", "dept": "Student Support"},
            {"name": "ROI Calculator", "dept": "Sales"},
            {"name": "Renewal Predictor", "dept": "Sales"},
            {"name": "Invoice Automator", "dept": "Finance"}
        ],
        "kpis": {"savings": 260, "savings_suf": "K", "revenue": 1.1, "revenue_suf": "M", "roi": 350, "agents": 10},
        "sim": [
            {"agent": "AI Control Tower", "action": "Platform scan: 3,200 active learners, 14 at-risk completions, 8 certifications expiring", "narr": "The AI Control Tower monitors every learner, program, and client across your platform."},
            {"agent": "Path Designer", "action": "Generated personalized learning paths for 120 new learners in Acme Corp cohort", "narr": "AI creates tailored paths based on role, skill gaps, and learning style."},
            {"agent": "Certification Tracker", "action": "Alert: 8 learners need compliance certification renewal within 14 days", "narr": "No more spreadsheet tracking. AI catches every expiration."},
            {"agent": "Trainer Scheduler", "action": "Optimized next week's trainer assignments across 12 programs", "narr": "What took days of coordination happens in seconds."},
            {"agent": "ROI Calculator", "action": "Generated impact report for Acme Corp: 34% skill improvement, $890K business value", "narr": "AI proves ROI to clients automatically, making renewals easy."},
            {"agent": "AI Control Tower", "action": "Client insight: renewal risk detected for 2 accounts, proactive outreach triggered", "narr": "The Control Tower connects learning outcomes, operations, and client success."}
        ],
        "narr": {
            "idea": "Imagine you run a workforce training company with 200+ enterprise clients...",
            "questions": "The AI advisor learns about your training operations...",
            "design": "AI recommends learning, operations, and control engines...",
            "results": "Your AI organization: 10 agents, $1.1M revenue impact, 350% ROI.",
            "sim": "Watch your AI workforce personalize learning and prove ROI."
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
