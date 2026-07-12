"""Deterministic requirements-traceability gate for the story-driven build plan.

This is the Stage-6 gate of the story decomposition pipeline (see
``docs/specs/myday-project-build-story-decomposition.md``). It is intentionally
PURE and deterministic — no LLM, no I/O — so it can be unit-tested and is a
trustworthy publish guard (CLAUDE.md Layer 3 / Layer 4).

It closes the "tickets don't coordinate the requirements document" gap *by
construction*: a plan cannot pass with a story that floats free of the
requirements, or with a must-have requirement that no story fulfills.

Contracts (mirrors the spec data shapes)
-----------------------------------------
``reqs``    : list of ``{"id": "REQ-001", "priority": "must|should|could", ...}``
``stories`` : list of ``{"id": "STORY-001", "fulfills": ["REQ-001", ...],
                          "release": "r0", ...}``

Gate result (``validate``)
--------------------------
``ok``               — True only if NO fail-closed condition tripped.
``invalid_citations``— stories citing a REQ id that doesn't exist  (FAIL)
``uncited_stories``  — stories with an empty/missing ``fulfills``    (FAIL)
``must_orphans``     — ``must`` requirements covered by zero stories (FAIL)
``should_orphans``   — ``should`` requirements covered by zero stories (WARN)
``thin_releases``    — releases with fewer than 2 stories            (FAIL)
``below_floor``      — fewer than ``min_stories`` stories total       (FAIL)
``warnings``         — human-readable warnings (don't block publish)
``rtm``              — ``{req_id: [story_id, ...]}`` coverage map
"""
from __future__ import annotations

from collections import OrderedDict

MIN_STORIES = 12          # the floor: "build out as much as possible", never fewer
MIN_PER_RELEASE = 2       # no single-story release


def _norm(s) -> str:
    return str(s or "").strip().upper()


def validate(reqs, stories, *, min_stories: int = MIN_STORIES,
             min_per_release: int = MIN_PER_RELEASE) -> dict:
    """Run the deterministic trace gate. Returns the result dict (see module doc).

    Fail-closed on: invalid citations, uncited stories, orphan ``must`` reqs,
    thin releases, below the story floor. ``should`` orphans only warn.
    """
    reqs = list(reqs or [])
    stories = list(stories or [])
    req_ids = {_norm(r.get("id")) for r in reqs if r.get("id")}
    priority = {_norm(r.get("id")): str(r.get("priority", "should")).lower() for r in reqs}

    # Coverage map (ordered by requirement declaration order, for a stable RTM).
    rtm: "OrderedDict[str, list]" = OrderedDict((_norm(r.get("id")), []) for r in reqs if r.get("id"))

    invalid_citations: list[dict] = []
    uncited_stories: list[str] = []

    for s in stories:
        sid = _norm(s.get("id"))
        cited = [_norm(c) for c in (s.get("fulfills") or []) if str(c).strip()]
        if not cited:
            uncited_stories.append(sid)
            continue
        for c in cited:
            if c not in req_ids:
                invalid_citations.append({"story": sid, "req": c})
            else:
                rtm[c].append(sid)

    must_orphans = [rid for rid, covers in rtm.items() if not covers and priority.get(rid) == "must"]
    should_orphans = [rid for rid, covers in rtm.items() if not covers and priority.get(rid) == "should"]

    # Release shape checks (deterministic).
    by_release: "OrderedDict[str, list]" = OrderedDict()
    for s in stories:
        by_release.setdefault(str(s.get("release") or "—"), []).append(_norm(s.get("id")))
    thin_releases = [rk for rk, ss in by_release.items() if rk != "—" and len(ss) < min_per_release]

    below_floor = len(stories) < min_stories

    ok = not (invalid_citations or uncited_stories or must_orphans or thin_releases or below_floor)

    warnings: list[str] = []
    for rid in should_orphans:
        warnings.append(f"{rid} (should) is not fulfilled by any story — consider adding coverage.")

    return {
        "ok": ok,
        "story_count": len(stories),
        "req_count": len(reqs),
        "invalid_citations": invalid_citations,
        "uncited_stories": uncited_stories,
        "must_orphans": must_orphans,
        "should_orphans": should_orphans,
        "thin_releases": thin_releases,
        "below_floor": below_floor,
        "warnings": warnings,
        "rtm": {k: list(v) for k, v in rtm.items()},
    }


def summarize(result: dict) -> str:
    """One-line summary for logs."""
    if result.get("ok"):
        warn = f", {len(result['warnings'])} warning(s)" if result.get("warnings") else ""
        return f"trace gate PASS — {result['story_count']} stories, {result['req_count']} reqs, all must covered{warn}"
    reasons = []
    if result.get("invalid_citations"):
        reasons.append(f"{len(result['invalid_citations'])} invalid citation(s)")
    if result.get("uncited_stories"):
        reasons.append(f"{len(result['uncited_stories'])} uncited story(ies)")
    if result.get("must_orphans"):
        reasons.append(f"orphan must: {', '.join(result['must_orphans'])}")
    if result.get("thin_releases"):
        reasons.append(f"thin release(s): {', '.join(result['thin_releases'])}")
    if result.get("below_floor"):
        reasons.append(f"below story floor ({result['story_count']})")
    return "trace gate FAIL — " + "; ".join(reasons)


def render_rtm_md(reqs, stories, result: dict | None = None) -> str:
    """Render a Requirements Traceability Matrix as markdown (for the BC doc)."""
    result = result or validate(reqs, stories)
    rtm = result["rtm"]
    priority = {_norm(r.get("id")): str(r.get("priority", "should")).lower() for r in reqs}
    statement = {_norm(r.get("id")): (r.get("statement") or r.get("text") or "") for r in reqs}

    lines = ["# Requirements Traceability Matrix", "",
             "Every requirement ⇄ its covering stories. Green = covered; an uncovered "
             "**must** blocks the build, an uncovered **should** is flagged.", "",
             "| REQ | Priority | Covered by | Status |", "|---|---|---|---|"]
    for rid, covers in rtm.items():
        pri = priority.get(rid, "should")
        if covers:
            status = "✅ covered"
        elif pri == "must":
            status = "❌ ORPHAN (must) — blocks build"
        else:
            status = "⚠️ uncovered (should)"
        cov = ", ".join(covers) if covers else "—"
        lines.append(f"| {rid} | {pri} | {cov} | {status} |")

    lines += ["", "## Gate result", "", summarize(result)]
    if result.get("warnings"):
        lines += ["", "### Warnings"] + [f"- {w}" for w in result["warnings"]]
    return "\n".join(lines)
