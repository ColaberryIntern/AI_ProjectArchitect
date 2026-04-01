"""Tests for demo configuration generator."""

from execution.demo.demo_generator import generate_demo_config


def _sample_state(**overrides):
    """Build a project state with advisory metadata."""
    state = {
        "project": {"name": "Swift Delivery - AI Ops", "slug": "swift-delivery-ai-ops", "created_at": "2026-01-01", "updated_at": "2026-01-01"},
        "current_phase": "idea_intake",
        "idea": {"original_raw": "We are a logistics company with route planning bottlenecks", "captured_at": None},
        "features": {"core": [
            {"id": "route_opt", "name": "Route Optimization", "description": "Optimize delivery routes"},
            {"id": "dispatch", "name": "Auto Dispatch", "description": "Automatically assign drivers"},
        ], "optional": [], "approved": False, "catalog": []},
        "advisory": {
            "source": "advisory",
            "advisory_session_id": "test-session",
            "company_name": "Swift Delivery Co",
            "contact_name": "Jane",
            "contact_email": "jane@swift.com",
            "role": "VP Ops",
            "industry": "Logistics",
            "selected_capabilities": ["route_optimization", "auto_dispatch", "ticket_auto_triage"],
            "selected_outcomes": ["reduce_costs", "scale_operations"],
        },
    }
    state.update(overrides)
    return state


class TestGenerateDemoConfig:
    def test_returns_all_sections(self):
        config = generate_demo_config(_sample_state())
        assert "departments" in config
        assert "agents" in config
        assert "flows" in config
        assert "scenarios" in config
        assert "metrics" in config
        assert "graph" in config

    def test_departments_detected_from_capabilities(self):
        config = generate_demo_config(_sample_state())
        dept_names = [d["name"] for d in config["departments"]]
        # Route + dispatch → Operations/Logistics; ticket → Customer Support
        assert any(d in dept_names for d in ["Operations", "Logistics"])

    def test_always_has_control_tower(self):
        config = generate_demo_config(_sample_state())
        agent_names = [a["name"] for a in config["agents"]]
        assert "AI Control Tower" in agent_names

    def test_agents_have_required_fields(self):
        config = generate_demo_config(_sample_state())
        for agent in config["agents"]:
            assert "id" in agent
            assert "name" in agent
            assert "role" in agent
            assert "department" in agent

    def test_scenarios_not_empty(self):
        config = generate_demo_config(_sample_state())
        assert len(config["scenarios"]) >= 1
        for s in config["scenarios"]:
            assert "name" in s
            assert "events" in s
            assert len(s["events"]) >= 1

    def test_graph_has_nodes_and_links(self):
        config = generate_demo_config(_sample_state())
        assert len(config["graph"]["nodes"]) >= 2
        assert len(config["graph"]["links"]) >= 1

    def test_deterministic_output(self):
        """Same input produces same output (seeded RNG)."""
        c1 = generate_demo_config(_sample_state())
        c2 = generate_demo_config(_sample_state())
        assert c1["agents"] == c2["agents"]
        assert c1["metrics"] == c2["metrics"]

    def test_handles_no_advisory(self):
        state = _sample_state(advisory=None)
        config = generate_demo_config(state)
        assert len(config["departments"]) >= 1
        assert len(config["agents"]) >= 2

    def test_flows_generated(self):
        config = generate_demo_config(_sample_state())
        assert len(config["flows"]) >= 1
        for flow in config["flows"]:
            assert "name" in flow
            assert "steps" in flow
            assert len(flow["steps"]) >= 2

    def test_metrics_have_required_fields(self):
        config = generate_demo_config(_sample_state())
        m = config["metrics"]
        assert "revenue_impact" in m
        assert "cost_savings" in m
        assert "efficiency_gain" in m
        assert "active_agents" in m
