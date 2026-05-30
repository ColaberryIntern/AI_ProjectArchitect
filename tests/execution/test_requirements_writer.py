"""Unit tests for execution/requirements_writer.py."""

import json

import pytest

from execution import requirements_writer
from execution.requirements_writer import (
    build_requirements_document,
    collect_requirements,
    read_requirements,
    write_requirements,
)


@pytest.fixture
def base_state():
    return {
        "project": {"name": "Demo App", "slug": "demo-app"},
        "current_phase": "feature_discovery",
        "features": {
            "core": [
                {
                    "id": "REQ-001",
                    "name": "User Login",
                    "description": "Allow users to log in.",
                    "rationale": "Authenticated access is required.",
                    "type": "core",
                    "problem_mapped_to": "auth",
                    "build_order": 2,
                    "priority": "must",
                    "actor": "user",
                    "action": "log in with email and password",
                    "value": "they can access their data",
                    "acceptance_criteria": [
                        {
                            "id": "AC-001-1",
                            "given": "a registered user with valid creds",
                            "when": "they POST /login with correct creds",
                            "then": "the response is 200 and a session cookie is set",
                            "measurable": True,
                        }
                    ],
                },
                {
                    "id": "REQ-002",
                    "name": "Dashboard",
                    "description": "Show user dashboard.",
                    "rationale": "Users land here after login.",
                    "type": "core",
                    "problem_mapped_to": "ux",
                    "build_order": 1,
                    "priority": "must",
                    "acceptance_criteria": [
                        {
                            "id": "AC-002-1",
                            "given": "a logged-in user",
                            "when": "they GET /dashboard",
                            "then": "the response renders <= 500ms with their name",
                            "measurable": True,
                        }
                    ],
                },
            ],
            "optional": [
                {
                    "id": "REQ-003",
                    "name": "Dark Mode",
                    "description": "Dark theme toggle.",
                    "rationale": "User preference.",
                    "type": "optional",
                    "deferred": False,
                    "priority": "could",
                }
            ],
        },
    }


class TestCollectRequirements:
    def test_orders_core_by_build_order_then_optional(self, base_state):
        reqs = collect_requirements(base_state)
        assert [r["id"] for r in reqs] == ["REQ-002", "REQ-001", "REQ-003"]

    def test_promotes_features_missing_requirement_fields(self, base_state):
        # Strip Requirement fields from one core entry to test promotion.
        base_state["features"]["core"][0].pop("priority")
        base_state["features"]["core"][0].pop("acceptance_criteria")
        reqs = collect_requirements(base_state)
        login = next(r for r in reqs if r["id"] == "REQ-001")
        assert login["priority"] == "must"  # default for core
        assert login["acceptance_criteria"] == []
        assert login["requirement_type"] == "functional"
        assert login["traces_to"]["problem_id"] == "auth"

    def test_handles_empty_state(self):
        reqs = collect_requirements({"project": {"slug": "x"}})
        assert reqs == []

    def test_features_in_core_bucket_get_must_priority(self):
        # Regression: features stored under state.features.core[] don't
        # carry an explicit ``type`` field — their MVP class is implied
        # by the bucket. Ensure they still get priority "must" by default.
        state = {
            "project": {"slug": "x"},
            "features": {
                "core": [
                    {
                        "id": "REQ-X",
                        "name": "X",
                        "description": "x",
                        "rationale": "x",
                        "build_order": 1,
                    }
                ],
                "optional": [],
            },
        }
        reqs = collect_requirements(state)
        assert reqs[0]["priority"] == "must"
        assert reqs[0]["type"] == "core"

    def test_features_in_optional_bucket_get_should_priority(self):
        state = {
            "project": {"slug": "x"},
            "features": {
                "core": [],
                "optional": [
                    {
                        "id": "REQ-Y",
                        "name": "Y",
                        "description": "y",
                        "rationale": "y",
                    }
                ],
            },
        }
        reqs = collect_requirements(state)
        assert reqs[0]["priority"] == "should"
        assert reqs[0]["type"] == "optional"


class TestBuildRequirementsDocument:
    def test_envelope_fields(self, base_state):
        doc = build_requirements_document(base_state)
        assert doc["schema_version"] == "1.0"
        assert doc["project"]["slug"] == "demo-app"
        assert "generated_at" in doc
        assert isinstance(doc["requirements"], list)
        assert doc["summary"]["total"] == 3

    def test_summary_counts(self, base_state):
        doc = build_requirements_document(base_state)
        s = doc["summary"]
        assert s["by_priority"]["must"] == 2
        assert s["by_priority"]["could"] == 1
        assert s["by_mvp_class"]["core"] == 2
        assert s["by_mvp_class"]["optional"] == 1
        assert s["with_acceptance_criteria"] == 2


class TestWriteAndReadRequirements:
    def test_writes_file_to_specs_dir(self, base_state, tmp_path, monkeypatch):
        monkeypatch.setattr(requirements_writer, "OUTPUT_DIR", tmp_path)
        path = write_requirements(base_state)
        assert path == tmp_path / "demo-app" / "specs" / "requirements.json"
        assert path.exists()

    def test_round_trip(self, base_state, tmp_path, monkeypatch):
        monkeypatch.setattr(requirements_writer, "OUTPUT_DIR", tmp_path)
        write_requirements(base_state)
        loaded = read_requirements("demo-app")
        assert loaded["project"]["slug"] == "demo-app"
        assert len(loaded["requirements"]) == 3

    def test_read_nonexistent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(requirements_writer, "OUTPUT_DIR", tmp_path)
        assert read_requirements("nope") is None

    def test_atomic_write_no_temp_files_left(self, base_state, tmp_path, monkeypatch):
        monkeypatch.setattr(requirements_writer, "OUTPUT_DIR", tmp_path)
        write_requirements(base_state)
        specs_dir = tmp_path / "demo-app" / "specs"
        leftover = list(specs_dir.glob("*.tmp"))
        assert leftover == []

    def test_raises_on_missing_slug(self):
        with pytest.raises(ValueError):
            write_requirements({"project": {}})

    def test_output_is_valid_json(self, base_state, tmp_path, monkeypatch):
        monkeypatch.setattr(requirements_writer, "OUTPUT_DIR", tmp_path)
        path = write_requirements(base_state)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["requirements"][0]["id"] in {"REQ-001", "REQ-002"}
