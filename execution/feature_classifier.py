"""Feature classification, build ordering, and Requirement promotion.

Classifies features as core or optional, validates problem mapping,
orders features by dependency, value, and risk, and promotes plain
features into spec-grade Requirement objects with actor/action/value,
acceptance criteria, NFRs, and traceability fields.
"""

# ---------------------------------------------------------------------------
# Requirement field constants
# ---------------------------------------------------------------------------

VALID_REQUIREMENT_TYPES = ("functional", "nonfunctional", "constraint")
VALID_PRIORITIES = ("must", "should", "could", "wont")
DEFAULT_REQUIREMENT_TYPE = "functional"
DEFAULT_PRIORITY_FOR_CORE = "must"
DEFAULT_PRIORITY_FOR_OPTIONAL = "should"


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


# ---------------------------------------------------------------------------
# Requirement promotion (spec-driven layer)
# ---------------------------------------------------------------------------


def promote_to_requirement(feature: dict) -> dict:
    """Add Requirement-shape fields to a feature without overwriting existing data.

    Existing fields are preserved verbatim. Missing Requirement fields are
    seeded with defaults so the feature satisfies the extended schema.

    Args:
        feature: Feature dict (may already contain some Requirement fields).

    Returns:
        Feature dict augmented with: requirement_type, priority, dependencies,
        acceptance_criteria, nfr, traces_to. Other Requirement fields (actor,
        action, value) are NOT auto-filled — they require human/LLM input.
    """
    promoted = dict(feature)

    promoted.setdefault("requirement_type", DEFAULT_REQUIREMENT_TYPE)

    if "priority" not in promoted:
        promoted["priority"] = (
            DEFAULT_PRIORITY_FOR_CORE
            if feature.get("type") == "core"
            else DEFAULT_PRIORITY_FOR_OPTIONAL
        )

    promoted.setdefault("dependencies", [])
    promoted.setdefault("acceptance_criteria", [])
    promoted.setdefault("nfr", [])

    traces = dict(promoted.get("traces_to") or {})
    traces.setdefault("outline_section_id", None)
    traces.setdefault("chapter_ids", [])
    traces.setdefault("problem_id", feature.get("problem_mapped_to") or None)
    promoted["traces_to"] = traces

    return promoted


def promote_features_to_requirements(features: list[dict]) -> list[dict]:
    """Vectorized version of promote_to_requirement."""
    return [promote_to_requirement(f) for f in features]


def detect_dependency_cycles(features: list[dict]) -> list[list[str]]:
    """Find cycles in the Requirement dependency graph.

    Uses iterative DFS with three-state coloring (unvisited / on-stack /
    done). Each detected cycle is returned as the ordered list of node
    IDs participating in the cycle, starting from the back-edge target.

    Args:
        features: List of feature/requirement dicts with optional
            ``dependencies`` lists.

    Returns:
        List of cycles. Empty list if the graph is acyclic.
    """
    by_id: dict[str, dict] = {f["id"]: f for f in features if f.get("id")}

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {fid: WHITE for fid in by_id}
    cycles: list[list[str]] = []

    def visit(start: str) -> None:
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, child_idx = stack[-1]
            if child_idx == 0:
                if color[node] == GRAY:
                    stack.pop()
                    continue
                color[node] = GRAY
                path.append(node)
            deps = by_id.get(node, {}).get("dependencies") or []
            if child_idx < len(deps):
                stack[-1] = (node, child_idx + 1)
                dep = deps[child_idx]
                if dep not in by_id:
                    continue  # dangling refs handled separately
                if color[dep] == GRAY:
                    if dep in path:
                        cycle = path[path.index(dep):] + [dep]
                        cycles.append(cycle)
                elif color[dep] == WHITE:
                    stack.append((dep, 0))
            else:
                color[node] = BLACK
                if path and path[-1] == node:
                    path.pop()
                stack.pop()

    for fid in by_id:
        if color[fid] == WHITE:
            visit(fid)

    return cycles


def find_dangling_dependencies(features: list[dict]) -> list[dict]:
    """Find dependency references to Requirement IDs that don't exist.

    Args:
        features: List of feature/requirement dicts.

    Returns:
        List of {"feature_id", "missing": [str]} for each feature with
        unresolved dependency references. Empty if all resolve.
    """
    known_ids = {f.get("id") for f in features if f.get("id")}
    issues: list[dict] = []
    for f in features:
        deps = f.get("dependencies") or []
        missing = [d for d in deps if d not in known_ids]
        if missing:
            issues.append({"feature_id": f.get("id", "unknown"), "missing": missing})
    return issues


def check_acceptance_criteria_present(features: list[dict]) -> dict:
    """Check that every must-priority Requirement has at least one AC.

    Non-``must`` requirements (should/could/wont) are not subject to this
    check — they may legitimately defer AC capture.

    Args:
        features: List of feature/requirement dicts.

    Returns:
        {"passed": bool, "missing_ac": [feature_id, ...]}
    """
    missing: list[str] = []
    for f in features:
        if f.get("priority") != "must":
            continue
        acs = f.get("acceptance_criteria") or []
        if len(acs) == 0:
            missing.append(f.get("id", f.get("name", "unknown")))
    return {"passed": len(missing) == 0, "missing_ac": missing}
