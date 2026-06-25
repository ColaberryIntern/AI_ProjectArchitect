"""Tests for the Build Guide → workstream project-plan generation."""
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

The system supports role management with enforcement across screens.

---

# Chapter 2: Reporting

Managers can export weekly reports as CSV.
"""


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    # Force deterministic fallback (no network/LLM) for both generators.
    monkeypatch.setattr(gen, "is_available", lambda: False)


# ── parser (spine context) ──────────────────────────────────────────

def test_parse_extracts_chapter_spine():
    chapters = parser.parse_build_guide(SAMPLE_GUIDE)
    assert [c["order"] for c in chapters] == [1, 2]
    assert chapters[0]["title"] == "Role Management"


def test_source_sha_is_stable():
    assert parser.source_sha256(SAMPLE_GUIDE) == parser.source_sha256(SAMPLE_GUIDE)


# ── business-process generator (fallback) ───────────────────────────

def test_generate_process_plan_fallback_has_build_and_break():
    plan = gen.generate_process_plan("an app", ["Role Management", "Reporting"])
    assert 7 <= len(plan) <= 10            # 7-10 business processes
    for proc in plan:
        assert proc["title"]
        phases = {t["phase"] for t in proc["tasks"]}
        assert "BUILD" in phases and "BREAK" in phases
        for t in proc["tasks"]:
            assert t["acceptance"] and t["kind"] in ("ai", "human")


# ── build_plan: one list grouped by business processes ──────────────

def test_build_plan_is_one_list_of_business_processes_and_valid():
    plan = plan_builder.build_plan("acme", SAMPLE_GUIDE, "an app for roles + reporting",
                                   project_name="Acme", pace="standard")
    # ONE initiative (the single project list) grouped by business processes
    assert len(plan["initiatives"]) == 1
    lists = [n for lvl, n, _ in project_plan.iter_nodes(plan) if lvl == "list"]
    todos = [n for lvl, n, _ in project_plan.iter_nodes(plan) if lvl == "todo"]
    assert 7 <= len(lists) <= 10       # 7-10 understandable process groups
    assert todos
    for t in todos:
        assert t["acceptance"] and t["kind"] in ("ai", "human")
        assert t["phase"] in ("BUILD", "BREAK", "HARDEN")
        assert 1 <= t["dueOffsetDays"] <= 30
    # the whole plan passes the validation gate (Failure-First per process)
    assert project_plan.validate_plan(plan) == []


def test_build_plan_due_offsets_increase_by_order():
    plan = plan_builder.build_plan("acme", SAMPLE_GUIDE, "x", pace="sprint")
    offs = [n["dueOffsetDays"] for lvl, n, _ in project_plan.iter_nodes(plan) if lvl == "todo"]
    assert offs == sorted(offs) and max(offs) <= 7


def test_save_and_load_plan_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_builder, "OUTPUT_DIR", tmp_path)
    plan = plan_builder.build_plan("acme", SAMPLE_GUIDE, "x", project_name="Acme")
    plan_builder.save_plan("acme", plan)
    loaded = plan_builder.load_plan("acme")
    assert loaded["projectSlug"] == "acme"
    assert loaded["projectName"] == "Acme"
    assert len(loaded["initiatives"]) == 1
