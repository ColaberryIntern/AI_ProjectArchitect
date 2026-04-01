"""Build readiness assessment service.

Determines whether a project has all required components to begin
implementation.  All checks are deterministic — no LLM calls.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rule tables
# ---------------------------------------------------------------------------

# Monetization models that require a payment gateway feature.
PAID_MONETIZATION_MODELS = {
    "freemium",
    "subscription",
    "usage_based",
    "marketplace",
}

# AI depth levels that require at least one AI/ML feature.
AI_DEPTH_LEVELS = {
    "ai_assisted",
    "predictive_ml",
    "autonomous_ai",
}

# Feature IDs considered AI/ML features.
AI_FEATURE_IDS = {
    "ai_recommendations",
    "content_generation",
    "nlp_search",
    "adaptive_system",
    "recommender_system",
    "time_series_forecasting",
    "transformer_nlp",
}

# Supporting features required when microservices is selected.
MICROSERVICES_SUPPORT = [
    "api_gateway",
    "distributed_tracing",
    "message_queue",
]

# Baseline observability features expected for any non-trivial project.
BASELINE_OBSERVABILITY = [
    "app_logging",
    "health_checks",
]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_missing_core_features(
    features: list[dict],
    profile: dict,
) -> dict:
    """Identify essential features missing based on the project profile.

    Rules applied:
    - Paid monetization → needs ``payment_gateway``
    - AI depth (non-trivial) → needs at least one AI feature
    - SaaS deployment → needs ``user_registration`` and ``dashboard``
    - All projects → needs ``ci_cd_pipeline``

    Args:
        features: Currently selected feature dicts.
        profile: The project profile dict.

    Returns:
        {"passed": bool, "missing": [str]}
    """
    feature_ids = {f.get("id", "") for f in features}
    missing: list[str] = []

    # Monetization check
    monetization = (
        profile.get("monetization_model", {}).get("selected", "") or ""
    )
    if monetization in PAID_MONETIZATION_MODELS:
        if "payment_gateway" not in feature_ids:
            missing.append("payment_gateway")

    # AI depth check
    ai_depth = profile.get("ai_depth", {}).get("selected", "") or ""
    if ai_depth in AI_DEPTH_LEVELS:
        if not feature_ids & AI_FEATURE_IDS:
            missing.append("ai_feature (at least one AI/ML feature)")

    # SaaS deployment check
    deployment = (
        profile.get("deployment_type", {}).get("selected", "") or ""
    )
    if deployment == "saas":
        for fid in ["user_registration", "dashboard"]:
            if fid not in feature_ids:
                missing.append(fid)

    # Universal baseline
    if "ci_cd_pipeline" not in feature_ids:
        missing.append("ci_cd_pipeline")

    passed = len(missing) == 0
    if not passed:
        logger.warning("Missing core features: %s", missing)
    return {"passed": passed, "missing": missing}


def check_architecture_completeness(
    features: list[dict],
    profile: dict,
) -> dict:
    """Check for gaps in architectural feature selection.

    Rules applied:
    - Microservices selected → need supporting infra features
    - More than 5 features → need baseline observability
    - AI depth (non-trivial) → need monitoring

    Args:
        features: Currently selected feature dicts.
        profile: The project profile dict.

    Returns:
        {"passed": bool, "missing": [str]}
    """
    feature_ids = {f.get("id", "") for f in features}
    missing: list[str] = []

    # Microservices support
    if "microservices" in feature_ids:
        for fid in MICROSERVICES_SUPPORT:
            if fid not in feature_ids:
                missing.append(fid)

    # Baseline observability for non-trivial projects
    if len(features) > 5:
        for fid in BASELINE_OBSERVABILITY:
            if fid not in feature_ids:
                missing.append(fid)

    # AI monitoring
    ai_depth = profile.get("ai_depth", {}).get("selected", "") or ""
    if ai_depth in AI_DEPTH_LEVELS:
        monitoring_ids = {"ai_model_monitoring", "performance_monitoring"}
        if not feature_ids & monitoring_ids:
            missing.append("ai_model_monitoring or performance_monitoring")

    passed = len(missing) == 0
    if not passed:
        logger.warning("Architecture gaps: %s", missing)
    return {"passed": passed, "missing": missing}


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def compute_build_readiness(
    features: list[dict],
    selected_skills: list[dict],
    profile: dict,
) -> dict:
    """Aggregate all readiness checks and determine risk level.

    Risk levels:
    - ``"low"``: 0 missing components — ready to build
    - ``"medium"``: 1-3 missing components
    - ``"high"``: 4+ missing components

    Args:
        features: Currently selected feature dicts.
        selected_skills: Full skill dicts for selected skills.
        profile: The project profile dict.

    Returns:
        {"ready": bool, "missing_components": [str],
         "risk_level": str, "details": dict}
    """
    core_result = check_missing_core_features(features, profile)
    arch_result = check_architecture_completeness(features, profile)

    all_missing = list(dict.fromkeys(
        core_result["missing"] + arch_result["missing"]
    ))

    count = len(all_missing)
    if count == 0:
        risk_level = "low"
    elif count <= 3:
        risk_level = "medium"
    else:
        risk_level = "high"

    ready = risk_level == "low"

    if ready:
        logger.info("Build readiness: READY (risk=low)")
    else:
        logger.warning(
            "Build readiness: NOT READY (risk=%s, missing=%d components)",
            risk_level,
            count,
        )

    return {
        "ready": ready,
        "missing_components": all_missing,
        "risk_level": risk_level,
        "details": {
            "core_features": core_result,
            "architecture": arch_result,
        },
    }
