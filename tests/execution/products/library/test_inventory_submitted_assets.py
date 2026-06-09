"""Tests for execution/products/library/inventory.py merge of submitted assets.

Covers _load_submitted_assets and the load_category merge path that surfaces
auto-approved colaberry_propose_asset writes (which land as AssetMetadata
JSON files under output/library/<ws>/<category>/) in the same list the UI and
colaberry_list_assets read. Pre-patch these were invisible because the
per-category LOADERs only read from the legacy registries.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.products.library import inventory


def _write_asset(ws_dir: Path, category: str, asset_id: str, **fields) -> Path:
    d = ws_dir / category
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{asset_id}.json"
    payload = {
        "asset_id": asset_id,
        "category": category,
        "workspace": ws_dir.name,
        "name": fields.get("name", asset_id),
        "description": fields.get("description", "test"),
        "owner": "test@example.com",
        "tags": fields.get("tags", []),
        "source": fields.get("source", "user-submitted"),
        "owning_company_id": fields.get("owning_company_id", "colaberry"),
        "vetted": fields.get("vetted", True),
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    # Redirect inventory.ROOT so output/library/* resolves under tmp_path.
    monkeypatch.setattr(inventory, "ROOT", tmp_path)
    (tmp_path / "output" / "library" / "global").mkdir(parents=True)
    return tmp_path


class TestLoadSubmittedAssets:

    def test_empty_when_lib_root_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(inventory, "ROOT", tmp_path / "nonexistent")
        assert inventory._load_submitted_assets("skills") == []

    def test_empty_when_no_category_dir(self, fake_root):
        assert inventory._load_submitted_assets("skills") == []

    def test_reads_one_asset(self, fake_root):
        _write_asset(fake_root / "output" / "library" / "global",
                                "skills", "sub-abc123",
                                name="build-asset-catalog",
                                description="Meta-skill",
                                tags=["catalog", "colaberry"])
        rows = inventory._load_submitted_assets("skills")
        assert len(rows) == 1
        r = rows[0]
        assert r["name"] == "build-asset-catalog"
        assert r["id"] == "sub-abc123"
        assert r["owning_company_id"] == "colaberry"
        assert "colaberry" in r["tags"]

    def test_walks_multiple_workspaces(self, fake_root):
        ws_global = fake_root / "output" / "library" / "global"
        ws_acme = fake_root / "output" / "library" / "acme"
        ws_acme.mkdir(parents=True)
        _write_asset(ws_global, "agents", "sub-aaa", name="alpha")
        _write_asset(ws_acme, "agents", "sub-bbb", name="bravo")
        names = {r["name"] for r in inventory._load_submitted_assets("agents")}
        assert names == {"alpha", "bravo"}

    def test_skips_underscore_dirs(self, fake_root):
        # _submissions/ holds Submission JSON not AssetMetadata — must not leak in.
        subs = fake_root / "output" / "library" / "_submissions"
        subs.mkdir(parents=True)
        (subs / "raw-submission.json").write_text(
            json.dumps({"submission_id": "x", "status": "pending"}),
            encoding="utf-8",
        )
        assert inventory._load_submitted_assets("skills") == []

    def test_bad_json_does_not_crash(self, fake_root):
        d = fake_root / "output" / "library" / "global" / "skills"
        d.mkdir(parents=True)
        (d / "broken.json").write_text("{not valid json", encoding="utf-8")
        _write_asset(fake_root / "output" / "library" / "global",
                                "skills", "sub-ok", name="good")
        rows = inventory._load_submitted_assets("skills")
        assert [r["name"] for r in rows] == ["good"]

    def test_defaults_owning_company_to_community(self, fake_root):
        d = fake_root / "output" / "library" / "global" / "skills"
        d.mkdir(parents=True)
        (d / "no-owner.json").write_text(
            json.dumps({"asset_id": "sub-zzz", "category": "skills",
                              "workspace": "global", "name": "orphan",
                              "description": "no owning_company_id"}),
            encoding="utf-8",
        )
        rows = inventory._load_submitted_assets("skills")
        assert rows[0]["owning_company_id"] == "community"


class TestLoadCategoryMerge:

    def test_appends_submitted_to_legacy_result(self, fake_root, monkeypatch):
        monkeypatch.setitem(
            inventory.LOADERS, "chaos",
            lambda: [{"name": "legacy-drill", "kind": "chaos_drill",
                            "version": "1.0", "owner": "platform",
                            "description": "", "tags": [], "source": "chaos_engine"}],
        )
        _write_asset(fake_root / "output" / "library" / "global",
                                "chaos", "sub-chaos1",
                                name="rollback-simulation-engine")
        names = [r["name"] for r in inventory.load_category("chaos")]
        assert names == ["legacy-drill", "rollback-simulation-engine"]

    def test_dedupes_by_name(self, fake_root, monkeypatch):
        # Legacy registry + a submitted asset with the same name → only one row.
        monkeypatch.setitem(
            inventory.LOADERS, "skills",
            lambda: [{"name": "build-asset-catalog", "kind": "skill",
                            "version": "1.0", "owner": "platform",
                            "description": "from registry", "tags": [],
                            "source": "skill_catalog"}],
        )
        _write_asset(fake_root / "output" / "library" / "global",
                                "skills", "sub-dup",
                                name="build-asset-catalog",
                                description="from submission")
        rows = inventory.load_category("skills")
        assert len(rows) == 1
        # The registry row wins (came first in the merge order).
        assert rows[0]["description"] == "from registry"

    def test_returns_base_when_no_submissions(self, fake_root, monkeypatch):
        monkeypatch.setitem(
            inventory.LOADERS, "policies",
            lambda: [{"name": "legacy-policy", "kind": "policy",
                            "version": "1.0", "owner": "platform",
                            "description": "", "tags": [], "source": "config/policies"}],
        )
        rows = inventory.load_category("policies")
        assert [r["name"] for r in rows] == ["legacy-policy"]

    def test_unknown_category_returns_empty(self, fake_root):
        assert inventory.load_category("totally-bogus-category") == []


class TestFilterForCompany:
    """filter_for_company must work for rows produced by _load_submitted_assets.

    Pre-patch the filter looked up tenancy by row['name'] only, but
    propose_asset writes asset files keyed by row['id'] (= "sub-XXXX") so
    every submitted row was dropped at the tenancy check. Verify the
    filter now (a) trusts the row's own owning_company_id when present
    and (b) falls back to id-first lookup for legacy rows.
    """

    def test_trusts_row_owning_company_id(self, fake_root):
        # Row carries its own owning_company_id (set by _load_submitted_assets).
        # Filter must NOT need a metadata store lookup to keep this row.
        row = {"name": "build-asset-catalog", "id": "sub-abc123",
                  "owning_company_id": "colaberry", "tags": [], "kind": "skill",
                  "version": "1.0", "owner": "—", "description": "", "source": ""}
        out = inventory.filter_for_company([row], "skills", "colaberry")
        assert len(out) == 1
        assert out[0]["name"] == "build-asset-catalog"

    def test_drops_when_owning_company_mismatches(self, fake_root):
        row = {"name": "x", "id": "sub-zzz", "owning_company_id": "other-co",
                  "tags": [], "kind": "skill", "version": "1.0", "owner": "—",
                  "description": "", "source": ""}
        # No tenancy approval row, no shared visibility — should be dropped.
        out = inventory.filter_for_company([row], "skills", "colaberry")
        assert out == []

    def test_legacy_row_without_id_still_works(self, fake_root, monkeypatch):
        # Legacy registry row has name only; the filter falls back to a
        # metadata lookup via store.get_metadata. Stub it to return a
        # matching owner so the legacy code path stays functional.
        from execution.products.library import store as store_mod

        class _FakeMeta:
            owning_company_id = "colaberry"

        monkeypatch.setattr(store_mod, "get_metadata",
                                  lambda ws, cat, aid: _FakeMeta())
        row = {"name": "legacy-skill", "tags": [], "kind": "skill",
                  "version": "1.0", "owner": "—", "description": "", "source": ""}
        out = inventory.filter_for_company([row], "skills", "colaberry")
        assert len(out) == 1
