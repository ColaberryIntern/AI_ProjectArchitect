"""Business capability catalog for AI Advisory.

A curated catalog of ~45 business capabilities organized by department.
Each capability has a business-friendly name and description (shown to users)
plus hidden technical mappings to AI agents, MCP servers, and skills.

Users select capabilities they want. The system maps selections to the
technical architecture needed to deliver them.
"""

CAPABILITY_CATALOG = [
    # ── Sales ────────────────────────────────────────────────────────
    {
        "id": "auto_lead_scoring",
        "name": "Automated Lead Scoring",
        "description": "Automatically score and rank incoming leads based on engagement, fit, and buying signals",
        "department": "Sales",
        "category": "Lead Management",
        "agents": ["AI Lead Qualifier"],
        "mcp_servers": ["mcp_slack", "mcp_google_drive"],
        "skills": ["crm_lead", "recommendation_engine", "data_analytics"],
        "skill_categories": ["Automation & Integration", "AI Agent Frameworks"],
    },
    {
        "id": "sales_pipeline_forecast",
        "name": "Sales Pipeline Forecasting",
        "description": "Predict deal outcomes and revenue with AI-driven pipeline analysis",
        "department": "Sales",
        "category": "Revenue Intelligence",
        "agents": ["AI Sales Forecaster"],
        "mcp_servers": ["mcp_postgres", "mcp_google_drive"],
        "skills": ["data_analytics", "sql_query_tool", "trend_scanner"],
        "skill_categories": ["Data & RAG", "AI Agent Frameworks"],
    },
    {
        "id": "outreach_automation",
        "name": "Outreach & Follow-up Automation",
        "description": "Automate email sequences, follow-ups, and touchpoints based on lead behavior",
        "department": "Sales",
        "category": "Lead Management",
        "agents": ["AI Outreach Manager"],
        "mcp_servers": ["mcp_slack", "mcp_google_drive"],
        "skills": ["email_sender", "workflow_automation", "content_generator"],
        "skill_categories": ["Automation & Integration", "Communication & Collaboration"],
    },
    {
        "id": "deal_intelligence",
        "name": "Deal Intelligence & Coaching",
        "description": "Get AI-powered insights on active deals with recommended next actions",
        "department": "Sales",
        "category": "Revenue Intelligence",
        "agents": ["AI Deal Advisor"],
        "mcp_servers": ["mcp_postgres"],
        "skills": ["recommendation_engine", "sentiment_analyzer", "data_analytics"],
        "skill_categories": ["AI Agent Frameworks", "LLM Tool Libraries"],
    },
    {
        "id": "proposal_generator",
        "name": "Proposal & Quote Generation",
        "description": "Auto-generate customized proposals and quotes from templates and deal data",
        "department": "Sales",
        "category": "Sales Operations",
        "agents": ["AI Proposal Writer"],
        "mcp_servers": ["mcp_google_drive", "mcp_filesystem"],
        "skills": ["content_generator", "pdf_generator", "document_loader"],
        "skill_categories": ["LLM Tool Libraries", "Data & RAG"],
    },

    # ── Customer Support ─────────────────────────────────────────────
    {
        "id": "ai_chat_support",
        "name": "24/7 AI Chat Support",
        "description": "AI-powered chat that handles common questions and routes complex issues to your team",
        "department": "Customer Support",
        "category": "Customer Engagement",
        "agents": ["AI Support Agent", "AI Chat Concierge"],
        "mcp_servers": ["mcp_slack", "mcp_memory"],
        "skills": ["chatbot_builder", "knowledge_base_retrieval", "sentiment_analyzer"],
        "skill_categories": ["AI Agent Frameworks", "Data & RAG"],
    },
    {
        "id": "ticket_auto_triage",
        "name": "Smart Ticket Routing & Triage",
        "description": "Automatically categorize, prioritize, and route support tickets to the right team",
        "department": "Customer Support",
        "category": "Support Operations",
        "agents": ["AI Ticket Triage Bot"],
        "mcp_servers": ["mcp_linear", "mcp_slack"],
        "skills": ["ticket_creation", "sentiment_analyzer", "workflow_automation"],
        "skill_categories": ["Automation & Integration", "AI Agent Frameworks"],
    },
    {
        "id": "sentiment_monitoring",
        "name": "Customer Sentiment Monitoring",
        "description": "Track customer happiness across all channels and get alerts on negative trends",
        "department": "Customer Support",
        "category": "Customer Intelligence",
        "agents": ["AI Sentiment Analyst"],
        "mcp_servers": ["mcp_postgres", "mcp_slack"],
        "skills": ["sentiment_analyzer", "data_analytics", "notification_hub", "trend_scanner"],
        "skill_categories": ["LLM Tool Libraries", "Data & RAG"],
    },
    {
        "id": "knowledge_base_qa",
        "name": "Intelligent Knowledge Base",
        "description": "AI that answers questions from your docs, SOPs, and training materials instantly",
        "department": "Customer Support",
        "category": "Knowledge Management",
        "agents": ["AI Knowledge Assistant"],
        "mcp_servers": ["mcp_filesystem", "mcp_google_drive", "mcp_memory"],
        "skills": ["rag_pipeline", "knowledge_base_retrieval", "document_loader", "embedding_generator"],
        "skill_categories": ["Data & RAG", "LLM Tool Libraries"],
    },
    {
        "id": "churn_prediction",
        "name": "Customer Churn Prediction",
        "description": "Identify at-risk customers before they leave with predictive AI analysis",
        "department": "Customer Support",
        "category": "Customer Intelligence",
        "agents": ["AI Retention Analyst"],
        "mcp_servers": ["mcp_postgres"],
        "skills": ["data_analytics", "recommendation_engine", "notification_hub"],
        "skill_categories": ["Data & RAG", "AI Agent Frameworks"],
    },

    # ── Marketing ────────────────────────────────────────────────────
    {
        "id": "content_generation",
        "name": "AI Content Creation",
        "description": "Generate blog posts, emails, social media content, and ad copy at scale",
        "department": "Marketing",
        "category": "Content",
        "agents": ["AI Content Strategist"],
        "mcp_servers": ["mcp_google_drive"],
        "skills": ["content_generator", "social_posting", "translation_service"],
        "skill_categories": ["LLM Tool Libraries", "Communication & Collaboration"],
    },
    {
        "id": "campaign_optimization",
        "name": "Campaign Performance Optimizer",
        "description": "AI analyzes campaign data and recommends budget, timing, and targeting changes",
        "department": "Marketing",
        "category": "Campaign Management",
        "agents": ["AI Campaign Manager"],
        "mcp_servers": ["mcp_postgres", "mcp_google_drive"],
        "skills": ["data_analytics", "ad_campaign_creator", "marketing_campaign", "ab_testing"],
        "skill_categories": ["Automation & Integration", "Data & RAG"],
    },
    {
        "id": "audience_segmentation",
        "name": "Smart Audience Segmentation",
        "description": "Automatically segment your audience based on behavior, demographics, and engagement",
        "department": "Marketing",
        "category": "Campaign Management",
        "agents": ["AI Audience Analyst"],
        "mcp_servers": ["mcp_postgres"],
        "skills": ["data_analytics", "recommendation_engine", "sql_query_tool"],
        "skill_categories": ["Data & RAG", "AI Agent Frameworks"],
    },
    {
        "id": "social_media_management",
        "name": "Social Media Management",
        "description": "Schedule, publish, and analyze social media posts across platforms",
        "department": "Marketing",
        "category": "Content",
        "agents": ["AI Social Media Manager"],
        "mcp_servers": ["mcp_slack"],
        "skills": ["social_posting", "content_generator", "notification_scheduler"],
        "skill_categories": ["Communication & Collaboration", "Automation & Integration"],
    },
    {
        "id": "seo_optimization",
        "name": "SEO & Search Optimization",
        "description": "AI-powered keyword research, content optimization, and ranking tracking",
        "department": "Marketing",
        "category": "Content",
        "agents": ["AI SEO Analyst"],
        "mcp_servers": ["mcp_brave_search"],
        "skills": ["web_search", "web_scraper", "content_generator", "data_analytics"],
        "skill_categories": ["Data & RAG", "LLM Tool Libraries"],
    },

    # ── Operations ───────────────────────────────────────────────────
    {
        "id": "workflow_automation",
        "name": "Workflow & Process Automation",
        "description": "Automate repetitive business processes and approval workflows",
        "department": "Operations",
        "category": "Process Automation",
        "agents": ["AI Process Optimizer"],
        "mcp_servers": ["mcp_slack", "mcp_google_drive"],
        "skills": ["workflow_automation", "notification_hub", "queue_processor"],
        "skill_categories": ["Automation & Integration"],
    },
    {
        "id": "resource_scheduling",
        "name": "Intelligent Resource Scheduling",
        "description": "Optimize staff schedules, room bookings, and resource allocation with AI",
        "department": "Operations",
        "category": "Resource Management",
        "agents": ["AI Resource Scheduler"],
        "mcp_servers": ["mcp_google_drive", "mcp_postgres"],
        "skills": ["calendar_scheduling", "recommendation_engine", "data_analytics"],
        "skill_categories": ["Automation & Integration", "AI Agent Frameworks"],
    },
    {
        "id": "inventory_optimization",
        "name": "Inventory & Supply Chain Optimization",
        "description": "Predict demand, optimize stock levels, and automate reordering",
        "department": "Operations",
        "category": "Supply Chain",
        "agents": ["AI Supply Chain Manager", "AI Inventory Optimizer"],
        "mcp_servers": ["mcp_postgres"],
        "skills": ["data_analytics", "sql_query_tool", "anomaly_detection", "workflow_automation"],
        "skill_categories": ["Data & RAG", "Monitoring & Observability"],
    },
    {
        "id": "quality_monitoring",
        "name": "Quality & Process Monitoring",
        "description": "Real-time monitoring of operational quality with automated alerts",
        "department": "Operations",
        "category": "Process Automation",
        "agents": ["AI Quality Monitor"],
        "mcp_servers": ["mcp_postgres", "mcp_slack"],
        "skills": ["anomaly_detection", "notification_hub", "data_analytics"],
        "skill_categories": ["Monitoring & Observability", "Data & RAG"],
    },
    {
        "id": "route_optimization",
        "name": "Route & Delivery Optimization",
        "description": "Optimize delivery routes, dispatching, and logistics scheduling",
        "department": "Operations",
        "category": "Supply Chain",
        "agents": ["AI Dispatch Optimizer"],
        "mcp_servers": ["mcp_postgres"],
        "skills": ["recommendation_engine", "geo_location", "data_analytics"],
        "skill_categories": ["AI Agent Frameworks", "Data & RAG"],
    },
    {
        "id": "project_management",
        "name": "AI Project Coordination",
        "description": "Auto-assign tasks, track progress, and predict project risks",
        "department": "Operations",
        "category": "Resource Management",
        "agents": ["AI Project Coordinator"],
        "mcp_servers": ["mcp_linear", "mcp_github", "mcp_slack"],
        "skills": ["ticket_creation", "workflow_automation", "notification_hub"],
        "skill_categories": ["Automation & Integration", "Communication & Collaboration"],
    },

    # ── Finance ──────────────────────────────────────────────────────
    {
        "id": "invoice_processing",
        "name": "Automated Invoice Processing",
        "description": "Extract, validate, and process invoices automatically with AI",
        "department": "Finance",
        "category": "Accounts & Billing",
        "agents": ["AI Invoice Processor"],
        "mcp_servers": ["mcp_postgres", "mcp_google_drive"],
        "skills": ["ocr_processor", "data_analytics", "pdf_generator", "workflow_automation"],
        "skill_categories": ["Data & RAG", "Automation & Integration"],
    },
    {
        "id": "expense_categorization",
        "name": "Smart Expense Categorization",
        "description": "Automatically categorize and code expenses with AI pattern recognition",
        "department": "Finance",
        "category": "Accounts & Billing",
        "agents": ["AI Expense Analyst"],
        "mcp_servers": ["mcp_postgres"],
        "skills": ["data_analytics", "sql_query_tool", "csv_processor"],
        "skill_categories": ["Data & RAG"],
    },
    {
        "id": "financial_forecasting",
        "name": "Financial Forecasting & Planning",
        "description": "AI-powered cash flow predictions, budget scenarios, and financial modeling",
        "department": "Finance",
        "category": "Financial Intelligence",
        "agents": ["AI Finance Analyst"],
        "mcp_servers": ["mcp_postgres", "mcp_google_drive"],
        "skills": ["data_analytics", "trend_scanner", "sql_query_tool", "pdf_generator"],
        "skill_categories": ["Data & RAG", "LLM Tool Libraries"],
    },
    {
        "id": "fraud_detection",
        "name": "Fraud & Anomaly Detection",
        "description": "Monitor transactions for unusual patterns and potential fraud in real-time",
        "department": "Finance",
        "category": "Financial Intelligence",
        "agents": ["AI Fraud Monitor"],
        "mcp_servers": ["mcp_postgres"],
        "skills": ["anomaly_detection", "data_analytics", "notification_hub"],
        "skill_categories": ["Monitoring & Observability", "Data & RAG"],
    },

    # ── Human Resources ──────────────────────────────────────────────
    {
        "id": "resume_screening",
        "name": "AI Resume Screening & Ranking",
        "description": "Automatically screen resumes and rank candidates based on job requirements",
        "department": "Human Resources",
        "category": "Talent Acquisition",
        "agents": ["AI Talent Screener"],
        "mcp_servers": ["mcp_google_drive", "mcp_filesystem"],
        "skills": ["document_loader", "recommendation_engine", "content_generator"],
        "skill_categories": ["Data & RAG", "AI Agent Frameworks"],
    },
    {
        "id": "onboarding_automation",
        "name": "Employee Onboarding Automation",
        "description": "Guided onboarding workflows with automated tasks, reminders, and training delivery",
        "department": "Human Resources",
        "category": "Employee Experience",
        "agents": ["AI Onboarding Coordinator"],
        "mcp_servers": ["mcp_google_drive", "mcp_slack"],
        "skills": ["workflow_automation", "email_sender", "calendar_scheduling", "notification_hub"],
        "skill_categories": ["Automation & Integration", "Communication & Collaboration"],
    },
    {
        "id": "performance_analytics",
        "name": "Workforce Performance Analytics",
        "description": "Track team productivity, identify trends, and surface coaching opportunities",
        "department": "Human Resources",
        "category": "People Analytics",
        "agents": ["AI People Analyst"],
        "mcp_servers": ["mcp_postgres"],
        "skills": ["data_analytics", "trend_scanner", "sql_query_tool"],
        "skill_categories": ["Data & RAG"],
    },
    {
        "id": "training_delivery",
        "name": "AI-Powered Training & Development",
        "description": "Personalized learning paths and skill gap analysis for your team",
        "department": "Human Resources",
        "category": "Employee Experience",
        "agents": ["AI Learning Coach"],
        "mcp_servers": ["mcp_google_drive", "mcp_memory"],
        "skills": ["recommendation_engine", "knowledge_base_retrieval", "content_generator"],
        "skill_categories": ["AI Agent Frameworks", "Data & RAG"],
    },

    # ── Technology ───────────────────────────────────────────────────
    {
        "id": "data_pipeline_automation",
        "name": "Automated Data Pipelines",
        "description": "Build and maintain data flows between your systems automatically",
        "department": "Technology",
        "category": "Data Infrastructure",
        "agents": ["AI Data Engineer"],
        "mcp_servers": ["mcp_postgres", "mcp_sqlite", "mcp_redis"],
        "skills": ["etl_pipeline", "sql_query_tool", "api_connector", "queue_processor"],
        "skill_categories": ["Data & RAG", "Code & Development"],
    },
    {
        "id": "system_integration",
        "name": "Smart System Integration",
        "description": "Connect your tools and platforms so data flows seamlessly between them",
        "department": "Technology",
        "category": "Integration",
        "agents": ["AI Integration Architect"],
        "mcp_servers": ["mcp_postgres", "mcp_github", "mcp_redis"],
        "skills": ["api_connector", "webhook_manager", "workflow_automation"],
        "skill_categories": ["Automation & Integration", "Code & Development"],
    },
    {
        "id": "auto_reporting",
        "name": "Automated Report Generation",
        "description": "Generate business reports and dashboards automatically on schedule",
        "department": "Technology",
        "category": "Business Intelligence",
        "agents": ["AI Report Generator"],
        "mcp_servers": ["mcp_postgres", "mcp_google_drive"],
        "skills": ["sql_query_tool", "data_analytics", "pdf_generator", "csv_processor"],
        "skill_categories": ["Data & RAG"],
    },
    {
        "id": "security_monitoring",
        "name": "Security & Access Monitoring",
        "description": "Monitor system access, detect threats, and enforce security policies",
        "department": "Technology",
        "category": "Security",
        "agents": ["AI Security Monitor"],
        "mcp_servers": ["mcp_filesystem", "mcp_postgres"],
        "skills": ["vulnerability_scanner", "audit_logger", "anomaly_detection", "rbac_engine"],
        "skill_categories": ["Security & Auth", "Monitoring & Observability"],
    },
    {
        "id": "compliance_automation",
        "name": "Compliance & Audit Automation",
        "description": "Automated compliance checks, audit trail generation, and regulatory reporting",
        "department": "Technology",
        "category": "Security",
        "agents": ["AI Compliance Monitor"],
        "mcp_servers": ["mcp_filesystem", "mcp_postgres"],
        "skills": ["compliance_checker", "audit_logger", "data_anonymizer"],
        "skill_categories": ["Security & Auth"],
    },
    {
        "id": "system_health_monitoring",
        "name": "System Health & Uptime Monitoring",
        "description": "24/7 monitoring of your infrastructure with auto-healing and smart alerts",
        "department": "Technology",
        "category": "Infrastructure",
        "agents": ["AI Systems Monitor"],
        "mcp_servers": ["mcp_docker"],
        "skills": ["prometheus_monitoring", "uptime_monitor", "error_tracker", "notification_hub"],
        "skill_categories": ["Monitoring & Observability"],
    },

    # ── Communication ────────────────────────────────────────────────
    {
        "id": "email_drafting",
        "name": "AI Email Drafting & Responses",
        "description": "Draft professional emails and auto-respond to routine inquiries",
        "department": "Communication",
        "category": "Email",
        "agents": ["AI Email Manager"],
        "mcp_servers": ["mcp_slack", "mcp_google_drive"],
        "skills": ["email_sender", "content_generator", "sentiment_analyzer"],
        "skill_categories": ["Communication & Collaboration", "LLM Tool Libraries"],
    },
    {
        "id": "meeting_summarization",
        "name": "Meeting Notes & Action Items",
        "description": "Automatically summarize meetings and extract action items for follow-up",
        "department": "Communication",
        "category": "Productivity",
        "agents": ["AI Meeting Assistant"],
        "mcp_servers": ["mcp_google_drive", "mcp_slack"],
        "skills": ["content_generator", "calendar_scheduling", "notification_hub"],
        "skill_categories": ["LLM Tool Libraries", "Communication & Collaboration"],
    },
    {
        "id": "notification_routing",
        "name": "Smart Notification Routing",
        "description": "Route alerts and notifications to the right people through the right channels",
        "department": "Communication",
        "category": "Productivity",
        "agents": ["AI Notification Router"],
        "mcp_servers": ["mcp_slack"],
        "skills": ["notification_hub", "workflow_automation", "sms_messaging"],
        "skill_categories": ["Communication & Collaboration", "Automation & Integration"],
    },
    {
        "id": "document_qa",
        "name": "Document Search & Q&A",
        "description": "Ask questions about your company documents and get instant AI answers",
        "department": "Communication",
        "category": "Knowledge",
        "agents": ["AI Document Assistant"],
        "mcp_servers": ["mcp_filesystem", "mcp_google_drive", "mcp_notion", "mcp_memory"],
        "skills": ["rag_pipeline", "knowledge_base_retrieval", "document_loader", "search_engine"],
        "skill_categories": ["Data & RAG"],
    },
    {
        "id": "internal_chatbot",
        "name": "Internal Team Chatbot",
        "description": "AI assistant for your team that answers HR, IT, and policy questions",
        "department": "Communication",
        "category": "Knowledge",
        "agents": ["AI Team Assistant"],
        "mcp_servers": ["mcp_slack", "mcp_memory"],
        "skills": ["chatbot_builder", "knowledge_base_retrieval", "rag_pipeline"],
        "skill_categories": ["AI Agent Frameworks", "Data & RAG"],
    },
]


# ── Department metadata ─────────────────────────────────────────────

DEPARTMENTS = [
    {"id": "Sales", "icon": "bi-graph-up", "color": "primary"},
    {"id": "Customer Support", "icon": "bi-headset", "color": "info"},
    {"id": "Marketing", "icon": "bi-megaphone", "color": "success"},
    {"id": "Operations", "icon": "bi-gear", "color": "warning"},
    {"id": "Finance", "icon": "bi-cash-stack", "color": "danger"},
    {"id": "Human Resources", "icon": "bi-people", "color": "secondary"},
    {"id": "Technology", "icon": "bi-cpu", "color": "dark"},
    {"id": "Communication", "icon": "bi-chat-dots", "color": "primary"},
]

TOTAL_CAPABILITIES = len(CAPABILITY_CATALOG)


def get_all_capabilities() -> list[dict]:
    """Return all capabilities."""
    return list(CAPABILITY_CATALOG)


def get_capabilities_by_department() -> dict[str, list[dict]]:
    """Group capabilities by department."""
    by_dept = {}
    for cap in CAPABILITY_CATALOG:
        dept = cap["department"]
        if dept not in by_dept:
            by_dept[dept] = []
        by_dept[dept].append(cap)
    return by_dept


def get_capabilities_by_ids(ids: list[str]) -> list[dict]:
    """Get capabilities matching a list of IDs."""
    id_set = set(ids)
    return [c for c in CAPABILITY_CATALOG if c["id"] in id_set]


def get_department_meta() -> list[dict]:
    """Return department metadata (id, icon, color) for UI rendering."""
    by_dept = get_capabilities_by_department()
    result = []
    for dept in DEPARTMENTS:
        caps = by_dept.get(dept["id"], [])
        result.append({**dept, "count": len(caps), "capabilities": caps})
    return result


def get_ai_mappings_for_selection(selected_ids: list[str]) -> dict:
    """Aggregate all AI mappings from selected capabilities.

    Returns dict with:
      - departments: list of dept dicts with capabilities, agents, MCP servers, skills
      - all_agents: deduplicated list
      - all_mcp_servers: deduplicated list
      - all_skills: deduplicated list
    """
    selected = get_capabilities_by_ids(selected_ids)

    # Group by department
    dept_map = {}
    all_agents = set()
    all_mcp_servers = set()
    all_skills = set()

    for cap in selected:
        dept_name = cap["department"]
        if dept_name not in dept_map:
            dept_map[dept_name] = {
                "id": dept_name.lower().replace(" ", "_"),
                "name": dept_name,
                "capabilities": [],
                "recommended_agents": [],
                "recommended_mcp_servers": [],
                "recommended_skills": [],
            }
        dept = dept_map[dept_name]

        dept["capabilities"].append({
            "id": cap["id"],
            "name": cap["name"],
            "description": cap["description"],
            "automation_potential": "high",
            "current_pain_points": [],
        })

        for agent in cap.get("agents", []):
            if agent not in dept["recommended_agents"]:
                dept["recommended_agents"].append(agent)
            all_agents.add(agent)
        for server in cap.get("mcp_servers", []):
            if server not in dept["recommended_mcp_servers"]:
                dept["recommended_mcp_servers"].append(server)
            all_mcp_servers.add(server)
        for skill in cap.get("skills", []):
            if skill not in dept["recommended_skills"]:
                dept["recommended_skills"].append(skill)
            all_skills.add(skill)

    return {
        "departments": list(dept_map.values()),
        "all_agents": sorted(all_agents),
        "all_mcp_servers": sorted(all_mcp_servers),
        "all_skills": sorted(all_skills),
        "total_selected": len(selected),
    }
