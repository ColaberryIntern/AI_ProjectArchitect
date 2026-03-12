"""Skill catalog and suggestion engine for Skill Discovery.

Loads a global skill registry (config/skill_registry.json) containing
Claude-compatible skills/tools scraped from external sources.  Suggests
relevant skills for a project based on its profile and selected features.
Falls back to a hardcoded subset when the registry file is missing.
"""

import json
import logging
from pathlib import Path

from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent.parent / "config" / "skill_registry.json"

SKILL_CATEGORIES = [
    "MCP Servers",
    "AI Agent Frameworks",
    "LLM Tool Libraries",
    "Automation & Integration",
    "Data & RAG",
    "Code & Development",
    "Communication & Collaboration",
    "Monitoring & Observability",
    "Security & Auth",
    "Custom Skills",
]

# Minimal fallback when registry file is missing (~50 high-value skills)
FALLBACK_SKILLS = [
    {"id": "web_search", "name": "Web Search", "description": "Search the internet for real-time information via Google, Bing, or Brave", "category": "Data & RAG", "tags": ["search", "web", "real-time"]},
    {"id": "rag_pipeline", "name": "RAG Pipeline", "description": "Retrieve relevant documents from vector stores to ground LLM responses", "category": "Data & RAG", "tags": ["rag", "retrieval", "vector-store"]},
    {"id": "sql_query_tool", "name": "SQL Database Query Tool", "description": "Translate natural language to SQL and query relational databases", "category": "Data & RAG", "tags": ["sql", "database", "query"]},
    {"id": "document_loader", "name": "Document Loader & Parser", "description": "Load and parse PDFs, Word docs, CSVs, and other file formats", "category": "Data & RAG", "tags": ["documents", "parsing", "pdf"]},
    {"id": "embedding_generator", "name": "Embedding Generator", "description": "Generate vector embeddings from text for semantic search", "category": "Data & RAG", "tags": ["embeddings", "vectors", "semantic-search"]},
    {"id": "web_scraper", "name": "Web Scraper & Data Extractor", "description": "Scrape websites and extract structured data from HTML pages", "category": "Data & RAG", "tags": ["scraping", "web", "extraction"]},
    {"id": "data_analytics", "name": "Data Analytics & Reporting", "description": "Generate analytics reports, charts, and insights from structured data", "category": "Data & RAG", "tags": ["analytics", "reporting", "charts"]},
    {"id": "mcp_filesystem", "name": "MCP Filesystem Server", "description": "Read, write, and manage local files via Model Context Protocol", "category": "MCP Servers", "tags": ["file-io", "mcp", "local"]},
    {"id": "mcp_github", "name": "MCP GitHub Server", "description": "Interact with GitHub repos, issues, PRs, and actions via MCP", "category": "MCP Servers", "tags": ["github", "mcp", "vcs"]},
    {"id": "mcp_slack", "name": "MCP Slack Server", "description": "Send messages, read channels, and manage Slack workspaces via MCP", "category": "MCP Servers", "tags": ["slack", "mcp", "messaging"]},
    {"id": "mcp_postgres", "name": "MCP PostgreSQL Server", "description": "Query and manage PostgreSQL databases via Model Context Protocol", "category": "MCP Servers", "tags": ["database", "mcp", "sql"]},
    {"id": "mcp_browser", "name": "MCP Browser Automation", "description": "Control web browsers for scraping, testing, and automation via MCP", "category": "MCP Servers", "tags": ["browser", "mcp", "automation"]},
    {"id": "mcp_memory", "name": "MCP Memory Server", "description": "Persistent knowledge graph memory for Claude conversations via MCP", "category": "MCP Servers", "tags": ["memory", "mcp", "knowledge-graph"]},
    {"id": "mcp_brave_search", "name": "MCP Brave Search Server", "description": "Web and local search capabilities via Brave Search API and MCP", "category": "MCP Servers", "tags": ["search", "mcp", "brave"]},
    {"id": "claude_tool_use", "name": "Claude Tool Use (Function Calling)", "description": "Define custom functions Claude can call to interact with external systems", "category": "LLM Tool Libraries", "tags": ["claude", "tool-use", "function-calling"]},
    {"id": "claude_computer_use", "name": "Claude Computer Use", "description": "Let Claude control a computer by viewing screens and performing actions", "category": "LLM Tool Libraries", "tags": ["claude", "computer-use", "automation"]},
    {"id": "claude_vision", "name": "Claude Vision (Image Analysis)", "description": "Analyze images, screenshots, and documents using multimodal vision", "category": "LLM Tool Libraries", "tags": ["claude", "vision", "image"]},
    {"id": "langchain_agents", "name": "LangChain Agent Executor", "description": "Build reasoning agents with tool access using LangChain", "category": "AI Agent Frameworks", "tags": ["langchain", "agent", "reasoning"]},
    {"id": "crewai_framework", "name": "CrewAI Multi-Agent Framework", "description": "Orchestrate role-playing AI agents working together on complex tasks", "category": "AI Agent Frameworks", "tags": ["multi-agent", "roles", "collaboration"]},
    {"id": "semantic_kernel_framework", "name": "Microsoft Semantic Kernel", "description": "Integrate AI models into apps with plugins, planners, and memory", "category": "AI Agent Frameworks", "tags": ["semantic-kernel", "plugins", "planner"]},
    {"id": "task_planner", "name": "AI Task Planner", "description": "Decompose complex goals into ordered task sequences with dependencies", "category": "AI Agent Frameworks", "tags": ["planner", "task-decomposition", "goals"]},
    {"id": "memory_system", "name": "Agent Memory System", "description": "Provide short-term and long-term memory for AI agent conversations", "category": "AI Agent Frameworks", "tags": ["memory", "context", "persistence"]},
    {"id": "multi_agent_orchestrator", "name": "Multi-Agent Orchestrator", "description": "Coordinate multiple specialized AI agents working on shared tasks", "category": "AI Agent Frameworks", "tags": ["multi-agent", "orchestration", "coordination"]},
    {"id": "code_interpreter", "name": "Code Interpreter / Sandbox Execution", "description": "Execute code in sandboxed environments for computation and analysis", "category": "Code & Development", "tags": ["code", "execution", "sandbox"]},
    {"id": "git_operations", "name": "Git Version Control Operations", "description": "Perform git operations like commit, branch, merge, and PR creation", "category": "Code & Development", "tags": ["git", "version-control", "branches"]},
    {"id": "github_actions", "name": "GitHub Actions CI/CD", "description": "Trigger and manage GitHub Actions workflows for build, test, deploy", "category": "Code & Development", "tags": ["github", "ci-cd", "actions"]},
    {"id": "test_generator", "name": "Automated Test Generator", "description": "Generate unit, integration, and end-to-end tests from source code", "category": "Code & Development", "tags": ["testing", "test-generation", "automation"]},
    {"id": "api_connector", "name": "Universal API Connector", "description": "Connect to any REST or GraphQL API with configurable authentication", "category": "Code & Development", "tags": ["api", "rest", "graphql"]},
    {"id": "n8n_http_request", "name": "n8n HTTP Request Node", "description": "Make arbitrary HTTP requests to any REST API endpoint via n8n", "category": "Automation & Integration", "tags": ["n8n", "http", "api"]},
    {"id": "n8n_webhook", "name": "n8n Webhook Trigger", "description": "Receive and process incoming webhooks to trigger n8n workflows", "category": "Automation & Integration", "tags": ["n8n", "webhook", "trigger"]},
    {"id": "zapier_email", "name": "Zapier Send Email Action", "description": "Send transactional or notification emails through Zapier automations", "category": "Automation & Integration", "tags": ["zapier", "email", "notifications"]},
    {"id": "workflow_automation", "name": "Workflow Automation Engine", "description": "Build multi-step automated workflows with conditional branching", "category": "Automation & Integration", "tags": ["workflow", "automation", "branching"]},
    {"id": "email_sender", "name": "Email Sending Service", "description": "Send transactional and notification emails via SendGrid, SES, or SMTP", "category": "Communication & Collaboration", "tags": ["email", "sendgrid", "notifications"]},
    {"id": "calendar_scheduling", "name": "Calendar & Scheduling", "description": "Create, update, and manage calendar events across Google and Outlook", "category": "Communication & Collaboration", "tags": ["calendar", "scheduling", "google"]},
    {"id": "ticket_creation", "name": "Issue/Ticket Creation", "description": "Create and manage tickets in Jira, Linear, or GitHub Issues", "category": "Communication & Collaboration", "tags": ["tickets", "jira", "project-management"]},
    {"id": "notification_hub", "name": "Multi-Channel Notification Hub", "description": "Route notifications to email, Slack, SMS, push, or webhook channels", "category": "Communication & Collaboration", "tags": ["notifications", "multi-channel", "routing"]},
    {"id": "prometheus_monitoring", "name": "Prometheus Metrics Collection", "description": "Collect, store, and query application metrics with Prometheus", "category": "Monitoring & Observability", "tags": ["prometheus", "metrics", "monitoring"]},
    {"id": "error_tracker", "name": "Error Tracking (Sentry)", "description": "Capture, track, and alert on application errors with Sentry", "category": "Monitoring & Observability", "tags": ["sentry", "errors", "tracking"]},
    {"id": "log_aggregator", "name": "Log Aggregation & Analysis", "description": "Collect and analyze application logs with ELK, Loki, or CloudWatch", "category": "Monitoring & Observability", "tags": ["logging", "elk", "analysis"]},
    {"id": "oauth_provider", "name": "OAuth 2.0 / OIDC Provider", "description": "Implement OAuth 2.0 and OpenID Connect authentication flows", "category": "Security & Auth", "tags": ["oauth", "oidc", "authentication"]},
    {"id": "secrets_manager", "name": "Secrets Manager (Vault/AWS)", "description": "Store and retrieve secrets securely via HashiCorp Vault or AWS", "category": "Security & Auth", "tags": ["secrets", "vault", "security"]},
    {"id": "rbac_engine", "name": "Role-Based Access Control Engine", "description": "Enforce fine-grained permissions based on user roles", "category": "Security & Auth", "tags": ["rbac", "permissions", "authorization"]},
    {"id": "vulnerability_scanner", "name": "Security Vulnerability Scanner", "description": "Scan code and dependencies for known security vulnerabilities", "category": "Security & Auth", "tags": ["security", "vulnerabilities", "scanning"]},
    {"id": "guardrails", "name": "AI Guardrails & Safety Filters", "description": "Add content filtering, PII detection, and safety guardrails to LLMs", "category": "AI Agent Frameworks", "tags": ["guardrails", "safety", "pii"]},
    {"id": "content_generator", "name": "Content Generation Engine", "description": "Generate blog posts, social media content, and marketing copy", "category": "Communication & Collaboration", "tags": ["content", "marketing", "copywriting"]},
    {"id": "pdf_generator", "name": "PDF Report Generator", "description": "Generate formatted PDF reports and documents from templates", "category": "Data & RAG", "tags": ["pdf", "reports", "documents"]},
    {"id": "vector_db_chromadb", "name": "ChromaDB Vector Store", "description": "Open-source embedding database for building AI apps with retrieval", "category": "Data & RAG", "tags": ["chroma", "vector-db", "embeddings"]},
    {"id": "etl_pipeline", "name": "ETL Data Pipeline", "description": "Extract, transform, and load data between systems and databases", "category": "Data & RAG", "tags": ["etl", "data-pipeline", "transformation"]},
    {"id": "payment_processing", "name": "Payment Processing Integration", "description": "Accept payments with Stripe, PayPal, or Square", "category": "Automation & Integration", "tags": ["payments", "stripe", "subscriptions"]},
]

# ---------- LLM prompts for skill suggestion ----------

SKILL_SUGGEST_SYSTEM_PROMPT = (
    "You are an expert in AI development tools and Claude-compatible skills. "
    "Recommend the most relevant skills for a software project."
)

SKILL_SUGGEST_USER_PROMPT = """Given this project profile:
Problem: {problem_definition}
Target User: {target_user}
Value Proposition: {value_proposition}
Deployment: {deployment_type}
AI Depth: {ai_depth}

Selected features:
{feature_list}

Available skills:
{skill_list}

Select the {max_suggestions} most relevant skills for this project.
Return the top {default_selected} as "suggested" (pre-checked) and the rest as "available".

Return ONLY valid JSON:
{{"suggested": ["skill_id_1", "skill_id_2", ...], "available": ["skill_id_3", ...]}}

Rules:
- Select exactly {max_suggestions} skills total
- Top {default_selected} are "suggested" (highest priority)
- Remaining are "available" (shown but unchecked)
- Prioritize skills that align with the project's AI depth and deployment type
- Return ONLY the JSON object, no markdown"""


def load_registry() -> list[dict]:
    """Load the global skill registry from config/skill_registry.json.

    Falls back to FALLBACK_SKILLS if the file is missing or corrupt.

    Returns:
        List of skill dicts.
    """
    if not REGISTRY_PATH.exists():
        logger.info("Skill registry not found at %s, using fallback", REGISTRY_PATH)
        return [dict(s) for s in FALLBACK_SKILLS]

    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        skills = data.get("skills", [])
        if not isinstance(skills, list) or len(skills) < 10:
            logger.warning("Skill registry has too few skills (%d), using fallback", len(skills))
            return [dict(s) for s in FALLBACK_SKILLS]
        return skills
    except (json.JSONDecodeError, TypeError, OSError) as e:
        logger.warning("Failed to load skill registry: %s. Using fallback.", e)
        return [dict(s) for s in FALLBACK_SKILLS]


def get_skills_by_category(skills: list[dict]) -> list[dict]:
    """Group a flat skill list into category sections.

    Returns:
        List of dicts: [{"name": "Category", "skills": [...]}, ...]
    """
    categories: dict[str, list[dict]] = {}
    order: list[str] = []
    for skill in skills:
        cat = skill.get("category", "Custom Skills")
        if cat not in categories:
            categories[cat] = []
            order.append(cat)
        categories[cat].append(skill)
    return [{"name": cat, "skills": categories[cat]} for cat in order]


def get_skills_by_ids(skills: list[dict], skill_ids: list[str]) -> list[dict]:
    """Filter skills to those matching the given IDs.

    Args:
        skills: Full skill list.
        skill_ids: List of skill ID strings to select.

    Returns:
        List of matching skill dicts, in original order.
    """
    id_set = set(skill_ids)
    return [s for s in skills if s["id"] in id_set]


def suggest_skills(
    profile: dict,
    features: list[dict],
    registry: list[dict],
    max_suggestions: int = 50,
    default_selected: int = 15,
) -> dict:
    """Suggest skills for a project using LLM with tag-based fallback.

    Args:
        profile: The project_profile dictionary.
        features: List of selected feature dicts.
        registry: Full skill registry list.
        max_suggestions: Max total skills to suggest.
        default_selected: How many to pre-check as "suggested".

    Returns:
        Dict with "suggested" (list of skill IDs) and "available" (list of skill IDs).
    """
    if not is_available():
        return _match_skills_by_tags(profile, features, registry, max_suggestions, default_selected)

    # Extract profile fields
    fields = {}
    for field_name in ["problem_definition", "target_user", "value_proposition",
                       "deployment_type", "ai_depth"]:
        field_data = profile.get(field_name, {})
        fields[field_name] = field_data.get("selected", "") or ""

    if not any(fields.values()):
        return _match_skills_by_tags(profile, features, registry, max_suggestions, default_selected)

    feature_list = "\n".join(
        f"- {f['name']}: {f.get('description', '')}" for f in features[:30]
    ) or "- No features selected"

    skill_list = "\n".join(
        f"- {s['id']}: {s['name']} — {s['description']}" for s in registry
    )

    try:
        prompt = SKILL_SUGGEST_USER_PROMPT.format(
            **fields,
            feature_list=feature_list,
            skill_list=skill_list,
            max_suggestions=max_suggestions,
            default_selected=default_selected,
        )
        response = chat(
            system_prompt=SKILL_SUGGEST_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            response_format={"type": "json_object"},
        )
        return _parse_suggestion_response(
            response.content, registry, max_suggestions, default_selected,
        )
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM skill suggestion failed: %s. Using tag matching.", e)
        return _match_skills_by_tags(profile, features, registry, max_suggestions, default_selected)
    except Exception as e:
        logger.warning("Unexpected error suggesting skills: %s. Using tag matching.", e)
        return _match_skills_by_tags(profile, features, registry, max_suggestions, default_selected)


def _parse_suggestion_response(
    raw_json: str,
    registry: list[dict],
    max_suggestions: int,
    default_selected: int,
) -> dict:
    """Parse LLM skill suggestion response."""
    valid_ids = {s["id"] for s in registry}
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return _fallback_suggestion(registry, max_suggestions, default_selected)

    suggested = [sid for sid in data.get("suggested", []) if sid in valid_ids]
    available = [sid for sid in data.get("available", []) if sid in valid_ids]

    # Ensure we have enough suggestions
    if len(suggested) < 5:
        return _fallback_suggestion(registry, max_suggestions, default_selected)

    return {"suggested": suggested[:default_selected], "available": available}


def _fallback_suggestion(
    registry: list[dict],
    max_suggestions: int,
    default_selected: int,
) -> dict:
    """Simple fallback: take first N skills from registry."""
    all_ids = [s["id"] for s in registry[:max_suggestions]]
    return {
        "suggested": all_ids[:default_selected],
        "available": all_ids[default_selected:],
    }


def _match_skills_by_tags(
    profile: dict,
    features: list[dict],
    registry: list[dict],
    max_results: int = 50,
    default_selected: int = 15,
) -> dict:
    """Deterministic fallback: keyword-match project context against skill tags.

    Scores each skill by how many of its tags match words found in the
    project profile and feature descriptions, then returns the top matches.
    """
    # Build keyword set from profile and features
    keywords: set[str] = set()
    for field_name in ["problem_definition", "target_user", "value_proposition",
                       "deployment_type", "ai_depth", "monetization_model"]:
        field_data = profile.get(field_name, {})
        selected = field_data.get("selected", "") or ""
        keywords.update(w.lower() for w in selected.split() if len(w) > 2)

    for feat in features:
        keywords.update(w.lower() for w in feat.get("name", "").split() if len(w) > 2)
        keywords.update(w.lower() for w in feat.get("description", "").split() if len(w) > 2)

    # Score each skill
    scored = []
    for skill in registry:
        tags = skill.get("tags", [])
        name_words = [w.lower() for w in skill.get("name", "").split()]
        desc_words = [w.lower() for w in skill.get("description", "").split()]
        all_terms = tags + name_words + desc_words

        score = sum(1 for term in all_terms if term in keywords)
        scored.append((score, skill["id"]))

    # Sort by score descending, then by original order for ties
    scored.sort(key=lambda x: -x[0])

    top_ids = [sid for _, sid in scored[:max_results]]
    return {
        "suggested": top_ids[:default_selected],
        "available": top_ids[default_selected:],
    }


def build_skill_chapter_context(selected_skills: list[dict]) -> str:
    """Build prompt content describing selected skills for chapter generation.

    Args:
        selected_skills: List of selected skill dicts.

    Returns:
        Formatted string for injection into chapter prompts.
    """
    if not selected_skills:
        return ""

    by_category = get_skills_by_category(selected_skills)
    lines = []
    for cat_group in by_category:
        lines.append(f"\n### {cat_group['name']}")
        for skill in cat_group["skills"]:
            lines.append(f"- **{skill['name']}**: {skill['description']}")

    return "\n".join(lines)
