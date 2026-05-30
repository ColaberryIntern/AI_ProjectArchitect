"""Quality gate runner for chapters and final documents.

Implements the 5 lexical quality gates: Completeness, Clarity, Build
Readiness, Anti-Vagueness, and Intern Success Test. All lexical checks
are deterministic regex/keyword matches.

Also implements composite quality scoring (0-100) for enterprise chapters
across 4 dimensions: word count, subsection coverage, technical density,
and implementation specificity.

Spec-driven gates (added in Phase B of the spec-driven upgrade) live in
``run_spec_gates()`` and require Requirement objects from
``state.features.core[*]`` / ``state.features.optional[*]``:

- Requirement Coverage Gate (structural, no LLM)
- AC Testability Gate (LLM-judged via ``execution.semantic_judge``)
- Semantic Intern Test (LLM-judged, replaces the regex Intern Test for
  per-chapter use; the document-level regex Intern Test remains for
  backward compatibility in ``run_final_gates``)
"""

import re

from execution import semantic_judge
from execution.ambiguity_detector import detect_forbidden_phrases
from execution.build_depth import get_chapter_subsections, get_depth_config

# Required chapter elements
REQUIRED_CHAPTER_ELEMENTS = ["purpose", "design intent", "implementation guidance"]

# Placeholder patterns
PLACEHOLDER_PATTERNS = [
    r"\bTBD\b",
    r"\bTBA\b",
    r"\bTBC\b",
    r"to be determined",
    r"to be decided",
    r"we'll decide later",
    r"we'll figure out",
    r"placeholder",
    r"\bTODO\b",
    r"\bFIXME\b",
]

# Build readiness indicators
BUILD_READINESS_SIGNALS = {
    "execution_order": [r"first", r"then", r"next", r"after", r"before", r"step \d", r"phase \d", r"order"],
    "inputs_outputs": [r"input", r"output", r"produce", r"accept", r"return", r"receive", r"generate"],
    "dependencies": [r"depend", r"require", r"prerequisite", r"before", r"block"],
}

# Intern success test questions
INTERN_TEST_QUESTIONS = [
    "what_building",
    "what_first",
    "what_done_looks_like",
]


REQ_CITATION_PATTERN = re.compile(r"\[REQ-\d+\]")


def check_requirement_citations(
    chapter_text: str,
    linked_requirements: list[dict] | None,
) -> dict:
    """Verify the chapter cites every linked Requirement at least once.

    Citations use the form ``[REQ-NNN]`` (matched by REQ_CITATION_PATTERN).
    AC citations (``[AC-NNN-N]``) are not currently required by this gate
    — the directive recommends them but a missing AC citation is not a
    completeness failure.

    Args:
        chapter_text: The chapter text to scan.
        linked_requirements: Requirements that should be cited. None or
            empty list short-circuits to passed=True (no requirements
            were linked, so nothing to cite).

    Returns:
        Dict with 'passed' bool, 'missing_citations' list of req IDs,
        and 'issues' list for the report.
    """
    if not linked_requirements:
        return {"passed": True, "missing_citations": [], "issues": []}

    cited = set(REQ_CITATION_PATTERN.findall(chapter_text))
    missing: list[str] = []
    for r in linked_requirements:
        rid = r.get("id", "")
        if not rid:
            continue
        if f"[{rid}]" not in cited:
            missing.append(rid)
    issues = [
        f"Linked requirement {rid} is not cited in the chapter (use [{rid}])"
        for rid in missing
    ]
    return {"passed": len(missing) == 0, "missing_citations": missing, "issues": issues}


def check_completeness(
    chapter_text: str,
    chapter_title: str = "",
    linked_requirements: list[dict] | None = None,
) -> dict:
    """Check a chapter for completeness.

    Verifies required elements are present, no placeholder content
    exists, and (when ``linked_requirements`` is provided) that every
    linked Requirement is cited inline as ``[REQ-NNN]``.

    Args:
        chapter_text: The full chapter text.
        chapter_title: Optional chapter title for reporting.
        linked_requirements: Optional list of Requirement dicts traced to
            this chapter. When provided, missing citations fail the gate.

    Returns:
        Dict with 'passed' bool and 'issues' list.
    """
    issues = []
    text_lower = chapter_text.lower()

    # Check for required elements
    for element in REQUIRED_CHAPTER_ELEMENTS:
        if element not in text_lower:
            issues.append(f"Missing required element: '{element}'")

    # Check for placeholders
    for pattern in PLACEHOLDER_PATTERNS:
        matches = re.findall(pattern, chapter_text, re.IGNORECASE)
        if matches:
            issues.append(f"Contains placeholder language: '{matches[0]}'")

    # Check minimum content length (a chapter should have substance)
    non_heading_lines = [
        l.strip()
        for l in chapter_text.split("\n")
        if l.strip() and not l.strip().startswith("#")
    ]
    if len(non_heading_lines) < 10:
        issues.append(
            f"Chapter has only {len(non_heading_lines)} content lines (minimum 10)"
        )

    # Citation-presence check (only runs when linked_requirements given)
    citation_result = check_requirement_citations(chapter_text, linked_requirements)
    issues.extend(citation_result["issues"])

    return {
        "gate": "completeness",
        "passed": len(issues) == 0,
        "issues": issues,
    }


def check_clarity(chapter_text: str) -> dict:
    """Check a chapter for clarity.

    Verifies clear outcomes, consistent terminology, and assigned responsibilities.

    Args:
        chapter_text: The full chapter text.

    Returns:
        Dict with 'passed' bool and 'issues' list.
    """
    issues = []

    # Check for outcome/purpose signals
    outcome_signals = [
        r"this chapter",
        r"the goal",
        r"the purpose",
        r"this section",
        r"this ensures",
        r"the objective",
    ]
    has_outcome = any(
        re.search(p, chapter_text, re.IGNORECASE) for p in outcome_signals
    )
    if not has_outcome:
        issues.append("No clear outcome or purpose statement found")

    # Check for heading structure (clarity requires organization)
    headings = re.findall(r"^#+\s+.+$", chapter_text, re.MULTILINE)
    if len(headings) < 2:
        issues.append(
            f"Only {len(headings)} heading(s) found — chapter needs more structure"
        )

    return {
        "gate": "clarity",
        "passed": len(issues) == 0,
        "issues": issues,
    }


def check_build_readiness(chapter_text: str) -> dict:
    """Check if the chapter provides enough detail for execution.

    Verifies execution order signals, input/output definitions, and dependencies.

    Args:
        chapter_text: The full chapter text.

    Returns:
        Dict with 'passed' bool and 'issues' list.
    """
    issues = []
    text_lower = chapter_text.lower()

    for category, patterns in BUILD_READINESS_SIGNALS.items():
        has_signal = any(re.search(p, text_lower) for p in patterns)
        if not has_signal:
            readable = category.replace("_", " ")
            issues.append(f"No {readable} signals found")

    return {
        "gate": "build_readiness",
        "passed": len(issues) == 0,
        "issues": issues,
    }


def check_anti_vagueness(text: str) -> dict:
    """Scan text for forbidden vagueness phrases.

    Args:
        text: The text to scan.

    Returns:
        Dict with 'passed' bool and 'flagged_phrases' list.
    """
    findings = detect_forbidden_phrases(text)
    flagged = [f["phrase"] for f in findings]

    return {
        "gate": "anti_vagueness",
        "passed": len(flagged) == 0,
        "flagged_phrases": flagged,
        "issues": [
            f"Vague phrase '{p}' must be replaced with specifics" for p in flagged
        ],
    }


def check_intern_test(document_text: str) -> dict:
    """Evaluate the Intern Success Test.

    Checks whether the document answers the 3 key intern questions:
    - What am I building?
    - What do I build first?
    - What does done look like?

    Args:
        document_text: The full document text.

    Returns:
        Dict with 'passed' bool and evaluation details.
    """
    text_lower = document_text.lower()
    results = {}

    # Q1: What am I building?
    building_signals = [
        r"this (system|project|tool|application) (is|does|exists to|will)",
        r"the system (must|should|will)",
        r"core capabilit",
        r"what (the system|this) does",
        r"purpose",
    ]
    results["what_building"] = any(
        re.search(p, text_lower) for p in building_signals
    )

    # Q2: What do I build first?
    order_signals = [
        r"build order",
        r"start with",
        r"first",
        r"phase 1",
        r"step 1",
        r"priorit",
        r"execution phase",
    ]
    results["what_first"] = any(
        re.search(p, text_lower) for p in order_signals
    )

    # Q3: What does done look like?
    done_signals = [
        r"done (when|means|criteria|looks like)",
        r"success (is|means|criteria|when)",
        r"complet(e|ed|ion) (when|criteria)",
        r"definition of done",
        r"acceptance criteria",
        r"deliverable",
    ]
    results["what_done_looks_like"] = any(
        re.search(p, text_lower) for p in done_signals
    )

    all_answered = all(results.values())
    missing = [q for q, answered in results.items() if not answered]

    return {
        "gate": "intern_test",
        "passed": all_answered,
        "questions_answered": results,
        "missing_answers": missing,
        "issues": [
            f"Document does not clearly answer: '{q.replace('_', ' ')}'"
            for q in missing
        ],
    }


def run_chapter_gates(chapter_text: str, chapter_title: str = "") -> dict:
    """Run all per-chapter quality gates.

    Args:
        chapter_text: The full chapter text.
        chapter_title: Optional chapter title for reporting.

    Returns:
        Dict with per-gate results and 'all_passed' bool.
    """
    results = {
        "completeness": check_completeness(chapter_text, chapter_title),
        "clarity": check_clarity(chapter_text),
        "build_readiness": check_build_readiness(chapter_text),
        "anti_vagueness": check_anti_vagueness(chapter_text),
    }

    results["all_passed"] = all(r["passed"] for r in results.values())
    return results


def run_final_gates(document_text: str) -> dict:
    """Run all document-level quality gates including the intern test.

    Args:
        document_text: The full document text.

    Returns:
        Dict with per-gate results and 'all_passed' bool.
    """
    results = {
        "completeness": check_completeness(document_text),
        "clarity": check_clarity(document_text),
        "build_readiness": check_build_readiness(document_text),
        "anti_vagueness": check_anti_vagueness(document_text),
        "intern_test": check_intern_test(document_text),
    }

    results["all_passed"] = all(r["passed"] for r in results.values())
    return results


def generate_quality_report(results: dict) -> str:
    """Generate a human-readable quality report from gate results.

    Args:
        results: The results dict from run_chapter_gates or run_final_gates.

    Returns:
        A formatted string report.
    """
    lines = ["# Quality Gate Report", ""]
    overall = "PASS" if results.get("all_passed") else "FAIL"
    lines.append(f"**Overall: {overall}**")
    lines.append("")

    for gate_name, gate_result in results.items():
        if gate_name == "all_passed":
            continue
        if not isinstance(gate_result, dict):
            continue
        status = "PASS" if gate_result.get("passed") else "FAIL"
        lines.append(f"## {gate_name.replace('_', ' ').title()}: {status}")
        issues = gate_result.get("issues", [])
        if issues:
            for issue in issues:
                lines.append(f"- {issue}")
        else:
            lines.append("- No issues found")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Enterprise Quality Scoring (0-100)
# ---------------------------------------------------------------------------

# Technical density signal patterns
TECHNICAL_PATTERNS = {
    "code_blocks": r"```",
    "file_paths": r"(?:/[\w.-]+){2,}|[\w.-]+\.(?:py|js|ts|json|yaml|yml|toml|md|html|css|sql|sh|env)",
    "cli_commands": r"(?:npm|pip|python|docker|git|curl|mkdir|cd|export|uvicorn|pytest|make)\s+\w+",
    "tables": r"\|.+\|",
    "env_vars": r"[A-Z][A-Z_]{3,}(?:=|:)",
    "urls_ports": r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?|port\s+\d+",
}

# Implementation specificity signal patterns
SPECIFICITY_PATTERNS = {
    "execution_order": [r"step\s+\d", r"phase\s+\d", r"first,?\s", r"then,?\s", r"next,?\s", r"finally,?\s"],
    "io_definitions": [r"input[s]?\s*:", r"output[s]?\s*:", r"returns?\s+", r"accepts?\s+", r"produces?\s+"],
    "dependencies": [r"depends?\s+on", r"requires?\s+", r"prerequisite", r"must be .+? before"],
    "env_config": [r"environment variable", r"\.env", r"config\s", r"setting[s]?\s"],
    "testing": [r"test\s+", r"pytest", r"unit test", r"integration test", r"test case"],
    "deployment": [r"deploy", r"production", r"staging", r"docker", r"CI/CD", r"pipeline"],
}


def score_chapter(chapter_text: str, section_title: str, depth_mode: str = "enterprise") -> dict:
    """Score a chapter across multiple dimensions (0-100).

    Args:
        chapter_text: Full rendered chapter text.
        section_title: The chapter's outline section title.
        depth_mode: Build depth mode.

    Returns:
        Dict with total_score, per-dimension scores, status, and gate_results.
    """
    config = get_depth_config(depth_mode)
    min_words = config["min_words"]
    required_subs = get_chapter_subsections(section_title, depth_mode)

    word_count, word_score = _score_word_count(chapter_text, min_words)
    found, missing, sub_score = _score_subsections(chapter_text, required_subs)
    tech_score = _score_technical_density(chapter_text)
    spec_score = _score_implementation_specificity(chapter_text)

    total = word_score + sub_score + tech_score + spec_score

    # Use per-depth scoring thresholds
    from execution.build_depth import get_scoring_thresholds
    thresholds = get_scoring_thresholds(depth_mode)
    complete_threshold = thresholds["complete_threshold"]
    incomplete_threshold = thresholds["incomplete_threshold"]

    if total >= complete_threshold:
        status = "complete"
    elif total >= incomplete_threshold:
        status = "needs_expansion"
    else:
        status = "incomplete"

    # Also run the existing binary gates
    gate_results = run_chapter_gates(chapter_text, section_title)

    return {
        "total_score": total,
        "word_count": word_count,
        "word_count_score": word_score,
        "subsections_found": found,
        "subsections_missing": missing,
        "subsection_score": sub_score,
        "technical_density_score": tech_score,
        "implementation_specificity_score": spec_score,
        "status": status,
        "gate_results": gate_results,
    }


def _score_word_count(text: str, min_words: int) -> tuple[int, int]:
    """Score word count dimension (0-25 points).

    Returns:
        (word_count, score)
    """
    words = len(text.split())
    if min_words <= 0:
        return (words, 25)
    ratio = words / min_words
    score = min(25, int(ratio * 25))
    return (words, score)


def _score_subsections(text: str, required: list[str]) -> tuple[list[str], list[str], int]:
    """Score subsection coverage (0-25 points).

    Returns:
        (found_subsections, missing_subsections, score)
    """
    if not required:
        return ([], [], 25)

    found = []
    missing = []
    text_lower = text.lower()

    for sub in required:
        # Look for the subsection as a heading (## Sub) or as a phrase
        heading_pattern = r"##\s+" + re.escape(sub)
        if re.search(heading_pattern, text, re.IGNORECASE):
            found.append(sub)
        elif sub.lower() in text_lower:
            found.append(sub)
        else:
            missing.append(sub)

    ratio = len(found) / len(required)
    score = min(25, int(ratio * 25))
    return (found, missing, score)


def _score_technical_density(text: str) -> int:
    """Score technical density (0-25 points).

    Measures code blocks, file paths, CLI commands, tables, env vars.
    """
    total_signals = 0
    for _category, pattern in TECHNICAL_PATTERNS.items():
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        total_signals += len(matches)

    # Scale: 0 signals = 0, 5+ = 10, 10+ = 15, 20+ = 20, 30+ = 25
    if total_signals >= 30:
        return 25
    elif total_signals >= 20:
        return 20
    elif total_signals >= 10:
        return 15
    elif total_signals >= 5:
        return 10
    elif total_signals >= 2:
        return 5
    else:
        return 0


def _score_implementation_specificity(text: str) -> int:
    """Score implementation specificity (0-25 points).

    Measures execution order, I/O definitions, dependencies, env config,
    testing references, deployment considerations.
    """
    text_lower = text.lower()
    categories_found = 0

    for _category, patterns in SPECIFICITY_PATTERNS.items():
        if any(re.search(p, text_lower) for p in patterns):
            categories_found += 1

    total_categories = len(SPECIFICITY_PATTERNS)
    # Scale from 0-25 based on coverage
    score = min(25, int((categories_found / total_categories) * 25))
    return score


def score_document(chapter_scores: list[dict], depth_mode: str = "enterprise") -> dict:
    """Aggregate document-level scoring from chapter scores.

    Args:
        chapter_scores: List of score dicts from score_chapter().
        depth_mode: Build depth mode.

    Returns:
        Dict with average_score, total_word_count, estimated_pages,
        chapter_count, chapters_complete, chapters_incomplete, status.
    """
    if not chapter_scores:
        return {
            "average_score": 0,
            "total_word_count": 0,
            "estimated_pages": 0,
            "chapter_count": 0,
            "chapters_complete": 0,
            "chapters_needs_expansion": 0,
            "chapters_incomplete": 0,
            "status": "incomplete",
        }

    total_words = sum(s.get("word_count", 0) for s in chapter_scores)
    avg_score = sum(s.get("total_score", 0) for s in chapter_scores) // len(chapter_scores)
    complete = sum(1 for s in chapter_scores if s.get("status") == "complete")
    needs_exp = sum(1 for s in chapter_scores if s.get("status") == "needs_expansion")
    incomplete = sum(1 for s in chapter_scores if s.get("status") == "incomplete")

    from execution.build_depth import estimate_pages
    pages = estimate_pages(total_words)

    if avg_score >= 75 and incomplete == 0:
        doc_status = "complete"
    elif avg_score >= 40:
        doc_status = "needs_expansion"
    else:
        doc_status = "incomplete"

    return {
        "average_score": avg_score,
        "total_word_count": total_words,
        "estimated_pages": pages,
        "chapter_count": len(chapter_scores),
        "chapters_complete": complete,
        "chapters_needs_expansion": needs_exp,
        "chapters_incomplete": incomplete,
        "status": doc_status,
    }


# ---------------------------------------------------------------------------
# Spec-driven gates (Phase B)
#
# These gates operate on Requirement objects (from feature_classifier's
# promoted features) plus chapter text. They are gated behind a separate
# entry point so existing callers of run_chapter_gates / run_final_gates
# don't pick them up automatically.
# ---------------------------------------------------------------------------


# AC Testability minimum score for "must"-priority requirements.
# Scores: 0 = untestable, 1 = weak, 2 = testable, 3 = fully testable.
AC_TESTABILITY_MIN_SCORE_MUST = 2


def check_requirement_coverage(requirements: list[dict]) -> dict:
    """Verify every must-priority Requirement is referenced by >=1 chapter.

    Pure structural check — no LLM. Reads ``traces_to.chapter_ids`` on
    each Requirement; the chapter_writer keeps this populated as
    chapters are approved.

    Args:
        requirements: List of Requirement dicts (post-promotion).

    Returns:
        Dict with ``passed`` bool, ``orphaned`` list of Requirement IDs
        without chapter coverage, and ``issues`` list for the report.
    """
    orphaned: list[str] = []
    for r in requirements:
        if r.get("priority") != "must":
            continue
        traced = (r.get("traces_to") or {}).get("chapter_ids") or []
        if not traced:
            orphaned.append(r.get("id", r.get("name", "unknown")))

    issues = [
        f"Must-priority requirement '{rid}' is not referenced by any chapter"
        for rid in orphaned
    ]
    return {
        "gate": "requirement_coverage",
        "passed": len(orphaned) == 0,
        "orphaned": orphaned,
        "issues": issues,
    }


def check_ac_testability(requirements: list[dict]) -> dict:
    """LLM-judged: every must-priority AC scores >= AC_TESTABILITY_MIN_SCORE_MUST.

    Calls ``semantic_judge.score_acceptance_criteria()``. If the LLM is
    unavailable, the gate is reported as ``skipped`` with passed=True
    (advisory mode) — a CI environment without OPENAI_API_KEY should not
    fail builds on this gate alone.

    Args:
        requirements: List of Requirement dicts.

    Returns:
        Dict with ``gate``, ``passed``, ``status`` (one of
        ``ok``/``skipped``/``error``), ``failing`` list of
        {ac_id, score, reason}, and ``issues`` for the report.
    """
    must_acs: list[dict] = []
    must_ac_owners: dict[str, str] = {}  # ac_id -> req_id
    for r in requirements:
        if r.get("priority") != "must":
            continue
        for ac in r.get("acceptance_criteria") or []:
            ac_id = ac.get("id", "")
            if not ac_id:
                continue
            must_acs.append(ac)
            must_ac_owners[ac_id] = r.get("id", "unknown")

    if not must_acs:
        return {
            "gate": "ac_testability",
            "passed": True,
            "status": "ok",
            "failing": [],
            "issues": [],
        }

    judge_result = semantic_judge.score_acceptance_criteria(must_acs)
    if judge_result.status == "skipped":
        return {
            "gate": "ac_testability",
            "passed": True,
            "status": "skipped",
            "failing": [],
            "issues": [f"AC testability gate skipped: {judge_result.reason}"],
        }
    if judge_result.status == "error":
        return {
            "gate": "ac_testability",
            "passed": False,
            "status": "error",
            "failing": [],
            "issues": [f"AC testability gate errored: {judge_result.reason}"],
        }

    failing: list[dict] = []
    for entry in (judge_result.data or {}).get("results", []):
        score = entry.get("score")
        if not isinstance(score, (int, float)) or score < AC_TESTABILITY_MIN_SCORE_MUST:
            failing.append({
                "ac_id": entry.get("id", ""),
                "requirement_id": must_ac_owners.get(entry.get("id", ""), "unknown"),
                "score": score,
                "reason": entry.get("reason", ""),
            })

    issues = [
        (
            f"AC '{f['ac_id']}' on requirement '{f['requirement_id']}' "
            f"scored {f['score']} (< {AC_TESTABILITY_MIN_SCORE_MUST}): {f['reason']}"
        )
        for f in failing
    ]
    return {
        "gate": "ac_testability",
        "passed": len(failing) == 0,
        "status": "ok",
        "failing": failing,
        "issues": issues,
    }


def check_chapter_intern_semantic(
    chapter_text: str,
    linked_requirements: list[dict],
) -> dict:
    """LLM-judged Intern Test for a chapter.

    Replaces the regex-based Intern Test (``check_intern_test``) for
    per-chapter use. Asks the judge if the chapter + linked Requirements
    answer: inputs, outputs, one runnable test scenario, definition of
    done. Fails if any answer is False.

    Args:
        chapter_text: Full chapter text.
        linked_requirements: Requirements traced to this chapter.

    Returns:
        Dict with ``gate``, ``passed``, ``status``, ``answers`` (per
        question), and ``issues``.
    """
    judge_result = semantic_judge.evaluate_chapter_intern_test(
        chapter_text, linked_requirements
    )
    if judge_result.status == "skipped":
        return {
            "gate": "chapter_intern_semantic",
            "passed": True,
            "status": "skipped",
            "answers": {},
            "issues": [f"Semantic intern test skipped: {judge_result.reason}"],
        }
    if judge_result.status == "error":
        return {
            "gate": "chapter_intern_semantic",
            "passed": False,
            "status": "error",
            "answers": {},
            "issues": [f"Semantic intern test errored: {judge_result.reason}"],
        }

    data = judge_result.data or {}
    questions = ("inputs", "outputs", "test_scenario", "definition_of_done")
    answers = {q: data.get(q) or {"answered": False, "evidence": None} for q in questions}
    unanswered = [q for q in questions if not answers[q].get("answered")]
    issues = [
        f"Chapter does not answer '{q.replace('_', ' ')}' (no concrete evidence)"
        for q in unanswered
    ]
    return {
        "gate": "chapter_intern_semantic",
        "passed": len(unanswered) == 0,
        "status": "ok",
        "answers": answers,
        "issues": issues,
    }


def run_spec_gates(
    requirements: list[dict],
    chapters: list[dict] | None = None,
) -> dict:
    """Run all spec-driven gates against a project's Requirements + chapters.

    Args:
        requirements: Promoted Requirement list (e.g. from
            ``requirements_writer.collect_requirements(state)``).
        chapters: Optional list of {"id": str, "text": str} dicts for the
            per-chapter Semantic Intern Test. If None or empty, the gate
            is reported as not-applicable (passed=True, status=ok, with
            an empty per-chapter result list).

    Returns:
        Dict with the three spec gates plus ``all_passed``::

            {
                "requirement_coverage": {...},
                "ac_testability": {...},
                "chapter_intern_semantic": {
                    "passed": bool,
                    "per_chapter": [{"chapter_id": ..., ...}, ...],
                },
                "all_passed": bool,
            }
    """
    coverage = check_requirement_coverage(requirements)
    ac = check_ac_testability(requirements)

    per_chapter_results: list[dict] = []
    chapters_passed = True
    for chapter in chapters or []:
        chapter_id = chapter.get("id", "")
        text = chapter.get("text", "")
        linked = [
            r for r in requirements
            if chapter_id and chapter_id in (
                (r.get("traces_to") or {}).get("chapter_ids") or []
            )
        ]
        result = check_chapter_intern_semantic(text, linked)
        result["chapter_id"] = chapter_id
        per_chapter_results.append(result)
        if not result["passed"]:
            chapters_passed = False

    intern_result = {
        "gate": "chapter_intern_semantic",
        "passed": chapters_passed,
        "per_chapter": per_chapter_results,
    }

    all_passed = coverage["passed"] and ac["passed"] and chapters_passed
    return {
        "requirement_coverage": coverage,
        "ac_testability": ac,
        "chapter_intern_semantic": intern_result,
        "all_passed": all_passed,
    }
