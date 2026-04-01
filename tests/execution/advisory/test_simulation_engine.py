"""Tests for the AI simulation engine."""

from execution.advisory.simulation_engine import run_simulation


def _make_session(cap_count=5, include_cory=True):
    """Build a test session with agents."""
    from execution.advisory.agent_generator import generate_agents
    caps = [
        "auto_lead_scoring", "ai_chat_support", "workflow_automation",
        "invoice_processing", "content_generation",
        "email_drafting", "data_pipeline_automation",
    ][:cap_count]
    agents = generate_agents(caps, include_cory=include_cory)
    return {
        "session_id": "test",
        "agents": agents,
        "selected_capabilities": caps,
        "selected_outcomes": ["increase_revenue", "reduce_costs"],
        "selected_ai_systems": ["revenue_engine", "operations_engine"],
        "business_idea": "Logistics company",
        "answers": [],
    }


class TestRunSimulation:
    def test_returns_required_fields(self):
        result = run_simulation(_make_session())
        assert "events" in result
        assert "summary" in result
        assert "total_events" in result
        assert result["total_events"] > 0

    def test_events_have_required_fields(self):
        result = run_simulation(_make_session())
        for event in result["events"]:
            assert "timestamp" in event
            assert "agent" in event
            assert "event" in event
            assert "action" in event
            assert "impact" in event
            assert "type" in event

    def test_generates_multiple_events(self):
        result = run_simulation(_make_session(cap_count=5))
        assert result["total_events"] >= 5

    def test_caps_at_25_events(self):
        result = run_simulation(_make_session(cap_count=7))
        assert result["total_events"] <= 25

    def test_includes_cory_events_when_present(self):
        result = run_simulation(_make_session(include_cory=True))
        cory = [e for e in result["events"] if e["type"] == "cory_intelligence"]
        assert len(cory) >= 1

    def test_no_cory_when_excluded(self):
        result = run_simulation(_make_session(include_cory=False))
        cory = [e for e in result["events"] if e["type"] == "cory_intelligence"]
        assert len(cory) == 0

    def test_events_sorted_by_time(self):
        result = run_simulation(_make_session())
        offsets = [e["time_offset"] for e in result["events"]]
        assert offsets == sorted(offsets)

    def test_summary_has_highlights(self):
        result = run_simulation(_make_session())
        assert "highlights" in result["summary"]
        assert len(result["summary"]["highlights"]) >= 2

    def test_no_template_placeholders_in_output(self):
        result = run_simulation(_make_session())
        for event in result["events"]:
            for field in ["event", "action", "impact"]:
                assert "{" not in event[field], f"Unfilled template in {field}: {event[field]}"

    def test_empty_session_no_crash(self):
        result = run_simulation({"agents": [], "selected_capabilities": []})
        assert result["total_events"] == 0
