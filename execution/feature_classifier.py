"""Feature classification and build ordering.

Classifies features as core or optional, validates problem mapping,
and orders features by dependency, value, and risk.
"""


def classify_feature(
    feature_name: str,
    feature_description: str,
    is_blocking: bool,
    has_problem_mapping: bool,
) -> str:
    """Determine if a feature is core or optional.

    A feature is core if:
    - It is blocking for initial usefulness
    - It maps directly to a validated problem

    Args:
        feature_name: The feature name.
        feature_description: What the feature does.
        is_blocking: Whether the product fails without it.
        has_problem_mapping: Whether it maps to a validated problem.

    Returns:
        'core' or 'optional'.
    """
    if is_blocking and has_problem_mapping:
        return "core"
    return "optional"


def check_feature_problem_mapping(
    features: list[dict], problems: list[str]
) -> dict:
    """Verify each feature maps to at least one validated problem.

    Args:
        features: List of feature dicts with 'name' and 'problem_mapped_to'.
        problems: List of validated problem identifiers.

    Returns:
        Dict with 'passed' bool and 'unmapped' list of features without mapping.
    """
    unmapped = []
    for feature in features:
        mapped_to = feature.get("problem_mapped_to", "")
        if not mapped_to or mapped_to not in problems:
            unmapped.append(feature.get("name", feature.get("id", "unknown")))

    return {
        "passed": len(unmapped) == 0,
        "unmapped": unmapped,
    }


def check_intern_explainability(features: list[dict]) -> dict:
    """Check if features have sufficient rationale for an intern to understand.

    A feature passes if it has a non-empty rationale of at least 10 characters.

    Args:
        features: List of feature dicts with 'name' and 'rationale'.

    Returns:
        Dict with 'passed' bool and 'unclear' list.
    """
    unclear = []
    for feature in features:
        rationale = feature.get("rationale", "")
        if len(rationale) < 10:
            unclear.append(feature.get("name", feature.get("id", "unknown")))

    return {
        "passed": len(unclear) == 0,
        "unclear": unclear,
    }


def order_by_priority(features: list[dict]) -> list[dict]:
    """Sort features by dependency, value, and risk.

    Features with lower build_order come first. Features without
    a build_order are placed at the end.

    Args:
        features: List of feature dicts with optional 'build_order'.

    Returns:
        Sorted list of features.
    """
    def sort_key(f):
        return f.get("build_order", 999)

    return sorted(features, key=sort_key)


def flag_deferred(features: list[dict]) -> dict:
    """Identify features that should be deferred.

    Features are flagged for deferment if they are optional and either:
    - Have no problem mapping
    - Are explicitly marked as deferred

    Args:
        features: List of feature dicts.

    Returns:
        Dict with 'deferred' list and 'active' list.
    """
    deferred = []
    active = []

    for feature in features:
        if feature.get("deferred", False):
            deferred.append(feature)
        else:
            active.append(feature)

    return {
        "deferred": deferred,
        "active": active,
        "deferred_count": len(deferred),
        "active_count": len(active),
    }


def check_mutual_exclusions(
    selected_ids: list[str], exclusion_groups: list[dict]
) -> dict:
    """Check if selected features violate any mutual exclusion constraints.

    Two features in the same exclusion group cannot both be selected
    (e.g., Microservices and Modular Monolith).

    Args:
        selected_ids: List of selected feature ID strings.
        exclusion_groups: List of exclusion group dicts, each with
            'group', 'feature_ids', and 'label'.

    Returns:
        Dict with 'passed' bool and 'violations' list of
        {"group", "label", "conflicting_ids", "message"}.
    """
    selected_set = set(selected_ids)
    violations = []

    for group in exclusion_groups:
        conflicting = [fid for fid in group["feature_ids"] if fid in selected_set]
        if len(conflicting) > 1:
            violations.append({
                "group": group["group"],
                "label": group["label"],
                "conflicting_ids": conflicting,
                "message": (
                    f"{group['label']}: cannot select both "
                    f"{' and '.join(conflicting)} — pick one"
                ),
            })

    return {
        "passed": len(violations) == 0,
        "violations": violations,
    }
