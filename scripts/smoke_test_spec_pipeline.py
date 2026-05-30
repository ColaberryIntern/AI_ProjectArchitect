"""Smoke test for the spec-driven upgrade — runs against an existing project.

Usage:
    python -m scripts.smoke_test_spec_pipeline [slug]

Default slug is ``careeros-ai``. The script:

1. Loads the project state from output/{slug}/project_state.json.
2. Promotes its features to Requirements (idempotent).
3. Adds a synthetic "must"-priority AC and an NFR to the first core
   feature so the spec gates have something concrete to evaluate.
4. Writes output/{slug}/specs/requirements.json.
5. Runs the structural spec gates (Requirement Coverage, AC Testability
   in skipped/advisory mode without OPENAI_API_KEY).
6. Prints a one-screen report.

This is a NON-DESTRUCTIVE smoke test — it does NOT mutate
project_state.json on disk. State changes happen in-memory only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import OUTPUT_DIR
from execution.feature_validation_service import check_requirement_invariants
from execution.quality_gate_runner import run_spec_gates
from execution.requirements_writer import (
    build_requirements_document,
    collect_requirements,
)


def _decorate_for_smoke_test(state: dict) -> None:
    """Add one AC and one NFR to the first core feature so gates have data.

    In production these come from the directive flow (03-feature-discovery
    + 03b-acceptance-criteria). For the smoke test we synthesize them so
    the structural gates can run end-to-end without requiring the user to
    have already gone through the full new flow.
    """
    core = (state.get("features") or {}).get("core") or []
    if not core:
        return

    first = core[0]
    first.setdefault("priority", "must")
    first.setdefault("actor", "user")
    first.setdefault("action", first.get("name", "use this feature").lower())
    first.setdefault("value", "they get the value described in the rationale")
    first.setdefault("acceptance_criteria", [
        {
            "id": f"AC-{first.get('id', 'REQ-001')}-1",
            "given": "an authenticated user with the prerequisites met",
            "when": "they invoke the feature flow described above",
            "then": (
                "the system returns a 200 response within 500ms and "
                "the persisted state reflects the change in <= 1s"
            ),
            "measurable": True,
        }
    ])
    first.setdefault("nfr", [
        {
            "category": "performance",
            "metric": "p95 response latency",
            "threshold": "< 500ms",
            "verification": "k6 load test over 1000 rps",
        }
    ])


def main(slug: str = "careeros-ai") -> int:
    state_path = OUTPUT_DIR / slug / "project_state.json"
    if not state_path.exists():
        print(f"ERROR: {state_path} does not exist")
        return 1

    state = json.loads(state_path.read_text(encoding="utf-8"))

    print("=" * 72)
    print(f"SMOKE TEST — Spec-driven pipeline against project '{slug}'")
    print("=" * 72)
    print()
    print(f"Loaded state: {state_path}")
    print(f"Project: {state.get('project', {}).get('name', '?')}")
    print(f"Phase:   {state.get('current_phase', '?')}")

    core = (state.get("features") or {}).get("core") or []
    optional = (state.get("features") or {}).get("optional") or []
    print(f"Features: {len(core)} core, {len(optional)} optional")
    print()

    # Step 1: synthesize one AC + one NFR on the first core feature
    # (we don't pre-promote — collect_requirements does that, and it
    # injects the correct type/priority based on the bucket)
    _decorate_for_smoke_test(state)

    # Step 2: run requirement invariants (structural). collect_requirements
    # promotes each feature to a Requirement with the right priority.
    requirements = collect_requirements(state)
    invariants = check_requirement_invariants(requirements)
    print("--- Step 1: Requirement invariants ---")
    print(f"  passed: {invariants['passed']}")
    if invariants["issues"]:
        for issue in invariants["issues"][:5]:
            print(f"   - [{issue['check']}] {issue['message']}")
        extra = len(invariants["issues"]) - 5
        if extra > 0:
            print(f"   - (+{extra} more)")
    print()

    # Step 4: build the requirements artifact (in-memory; do NOT write)
    doc = build_requirements_document(state)
    print("--- Step 2: Requirements artifact (in-memory) ---")
    print(f"  schema_version: {doc['schema_version']}")
    print(f"  total: {doc['summary']['total']}")
    print(f"  by_priority: {doc['summary']['by_priority']}")
    print(f"  by_type:     {doc['summary']['by_type']}")
    print(f"  with_ac:     {doc['summary']['with_acceptance_criteria']}")
    print(f"  with_nfr:    {doc['summary']['with_nfr']}")
    print()

    # Step 5: run spec gates
    chapters = state.get("chapters") or []
    chapter_payloads = []
    for ch in chapters:
        ch_path = ch.get("content_path")
        if ch_path and Path(ch_path).exists():
            chapter_payloads.append({
                "id": str(ch["index"]),
                "text": Path(ch_path).read_text(encoding="utf-8"),
            })
    spec_results = run_spec_gates(requirements, chapter_payloads)
    print("--- Step 3: Spec gates ---")
    cov = spec_results["requirement_coverage"]
    print(f"  Requirement Coverage: passed={cov['passed']}")
    if cov["orphaned"]:
        for rid in cov["orphaned"][:3]:
            print(f"    orphan: {rid}")
        extra = len(cov["orphaned"]) - 3
        if extra > 0:
            print(f"    (+{extra} more orphans)")

    ac = spec_results["ac_testability"]
    print(f"  AC Testability:       status={ac['status']}, passed={ac['passed']}")
    if ac.get("issues"):
        for issue in ac["issues"][:2]:
            print(f"    {issue}")

    intern = spec_results["chapter_intern_semantic"]
    print(f"  Chapter Intern Test:  passed={intern['passed']} "
          f"({len(intern['per_chapter'])} chapters)")
    print()
    print(f"all_passed: {spec_results['all_passed']}")
    print()

    # Step 6: report which chapters cite which Requirements (current state)
    print("--- Step 4: Trace state ---")
    traces_count = 0
    for r in requirements:
        cids = (r.get("traces_to") or {}).get("chapter_ids") or []
        if cids:
            traces_count += 1
            print(f"  {r['id']:12} -> chapters {cids}")
    if traces_count == 0:
        print("  (no traces yet — populated by chapter_build phase)")
    print()
    print("=" * 72)
    print("SMOKE TEST COMPLETE — non-destructive (state on disk unchanged)")
    print("=" * 72)
    return 0 if invariants["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "careeros-ai"))
