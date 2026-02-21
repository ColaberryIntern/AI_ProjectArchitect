"""Shared test fixtures for the AI Project Architect test suite."""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from config.settings import OUTPUT_DIR


@pytest.fixture(autouse=True)
def set_test_environment(monkeypatch):
    """Ensure all tests run with ENVIRONMENT=test."""
    monkeypatch.setenv("ENVIRONMENT", "test")


@pytest.fixture
def tmp_output_dir(monkeypatch, tmp_path):
    """Redirect OUTPUT_DIR to a temporary directory for test isolation."""
    import config.settings as settings

    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    # Also patch it in modules that import OUTPUT_DIR at module level
    import execution.state_manager as sm
    import execution.full_pipeline as fp

    monkeypatch.setattr(sm, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(fp, "OUTPUT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def sample_state():
    """Return a minimal valid project state for testing."""
    return {
        "project": {
            "name": "Test Project",
            "slug": "test-project",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
        },
        "current_phase": "idea_intake",
        "idea": {"original_raw": "", "captured_at": None},
        "ideation": {
            "business_model": {"status": "open", "responses": [], "summary": None},
            "user_problem": {"status": "open", "responses": [], "summary": None},
            "differentiation": {"status": "open", "responses": [], "summary": None},
            "ai_leverage": {"status": "open", "responses": [], "summary": None},
            "ideation_summary": None,
            "approved": False,
        },
        "features": {"core": [], "optional": [], "approved": False},
        "outline": {
            "version": 1,
            "status": "draft",
            "locked_at": None,
            "locked_hash": None,
            "sections": [],
            "approval_history": [],
        },
        "chapters": [],
        "quality": {
            "final_report": {"ran_at": None, "all_passed": False, "details": []}
        },
        "document": {
            "version": "v1",
            "filename": None,
            "assembled_at": None,
            "output_path": None,
        },
        "version_history": [
            {
                "version": 1,
                "created_at": "2025-01-01T00:00:00+00:00",
                "change_summary": "Initial project creation",
            }
        ],
    }


@pytest.fixture
def sample_outline_sections():
    """Return sample outline sections for testing."""
    return [
        {
            "index": 1,
            "title": "System Purpose & Context",
            "type": "required",
            "summary": "Why this project exists and what problem it solves.",
        },
        {
            "index": 2,
            "title": "Target Users & Roles",
            "type": "required",
            "summary": "Who interacts with the system and in what capacity.",
        },
        {
            "index": 3,
            "title": "Core Capabilities",
            "type": "required",
            "summary": "What the system must do to deliver value.",
        },
        {
            "index": 4,
            "title": "Non-Goals & Explicit Exclusions",
            "type": "required",
            "summary": "What the system intentionally does not attempt.",
        },
        {
            "index": 5,
            "title": "High-Level Architecture",
            "type": "required",
            "summary": "How major components interact.",
        },
        {
            "index": 6,
            "title": "Execution Phases",
            "type": "required",
            "summary": "How the build is broken into logical chunks.",
        },
        {
            "index": 7,
            "title": "Risks, Constraints, and Assumptions",
            "type": "required",
            "summary": "What could go wrong and what is being assumed.",
        },
    ]


@pytest.fixture
def sample_profile():
    """Return a fully confirmed project profile for testing."""
    def _make_field(selected, confidence=0.85):
        return {
            "selected": selected,
            "confidence": confidence,
            "confirmed": True,
            "options": [
                {"value": selected, "label": selected, "description": f"Option: {selected}"},
                {"value": "alt_option", "label": "Alternative", "description": "An alternative"},
            ],
        }

    return {
        "problem_definition": _make_field("Users lack efficient project planning tools"),
        "target_user": _make_field("Non-technical business users"),
        "value_proposition": _make_field("Automate requirements gathering with AI"),
        "deployment_type": _make_field("SaaS multi-tenant"),
        "ai_depth": _make_field("AI-assisted"),
        "monetization_model": _make_field("Freemium SaaS"),
        "mvp_scope": _make_field("Core features only"),
        "technical_constraints": ["Must support modern browsers", "REST API backend"],
        "non_functional_requirements": ["99.9% uptime", "Sub-2s response time"],
        "success_metrics": ["50% reduction in planning time"],
        "risk_assessment": ["LLM availability dependency"],
        "core_use_cases": ["Submit idea", "Review profile", "Select features"],
        "selected_features": [],
        "generated_at": "2025-01-01T00:00:00+00:00",
        "confirmed_at": "2025-01-01T00:01:00+00:00",
    }


@pytest.fixture
def sample_enhanced_outline_sections():
    """Return sample 10-section enhanced outline for testing."""
    return [
        {"index": 1, "title": "Executive Summary", "type": "required", "summary": "High-level overview of the product vision and goals."},
        {"index": 2, "title": "Problem & Market Context", "type": "required", "summary": "The core problem being solved and market landscape."},
        {"index": 3, "title": "User Personas & Core Use Cases", "type": "required", "summary": "Who uses the system and their primary workflows."},
        {"index": 4, "title": "Functional Requirements", "type": "required", "summary": "Detailed capabilities the system must provide."},
        {"index": 5, "title": "AI & Intelligence Architecture", "type": "required", "summary": "How AI components are designed and integrated."},
        {"index": 6, "title": "Non-Functional Requirements", "type": "required", "summary": "Performance, scalability, and reliability constraints."},
        {"index": 7, "title": "Technical Architecture & Data Model", "type": "required", "summary": "System components, data flow, and storage design."},
        {"index": 8, "title": "Security & Compliance", "type": "required", "summary": "Authentication, authorization, and regulatory compliance."},
        {"index": 9, "title": "Success Metrics & KPIs", "type": "required", "summary": "Measurable outcomes defining project success."},
        {"index": 10, "title": "Roadmap & Phased Delivery", "type": "required", "summary": "Implementation timeline broken into delivery phases."},
    ]


@pytest.fixture
def sample_core_feature():
    """Return a sample core feature for testing."""
    return {
        "id": "feat-001",
        "name": "Guided Questioning",
        "description": "Ask structured questions to clarify user intent",
        "rationale": "Eliminates idea vagueness through systematic inquiry",
        "problem_mapped_to": "idea_vagueness",
        "build_order": 1,
    }


@pytest.fixture
def sample_optional_feature():
    """Return a sample optional feature for testing."""
    return {
        "id": "feat-opt-001",
        "name": "PDF Export",
        "description": "Export final document as PDF",
        "rationale": "Enables distribution to stakeholders without Markdown support",
        "deferred": True,
        "defer_reason": "Not needed for MVP",
    }
