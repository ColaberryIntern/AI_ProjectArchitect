"""Re-parse: apply a Build Guide edit to a project's plan.

The plan is now a tight set of generated workstreams (not a 1:1 chapter map), so
a per-chapter delta no longer applies. On a doc change we regenerate the plan
(bumping planRevision); an unchanged doc is a no-op. The reconciler's
content-hash gate then makes the downstream Basecamp sync minimal — unchanged
workstreams/tasks keep their ids and produce zero writes.
"""
from __future__ import annotations

from execution.advisory import build_guide_parser, plan_builder


def reparse(slug: str, new_md: str, *, idea: str = "", project_name: str = "",
            pace: str = "standard") -> dict:
    """Rebuild slug's plan from an edited Build Guide. Returns a summary."""
    old = plan_builder.load_plan(slug)
    new_sha = build_guide_parser.source_sha256(new_md)
    if old is not None and new_sha == old.get("sourceDocSha256"):
        return {"changed": False, "planRevision": old.get("planRevision", 1)}

    plan = plan_builder.build_plan(slug, new_md, idea, project_name=project_name, pace=pace)
    if old is not None:
        plan["planRevision"] = int(old.get("planRevision", 1)) + 1
    plan_builder.save_plan(slug, plan)
    return {
        "changed": True,
        "first_build": old is None,
        "planRevision": plan["planRevision"],
    }
