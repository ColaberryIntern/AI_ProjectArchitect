"""Tests for the AI org structure builder."""

import pytest

from execution.advisory.org_builder import (
    build_org_structure,
    flatten_org_tree,
    get_org_stats,
    _fallback_org_structure,
)


def _sample_capability_map():
    return {
        "departments": [
            {
                "id": "operations",
                "name": "Operations",
                "capabilities": [
                    {"id": "ops_1", "name": "Workflow Automation"},
                    {"id": "ops_2", "name": "Resource Scheduling"},
                ],
            },
            {
                "id": "sales",
                "name": "Sales",
                "capabilities": [
                    {"id": "sales_1", "name": "Lead Qualification"},
                ],
            },
            {
                "id": "customer_support",
                "name": "Customer Support",
                "capabilities": [
                    {"id": "cs_1", "name": "Ticket Triage"},
                ],
            },
        ],
    }


class TestFallbackOrgStructure:
    def test_produces_nodes(self):
        nodes = _fallback_org_structure(_sample_capability_map())
        assert len(nodes) >= 4  # root + at least 1 per dept

    def test_has_root_node(self):
        nodes = _fallback_org_structure(_sample_capability_map())
        root_nodes = [n for n in nodes if n["parent_id"] is None]
        assert len(root_nodes) == 1
        assert root_nodes[0]["type"] == "executive"

    def test_all_nodes_have_required_fields(self):
        nodes = _fallback_org_structure(_sample_capability_map())
        for node in nodes:
            assert "id" in node
            assert "title" in node
            assert "type" in node
            assert node["type"] in ("executive", "department_head", "specialist", "agent")
            assert "parent_id" in node
            assert "department" in node
            assert "responsibilities" in node
            assert isinstance(node["responsibilities"], list)
            assert "estimated_fte_equivalent" in node

    def test_department_heads_report_to_root(self):
        nodes = _fallback_org_structure(_sample_capability_map())
        root = [n for n in nodes if n["parent_id"] is None][0]
        dept_heads = [n for n in nodes if n["type"] == "department_head"]
        for head in dept_heads:
            assert head["parent_id"] == root["id"]

    def test_specialists_report_to_dept_heads(self):
        nodes = _fallback_org_structure(_sample_capability_map())
        dept_head_ids = {n["id"] for n in nodes if n["type"] == "department_head"}
        specialists = [n for n in nodes if n["type"] in ("specialist", "agent")]
        for spec in specialists:
            assert spec["parent_id"] in dept_head_ids

    def test_node_ids_are_unique(self):
        nodes = _fallback_org_structure(_sample_capability_map())
        ids = [n["id"] for n in nodes]
        assert len(ids) == len(set(ids))


class TestBuildOrgStructure:
    def test_uses_fallback_when_llm_unavailable(self, mocker):
        mocker.patch(
            "execution.advisory.org_builder._llm_build_org",
            side_effect=Exception("LLM unavailable"),
        )
        nodes = build_org_structure(_sample_capability_map(), business_idea="test")
        assert len(nodes) >= 4

    def test_uses_llm_when_available(self, mocker):
        mock_nodes = [
            {"id": "1", "title": "AI COO", "type": "executive", "parent_id": None,
             "department": "Executive", "responsibilities": ["Lead"], "ai_tools": [], "estimated_fte_equivalent": 1.0},
        ]
        mocker.patch(
            "execution.advisory.org_builder._llm_build_org",
            return_value=mock_nodes,
        )
        nodes = build_org_structure(_sample_capability_map(), business_idea="test")
        assert nodes == mock_nodes


class TestFlattenOrgTree:
    def test_builds_nested_tree(self):
        nodes = [
            {"id": "root", "title": "AI COO", "type": "executive", "parent_id": None, "department": "Exec"},
            {"id": "ops", "title": "AI Ops", "type": "department_head", "parent_id": "root", "department": "Ops"},
            {"id": "spec", "title": "AI Agent", "type": "specialist", "parent_id": "ops", "department": "Ops"},
        ]
        tree = flatten_org_tree(nodes)
        assert tree["id"] == "root"
        assert len(tree["children"]) == 1
        assert tree["children"][0]["id"] == "ops"
        assert len(tree["children"][0]["children"]) == 1

    def test_handles_empty_list(self):
        tree = flatten_org_tree([])
        assert tree["id"] == "root"
        assert tree["children"] == []


class TestGetOrgStats:
    def test_returns_summary(self):
        nodes = _fallback_org_structure(_sample_capability_map())
        stats = get_org_stats(nodes)
        assert stats["total_roles"] == len(nodes)
        assert stats["total_fte_equivalent"] > 0
        assert stats["departments"] >= 3
        assert "role_type_breakdown" in stats
