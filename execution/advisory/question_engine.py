"""Business context question engine for AI Advisory sessions.

10 questions that capture business context — who they are, how they operate,
and what they need. After these questions, users proceed to the capability
selector to choose specific AI capabilities they want.
"""


ADVISORY_QUESTIONS = [
    {
        "id": "q1_business_overview",
        "index": 0,
        "category": "business_objective",
        "text": "What does your business do and what industry are you in?",
        "help_text": "Tell us about your company and market.",
        "examples": [
            "We're a logistics company handling last-mile delivery for e-commerce",
            "B2B SaaS platform for project management with 500 customers",
            "Healthcare staffing agency placing nurses across 3 states",
        ],
    },
    {
        "id": "q2_company_size",
        "index": 1,
        "category": "company_profile",
        "text": "How large is your organization?",
        "help_text": "Employees, locations, revenue range.",
        "options": ["1-10 employees", "11-50 employees", "51-200 employees", "201-1000 employees", "1000+ employees"],
    },
    {
        "id": "q3_departments",
        "index": 2,
        "category": "organization",
        "text": "Which departments are most critical to your operations?",
        "help_text": "Select all that apply or type your own.",
        "options": ["Sales", "Operations", "Customer Support", "Marketing", "Finance", "HR", "Engineering", "Logistics"],
        "multi_select": True,
    },
    {
        "id": "q4_bottlenecks",
        "index": 3,
        "category": "operations",
        "text": "What are your biggest bottlenecks right now?",
        "help_text": "What slows your team down the most?",
        "examples": [
            "Manual data entry consuming hours every day",
            "Slow response times to customer inquiries",
            "Inconsistent follow-ups with leads",
            "Scheduling and coordination across teams",
        ],
    },
    {
        "id": "q5_customer_journey",
        "index": 4,
        "category": "customer",
        "text": "How do customers find you and how do you support them?",
        "help_text": "From first contact through ongoing support.",
        "examples": [
            "Inbound leads from website, sales team follows up, support via email",
            "Referrals and cold outreach, long sales cycle, dedicated account managers",
            "Online marketplace, self-service, chat support",
        ],
    },
    {
        "id": "q6_current_tools",
        "index": 5,
        "category": "technology",
        "text": "What tools and platforms does your team use daily?",
        "help_text": "Select the ones you use or type others.",
        "options": ["Salesforce", "HubSpot", "Slack", "Jira", "Excel/Sheets", "QuickBooks", "Zendesk", "Monday.com", "Custom/Internal"],
        "multi_select": True,
    },
    {
        "id": "q7_data_systems",
        "index": 6,
        "category": "data_infrastructure",
        "text": "How does your team make decisions today?",
        "help_text": "Data-driven or intuition?",
        "options": ["Spreadsheets and manual reports", "BI dashboards (Tableau, Power BI)", "Gut feel and experience", "Mix of data and intuition"],
    },
    {
        "id": "q8_manual_processes",
        "index": 7,
        "category": "automation",
        "text": "What manual work should be automated?",
        "help_text": "Select common ones or describe your own.",
        "examples": [
            "Data entry and report generation",
            "Email follow-ups and scheduling",
            "Invoice processing and expense tracking",
            "Customer ticket routing and responses",
        ],
    },
    {
        "id": "q9_budget_timeline",
        "index": 8,
        "category": "budget",
        "text": "What's your budget and timeline for AI?",
        "help_text": "Approximate range is fine.",
        "options": ["Under $25K", "$25K - $50K", "$50K - $100K", "$100K - $250K", "$250K+"],
    },
    {
        "id": "q10_success_vision",
        "index": 9,
        "category": "output_expectations",
        "text": "What does success look like in 12 months?",
        "help_text": "What outcomes would make this worthwhile?",
        "examples": [
            "Cut operational costs by 30% and eliminate manual work",
            "Double lead conversion rate with automated follow-ups",
            "24/7 customer support without hiring more staff",
            "Real-time visibility into all business operations",
        ],
    },
]

SYSTEM_INTEGRATION_OPTIONS = [
    "CRM (Salesforce, HubSpot, etc.)",
    "ERP (SAP, NetSuite, etc.)",
    "Customer Support (Zendesk, Freshdesk, etc.)",
    "Data Warehouse (Snowflake, BigQuery, etc.)",
    "Marketing Platform (Mailchimp, Marketo, etc.)",
    "Project Management (Jira, Asana, Monday, etc.)",
    "Communication (Slack, Teams, etc.)",
    "Accounting (QuickBooks, Xero, etc.)",
    "E-Commerce (Shopify, WooCommerce, etc.)",
    "HR Platform (Workday, BambooHR, etc.)",
]

TOTAL_QUESTIONS = len(ADVISORY_QUESTIONS)


def get_question(index: int) -> dict | None:
    if 0 <= index < TOTAL_QUESTIONS:
        return ADVISORY_QUESTIONS[index]
    return None


def get_all_questions() -> list[dict]:
    return list(ADVISORY_QUESTIONS)


def get_next_question(session: dict) -> dict | None:
    """Get the next unanswered, non-skipped question."""
    skipped = set(session.get("skipped_questions", []))
    current_index = session.get("current_question_index", 0)

    while current_index < TOTAL_QUESTIONS:
        q = ADVISORY_QUESTIONS[current_index]
        if q["id"] not in skipped:
            return q
        current_index += 1

    return None  # All questions answered or skipped


def get_remaining_question_ids(session: dict) -> list[str]:
    """Get IDs of questions not yet answered or skipped."""
    answered_ids = {a["question_id"] for a in session.get("answers", [])}
    skipped = set(session.get("skipped_questions", []))
    return [q["id"] for q in ADVISORY_QUESTIONS if q["id"] not in answered_ids and q["id"] not in skipped]


def is_complete(session: dict) -> bool:
    """Check if all questions have been answered or skipped."""
    answered = len(session.get("answers", []))
    skipped = len(session.get("skipped_questions", []))
    return (answered + skipped) >= TOTAL_QUESTIONS


def get_progress(session: dict) -> dict:
    answered = len(session.get("answers", []))
    # Questions = 50%, Design = 70%, Capabilities = 90%, Generate = 100%
    has_design = bool(session.get("selected_outcomes") or session.get("selected_ai_systems"))
    has_capabilities = bool(session.get("selected_capabilities"))
    if has_capabilities:
        overall_percent = 90
    elif has_design:
        overall_percent = 70
    else:
        overall_percent = round((answered / TOTAL_QUESTIONS) * 50)
    return {
        "current_index": answered,
        "total": TOTAL_QUESTIONS,
        "percent": overall_percent,
        "is_complete": answered >= TOTAL_QUESTIONS,
    }


def get_answer_by_question_id(session: dict, question_id: str) -> dict | None:
    for answer in session.get("answers", []):
        if answer["question_id"] == question_id:
            return answer
    return None


def get_answers_by_category(session: dict, category: str) -> list[dict]:
    question_ids = {q["id"] for q in ADVISORY_QUESTIONS if q["category"] == category}
    return [a for a in session.get("answers", []) if a["question_id"] in question_ids]
