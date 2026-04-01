"""Tests for the agent architecture generator."""

from execution.advisory.agent_generator import generate_agents, get_agent_stats


class TestGenerateAgents:
    def test_produces_agents_for_capabilities(self):
        agents = generate_agents(["auto_lead_scoring", "ai_chat_support", "workflow_automation"])
        assert len(agents) == 3

    def test_agents_have_required_fields(self):
        agents = generate_agents(["auto_lead_scoring"])
        a = agents[0]
        assert "id" in a
        assert "name" in a
        assert "department" in a
        assert "role" in a
        assert "trigger_type" in a
        assert "trigger" in a
        assert "inputs" in a
        assert "outputs" in a
        assert "connected_mcp_servers" in a
        assert "connected_skills" in a

    def test_trigger_types_are_valid(self):
        agents = generate_agents([
            "auto_lead_scoring",     # event
            "campaign_optimization",  # time
            "inventory_optimization",  # threshold
        ])
        types = {a["trigger_type"] for a in agents}
        assert types == {"event", "time", "threshold"}

    def test_cory_included_when_requested(self):
        agents = generate_agents(["auto_lead_scoring"], include_cory=True)
        cory = [a for a in agents if a.get("is_cory")]
        assert len(cory) == 1
        assert "AI COO" in cory[0]["name"]

    def test_cory_not_included_by_default(self):
        agents = generate_agents(["auto_lead_scoring"])
        cory = [a for a in agents if a.get("is_cory")]
        assert len(cory) == 0

    def test_cory_monitors_departments(self):
        agents = generate_agents(
            ["auto_lead_scoring", "ai_chat_support", "workflow_automation"],
            include_cory=True,
        )
        cory = next(a for a in agents if a.get("is_cory"))
        assert "Sales" in cory["monitors"]
        assert "Customer Support" in cory["monitors"]

    def test_empty_capabilities(self):
        agents = generate_agents([])
        assert agents == []


class TestGetAgentStats:
    def test_returns_summary(self):
        agents = generate_agents(["auto_lead_scoring", "ai_chat_support", "workflow_automation"])
        stats = get_agent_stats(agents)
        assert stats["total_agents"] == 3
        assert stats["departments"] >= 2
        assert "trigger_breakdown" in stats

    def test_cory_detection(self):
        agents = generate_agents(["auto_lead_scoring"], include_cory=True)
        stats = get_agent_stats(agents)
        assert stats["has_cory"] is True

        agents_no_cory = generate_agents(["auto_lead_scoring"])
        stats_no = get_agent_stats(agents_no_cory)
        assert stats_no["has_cory"] is False
