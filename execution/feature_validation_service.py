"""System Design Contract validation service.

Deterministic checks for feature dependencies, skill coverage, and MCP
server mapping.  No LLM calls — all rules are static and auditable.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static dependency maps
# ---------------------------------------------------------------------------

# Features that require other features to be selected.
# Key = feature that has a dependency, Value = list of required feature IDs.
FEATURE_DEPENDENCIES: dict[str, list[str]] = {
    "role_management": ["user_registration"],
    "rbac": ["user_registration"],
    "mfa": ["user_registration"],
    "third_party_auth": ["user_registration"],
    "sso_integration": ["user_registration"],
    "payment_gateway": ["user_registration"],
    "onboarding_flow": ["user_registration"],
    "dashboard": ["user_registration"],
    "notifications": ["user_registration"],
    "gamification": ["user_registration", "progress_tracking"],
    "social_features": ["user_registration"],
    "discussion_forums": ["user_registration"],
    "ai_recommendations": ["user_registration", "usage_analytics"],
    "adaptive_system": ["usage_analytics"],
    "nlp_search": ["search_filtering"],
    "content_generation": ["content_management"],
    "custom_reports": ["usage_analytics"],
    "realtime_dashboard": ["dashboard"],
    "ab_testing": ["feature_flags", "usage_analytics"],
    "database_per_service": ["microservices"],
    "distributed_tracing": ["microservices"],
    "api_gateway": ["api_access"],
    "api_rate_limiting": ["api_access"],
    "model_versioning": ["recommender_system"],
    "model_evaluation": ["recommender_system"],
    "feature_store": ["data_pipeline"],
    "ai_model_monitoring": ["recommender_system"],
    "ai_evaluation_suite": ["recommender_system"],
    "blue_green_deploy": ["ci_cd_pipeline"],
    "container_orchestration": ["ci_cd_pipeline"],
    "staging_environment": ["ci_cd_pipeline"],
    "load_testing": ["ci_cd_pipeline"],
}

# Feature categories that should be backed by at least one skill category.
FEATURE_SKILL_COVERAGE: dict[str, list[str]] = {
    "AI & Intelligence": ["AI Agent Frameworks", "LLM Tool Libraries", "Data & RAG"],
    "ML & Model Layer": ["ML & Data Science", "Data & RAG"],
    "Security & Compliance": ["Security & Auth"],
    "DevOps & Deployment": ["DevOps & Deployment", "Cloud & Infrastructure"],
    "Observability & Monitoring": ["Monitoring & Observability"],
    "Testing & QA": ["Testing & QA"],
    "Integrations": ["Automation & Integration"],
    "Engagement": ["Communication & Collaboration"],
}

# Features that should have specific MCP servers selected.
FEATURE_MCP_MAPPING: dict[str, list[str]] = {
    "social_features": ["mcp_slack"],
    "notifications": ["mcp_slack"],
    "discussion_forums": ["mcp_slack"],
    "content_management": ["mcp_filesystem", "mcp_google_drive"],
    "calendar_sync": ["mcp_google_drive"],
    "ci_cd_pipeline": ["mcp_github"],
    "api_access": ["mcp_github"],
    "webhooks": ["mcp_github"],
    "caching_layer": ["mcp_redis"],
    "message_queue": ["mcp_redis"],
    "nlp_search": ["mcp_brave_search"],
    "search_filtering": ["mcp_brave_search"],
    "container_orchestration": ["mcp_docker", "mcp_kubernetes"],
    "infrastructure_as_code": ["mcp_aws"],
}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_feature_dependencies(selected_feature_ids: list[str]) -> dict:
    """Verify every selected feature has its required dependencies selected.

    Args:
        selected_feature_ids: IDs of all currently selected features.

    Returns:
        {"passed": bool, "issues": [{"feature": str, "missing": [str],
         "message": str}]}
    """
    id_set = set(selected_feature_ids)
    issues: list[dict] = []

    for fid in selected_feature_ids:
        required = FEATURE_DEPENDENCIES.get(fid)
        if required is None:
            continue
        missing = [r for r in required if r not in id_set]
        if missing:
            issues.append({
                "feature": fid,
                "missing": missing,
                "message": (
                    f"Feature '{fid}' requires {missing} "
                    f"but they are not selected"
                ),
            })

    passed = len(issues) == 0
    if not passed:
        logger.warning(
            "Feature dependency issues: %d features have unmet dependencies",
            len(issues),
        )
    return {"passed": passed, "issues": issues}


def check_skill_coverage(
    features: list[dict],
    selected_skills: list[dict],
) -> dict:
    """Check that feature categories are backed by relevant skill categories.

    Args:
        features: Selected feature dicts (must have ``category`` key).
        selected_skills: Full skill dicts (must have ``category`` key).

    Returns:
        {"passed": bool, "gaps": [{"feature_category": str,
         "needed_skill_categories": [str], "message": str}]}
    """
    feature_categories = {f.get("category", "") for f in features}
    skill_categories = {s.get("category", "") for s in selected_skills}
    gaps: list[dict] = []

    for feat_cat, needed_skill_cats in FEATURE_SKILL_COVERAGE.items():
        if feat_cat not in feature_categories:
            continue
        if not any(sc in skill_categories for sc in needed_skill_cats):
            gaps.append({
                "feature_category": feat_cat,
                "needed_skill_categories": needed_skill_cats,
                "message": (
                    f"Features in '{feat_cat}' need skills from "
                    f"{needed_skill_cats} but none are selected"
                ),
            })

    passed = len(gaps) == 0
    if not passed:
        logger.warning("Skill coverage gaps: %d categories uncovered", len(gaps))
    return {"passed": passed, "gaps": gaps}


def check_mcp_mapping(
    selected_feature_ids: list[str],
    selected_skills: list[dict],
) -> dict:
    """Check that features needing MCP servers have them selected.

    Args:
        selected_feature_ids: IDs of selected features.
        selected_skills: Full skill dicts for selected skills.

    Returns:
        {"passed": bool, "gaps": [{"feature_id": str, "needed_mcp": [str],
         "message": str}]}
    """
    selected_skill_ids = {s.get("id", "") for s in selected_skills}
    gaps: list[dict] = []

    for fid in selected_feature_ids:
        needed = FEATURE_MCP_MAPPING.get(fid)
        if needed is None:
            continue
        missing = [m for m in needed if m not in selected_skill_ids]
        if missing:
            gaps.append({
                "feature_id": fid,
                "needed_mcp": missing,
                "message": (
                    f"Feature '{fid}' expects MCP servers {missing} "
                    f"but they are not selected"
                ),
            })

    passed = len(gaps) == 0
    if not passed:
        logger.warning("MCP mapping gaps: %d features missing MCP servers", len(gaps))
    return {"passed": passed, "gaps": gaps}


# ---------------------------------------------------------------------------
# MCP server derivation
# ---------------------------------------------------------------------------


def derive_mcp_servers(
    selected_skills: list[dict],
    selected_feature_ids: list[str],
) -> list[dict]:
    """Extract MCP servers from selected skills and enrich with purpose.

    Purpose is derived by reverse-looking which selected features map to
    each MCP server via ``FEATURE_MCP_MAPPING``.

    Args:
        selected_skills: Full skill dicts for all selected skills.
        selected_feature_ids: IDs of selected features.

    Returns:
        List of MCP server dicts with ``purpose`` field added.
    """
    # Build reverse map: mcp_id -> [feature_ids that need it]
    reverse_map: dict[str, list[str]] = {}
    for fid in selected_feature_ids:
        for mcp_id in FEATURE_MCP_MAPPING.get(fid, []):
            reverse_map.setdefault(mcp_id, []).append(fid)

    mcp_servers: list[dict] = []
    for skill in selected_skills:
        if skill.get("category") != "MCP Servers":
            continue
        sid = skill.get("id", "")
        mapped_features = reverse_map.get(sid, [])
        purpose = (
            f"Supports features: {', '.join(mapped_features)}"
            if mapped_features
            else "General MCP capability"
        )
        mcp_servers.append({
            "id": sid,
            "name": skill.get("name", ""),
            "description": skill.get("description", ""),
            "purpose": purpose,
            "source_url": skill.get("source_url", ""),
            "tags": skill.get("tags", []),
        })

    return mcp_servers


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def run_all_feature_validation(
    features: list[dict],
    selected_feature_ids: list[str],
    selected_skills: list[dict],
) -> dict:
    """Run all validation checks and compute a composite confidence score.

    Confidence scoring:
      +0.3 max for feature dependencies (proportional to pass rate)
      +0.3 max for skill coverage (proportional to pass rate)
      +0.4 max for MCP mapping (proportional to pass rate)

    ``is_valid`` is True only when there are zero error-severity issues
    (i.e. all feature dependencies are satisfied).

    Args:
        features: Selected feature dicts.
        selected_feature_ids: IDs of selected features.
        selected_skills: Full skill dicts for selected skills.

    Returns:
        {"is_valid": bool, "issues": [...], "warnings": [...],
         "confidence": float}
    """
    dep_result = check_feature_dependencies(selected_feature_ids)
    skill_result = check_skill_coverage(features, selected_skills)
    mcp_result = check_mcp_mapping(selected_feature_ids, selected_skills)

    # -- Build issues (error severity) and warnings lists --
    issues: list[dict] = []
    warnings: list[dict] = []

    for item in dep_result["issues"]:
        issues.append({
            "check": "feature_dependency",
            "severity": "error",
            "message": item["message"],
            "related_ids": [item["feature"]] + item["missing"],
        })

    for item in skill_result["gaps"]:
        warnings.append({
            "check": "skill_coverage",
            "severity": "warning",
            "message": item["message"],
            "related_ids": [],
        })

    for item in mcp_result["gaps"]:
        warnings.append({
            "check": "mcp_mapping",
            "severity": "warning",
            "message": item["message"],
            "related_ids": [item["feature_id"]] + item["needed_mcp"],
        })

    # -- Confidence scoring --
    # Dependencies: count features that have deps, compute ratio satisfied
    features_with_deps = [
        fid for fid in selected_feature_ids
        if fid in FEATURE_DEPENDENCIES
    ]
    if features_with_deps:
        satisfied = sum(
            1 for fid in features_with_deps
            if not any(
                i["feature"] == fid for i in dep_result["issues"]
            )
        )
        dep_score = 0.3 * (satisfied / len(features_with_deps))
    else:
        dep_score = 0.3  # no deps to check = full score

    # Skill coverage: ratio of covered categories
    feature_categories = {f.get("category", "") for f in features}
    applicable_cats = [
        c for c in FEATURE_SKILL_COVERAGE if c in feature_categories
    ]
    if applicable_cats:
        covered = len(applicable_cats) - len(skill_result["gaps"])
        skill_score = 0.3 * (covered / len(applicable_cats))
    else:
        skill_score = 0.3

    # MCP mapping: ratio of features with all MCP needs met
    features_with_mcp = [
        fid for fid in selected_feature_ids
        if fid in FEATURE_MCP_MAPPING
    ]
    if features_with_mcp:
        mapped = sum(
            1 for fid in features_with_mcp
            if not any(
                g["feature_id"] == fid for g in mcp_result["gaps"]
            )
        )
        mcp_score = 0.4 * (mapped / len(features_with_mcp))
    else:
        mcp_score = 0.4

    confidence = round(dep_score + skill_score + mcp_score, 2)

    is_valid = len(issues) == 0

    if is_valid:
        logger.info(
            "Validation passed (confidence=%.2f, warnings=%d)",
            confidence,
            len(warnings),
        )
    else:
        logger.warning(
            "Validation failed: %d errors, %d warnings (confidence=%.2f)",
            len(issues),
            len(warnings),
            confidence,
        )

    return {
        "is_valid": is_valid,
        "issues": issues,
        "warnings": warnings,
        "confidence": confidence,
    }
