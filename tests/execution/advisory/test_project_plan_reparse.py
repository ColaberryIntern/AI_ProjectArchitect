"""Tests for Build Guide re-parse (regenerate the workstream plan on change)."""
import pytest

from execution.advisory import feature_task_generator as gen, plan_builder, project_plan_reparse as rp

GUIDE_V1 = """# Acme — Build Guide
---
# Chapter 1: Role Management

Users can be assigned roles and the app enforces them.
"""

GUIDE_V2 = GUIDE_V1 + """
---

# Chapter 2: Reporting

Managers can export weekly reports.
"""


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(gen, "is_available", lambda: False)
    return tmp_path


def test_first_build_when_no_plan_exists():
    d = rp.reparse("acme", GUIDE_V1, idea="x", project_name="Acme")
    assert d["changed"] and d["first_build"]
    assert d["planRevision"] == 1
    assert plan_builder.load_plan("acme")["projectName"] == "Acme"


def test_noop_when_doc_unchanged():
    rp.reparse("acme", GUIDE_V1, project_name="Acme")
    d = rp.reparse("acme", GUIDE_V1, project_name="Acme")
    assert d["changed"] is False
    assert plan_builder.load_plan("acme")["planRevision"] == 1


def test_doc_change_regenerates_and_bumps_revision():
    rp.reparse("acme", GUIDE_V1, project_name="Acme")
    d = rp.reparse("acme", GUIDE_V2, project_name="Acme")
    assert d["changed"] and not d["first_build"]
    assert d["planRevision"] == 2
    assert plan_builder.load_plan("acme")["sourceDocSha256"]
