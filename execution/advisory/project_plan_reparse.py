"""Incremental re-parse: apply a Build Guide edit as a bounded plan delta.

After the first build, the doc is never regenerated from scratch. On a doc
change this computes a per-chapter delta against the frozen plan:

  * chapter unchanged (same body hash) → keep its initiative subtree verbatim
    (stable ids + hashes; the reconciler then makes zero BC calls for it)
  * chapter changed → regenerate that chapter's features/todos; ids that
    already existed stay ``active`` (in-place update downstream), genuinely new
    ids are emitted ``proposed`` (await human promotion), ids that disappeared
    are kept as ``retired`` (soft-delete, never dropped)
  * chapter added → new initiative, all nodes ``proposed``
  * chapter removed → initiative + subtree marked ``retired``

This keeps the stochastic task generation quarantined to *what changed*, and
everything untouched keeps its id byte-for-byte. ``proposed`` nodes are skipped
by the reconciler until a human promotes them, so a doc edit never silently
pushes new tasks to Basecamp.
"""
from __future__ import annotations

from execution.advisory import build_guide_parser, feature_task_generator, plan_builder, project_plan


def _mark_subtree(initiative: dict, status: str) -> None:
    initiative["status"] = status
    for lst in initiative.get("lists") or []:
        lst["status"] = status
        for todo in lst.get("todos") or []:
            todo["status"] = status


def _subtree_ids(initiative: dict) -> set[str]:
    return {n.get("id") for lvl, n, _ in project_plan.iter_nodes({"initiatives": [initiative]})
            if lvl in ("list", "todo")}


def _build_initiative(ch: dict, pace: str) -> dict:
    """Build a single fresh initiative subtree (features + todos) for a chapter."""
    features = feature_task_generator.generate_features(ch["title"], ch["body"])
    lists = []
    for fi, feat in enumerate(features, 1):
        todos = [{
            "title": t["title"], "phase": t["phase"], "kind": t.get("kind", "ai"),
            "acceptance": t["acceptance"], "order": ti, "status": "active", "deps": [],
        } for ti, t in enumerate(feat["todos"], 1)]
        lists.append({"title": feat["title"], "order": fi, "status": "active",
                      "designs": [], "todos": todos})
    return {
        "title": ch["title"], "order": ch["order"], "status": "active",
        "charter": build_guide_parser.first_sentence(ch["body"]),
        "docAnchor": ch["anchor"], "lists": lists,
        "sourceBodyHash": build_guide_parser.source_sha256(ch["body"]),
    }


def reparse(slug: str, new_md: str, *, pace: str = "standard") -> dict:
    """Apply a Build Guide edit to slug's plan. Returns a delta summary.

    If no plan exists yet, this is a first build (delegates to plan_builder).
    """
    old = plan_builder.load_plan(slug)
    if old is None:
        plan = plan_builder.build_plan(slug, new_md, pace=pace)
        plan_builder.save_plan(slug, plan)
        return {"changed": True, "first_build": True, "planRevision": plan["planRevision"]}

    new_sha = build_guide_parser.source_sha256(new_md)
    if new_sha == old.get("sourceDocSha256"):
        return {"changed": False, "planRevision": old.get("planRevision", 1)}

    old_inits = {i.get("id"): i for i in (old.get("initiatives") or [])}
    new_chapters = build_guide_parser.parse_build_guide(new_md)
    delta = {"kept": 0, "regenerated": 0, "added": 0, "retired": 0, "proposed": 0}

    new_initiatives: list[dict] = []
    seen: set[str] = set()
    for ch in new_chapters:
        iid = project_plan.init_id(ch["order"], ch["title"])
        seen.add(iid)
        body_hash = build_guide_parser.source_sha256(ch["body"])
        old_init = old_inits.get(iid)
        if old_init and old_init.get("sourceBodyHash") == body_hash:
            new_initiatives.append(old_init)  # unchanged → verbatim
            delta["kept"] += 1
            continue

        fresh = _build_initiative(ch, pace)
        project_plan.assign_ids({"initiatives": [fresh], "designs": []})
        if old_init is None:
            _mark_subtree(fresh, "proposed")  # brand-new chapter awaits promotion
            delta["added"] += 1
            delta["proposed"] += len(_subtree_ids(fresh))
        else:
            old_ids = _subtree_ids(old_init)
            fresh_ids = _subtree_ids(fresh)
            # new nodes → proposed; existing-id nodes stay active (in-place update)
            for lvl, node, _ in project_plan.iter_nodes({"initiatives": [fresh]}):
                if lvl in ("list", "todo") and node.get("id") not in old_ids:
                    node["status"] = "proposed"
                    delta["proposed"] += 1
            # nodes that disappeared from the chapter → carry over as retired
            for lst in old_init.get("lists") or []:
                if lst.get("id") not in fresh_ids:
                    _mark_list_retired(lst)
                    fresh["lists"].append(lst)
                    delta["retired"] += 1
                else:
                    fresh_list = next(fl for fl in fresh["lists"] if fl.get("id") == lst.get("id"))
                    for todo in lst.get("todos") or []:
                        if todo.get("id") not in {t.get("id") for t in fresh_list["todos"]}:
                            todo["status"] = "retired"
                            fresh_list["todos"].append(todo)
                            delta["retired"] += 1
            delta["regenerated"] += 1
        new_initiatives.append(fresh)

    # chapters removed from the doc → retire the whole initiative subtree
    for iid, old_init in old_inits.items():
        if iid not in seen:
            _mark_subtree(old_init, "retired")
            new_initiatives.append(old_init)
            delta["retired"] += 1

    new_plan = dict(old)
    new_plan["initiatives"] = new_initiatives
    new_plan["sourceDocSha256"] = new_sha
    new_plan["planRevision"] = int(old.get("planRevision", 1)) + 1
    project_plan.assign_ids(new_plan)
    plan_builder.save_plan(slug, new_plan)
    return {"changed": True, "planRevision": new_plan["planRevision"], **delta}


def _mark_list_retired(lst: dict) -> None:
    lst["status"] = "retired"
    for todo in lst.get("todos") or []:
        todo["status"] = "retired"
