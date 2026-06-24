"""Tests for the project-plan ID Law, content hash, and validation gate."""
import copy

from execution.advisory import project_plan as pp


# ── slug ────────────────────────────────────────────────────────────

def test_slug_basics():
    assert pp.slug("Functional Requirements") == "functional-requirements"
    assert pp.slug("Café  & Bar!!") == "cafe-bar"
    assert pp.slug("  --Hello-- ") == "hello"


def test_slug_truncates_at_word_boundary():
    s = pp.slug("a-very-long-feature-title-that-keeps-going-and-going-forever")
    assert len(s) <= 40
    assert not s.endswith("-")
    # truncation lands on a whole word, not mid-word
    assert s == s.rsplit("-", 0)[0]


# ── id derivers ─────────────────────────────────────────────────────

def test_id_formulas():
    assert pp.init_id(4, "Functional Requirements") == "INIT.ch04-functional-requirements"
    assert pp.list_id(4, "Role Management") == "LIST.ch04.role-management"
    lid = pp.list_id(4, "Role Management")
    assert pp.todo_id(lid, "[BUILD] CRUD endpoints for roles") == "TODO.ch04.role-management.crud-endpoints-for-roles"
    assert pp.design_id("Role Management Dashboard") == "DESIGN.role-management-dashboard"


def test_phase_tag_does_not_change_todo_id():
    lid = pp.list_id(4, "Role Management")
    a = pp.todo_id(lid, "[BUILD] CRUD endpoints")
    b = pp.todo_id(lid, "[HARDEN] CRUD endpoints")
    assert a == b  # re-tagging keeps the id stable


def test_chapter_number_is_the_spine_survives_rename():
    # Same chapter number, different title → only the slug label changes; the
    # ch04 spine is intact (so a human title tweak doesn't reparent the subtree).
    assert pp.init_id(4, "Functional Requirements").startswith("INIT.ch04-")
    assert pp.init_id(4, "Core Features").startswith("INIT.ch04-")


def test_collision_suffixing_is_deterministic():
    ids = ["LIST.ch04.x", "LIST.ch04.x", "LIST.ch04.y", "LIST.ch04.x"]
    assert pp.resolve_collisions(ids) == [
        "LIST.ch04.x", "LIST.ch04.x-2", "LIST.ch04.y", "LIST.ch04.x-3",
    ]


def _sample_plan():
    return {
        "$schema": pp.SCHEMA,
        "projectSlug": "demo",
        "initiatives": [
            {
                "title": "Functional Requirements", "order": 4, "status": "active",
                "charter": "Build the core features.",
                "lists": [
                    {
                        "title": "Role Management", "order": 1, "status": "active",
                        "designs": ["DESIGN.role-management-dashboard"],
                        "todos": [
                            {"title": "[BUILD] CRUD endpoints", "phase": "BUILD",
                             "acceptance": "Endpoints return correct codes.",
                             "dueOffsetDays": 5, "order": 1, "status": "active", "deps": []},
                            {"title": "[BREAK] Reject bad role payloads", "phase": "BREAK",
                             "acceptance": "Invalid payloads 422.", "dueOffsetDays": 6,
                             "order": 2, "status": "active", "deps": []},
                        ],
                    }
                ],
            }
        ],
        "designs": [{"title": "Role Management Dashboard"}],
    }


def test_assign_ids_populates_tree():
    plan = pp.assign_ids(_sample_plan())
    init = plan["initiatives"][0]
    assert init["id"] == "INIT.ch04-functional-requirements"
    lst = init["lists"][0]
    assert lst["id"] == "LIST.ch04.role-management"
    assert lst["todos"][0]["id"] == "TODO.ch04.role-management.crud-endpoints"
    assert plan["designs"][0]["id"] == "DESIGN.role-management-dashboard"


# ── content hash ────────────────────────────────────────────────────

def test_hash_changes_on_content_but_id_stable():
    plan = pp.assign_ids(_sample_plan())
    todo = plan["initiatives"][0]["lists"][0]["todos"][0]
    h1 = pp.content_hash(todo)
    before_id = todo["id"]
    todo["acceptance"] = "Endpoints return correct codes AND log."
    h2 = pp.content_hash(todo)
    assert h1 != h2
    assert todo["id"] == before_id  # same node, new hash


def test_hash_ignores_id_field():
    a = {"title": "X", "acceptance": "y", "phase": "BUILD", "order": 1, "status": "active"}
    b = dict(a, id="TODO.whatever")
    assert pp.content_hash(a) == pp.content_hash(b)


def test_hash_deps_order_independent():
    a = {"title": "X", "deps": ["b", "a"]}
    b = {"title": "X", "deps": ["a", "b"]}
    assert pp.content_hash(a) == pp.content_hash(b)


# ── validation gate ─────────────────────────────────────────────────

def test_valid_plan_passes():
    plan = pp.assign_ids(_sample_plan())
    assert pp.validate_plan(plan) == []


def test_rule3_todo_requires_phase_and_acceptance():
    plan = pp.assign_ids(_sample_plan())
    todo = plan["initiatives"][0]["lists"][0]["todos"][0]
    todo["acceptance"] = ""
    todo["phase"] = "NOPE"
    errs = pp.validate_plan(plan)
    assert any("acceptance" in e for e in errs)
    assert any("phase" in e for e in errs)


def test_rule7_feature_needs_build_and_break():
    plan = pp.assign_ids(_sample_plan())
    # drop the BREAK todo → rule 7 should fire
    lst = plan["initiatives"][0]["lists"][0]
    lst["todos"] = [t for t in lst["todos"] if t["phase"] != "BREAK"]
    errs = pp.validate_plan(plan)
    assert any("BREAK" in e for e in errs)


def test_rule1_hand_edited_id_is_rejected():
    plan = pp.assign_ids(_sample_plan())
    plan["initiatives"][0]["lists"][0]["todos"][0]["id"] = "TODO.hand.edited"
    errs = pp.validate_plan(plan)
    assert any("derived, not authored" in e for e in errs)


def test_rule4_dangling_dep_is_rejected():
    plan = pp.assign_ids(_sample_plan())
    plan["initiatives"][0]["lists"][0]["todos"][0]["deps"] = ["TODO.does.not.exist"]
    errs = pp.validate_plan(plan)
    assert any("missing id" in e for e in errs)


def test_rule6_active_cannot_reference_retired():
    plan = _sample_plan()
    # add a retired todo and have an active one depend on it
    lst = plan["initiatives"][0]["lists"][0]
    lst["todos"].append({"title": "[HARDEN] old", "phase": "HARDEN",
                         "acceptance": "x", "order": 3, "status": "retired", "deps": []})
    plan = pp.assign_ids(plan)
    retired_id = lst["todos"][-1]["id"]
    lst["todos"][0]["deps"] = [retired_id]
    errs = pp.validate_plan(plan)
    assert any("retired" in e for e in errs)


def test_docanchor_validation_when_anchors_supplied():
    plan = pp.assign_ids(_sample_plan())
    plan["initiatives"][0]["docAnchor"] = "#missing-anchor"
    errs = pp.validate_plan(plan, doc_anchors={"#something-else"})
    assert any("docAnchor" in e for e in errs)
