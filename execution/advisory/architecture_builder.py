"""AI Operating System Architecture Builder.

Transforms selected capabilities into a structured architecture:
engines (with purpose, inputs, outputs), dependency graph,
execution flows, and validation.

This replaces the flat capability list with a real system design.
"""

from execution.advisory.capability_catalog import get_capabilities_by_ids


# ── Engine Definitions ──────────────────────────────────────────────

_ENGINE_CONFIG = {
    "operations_engine": {
        "name": "Operations Engine",
        "purpose": "Automate and optimize core operational workflows",
        "departments": ["Operations"],
        "icon": "bi-gear-wide-connected",
        "color": "warning",
        "inputs": ["Orders", "Schedules", "Resource data", "Process triggers"],
        "outputs": ["Optimized routes", "Assigned resources", "Automated workflows", "Quality alerts"],
    },
    "revenue_engine": {
        "name": "Revenue Engine",
        "purpose": "Drive revenue through AI-enhanced sales and marketing",
        "departments": ["Sales", "Marketing"],
        "icon": "bi-currency-dollar",
        "color": "primary",
        "inputs": ["Leads", "CRM data", "Campaign metrics", "Market signals"],
        "outputs": ["Scored leads", "Outreach sequences", "Pipeline forecasts", "Content"],
    },
    "customer_engine": {
        "name": "Customer Experience Engine",
        "purpose": "Deliver fast, intelligent customer support",
        "departments": ["Customer Support"],
        "icon": "bi-people",
        "color": "info",
        "inputs": ["Support tickets", "Chat messages", "Customer history", "Feedback"],
        "outputs": ["Instant responses", "Triaged tickets", "Sentiment reports", "KB articles"],
    },
    "finance_engine": {
        "name": "Finance Engine",
        "purpose": "Automate financial processing and forecasting",
        "departments": ["Finance"],
        "icon": "bi-cash-stack",
        "color": "danger",
        "inputs": ["Invoices", "Expenses", "Transaction data", "Budget allocations"],
        "outputs": ["Processed invoices", "Categorized expenses", "Cash flow forecasts"],
    },
    "technology_engine": {
        "name": "Technology Enablement Layer",
        "purpose": "Connect systems, move data, and generate intelligence",
        "departments": ["Technology", "Communication"],
        "icon": "bi-cpu",
        "color": "dark",
        "inputs": ["All system data", "API feeds", "Database state"],
        "outputs": ["Synced data", "Generated reports", "System health metrics"],
    },
    "hr_engine": {
        "name": "People & HR Engine",
        "purpose": "Streamline hiring, onboarding, and workforce management",
        "departments": ["Human Resources"],
        "icon": "bi-people",
        "color": "secondary",
        "inputs": ["Applications", "Employee records", "Performance data"],
        "outputs": ["Screened candidates", "Onboarding tasks", "Performance insights"],
    },
    "intelligence_engine": {
        "name": "AI Control Tower",
        "purpose": "Monitor all systems, detect patterns, and trigger proactive actions",
        "departments": ["Executive"],
        "icon": "bi-stars",
        "color": "dark",
        "inputs": ["Output from all other engines"],
        "outputs": ["Strategic alerts", "Cross-system patterns", "Optimization triggers", "Executive briefings"],
    },
}

# ── Department → Engine Mapping ─────────────────────────────────────

_DEPT_TO_ENGINE = {
    "Sales": "revenue_engine",
    "Marketing": "revenue_engine",
    "Customer Support": "customer_engine",
    "Operations": "operations_engine",
    "Finance": "finance_engine",
    "Human Resources": "hr_engine",
    "Technology": "technology_engine",
    "Communication": "technology_engine",
}

# ── Dependency Rules ────────────────────────────────────────────────

_DEPENDENCY_RULES = {
    "operations_engine": ["technology_engine"],
    "revenue_engine": ["technology_engine"],
    "customer_engine": ["technology_engine"],
    "finance_engine": ["technology_engine"],
    "hr_engine": ["technology_engine"],
    "intelligence_engine": ["*"],  # depends on all others
}

# ── Flow Templates per Engine ───────────────────────────────────────

_ENGINE_FLOWS = {
    "operations_engine": {
        "name": "Operations Execution Flow",
        "steps": [
            {"label": "Request received", "type": "input", "icon": "bi-inbox"},
            {"label": "Data pipeline processes input", "type": "process", "icon": "bi-database"},
            {"label": "AI optimizes (routing/scheduling)", "type": "agent", "icon": "bi-robot"},
            {"label": "IF optimization needed", "type": "decision", "icon": "bi-signpost-split"},
            {"label": "Execute automated workflow", "type": "action", "icon": "bi-lightning"},
            {"label": "Monitor quality metrics", "type": "process", "icon": "bi-speedometer"},
            {"label": "Feed results to AI COO", "type": "feedback", "icon": "bi-arrow-up-right"},
            {"label": "Task completed efficiently", "type": "outcome", "icon": "bi-check-circle"},
        ],
    },
    "revenue_engine": {
        "name": "Revenue Generation Flow",
        "steps": [
            {"label": "Lead enters system", "type": "input", "icon": "bi-person-plus"},
            {"label": "AI scores and qualifies", "type": "agent", "icon": "bi-robot"},
            {"label": "IF high value lead", "type": "decision", "icon": "bi-signpost-split"},
            {"label": "Trigger personalized outreach", "type": "action", "icon": "bi-send"},
            {"label": "Track engagement signals", "type": "process", "icon": "bi-graph-up"},
            {"label": "Update pipeline forecast", "type": "process", "icon": "bi-bar-chart"},
            {"label": "Feed insights to AI COO", "type": "feedback", "icon": "bi-arrow-up-right"},
            {"label": "Revenue captured", "type": "outcome", "icon": "bi-trophy"},
        ],
    },
    "customer_engine": {
        "name": "Customer Resolution Flow",
        "steps": [
            {"label": "Customer reaches out", "type": "input", "icon": "bi-chat-dots"},
            {"label": "AI triages and categorizes", "type": "agent", "icon": "bi-robot"},
            {"label": "IF resolvable automatically", "type": "decision", "icon": "bi-signpost-split"},
            {"label": "Deliver instant resolution", "type": "action", "icon": "bi-check-lg"},
            {"label": "ELSE escalate with context", "type": "action", "icon": "bi-arrow-up"},
            {"label": "Monitor satisfaction", "type": "process", "icon": "bi-emoji-smile"},
            {"label": "Feed metrics to AI COO", "type": "feedback", "icon": "bi-arrow-up-right"},
            {"label": "Customer satisfied", "type": "outcome", "icon": "bi-heart"},
        ],
    },
    "finance_engine": {
        "name": "Financial Processing Flow",
        "steps": [
            {"label": "Financial data received", "type": "input", "icon": "bi-receipt"},
            {"label": "AI extracts and validates", "type": "agent", "icon": "bi-robot"},
            {"label": "IF anomaly detected", "type": "decision", "icon": "bi-signpost-split"},
            {"label": "Flag for review", "type": "action", "icon": "bi-exclamation-triangle"},
            {"label": "Process and reconcile", "type": "action", "icon": "bi-check2-all"},
            {"label": "Update forecasts", "type": "process", "icon": "bi-graph-up-arrow"},
            {"label": "Processed accurately", "type": "outcome", "icon": "bi-check-circle"},
        ],
    },
}


def build_architecture(selected_capability_ids: list[str], include_coo: bool = True) -> dict:
    """Build complete AI Operating System architecture from selected capabilities.

    Returns:
        Dict with engines, dependencies, flows, and validation results.
    """
    capabilities = get_capabilities_by_ids(selected_capability_ids)

    # 1. Group capabilities into engines
    engines = _group_into_engines(capabilities)

    # 2. Validate and auto-add required engines
    engines = _validate_architecture(engines, include_coo)

    # 3. Build dependency graph
    dependencies = _build_dependencies(engines)

    # 4. Generate execution flows
    flows = _generate_flows(engines)

    # 5. Classify engine roles
    engine_roles = _classify_roles(engines)

    return {
        "engines": engines,
        "dependencies": dependencies,
        "flows": flows,
        "engine_roles": engine_roles,
        "total_engines": len(engines),
        "total_capabilities": len(capabilities),
    }


def _group_into_engines(capabilities: list[dict]) -> dict:
    """Group capabilities into engine objects."""
    engine_caps = {}

    for cap in capabilities:
        engine_id = _DEPT_TO_ENGINE.get(cap["department"], "technology_engine")
        if engine_id not in engine_caps:
            config = _ENGINE_CONFIG.get(engine_id, {})
            engine_caps[engine_id] = {
                "id": engine_id,
                "name": config.get("name", engine_id.replace("_", " ").title()),
                "purpose": config.get("purpose", ""),
                "icon": config.get("icon", "bi-gear"),
                "color": config.get("color", "secondary"),
                "inputs": config.get("inputs", []),
                "outputs": config.get("outputs", []),
                "capabilities": [],
                "agents": [],
            }
        engine_caps[engine_id]["capabilities"].append({
            "id": cap["id"],
            "name": cap["name"],
            "description": cap["description"],
        })
        for agent in cap.get("agents", []):
            if agent not in engine_caps[engine_id]["agents"]:
                engine_caps[engine_id]["agents"].append(agent)

    return engine_caps


def _validate_architecture(engines: dict, include_coo: bool) -> dict:
    """Validate and auto-add required engines."""
    # Rule 1: If any engine exists, technology layer must exist
    if engines and "technology_engine" not in engines:
        config = _ENGINE_CONFIG["technology_engine"]
        engines["technology_engine"] = {
            "id": "technology_engine",
            "name": config["name"],
            "purpose": config["purpose"],
            "icon": config["icon"],
            "color": config["color"],
            "inputs": config["inputs"],
            "outputs": config["outputs"],
            "capabilities": [{"id": "auto_reporting", "name": "Automated Reporting", "description": "Foundation data layer"}],
            "agents": ["AI Data Engineer"],
            "auto_added": True,
        }

    # Rule 2: If >1 engine, include AI Control Tower
    if include_coo and len(engines) > 1 and "intelligence_engine" not in engines:
        config = _ENGINE_CONFIG["intelligence_engine"]
        engines["intelligence_engine"] = {
            "id": "intelligence_engine",
            "name": config["name"],
            "purpose": config["purpose"],
            "icon": config["icon"],
            "color": config["color"],
            "inputs": [f"Output from {e['name']}" for e in engines.values()],
            "outputs": config["outputs"],
            "capabilities": [{"id": "ai_coo", "name": "Central Intelligence", "description": "Cross-system orchestration"}],
            "agents": ["AI Control Tower"],
            "auto_added": True,
        }

    return engines


def _build_dependencies(engines: dict) -> list[dict]:
    """Build dependency edges between engines."""
    edges = []
    engine_ids = set(engines.keys())

    for engine_id, deps in _DEPENDENCY_RULES.items():
        if engine_id not in engine_ids:
            continue
        for dep in deps:
            if dep == "*":
                # Depends on all others
                for other_id in engine_ids:
                    if other_id != engine_id:
                        edges.append({"from": other_id, "to": engine_id, "type": "feeds"})
            elif dep in engine_ids:
                edges.append({"from": dep, "to": engine_id, "type": "enables"})

    return edges


def _generate_flows(engines: dict) -> list[dict]:
    """Generate execution flows for each engine."""
    flows = []
    for engine_id, engine in engines.items():
        if engine_id == "intelligence_engine":
            continue  # AI COO doesn't have its own flow — it monitors others
        template = _ENGINE_FLOWS.get(engine_id)
        if template:
            flows.append({
                "engine_id": engine_id,
                "engine_name": engine["name"],
                "name": template["name"],
                "steps": template["steps"],
            })
    return flows


def _classify_roles(engines: dict) -> dict:
    """Classify each engine's role: PRIMARY, SECONDARY, ENABLEMENT, INTELLIGENCE."""
    roles = {}
    # Count capabilities per engine (more caps = more important)
    cap_counts = {eid: len(e.get("capabilities", [])) for eid, e in engines.items()}
    max_caps = max(cap_counts.values()) if cap_counts else 1

    for engine_id, engine in engines.items():
        if engine_id == "intelligence_engine":
            roles[engine_id] = "INTELLIGENCE"
        elif engine_id == "technology_engine":
            roles[engine_id] = "ENABLEMENT"
        elif cap_counts.get(engine_id, 0) == max_caps:
            roles[engine_id] = "PRIMARY DRIVER"
        else:
            roles[engine_id] = "SECONDARY"

    return roles
