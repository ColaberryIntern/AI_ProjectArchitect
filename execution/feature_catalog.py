"""Dynamic feature catalog generator for Feature Discovery.

Generates a project-specific catalog of 50-75 features in up to 13 categories
via a ONE-TIME LLM call when the user first enters Feature Discovery.
Falls back to a generic catalog if LLM is unavailable or returns
an unparseable response.
"""

import json
import logging

from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

# ---------- Layer & category constants ----------

LAYER_FUNCTIONAL = "functional"
LAYER_ARCHITECTURAL = "architectural"

CATEGORY_LAYERS = {
    "Core Functionality": LAYER_FUNCTIONAL,
    "AI & Intelligence": LAYER_FUNCTIONAL,
    "User Experience": LAYER_FUNCTIONAL,
    "Assessment & Progress": LAYER_FUNCTIONAL,
    "Engagement": LAYER_FUNCTIONAL,
    "Integrations": LAYER_FUNCTIONAL,
    "Analytics & Reporting": LAYER_FUNCTIONAL,
    "Architecture & Infrastructure": LAYER_ARCHITECTURAL,
    "Security & Compliance": LAYER_ARCHITECTURAL,
    "ML & Model Layer": LAYER_ARCHITECTURAL,
    "DevOps & Deployment": LAYER_ARCHITECTURAL,
    "Observability & Monitoring": LAYER_ARCHITECTURAL,
    "Testing & QA": LAYER_ARCHITECTURAL,
}

MUTUAL_EXCLUSION_GROUPS = [
    {
        "group": "architecture_style",
        "feature_ids": ["microservices", "modular_monolith"],
        "label": "Architecture Style",
    },
    {
        "group": "deployment_strategy",
        "feature_ids": ["blue_green_deploy", "canary_releases"],
        "label": "Deployment Strategy",
    },
]

# ---------- LLM prompts ----------

CATALOG_SYSTEM_PROMPT = "You are a product feature expert. Generate feature catalogs for software products."

CATALOG_USER_PROMPT = """Given this project idea: "{idea}"

Generate 50-75 product features organized into up to 13 categories.
Return ONLY valid JSON with this structure:
{{"categories": [{{"name": "Category Name", "features": [{{"id": "unique_snake_case", "name": "Feature Name", "description": "one sentence, max 15 words"}}, ...]}}]}}

Rules:
- 50-75 features total across up to 13 categories
- 3-8 features per category
- Features must be specific to THIS project idea
- Each feature id must be unique, lowercase with underscores
- Categories should cover both functional and architectural aspects:
  Functional: Core Functionality, AI & Intelligence, User Experience, Assessment & Progress, Engagement, Integrations, Analytics & Reporting
  Architectural: Architecture & Infrastructure, Security & Compliance, ML & Model Layer, DevOps & Deployment, Observability & Monitoring, Testing & QA
- Descriptions are one sentence, max 15 words
- Return ONLY the JSON object, no markdown or explanation"""

# ---------- Fallback catalog (71 features, 13 categories) ----------

FALLBACK_CATALOG = [
    # ── Functional Layer ──────────────────────────────────────────
    # Core Functionality (5)
    {"id": "user_registration", "name": "User Registration", "description": "Account creation with email and profile setup", "category": "Core Functionality"},
    {"id": "dashboard", "name": "Dashboard", "description": "Central hub showing key metrics and recent activity", "category": "Core Functionality"},
    {"id": "search_filtering", "name": "Search & Filtering", "description": "Find and filter content with advanced search options", "category": "Core Functionality"},
    {"id": "content_management", "name": "Content Management", "description": "Create, edit, and organize content within the platform", "category": "Core Functionality"},
    {"id": "role_management", "name": "Role Management", "description": "Assign and manage user roles and permissions", "category": "Core Functionality"},
    # AI & Intelligence (4)
    {"id": "ai_recommendations", "name": "AI Recommendations", "description": "Personalized suggestions powered by machine learning algorithms", "category": "AI & Intelligence"},
    {"id": "content_generation", "name": "Content Generation", "description": "AI-powered automatic content creation and drafting", "category": "AI & Intelligence"},
    {"id": "nlp_search", "name": "Natural Language Search", "description": "Search using natural language queries instead of keywords", "category": "AI & Intelligence"},
    {"id": "adaptive_system", "name": "Adaptive System", "description": "System that learns and adapts to user behavior patterns", "category": "AI & Intelligence"},
    # User Experience (4)
    {"id": "responsive_design", "name": "Responsive Design", "description": "Optimized layout for desktop, tablet, and mobile devices", "category": "User Experience"},
    {"id": "accessibility", "name": "Accessibility", "description": "WCAG-compliant design for users with disabilities", "category": "User Experience"},
    {"id": "onboarding_flow", "name": "Onboarding Flow", "description": "Guided first-time user experience with helpful tutorials", "category": "User Experience"},
    {"id": "dark_mode", "name": "Dark Mode", "description": "Alternate color scheme reducing eye strain in low light", "category": "User Experience"},
    # Assessment & Progress (4)
    {"id": "progress_tracking", "name": "Progress Tracking", "description": "Visual indicators showing completion status and milestones", "category": "Assessment & Progress"},
    {"id": "skill_assessment", "name": "Skill Assessment", "description": "Evaluate user capabilities through structured assessments", "category": "Assessment & Progress"},
    {"id": "goal_setting", "name": "Goal Setting", "description": "Define and track personal or team objectives", "category": "Assessment & Progress"},
    {"id": "feedback_system", "name": "Feedback System", "description": "Collect and display structured feedback from users", "category": "Assessment & Progress"},
    # Engagement (4)
    {"id": "notifications", "name": "Notifications", "description": "Email and in-app alerts for important events", "category": "Engagement"},
    {"id": "gamification", "name": "Gamification", "description": "Points, badges, and streaks to motivate participation", "category": "Engagement"},
    {"id": "social_features", "name": "Social Features", "description": "Community interactions like comments, sharing, and collaboration", "category": "Engagement"},
    {"id": "discussion_forums", "name": "Discussion Forums", "description": "Threaded discussion boards for community knowledge sharing", "category": "Engagement"},
    # Integrations (6)
    {"id": "api_access", "name": "API Access", "description": "RESTful API for third-party integrations and extensions", "category": "Integrations"},
    {"id": "calendar_sync", "name": "Calendar Sync", "description": "Synchronize events with Google Calendar and Outlook", "category": "Integrations"},
    {"id": "third_party_auth", "name": "Third-party Auth", "description": "Login via Google, GitHub, or other OAuth providers", "category": "Integrations"},
    {"id": "webhooks", "name": "Webhooks", "description": "Automated event notifications to external services", "category": "Integrations"},
    {"id": "payment_gateway", "name": "Payment Gateway", "description": "Stripe or PayPal integration for billing and subscriptions", "category": "Integrations"},
    {"id": "sso_integration", "name": "SSO Integration", "description": "Enterprise single sign-on via SAML or OpenID Connect", "category": "Integrations"},
    # Analytics & Reporting (5)
    {"id": "usage_analytics", "name": "Usage Analytics", "description": "Track user engagement, retention, and feature adoption", "category": "Analytics & Reporting"},
    {"id": "custom_reports", "name": "Custom Reports", "description": "Generate tailored reports with flexible parameters", "category": "Analytics & Reporting"},
    {"id": "export_tools", "name": "Export Tools", "description": "Download data and reports in CSV, PDF formats", "category": "Analytics & Reporting"},
    {"id": "realtime_dashboard", "name": "Real-time Dashboard", "description": "Live-updating metrics dashboard with streaming data feeds", "category": "Analytics & Reporting"},
    {"id": "ab_testing", "name": "A/B Testing", "description": "Controlled experiments comparing feature variants with statistical analysis", "category": "Analytics & Reporting"},
    # ── Architectural Layer ───────────────────────────────────────
    # Architecture & Infrastructure (8)
    {"id": "microservices", "name": "Microservices", "description": "Decompose application into independently deployable service boundaries", "category": "Architecture & Infrastructure"},
    {"id": "modular_monolith", "name": "Modular Monolith", "description": "Single deployable unit with well-defined internal module boundaries", "category": "Architecture & Infrastructure"},
    {"id": "api_gateway", "name": "API Gateway", "description": "Centralized entry point handling routing, auth, and rate limiting", "category": "Architecture & Infrastructure"},
    {"id": "background_jobs", "name": "Background Jobs", "description": "Async task processing for long-running operations via worker queues", "category": "Architecture & Infrastructure"},
    {"id": "message_queue", "name": "Message Queue", "description": "Asynchronous inter-service communication via RabbitMQ or Redis Streams", "category": "Architecture & Infrastructure"},
    {"id": "caching_layer", "name": "Caching Layer", "description": "Redis or Memcached caching for frequently accessed data", "category": "Architecture & Infrastructure"},
    {"id": "event_driven_arch", "name": "Event-Driven Architecture", "description": "Publish-subscribe event bus for decoupled component communication", "category": "Architecture & Infrastructure"},
    {"id": "database_per_service", "name": "Database per Service", "description": "Isolated data stores per service for independent scaling", "category": "Architecture & Infrastructure"},
    # Security & Compliance (7)
    {"id": "rbac", "name": "Role-Based Access Control", "description": "Granular permissions system with hierarchical role definitions", "category": "Security & Compliance"},
    {"id": "mfa", "name": "Multi-Factor Authentication", "description": "TOTP and SMS-based second factor for account security", "category": "Security & Compliance"},
    {"id": "encryption_at_rest", "name": "Encryption at Rest", "description": "AES-256 encryption for stored data and database fields", "category": "Security & Compliance"},
    {"id": "gdpr_toolkit", "name": "GDPR Toolkit", "description": "Data export, deletion requests, and consent management tools", "category": "Security & Compliance"},
    {"id": "audit_logging", "name": "Audit Logging", "description": "Immutable logs tracking all data access and modifications", "category": "Security & Compliance"},
    {"id": "secrets_management", "name": "Secrets Management", "description": "Vault-based secrets storage with automatic rotation policies", "category": "Security & Compliance"},
    {"id": "api_rate_limiting", "name": "API Rate Limiting", "description": "Token-bucket rate limiting protecting APIs from abuse", "category": "Security & Compliance"},
    # ML & Model Layer (7)
    {"id": "recommender_system", "name": "Recommender System", "description": "Collaborative and content-based filtering recommendation engine", "category": "ML & Model Layer"},
    {"id": "time_series_forecasting", "name": "Time-Series Forecasting", "description": "ARIMA or Prophet models for temporal prediction tasks", "category": "ML & Model Layer"},
    {"id": "transformer_nlp", "name": "Transformer NLP", "description": "Fine-tuned transformer models for text classification and extraction", "category": "ML & Model Layer"},
    {"id": "model_versioning", "name": "Model Versioning", "description": "MLflow or DVC-based model registry with version tracking", "category": "ML & Model Layer"},
    {"id": "feature_store", "name": "Feature Store", "description": "Centralized repository for ML feature computation and serving", "category": "ML & Model Layer"},
    {"id": "model_evaluation", "name": "Model Evaluation", "description": "Automated model performance benchmarking with drift detection", "category": "ML & Model Layer"},
    {"id": "data_pipeline", "name": "Data Pipeline", "description": "ETL orchestration for training data collection and preprocessing", "category": "ML & Model Layer"},
    # DevOps & Deployment (6)
    {"id": "ci_cd_pipeline", "name": "CI/CD Pipeline", "description": "Automated build, test, and deployment via GitHub Actions", "category": "DevOps & Deployment"},
    {"id": "staging_environment", "name": "Staging Environment", "description": "Pre-production environment mirroring production for validation", "category": "DevOps & Deployment"},
    {"id": "blue_green_deploy", "name": "Blue-Green Deployment", "description": "Zero-downtime deployments switching between identical environments", "category": "DevOps & Deployment"},
    {"id": "infrastructure_as_code", "name": "Infrastructure as Code", "description": "Terraform or Pulumi templates for reproducible infrastructure provisioning", "category": "DevOps & Deployment"},
    {"id": "feature_flags", "name": "Feature Flags", "description": "Runtime feature toggles for gradual rollout and experimentation", "category": "DevOps & Deployment"},
    {"id": "container_orchestration", "name": "Container Orchestration", "description": "Kubernetes or Docker Compose for container management and scaling", "category": "DevOps & Deployment"},
    # Observability & Monitoring (6)
    {"id": "app_logging", "name": "Application Logging", "description": "Structured JSON logging with correlation IDs across services", "category": "Observability & Monitoring"},
    {"id": "performance_monitoring", "name": "Performance Monitoring", "description": "APM dashboards tracking latency, throughput, and error rates", "category": "Observability & Monitoring"},
    {"id": "ai_model_monitoring", "name": "AI Model Monitoring", "description": "Track model accuracy, drift, and prediction confidence over time", "category": "Observability & Monitoring"},
    {"id": "alerting_system", "name": "Alerting System", "description": "PagerDuty or Opsgenie alerts triggered by metric thresholds", "category": "Observability & Monitoring"},
    {"id": "health_checks", "name": "Health Checks", "description": "Liveness and readiness probes for all service endpoints", "category": "Observability & Monitoring"},
    {"id": "distributed_tracing", "name": "Distributed Tracing", "description": "OpenTelemetry tracing for request flow across service boundaries", "category": "Observability & Monitoring"},
    # Testing & QA (5)
    {"id": "unit_testing_framework", "name": "Unit Testing Framework", "description": "Pytest or Jest harness with coverage gates and fixtures", "category": "Testing & QA"},
    {"id": "integration_testing", "name": "Integration Testing", "description": "End-to-end API and database integration test suites", "category": "Testing & QA"},
    {"id": "load_testing", "name": "Load Testing", "description": "Locust or k6 performance tests simulating concurrent user loads", "category": "Testing & QA"},
    {"id": "security_testing", "name": "Security Testing", "description": "OWASP ZAP scans and dependency vulnerability auditing", "category": "Testing & QA"},
    {"id": "ai_evaluation_suite", "name": "AI Evaluation Suite", "description": "Automated benchmarks measuring AI output quality and consistency", "category": "Testing & QA"},
]


def generate_catalog(idea: str) -> list[dict]:
    """Generate a project-specific feature catalog.

    Makes a ONE-TIME LLM call to create 50-75 features in up to 13 categories
    tailored to the given project idea. Falls back to FALLBACK_CATALOG
    if LLM is unavailable or returns unparseable output.

    Args:
        idea: The user's raw project idea text.

    Returns:
        List of feature dicts, each with id, name, description, category.
    """
    if not idea or not idea.strip():
        logger.info("No idea provided, using fallback catalog")
        return list(FALLBACK_CATALOG)

    if not is_available():
        logger.info("LLM unavailable, using fallback catalog")
        return list(FALLBACK_CATALOG)

    try:
        prompt = CATALOG_USER_PROMPT.format(idea=idea.strip())
        response = chat(
            system_prompt=CATALOG_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        return _parse_catalog_response(response.content)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM catalog generation failed: %s. Using fallback.", e)
        return list(FALLBACK_CATALOG)
    except Exception as e:
        logger.warning("Unexpected error generating catalog: %s. Using fallback.", e)
        return list(FALLBACK_CATALOG)


def _parse_catalog_response(raw_json: str) -> list[dict]:
    """Parse LLM JSON response into a flat feature list.

    Expects: {"categories": [{"name": "...", "features": [{"id": "...", ...}]}]}
    Flattens into a list of dicts with category field added.

    Falls back to FALLBACK_CATALOG if parsing fails or result is invalid.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse catalog JSON, using fallback")
        return list(FALLBACK_CATALOG)

    if not isinstance(data, dict) or "categories" not in data:
        logger.warning("Catalog JSON missing 'categories' key, using fallback")
        return list(FALLBACK_CATALOG)

    features = []
    seen_ids = set()
    for cat in data["categories"]:
        cat_name = cat.get("name", "Uncategorized")
        for feat in cat.get("features", []):
            feat_id = feat.get("id", "")
            if not feat_id or feat_id in seen_ids:
                continue
            seen_ids.add(feat_id)
            features.append({
                "id": feat_id,
                "name": feat.get("name", feat_id),
                "description": feat.get("description", ""),
                "category": cat_name,
            })

    if len(features) < 20:
        logger.warning(
            "Catalog only has %d features (need >= 20), using fallback",
            len(features),
        )
        return list(FALLBACK_CATALOG)

    return features


CATALOG_FROM_PROFILE_PROMPT = """Given this structured project profile:

Problem: {problem_definition}
Target User: {target_user}
Value Proposition: {value_proposition}
Deployment: {deployment_type}
AI Depth: {ai_depth}
Monetization: {monetization_model}
MVP Scope: {mvp_scope}

Generate 50-75 product features organized into up to 13 categories.

Return ONLY valid JSON with this structure:
{{"categories": [{{"name": "Category Name", "features": [{{"id": "unique_snake_case", "name": "Feature Name", "description": "one sentence, max 15 words"}}, ...]}}]}}

Rules:
- 50-75 features total across up to 13 categories (3-8 features per category)
- Features must align with the deployment type and AI depth specified
- For AI depth '{ai_depth}': adjust AI feature complexity accordingly
- Each feature id must be unique, lowercase with underscores
- Categories should cover both functional and architectural aspects:
  Functional: Core Functionality, AI & Intelligence, User Experience, Assessment & Progress, Engagement, Integrations, Analytics & Reporting
  Architectural: Architecture & Infrastructure, Security & Compliance, ML & Model Layer, DevOps & Deployment, Observability & Monitoring, Testing & QA
- Descriptions are one sentence, max 15 words
- Return ONLY the JSON object, no markdown or explanation"""


def generate_catalog_from_profile(profile: dict) -> list[dict]:
    """Generate a feature catalog using the project_profile instead of raw idea.

    Makes a ONE-TIME LLM call to create 50-75 features in up to 13 categories
    tailored to the structured project profile. Falls back to FALLBACK_CATALOG
    if LLM is unavailable or returns unparseable output.

    Args:
        profile: The project_profile dictionary with confirmed fields.

    Returns:
        List of feature dicts, each with id, name, description, category.
    """
    # Extract selected values from profile fields
    fields = {}
    for field_name in ["problem_definition", "target_user", "value_proposition",
                       "deployment_type", "ai_depth", "monetization_model", "mvp_scope"]:
        field_data = profile.get(field_name, {})
        fields[field_name] = field_data.get("selected", "") or ""

    # If no meaningful profile data, fall back
    if not any(fields.values()):
        logger.info("No profile fields populated, using fallback catalog")
        return list(FALLBACK_CATALOG)

    if not is_available():
        logger.info("LLM unavailable, using fallback catalog")
        return list(FALLBACK_CATALOG)

    try:
        prompt = CATALOG_FROM_PROFILE_PROMPT.format(**fields)
        response = chat(
            system_prompt=CATALOG_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        return _parse_catalog_response(response.content)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM catalog generation from profile failed: %s. Using fallback.", e)
        return list(FALLBACK_CATALOG)
    except Exception as e:
        logger.warning("Unexpected error generating catalog from profile: %s. Using fallback.", e)
        return list(FALLBACK_CATALOG)


def get_features_by_ids(catalog: list[dict], feature_ids: list[str]) -> list[dict]:
    """Filter catalog to only features matching the given IDs.

    Args:
        catalog: Full feature catalog list.
        feature_ids: List of feature ID strings to select.

    Returns:
        List of matching feature dicts, in catalog order.
    """
    id_set = set(feature_ids)
    return [f for f in catalog if f["id"] in id_set]


def get_catalog_by_category(catalog: list[dict]) -> list[dict]:
    """Group a flat catalog list into category sections.

    Returns:
        List of dicts: [{"name": "Category", "features": [...]}, ...]
    """
    categories = {}
    order = []
    for feat in catalog:
        cat = feat.get("category", "Uncategorized")
        if cat not in categories:
            categories[cat] = []
            order.append(cat)
        categories[cat].append(feat)
    return [{"name": cat, "features": categories[cat]} for cat in order]


def get_feature_layer(category: str) -> str:
    """Return the layer ('functional' or 'architectural') for a category name."""
    return CATEGORY_LAYERS.get(category, LAYER_FUNCTIONAL)


def get_catalog_by_layer(catalog: list[dict]) -> dict:
    """Group a flat catalog into layer → category sections.

    Returns:
        {"functional": [{"name": "Cat", "features": [...]}],
         "architectural": [{"name": "Cat", "features": [...]}]}
    """
    layer_cats: dict[str, dict[str, list]] = {
        LAYER_FUNCTIONAL: {},
        LAYER_ARCHITECTURAL: {},
    }
    layer_order: dict[str, list] = {
        LAYER_FUNCTIONAL: [],
        LAYER_ARCHITECTURAL: [],
    }

    for feat in catalog:
        cat = feat.get("category", "Uncategorized")
        layer = get_feature_layer(cat)
        if cat not in layer_cats[layer]:
            layer_cats[layer][cat] = []
            layer_order[layer].append(cat)
        layer_cats[layer][cat].append(feat)

    return {
        layer: [
            {"name": cat, "features": layer_cats[layer][cat]}
            for cat in layer_order[layer]
        ]
        for layer in (LAYER_FUNCTIONAL, LAYER_ARCHITECTURAL)
    }
