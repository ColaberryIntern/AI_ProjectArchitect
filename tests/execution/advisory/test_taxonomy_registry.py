"""Tests for the business taxonomy registry.

Covers:
    - Deterministic slugification
    - Seeded profile hit (no LLM call)
    - Registry cache hit on second lookup (LLM called once)
    - LLM generation path + persistence
    - Schema validation rejects malformed LLM output
"""

import json
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def registry_dir(monkeypatch, tmp_path):
    """Redirect ADVISORY_OUTPUT_DIR so the registry writes into tmp."""
    import config.settings as settings
    import execution.advisory.advisory_state_manager as asm
    import execution.advisory.taxonomy_registry as reg

    advisory_dir = tmp_path / "advisory"
    advisory_dir.mkdir()
    monkeypatch.setattr(settings, "ADVISORY_OUTPUT_DIR", advisory_dir)
    monkeypatch.setattr(asm, "ADVISORY_OUTPUT_DIR", advisory_dir)
    monkeypatch.setattr(reg, "ADVISORY_OUTPUT_DIR", advisory_dir)
    return advisory_dir


def _fake_llm_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.content = json.dumps(payload)
    return resp


VALID_TAXONOMY_PAYLOAD = {
    "industry_key": "boutique-wine-importer",
    "label": "Boutique Wine Importer",
    "aliases": ["wine importer", "fine wine distribution", "beverage importer"],
    "dept_structure": {
        "operations": {"pct_of_headcount": 0.35, "avg_fte_cost": 55_000},
        "sales": {"pct_of_headcount": 0.30, "avg_fte_cost": 72_000},
        "compliance": {"pct_of_headcount": 0.10, "avg_fte_cost": 82_000},
        "finance": {"pct_of_headcount": 0.10, "avg_fte_cost": 78_000},
        "management": {"pct_of_headcount": 0.15, "avg_fte_cost": 125_000},
    },
    "revenue_per_employee": 310_000,
    "avg_margin": 0.12,
    "revenue_lift_by_dept": {"sales": 0.08, "operations": 0.04},
    "ai_adoption_rate": 0.08,
    "pain_catalog": [
        {
            "id": "allocation_mismatch",
            "label": "Allocation Mismatch",
            "root_cause": "Manual allocation of scarce wines to accounts based on relationship memory",
            "financial_formula": "lost_allocations * avg_bottle_margin",
            "typical_impact_pct": 0.05,
        },
        {
            "id": "ttb_compliance_lag",
            "label": "TTB Compliance Lag",
            "root_cause": "Hand-built state-by-state shipping rule checks",
            "financial_formula": "compliance_hours * hourly_rate * 52",
            "typical_impact_pct": 0.03,
        },
    ],
    "system_names": {
        "operations": "Allocation & Logistics Engine",
        "sales": "Sommelier Pipeline Intelligence",
        "compliance": "Beverage Compliance Engine",
    },
    "agent_roles": {
        "operations": [
            {"name": "AI Allocation Planner", "role": "Matches scarce SKUs to accounts by sell-through history"},
        ],
        "compliance": [
            {"name": "AI TTB Rule Checker", "role": "Validates shipments against state-specific beverage laws"},
        ],
    },
}


class TestSlugify:
    def test_basic_slug(self):
        from execution.advisory.taxonomy_registry import _slugify
        assert _slugify("Electric Cooperative") == "electric-cooperative"

    def test_collapses_non_alphanumerics(self):
        from execution.advisory.taxonomy_registry import _slugify
        assert _slugify("3PL / Freight & Logistics!") == "3pl-freight-logistics"

    def test_deterministic(self):
        from execution.advisory.taxonomy_registry import _slugify
        assert _slugify("boutique wine") == _slugify("Boutique  Wine")

    def test_empty_fallback(self):
        from execution.advisory.taxonomy_registry import _slugify
        assert _slugify("") == "unknown"


class TestSeededHit:
    def test_strong_alias_match_returns_seed_no_llm(self, registry_dir):
        from execution.advisory.taxonomy_registry import lookup_taxonomy

        with patch("execution.advisory.taxonomy_registry._generate_taxonomy") as gen:
            tax = lookup_taxonomy("We are a regional freight trucking company")

        gen.assert_not_called()
        assert tax["_meta"]["source"] == "seed"
        assert tax["_meta"]["industry_key"] == "logistics"
        assert "system_names" in tax


class TestGenerationAndCache:
    def test_miss_triggers_sync_generation_and_persists(self, registry_dir):
        from execution.advisory.taxonomy_registry import lookup_taxonomy

        novel_desc = "Niche natural wine import brand focusing on biodynamic growers"

        with patch("execution.llm_client.chat", return_value=_fake_llm_response(VALID_TAXONOMY_PAYLOAD)) as chat_mock:
            tax = lookup_taxonomy(novel_desc, "allocation challenges, TTB compliance")

        assert chat_mock.call_count == 1
        assert tax["_meta"]["source"] == "generated"
        assert tax["_meta"]["industry_key"] == "boutique-wine-importer"
        assert tax["system_names"]["operations"] == "Allocation & Logistics Engine"

        saved = registry_dir / "taxonomies" / "boutique-wine-importer.json"
        assert saved.exists()
        index = json.loads((registry_dir / "taxonomies" / "_index.json").read_text())
        assert index["boutique-wine-importer"] == "boutique-wine-importer"
        assert index["wine-importer"] == "boutique-wine-importer"

    def test_second_lookup_hits_registry_no_llm(self, registry_dir):
        from execution.advisory.taxonomy_registry import lookup_taxonomy

        novel_desc = "Niche natural wine import brand focusing on biodynamic growers"
        with patch("execution.llm_client.chat", return_value=_fake_llm_response(VALID_TAXONOMY_PAYLOAD)):
            lookup_taxonomy(novel_desc, "")

        with patch("execution.advisory.taxonomy_registry._generate_taxonomy") as gen:
            tax = lookup_taxonomy("Our business is a fine wine importer", "")

        gen.assert_not_called()
        assert tax["_meta"]["source"] == "registry"


class TestValidation:
    def test_rejects_missing_required_field(self, registry_dir):
        from execution.advisory.taxonomy_registry import lookup_taxonomy

        bad = dict(VALID_TAXONOMY_PAYLOAD)
        del bad["pain_catalog"]

        with patch("execution.llm_client.chat", return_value=_fake_llm_response(bad)):
            with pytest.raises(ValueError, match="pain_catalog"):
                lookup_taxonomy("Niche artisan leather atelier", "")

    def test_rejects_empty_aliases(self, registry_dir):
        from execution.advisory.taxonomy_registry import lookup_taxonomy

        bad = dict(VALID_TAXONOMY_PAYLOAD)
        bad["aliases"] = []

        with patch("execution.llm_client.chat", return_value=_fake_llm_response(bad)):
            with pytest.raises(ValueError, match="aliases"):
                lookup_taxonomy("Niche bespoke perfumery house", "")


class TestRecommendationIntegration:
    def test_recommend_design_uses_industry_system_names_on_seed_hit(self, registry_dir):
        from execution.advisory.recommendation_engine import recommend_design

        session = {
            "business_idea": "Regional freight trucking and last-mile distribution",
            "answers": [
                {"answer_text": "We run a freight and trucking operation with manual dispatch and route waste"},
                {"answer_text": "Biggest cost is empty miles and overhead"},
            ],
        }
        recs = recommend_design(session)

        assert recs.get("industry_source") == "seed"
        assert recs.get("industry_key") == "logistics"
        # Industry-specific system name appears in at least one system rationale.
        assert any("Fleet" in label or "Freight" in label or "Logistics" in label
                   for label in recs["recommended_systems"].values()), recs["recommended_systems"]
