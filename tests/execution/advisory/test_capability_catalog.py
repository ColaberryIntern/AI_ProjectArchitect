"""Tests for the business capability catalog."""

from execution.advisory.capability_catalog import (
    CAPABILITY_CATALOG,
    DEPARTMENTS,
    TOTAL_CAPABILITIES,
    get_ai_mappings_for_selection,
    get_all_capabilities,
    get_capabilities_by_department,
    get_capabilities_by_ids,
    get_department_meta,
)


class TestCatalogData:
    def test_has_substantial_catalog(self):
        assert TOTAL_CAPABILITIES >= 40

    def test_all_capabilities_have_required_fields(self):
        for cap in CAPABILITY_CATALOG:
            assert "id" in cap
            assert "name" in cap
            assert "description" in cap
            assert "department" in cap
            assert "category" in cap
            assert "agents" in cap
            assert "mcp_servers" in cap
            assert "skills" in cap

    def test_capability_ids_are_unique(self):
        ids = [c["id"] for c in CAPABILITY_CATALOG]
        assert len(ids) == len(set(ids))

    def test_all_departments_have_capabilities(self):
        by_dept = get_capabilities_by_department()
        for dept in DEPARTMENTS:
            assert dept["id"] in by_dept, f"No capabilities for {dept['id']}"
            assert len(by_dept[dept["id"]]) >= 3

    def test_agents_are_non_empty(self):
        for cap in CAPABILITY_CATALOG:
            assert len(cap["agents"]) >= 1, f"{cap['id']} has no agents"

    def test_skills_are_non_empty(self):
        for cap in CAPABILITY_CATALOG:
            assert len(cap["skills"]) >= 1, f"{cap['id']} has no skills"


class TestGetFunctions:
    def test_get_all(self):
        assert len(get_all_capabilities()) == TOTAL_CAPABILITIES

    def test_by_department(self):
        by_dept = get_capabilities_by_department()
        assert "Sales" in by_dept
        assert "Operations" in by_dept
        total = sum(len(caps) for caps in by_dept.values())
        assert total == TOTAL_CAPABILITIES

    def test_by_ids(self):
        caps = get_capabilities_by_ids(["auto_lead_scoring", "ai_chat_support"])
        assert len(caps) == 2
        names = {c["name"] for c in caps}
        assert "Automated Lead Scoring" in names

    def test_by_ids_empty(self):
        assert get_capabilities_by_ids([]) == []

    def test_by_ids_nonexistent(self):
        assert get_capabilities_by_ids(["nonexistent"]) == []

    def test_department_meta(self):
        meta = get_department_meta()
        assert len(meta) == len(DEPARTMENTS)
        for dept in meta:
            assert "id" in dept
            assert "icon" in dept
            assert "count" in dept
            assert "capabilities" in dept


class TestAIMappings:
    def test_mapping_aggregation(self):
        result = get_ai_mappings_for_selection([
            "auto_lead_scoring",
            "ai_chat_support",
            "workflow_automation",
        ])
        assert "departments" in result
        assert "all_agents" in result
        assert "all_mcp_servers" in result
        assert "all_skills" in result
        assert result["total_selected"] == 3

        # Should have Sales, Customer Support, Operations departments
        dept_names = {d["name"] for d in result["departments"]}
        assert "Sales" in dept_names
        assert "Customer Support" in dept_names
        assert "Operations" in dept_names

    def test_agents_are_aggregated(self):
        result = get_ai_mappings_for_selection(["auto_lead_scoring", "deal_intelligence"])
        assert "AI Lead Qualifier" in result["all_agents"]
        assert "AI Deal Advisor" in result["all_agents"]

    def test_mcp_servers_are_deduplicated(self):
        result = get_ai_mappings_for_selection([
            "auto_lead_scoring",  # uses mcp_slack
            "outreach_automation",  # also uses mcp_slack
        ])
        # mcp_slack should appear only once
        assert result["all_mcp_servers"].count("mcp_slack") == 1

    def test_empty_selection(self):
        result = get_ai_mappings_for_selection([])
        assert result["departments"] == []
        assert result["total_selected"] == 0

    def test_departments_have_recommended_fields(self):
        result = get_ai_mappings_for_selection(["auto_lead_scoring"])
        dept = result["departments"][0]
        assert "recommended_agents" in dept
        assert "recommended_mcp_servers" in dept
        assert "recommended_skills" in dept
        assert len(dept["recommended_agents"]) >= 1
