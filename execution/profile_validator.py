"""Profile-specific validation checks.

Validates project profiles for completeness, confidence thresholds,
and cross-field alignment with selected features. Pure deterministic
logic — no LLM calls.
"""

import re

from execution.intelligence_goals import check_intelligence_goals_alignment
from execution.state_manager import PROFILE_REQUIRED_FIELDS


def check_required_fields(profile: dict) -> dict:
    """Verify all 7 required fields have confirmed=True.

    Args:
        profile: The project_profile dictionary.

    Returns:
        Dict with 'passed' bool and 'missing' list of unconfirmed fields.
    """
    missing = []
    for field in PROFILE_REQUIRED_FIELDS:
        field_data = profile.get(field, {})
        if not field_data.get("confirmed", False):
            missing.append(field)

    return {
        "passed": len(missing) == 0,
        "missing": missing,
    }


def check_field_confidence(profile: dict, min_confidence: float = 0.7) -> dict:
    """Verify all fields meet minimum confidence threshold.

    Args:
        profile: The project_profile dictionary.
        min_confidence: Minimum acceptable confidence (0.0-1.0).

    Returns:
        Dict with 'passed' bool and 'low_confidence' list of fields below threshold.
    """
    low_confidence = []
    for field in PROFILE_REQUIRED_FIELDS:
        field_data = profile.get(field, {})
        confidence = field_data.get("confidence")
        if confidence is None or confidence < min_confidence:
            low_confidence.append({
                "field": field,
                "confidence": confidence,
                "threshold": min_confidence,
            })

    return {
        "passed": len(low_confidence) == 0,
        "low_confidence": low_confidence,
    }


def check_ai_depth_alignment(profile: dict, features: list[dict] | None = None) -> dict:
    """Verify AI features align with the ai_depth selection.

    If ai_depth is "no_ai" or "light_automation", warn about AI-heavy features.
    If ai_depth is "autonomous_ai" or "predictive_ml", warn if no AI features selected.

    Args:
        profile: The project_profile dictionary.
        features: Optional list of selected feature dicts.

    Returns:
        Dict with 'passed' bool and 'warnings' list.
    """
    features = features or []
    ai_depth = profile.get("ai_depth", {}).get("selected", "")
    warnings = []

    # Detect AI-related features by category or name keywords (word-boundary match)
    ai_patterns = [
        r"\bai\b", r"\bml\b", r"\bmachine learning\b", r"\bneural\b", r"\bnlp\b",
        r"\bnatural language\b", r"\brecommendation\b", r"\badaptive\b",
        r"\bintelligent\b", r"\bpredictive\b", r"\bautonomous\b",
    ]

    ai_feature_count = 0
    for feat in features:
        text = f"{feat.get('name', '')} {feat.get('description', '')} {feat.get('category', '')}".lower()
        if any(re.search(pat, text) for pat in ai_patterns):
            ai_feature_count += 1

    low_ai_depths = {"no_ai", "light_automation"}
    high_ai_depths = {"predictive_ml", "autonomous_ai"}

    if ai_depth in low_ai_depths and ai_feature_count > 0:
        warnings.append(
            f"AI depth is '{ai_depth}' but {ai_feature_count} AI-related feature(s) selected. "
            f"Consider upgrading ai_depth or removing AI features."
        )

    if ai_depth in high_ai_depths and features and ai_feature_count == 0:
        warnings.append(
            f"AI depth is '{ai_depth}' but no AI-related features selected. "
            f"Consider adding AI features or adjusting ai_depth."
        )

    return {
        "passed": len(warnings) == 0,
        "warnings": warnings,
    }


def check_deployment_alignment(profile: dict, features: list[dict] | None = None) -> dict:
    """Verify features are compatible with the deployment type.

    Args:
        profile: The project_profile dictionary.
        features: Optional list of selected feature dicts.

    Returns:
        Dict with 'passed' bool and 'warnings' list.
    """
    features = features or []
    deployment = profile.get("deployment_type", {}).get("selected", "")
    warnings = []

    # Cloud-specific features that conflict with on-premise
    cloud_keywords = {"saas", "cloud", "multi-tenant", "serverless"}
    onprem_types = {"on_premise"}

    if deployment in onprem_types:
        for feat in features:
            text = f"{feat.get('name', '')} {feat.get('description', '')}".lower()
            if any(kw in text for kw in cloud_keywords):
                warnings.append(
                    f"Feature '{feat.get('name', '')}' references cloud/SaaS capabilities "
                    f"but deployment type is '{deployment}'."
                )

    return {
        "passed": len(warnings) == 0,
        "warnings": warnings,
    }


def check_monetization_alignment(profile: dict, features: list[dict] | None = None) -> dict:
    """Verify features are compatible with the monetization model.

    Args:
        profile: The project_profile dictionary.
        features: Optional list of selected feature dicts.

    Returns:
        Dict with 'passed' bool and 'warnings' list.
    """
    features = features or []
    monetization = profile.get("monetization_model", {}).get("selected", "")
    warnings = []

    # Check if payment/billing features exist for paid models
    paid_models = {"freemium", "subscription", "usage_based", "enterprise_license"}
    payment_keywords = {"payment", "billing", "subscription", "pricing", "tier", "plan"}

    if monetization in paid_models and features:
        has_payment_feature = any(
            any(kw in f"{feat.get('name', '')} {feat.get('description', '')}".lower()
                for kw in payment_keywords)
            for feat in features
        )
        if not has_payment_feature:
            warnings.append(
                f"Monetization model is '{monetization}' but no payment/billing "
                f"features are selected. Consider adding billing capabilities."
            )

    return {
        "passed": len(warnings) == 0,
        "warnings": warnings,
    }


def check_mvp_scope_alignment(profile: dict, features: list[dict] | None = None) -> dict:
    """Verify feature count is appropriate for the MVP scope level.

    Args:
        profile: The project_profile dictionary.
        features: Optional list of selected feature dicts.

    Returns:
        Dict with 'passed' bool and 'warnings' list.
    """
    features = features or []
    mvp_scope = profile.get("mvp_scope", {}).get("selected", "")
    warnings = []
    feature_count = len(features)

    scope_limits = {
        "proof_of_concept": (1, 15),
        "core_only": (3, 20),
        "core_plus_ai": (5, 30),
        "full_vertical": (8, 45),
        "platform_foundation": (10, 60),
    }

    if mvp_scope in scope_limits and feature_count > 0:
        min_feat, max_feat = scope_limits[mvp_scope]
        if feature_count < min_feat:
            warnings.append(
                f"Only {feature_count} features selected for '{mvp_scope}' scope "
                f"(expected at least {min_feat})."
            )
        elif feature_count > max_feat:
            warnings.append(
                f"{feature_count} features selected for '{mvp_scope}' scope "
                f"(recommended max {max_feat}). Consider reducing scope."
            )

    return {
        "passed": len(warnings) == 0,
        "warnings": warnings,
    }


def check_success_metrics_exist(profile: dict) -> dict:
    """Verify at least one measurable success metric exists.

    Args:
        profile: The project_profile dictionary.

    Returns:
        Dict with 'passed' bool and 'details' string.
    """
    metrics = profile.get("success_metrics", [])
    has_metrics = isinstance(metrics, list) and len(metrics) > 0

    return {
        "passed": has_metrics,
        "details": (
            f"{len(metrics)} success metric(s) defined"
            if has_metrics
            else "No success metrics defined. Add at least one measurable metric."
        ),
    }


def run_all_profile_checks(profile: dict, features: list[dict] | None = None) -> dict:
    """Run all profile validation checks and return a structured report.

    Args:
        profile: The project_profile dictionary.
        features: Optional list of selected feature dicts for alignment checks.

    Returns:
        Dict with 'all_passed' bool and individual check results.
    """
    intelligence_goals = profile.get("intelligence_goals", [])
    results = {
        "required_fields": check_required_fields(profile),
        "field_confidence": check_field_confidence(profile),
        "ai_depth_alignment": check_ai_depth_alignment(profile, features),
        "deployment_alignment": check_deployment_alignment(profile, features),
        "monetization_alignment": check_monetization_alignment(profile, features),
        "mvp_scope_alignment": check_mvp_scope_alignment(profile, features),
        "success_metrics": check_success_metrics_exist(profile),
        "intelligence_goals_alignment": check_intelligence_goals_alignment(intelligence_goals, features or []),
    }

    results["all_passed"] = all(
        r["passed"] for k, r in results.items() if k != "all_passed"
    )
    return results
