"""Tests for the Build Guide → project-plan generation layer."""
import pytest

from execution.advisory import (
    build_guide_parser as parser,
    feature_task_generator as gen,
    plan_builder,
    project_plan,
)

SAMPLE_GUIDE = """# Acme — Build Guide

---

# Chapter 1: Role Management

> **Chapter purpose**: design intent for role management.

## Feature Specifications

The system supports role management. Users can be assigned roles and the app
enforces them across screens.

---

# Chapter 2: Reporting

> **Chapter purpose**: design intent for reporting.

## Feature Specifications

Managers can export weekly reports as CSV.
"""


# ── parser ──────────────────────────────────────────────────────────

def test_parse_extracts_chapter_spine():
    chapters = parser.parse_build_guide(SAMPLE_GUIDE)
    assert [c["order"] for c in chapters] == [1, 2]
    assert chapters[0]["title"] == "Role Management"
    assert "role management" in chapters[0]["body"].lower()


def test_chapter_anchor_is_in_doc_anchors():
    chapters = parser.parse_build_guide(SAMPLE_GUIDE)
    anchors = parser.doc_anchors(SAMPLE_GUIDE)
    for c in chapters:
        assert c["anchor"] in anchors  # initiative docAnchor will validate


def test_source_sha_is_stable():
    a = parser.source_sha256(SAMPLE_GUIDE)
    b = parser.source_sha256(SAMPLE_GUIDE)
    assert a == b and a.startswith("sha256:")


# ── feature/task generator (LLM offline) ────────────────────────────

@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.setattr(gen, "is_available", lambda: False)


def test_generate_features_fallback_has_build_and_break():
    feats = gen.generate_features("Role Management", "some prose")
    assert feats
    for f in feats:
        phases = {t["phase"] for t in f["todos"]}
        assert "BUILD" in phases and "BREAK" in phases


# ── plan_builder end-to-end (offline → valid plan) ──────────────────

def test_build_plan_produces_valid_plan_offline():
    plan = plan_builder.build_plan("acme", SAMPLE_GUIDE, pace="standard", source_doc="guide.md")
    # structure: 2 initiatives, each with ≥1 list, each list with todos
    assert len(plan["initiatives"]) == 2
    # IDs assigned by the ID Law
    assert plan["initiatives"][0]["id"] == "INIT.ch01-role-management"
    # every todo has a due offset within the pace window
    todos = [n for lvl, n, _ in project_plan.iter_nodes(plan) if lvl == "todo"]
    assert todos
    assert all(1 <= t["dueOffsetDays"] <= 30 for t in todos)
    # the whole plan PASSES the validation gate (verify step) including anchors
    errors = project_plan.validate_plan(plan, doc_anchors=parser.doc_anchors(SAMPLE_GUIDE))
    assert errors == [], errors


def test_build_plan_due_offsets_increase_by_build_order():
    plan = plan_builder.build_plan("acme", SAMPLE_GUIDE, pace="sprint")
    todos = [n for lvl, n, _ in project_plan.iter_nodes(plan) if lvl == "todo"]
    offs = [t["dueOffsetDays"] for t in todos]
    assert offs == sorted(offs)
    assert max(offs) <= 7  # sprint window


def test_save_and_load_plan_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_builder, "OUTPUT_DIR", tmp_path)
    plan = plan_builder.build_plan("acme", SAMPLE_GUIDE)
    plan_builder.save_plan("acme", plan)
    loaded = plan_builder.load_plan("acme")
    assert loaded["projectSlug"] == "acme"
    assert loaded["initiatives"][0]["id"] == "INIT.ch01-role-management"
