"""Regression: the use-case word cloud must respect tenant visibility.

Bug: /library/use-cases hero showed "0 use cases" but the "What's hot"
word cloud still rendered terms — because the page filtered the case list
by viewer_company_id but cloud_for_use_cases re-queried use_cases.list_all
without the same filter. Fixed by threading viewer_company_id through
cloud_for_use_cases and refinement_chips_for_use_cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.products.library import use_cases, word_cloud


@pytest.fixture
def two_company_workspace(tmp_path, monkeypatch):
    """Seed the workspace with two cases owned by different companies."""
    monkeypatch.setattr(use_cases, "UC_ROOT", tmp_path / "library")
    ws_dir = use_cases._uc_dir("global")

    acme = {
        "use_case_id": "uc-acme-1",
        "workspace": "global",
        "title": "Acme RFP Response Automation",
        "summary": "Automate Acme proposal generation",
        "persona": "Maya, Sales Engineer at SaaS co",
        "industry": "SaaS",
        "complexity": "moderate",
        "problem": "Acme spends time on RFPs",
        "solution": "Use generator",
        "tools_used": [],
        "outcome_metric": "10h/wk saved",
        "tags": ["acme-only"],
        "created_at": "2026-06-01T00:00:00Z",
        "owning_company_id": "acme",
        "vetted": False,
    }
    widgets = {
        "use_case_id": "uc-widgets-1",
        "workspace": "global",
        "title": "Widgets Inventory Forecast",
        "summary": "Forecast widget demand",
        "persona": "Jordan, Ops Manager",
        "industry": "Manufacturing",
        "complexity": "advanced",
        "problem": "Widgets stockouts",
        "solution": "Demand model",
        "tools_used": [],
        "outcome_metric": "30% fewer stockouts",
        "tags": ["widgets-only"],
        "created_at": "2026-06-02T00:00:00Z",
        "owning_company_id": "widgets",
        "vetted": False,
    }
    (ws_dir / "uc-acme-1.json").write_text(json.dumps(acme), encoding="utf-8")
    (ws_dir / "uc-widgets-1.json").write_text(json.dumps(widgets), encoding="utf-8")
    return ws_dir


def test_cloud_without_viewer_sees_everything(two_company_workspace):
    """Sanity: no viewer filter → terms from both cases appear."""
    terms = word_cloud.cloud_for_use_cases(
        workspace="global", dimension="keyword", current={},
    )
    words = {t.word.lower() for t in terms}
    assert "acme" in words
    assert "widgets" in words


def test_cloud_filters_by_viewer_company(two_company_workspace):
    """Viewer for company 'acme' must NOT see widgets terms in the cloud."""
    terms = word_cloud.cloud_for_use_cases(
        workspace="global", dimension="keyword", current={},
        viewer_company_id="acme",
    )
    words = {t.word.lower() for t in terms}
    assert "acme" in words
    assert "widgets" not in words


def test_cloud_empty_when_viewer_owns_nothing(two_company_workspace):
    """If the viewer's company has no cases, the cloud is empty —
    matching the "0 use cases" header. This is the original bug."""
    terms = word_cloud.cloud_for_use_cases(
        workspace="global", dimension="keyword", current={},
        viewer_company_id="someone-else",
    )
    assert terms == []


def test_refinement_chips_filter_by_viewer_company(two_company_workspace):
    """Same invariant for refinement chips."""
    chips_all = word_cloud.refinement_chips_for_use_cases(
        workspace="global", current={"industry": "SaaS"},
    )
    chips_acme = word_cloud.refinement_chips_for_use_cases(
        workspace="global", current={"industry": "SaaS"},
        viewer_company_id="acme",
    )
    chips_widgets = word_cloud.refinement_chips_for_use_cases(
        workspace="global", current={"industry": "SaaS"},
        viewer_company_id="widgets",
    )

    assert any(c.word == "SaaS" for c in chips_all)
    assert any(c.word == "SaaS" for c in chips_acme)
    assert chips_widgets == []
