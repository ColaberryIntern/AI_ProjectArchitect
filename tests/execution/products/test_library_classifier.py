"""Classifier table tests — fix every misclassification we've seen
in real catalog data.
"""

import pytest

from execution.products.library.classifier import (
    KNOWN_CATEGORIES,
    classify,
    classify_many,
)


# (input dict, expected category) — covers the precedence rules
CASES = [
    # 1 — Explicit kind wins
    ({"kind": "agent", "name": "x"}, "agents"),
    ({"kind": "Prompts", "name": "x"}, "prompts"),
    ({"kind": "workflow", "name": "x"}, "workflows"),

    # 2 — Existing category field
    ({"name": "MCP Filesystem Server", "category": "MCP Servers"}, "mcp"),
    ({"name": "x", "category": "MCP Servers — 🌎 Community Servers"}, "mcp"),
    ({"name": "x", "category": "Skills"}, "skills"),
    ({"name": "x", "category": "AI Agents"}, "agents"),
    ({"name": "x", "category": "Prompt Templates"}, "prompts"),
    ({"name": "x", "category": "Blueprints"}, "templates"),
    ({"name": "x", "category": "Recovery Playbook"}, "recovery"),
    ({"name": "x", "category": "Chaos Drill"}, "chaos"),

    # 3 — Source URL hints
    ({"name": "Whatever", "source_url": "https://modelcontextprotocol.io/server/xyz"}, "mcp"),
    ({"name": "Whatever", "source_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/x"}, "mcp"),
    ({"name": "Anything", "source_url": "https://github.com/org/repo/prompts/sales.md"}, "prompts"),
    ({"name": "Anything", "source_url": "https://github.com/org/repo/agents/coder.md"}, "agents"),
    ({"name": "Anything", "source_url": "https://github.com/org/repo/workflows/deploy.yaml"}, "workflows"),

    # 4 — Name patterns
    ({"name": "MCP Slack Server"}, "mcp"),
    ({"name": "RFP Analysis Agent"}, "agents"),
    ({"name": "Code-review prompt"}, "prompts"),
    ({"name": "Client Onboarding Workflow"}, "workflows"),
    ({"name": "Sales blueprint"}, "templates"),

    # 5 — Tags
    ({"name": "thing", "tags": ["mcp", "filesystem"]}, "mcp"),
    ({"name": "thing", "tags": ["agent"]}, "agents"),
    ({"name": "thing", "tags": ["prompt"]}, "prompts"),

    # 6 — Manifest shape
    ({"name": "thing", "mcp_servers": ["a", "b"]}, "mcp"),
    ({"name": "thing", "agent": {"persona": "x"}}, "agents"),
    ({"name": "thing", "prompt_template": "..."}, "prompts"),

    # 7 — Fallback
    ({"name": "totally novel asset"}, "skills"),  # default; low confidence
]


@pytest.mark.parametrize("item,expected", CASES,
                                ids=[f"{i}_{c['name']}_to_{exp}"[:60]
                                          for i, (c, exp) in enumerate(CASES)])
def test_classify_routes_to_expected_category(item, expected):
    result = classify(item)
    assert result.category == expected, (
        f"Expected '{expected}' but got '{result.category}'.\n"
        f"Item: {item}\n"
        f"Reasons: {result.reasons}"
    )


def test_classify_returns_high_confidence_for_explicit_category():
    r = classify({"name": "x", "category": "MCP Servers"})
    assert r.confidence >= 0.9


def test_classify_returns_low_confidence_for_fallback():
    r = classify({"name": "totally novel asset"})
    assert r.confidence <= 0.5


def test_classify_reasons_are_populated():
    r = classify({"name": "MCP Slack Server"})
    assert r.reasons
    assert any("MCP" in s for s in r.reasons)


def test_classify_many_buckets_correctly():
    items = [
        {"name": "MCP X", "category": "MCP Servers"},
        {"name": "MCP Y", "category": "MCP Servers"},
        {"name": "Sales Agent", "category": "AI Agents"},
        {"name": "Code prompt", "category": "Prompts"},
        {"name": "Unknown"},
    ]
    buckets = classify_many(items)
    assert len(buckets["mcp"]) == 2
    assert len(buckets["agents"]) == 1
    assert len(buckets["prompts"]) == 1
    assert len(buckets["skills"]) == 1  # the unknown one
    # Every bucket key is a valid category
    assert set(buckets) <= KNOWN_CATEGORIES | {"skills"}


def test_classify_catalog_splits_into_distinct_buckets():
    """Production smoke: the real skill_catalog should split across
    multiple categories, NOT all land in skills.

    Catalog composition (observed): ~72% MCP, ~3% agents (agent frameworks),
    and the rest split across various skill / tool buckets.
    """
    try:
        from execution.skill_catalog import load_registry
    except Exception:
        pytest.skip("skill_catalog not importable")
    raw = load_registry() or []
    if isinstance(raw, dict):
        raw = raw.get("skills") or []
    if not raw:
        pytest.skip("skill_catalog is empty")
    buckets = classify_many(list(raw))
    total = sum(len(v) for v in buckets.values())
    # MCP should be the largest bucket (60%+) — was 100% in skills before
    assert len(buckets["mcp"]) / total >= 0.6
    # Agents should pick up at least the "AI Agent Frameworks" items
    assert len(buckets["agents"]) >= 10
    # Skills should NOT be the dominant bucket (was 100% before classifier)
    assert len(buckets["skills"]) / total < 0.5
