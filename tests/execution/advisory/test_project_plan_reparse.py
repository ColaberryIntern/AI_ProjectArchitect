"""Tests for incremental Build Guide re-parse (deterministic, LLM offline)."""
import pytest

from execution.advisory import (
    feature_task_generator as gen,
    plan_builder,
    project_plan,
    project_plan_reparse as rp,
)

GUIDE_V1 = """# Acme — Build Guide
---
# Chapter 1: Role Management

Users can be assigned roles and the app enforces them.

---

# Chapter 2: Reporting

Managers can export weekly reports as CSV.
"""

GUIDE_ADD = GUIDE_V1 + """
---

# Chapter 3: Notifications

Send email alerts on key events.
"""

GUIDE_REMOVE = """# Acme — Build Guide
---
# Chapter 1: Role Management

Users can be assigned roles and the app enforces them.
"""


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(gen, "is_available", lambda: False)  # deterministic fallback
    return tmp_path


def _ids(plan, status=None):
    return {n["id"] for lvl, n, _ in project_plan.iter_nodes(plan)
            if lvl == "initiative" and (status is None or n.get("status") == status)}


def test_first_build_when_no_plan_exists():
    d = rp.reparse("acme", GUIDE_V1)
    assert d["changed"] and d.get("first_build")
    plan = plan_builder.load_plan("acme")
    assert {i["title"] for i in plan["initiatives"]} == {"Role Management", "Reporting"}


def test_noop_when_doc_unchanged():
    rp.reparse("acme", GUIDE_V1)
    d = rp.reparse("acme", GUIDE_V1)
    assert d["changed"] is False
    assert plan_builder.load_plan("acme")["planRevision"] == 1


def test_added_chapter_is_proposed():
    rp.reparse("acme", GUIDE_V1)
    d = rp.reparse("acme", GUIDE_ADD)
    assert d["changed"] and d["added"] == 1
    plan = plan_builder.load_plan("acme")
    assert plan["planRevision"] == 2
    # the new chapter's initiative is proposed (awaits human promotion)
    notif = next(i for i in plan["initiatives"] if i["title"] == "Notifications")
    assert notif["status"] == "proposed"
    # unchanged chapters retained active
    roles = next(i for i in plan["initiatives"] if i["title"] == "Role Management")
    assert roles["status"] == "active"


def test_removed_chapter_is_retired_not_deleted():
    rp.reparse("acme", GUIDE_V1)
    d = rp.reparse("acme", GUIDE_REMOVE)
    assert d["changed"] and d["retired"] >= 1
    plan = plan_builder.load_plan("acme")
    # Reporting kept but retired (soft-delete, audit trail)
    reporting = next(i for i in plan["initiatives"] if i["title"] == "Reporting")
    assert reporting["status"] == "retired"


def test_plan_revision_increments_on_change():
    rp.reparse("acme", GUIDE_V1)
    rp.reparse("acme", GUIDE_ADD)
    rp.reparse("acme", GUIDE_REMOVE)
    assert plan_builder.load_plan("acme")["planRevision"] == 3
