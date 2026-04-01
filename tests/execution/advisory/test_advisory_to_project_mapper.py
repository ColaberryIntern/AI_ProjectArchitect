"""Tests for advisory-to-project mapper."""

import os
import tempfile

import pytest

from execution.advisory.advisory_to_project_mapper import (
    generate_project_name,
    map_advisory_to_project_text,
    create_project_from_advisory,
)


def _sample_session(**overrides):
    """Build a realistic advisory session for testing."""
    session = {
        "session_id": "test-session-001",
        "status": "complete",
        "business_idea": "AI-powered logistics platform",
        "answers": [
            {"question_id": "q1_business_overview", "question_text": "What does your business do?", "answer_text": "We are a logistics and freight coordination company handling last-mile delivery"},
            {"question_id": "q2_company_size", "question_text": "How large is your organization?", "answer_text": "51-200 employees"},
            {"question_id": "q3_departments", "question_text": "Which departments?", "answer_text": "Operations, Sales, Customer Support"},
            {"question_id": "q4_bottlenecks", "question_text": "Bottlenecks?", "answer_text": "Manual route planning consuming hours daily"},
            {"question_id": "q6_current_tools", "question_text": "Current tools?", "answer_text": "Salesforce, Excel/Sheets"},
            {"question_id": "q9_budget_timeline", "question_text": "Budget?", "answer_text": "$50K - $100K"},
            {"question_id": "q10_success_vision", "question_text": "Success vision?", "answer_text": "Cut operational costs by 30% and automate route planning"},
        ],
        "lead": {
            "name": "Jane Smith",
            "email": "jane@xyzlogistics.com",
            "company": "XYZ Logistics",
            "role": "VP Operations",
        },
        "problem_analysis": {
            "primary_problem": "operations",
            "domain_scores": {"operations": 0.8, "sales": 0.3, "customer_support": 0.2},
        },
        "selected_outcomes": ["reduce_costs", "scale_operations"],
        "selected_capabilities": ["route_optimization", "demand_forecasting"],
        "capability_map": {
            "departments": [
                {"name": "Operations", "capabilities": [{"name": "Route Optimization"}, {"name": "Demand Forecasting"}]},
                {"name": "Sales", "capabilities": [{"name": "Lead Scoring"}]},
            ],
        },
        "agents": [
            {"name": "Route Optimizer", "role": "Optimizes delivery routes in real-time"},
            {"name": "Demand Forecaster", "role": "Predicts volume patterns"},
        ],
        "architecture": {
            "engines": [
                {"name": "Operations Engine"},
                {"name": "Intelligence Engine"},
            ],
        },
        "impact_model": {
            "cost_savings": {"total_annual": 180000},
            "revenue_impact": {"estimated_annual_revenue_gain": 50000},
            "roi_summary": {"payback_months": 8},
        },
        "linked_project_slug": None,
    }
    session.update(overrides)
    return session


class TestGenerateProjectName:
    def test_uses_company_and_primary_system(self):
        session = _sample_session()
        name = generate_project_name(session)
        assert "XYZ Logistics" in name
        assert "Operations Engine" in name

    def test_falls_back_to_q1_when_no_company(self):
        session = _sample_session(lead={})
        name = generate_project_name(session)
        # Should use Q1 answer truncated
        assert "logistics" in name.lower()

    def test_uses_outcome_when_no_problem_analysis(self):
        session = _sample_session(problem_analysis={})
        name = generate_project_name(session)
        assert "Operations Engine" in name  # from selected_outcomes


class TestMapAdvisoryToProjectText:
    def test_includes_all_sections(self):
        session = _sample_session()
        text = map_advisory_to_project_text(session)
        assert "COMPANY OVERVIEW" in text
        assert "BUSINESS PROBLEM" in text
        assert "OBJECTIVES" in text
        assert "PROPOSED AI SYSTEM" in text
        assert "DEPARTMENTS" in text
        assert "AI WORKFORCE" in text
        assert "FINANCIAL IMPACT" in text

    def test_includes_specific_data(self):
        session = _sample_session()
        text = map_advisory_to_project_text(session)
        assert "logistics and freight" in text.lower()
        assert "51-200 employees" in text
        assert "Route Optimizer" in text
        assert "$180,000" in text
        assert "8 months" in text

    def test_handles_minimal_session(self):
        session = {
            "session_id": "minimal",
            "business_idea": "Simple AI idea",
            "answers": [],
        }
        text = map_advisory_to_project_text(session)
        assert text == "Simple AI idea"

    def test_handles_empty_session(self):
        session = {"session_id": "empty", "business_idea": "", "answers": []}
        text = map_advisory_to_project_text(session)
        assert len(text) > 0  # Should have fallback text


class TestCreateProjectFromAdvisory:
    @pytest.fixture(autouse=True)
    def setup_dirs(self, tmp_path, monkeypatch):
        """Set up both advisory and project output directories."""
        advisory_dir = tmp_path / "advisory"
        advisory_dir.mkdir()
        project_dir = tmp_path / "projects"
        project_dir.mkdir()
        monkeypatch.setattr("config.settings.ADVISORY_OUTPUT_DIR", advisory_dir)
        monkeypatch.setattr("config.settings.OUTPUT_DIR", project_dir)
        monkeypatch.setattr("execution.advisory.advisory_state_manager.ADVISORY_OUTPUT_DIR", advisory_dir)
        monkeypatch.setattr("execution.state_manager.OUTPUT_DIR", project_dir)
        self.advisory_dir = advisory_dir
        self.project_dir = project_dir

    def _create_advisory_session(self, session):
        """Write a mock advisory session to disk."""
        import json
        session_dir = self.advisory_dir / session["session_id"]
        session_dir.mkdir(parents=True, exist_ok=True)
        with open(session_dir / "advisory_state.json", "w") as f:
            json.dump(session, f)

    def test_creates_project_and_links(self):
        session = _sample_session()
        self._create_advisory_session(session)

        result = create_project_from_advisory("test-session-001")
        assert result is not None
        assert result["project"]["name"] == "XYZ Logistics - AI Operations Engine"
        assert result["advisory"]["source"] == "advisory"
        assert result["advisory"]["advisory_session_id"] == "test-session-001"
        assert result["advisory"]["contact_email"] == "jane@xyzlogistics.com"

        # Verify idea was recorded
        assert result["idea"]["original_raw"] != ""
        assert "COMPANY OVERVIEW" in result["idea"]["original_raw"]

    def test_duplicate_protection(self):
        session = _sample_session()
        self._create_advisory_session(session)

        result1 = create_project_from_advisory("test-session-001")
        assert result1 is not None

        # Second call should skip (already linked)
        result2 = create_project_from_advisory("test-session-001")
        assert result2 is None

    def test_missing_session_returns_none(self):
        result = create_project_from_advisory("nonexistent-session")
        assert result is None

    def test_handles_session_without_lead(self):
        session = _sample_session(lead=None)
        self._create_advisory_session(session)

        result = create_project_from_advisory("test-session-001")
        assert result is not None
        assert result["advisory"]["contact_name"] == ""
