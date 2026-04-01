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
        "help_text": "Tell us about your company, products/services, and the market you operate in.",
    },
    {
        "id": "q2_company_size",
        "index": 1,
        "category": "company_profile",
        "text": "How large is your organization? (employees, locations, revenue range)",
        "help_text": "This helps us size the AI workforce appropriately for your scale.",
    },
    {
        "id": "q3_departments",
        "index": 2,
        "category": "organization",
        "text": "Which departments are most critical to your business operations?",
        "help_text": "e.g., Sales, Operations, Customer Support, Marketing, Finance, HR, Engineering, Logistics.",
    },
    {
        "id": "q4_bottlenecks",
        "index": 3,
        "category": "operations",
        "text": "What are your biggest operational bottlenecks right now?",
        "help_text": "Think about processes that slow down your team, cause delays, or waste resources.",
    },
    {
        "id": "q5_customer_journey",
        "index": 4,
        "category": "customer",
        "text": "Describe your customer journey — from first contact to ongoing support.",
        "help_text": "How do leads find you? How are they converted? How do you support them after purchase?",
    },
    {
        "id": "q6_current_tools",
        "index": 5,
        "category": "technology",
        "text": "What software tools and platforms does your team use daily?",
        "help_text": "e.g., Salesforce, HubSpot, Slack, Jira, Excel, QuickBooks, Google Workspace, custom tools.",
    },
    {
        "id": "q7_data_systems",
        "index": 6,
        "category": "data_infrastructure",
        "text": "Where does your business data live and how do you use it for decisions?",
        "help_text": "Databases, spreadsheets, data warehouses, BI dashboards, or 'we go by gut feel'.",
    },
    {
        "id": "q8_manual_processes",
        "index": 7,
        "category": "automation",
        "text": "What tasks does your team do manually that feel like they should be automated?",
        "help_text": "Data entry, report generation, email follow-ups, scheduling, invoice processing, etc.",
    },
    {
        "id": "q9_budget_timeline",
        "index": 8,
        "category": "budget",
        "text": "What is your annual technology budget and desired timeline for AI adoption?",
        "help_text": "An approximate range helps us model realistic scenarios. When would you like to see results?",
    },
    {
        "id": "q10_success_vision",
        "index": 9,
        "category": "output_expectations",
        "text": "What would a successful AI transformation look like for your organization in 12 months?",
        "help_text": "Describe the outcomes, metrics, or changes that would make this investment worthwhile.",
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
    current_index = session.get("current_question_index", 0)
    return get_question(current_index)


def is_complete(session: dict) -> bool:
    return len(session.get("answers", [])) >= TOTAL_QUESTIONS


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
