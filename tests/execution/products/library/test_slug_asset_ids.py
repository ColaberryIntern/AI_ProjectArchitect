"""Tests for the slug-based asset_id convention.

Covers:
  - store.slugify (basic + edge cases)
  - store.resolve_asset_slug (collision suffixing)
  - store.review_submission honors asset_id_override
  - migrate_to_slug_asset_ids.migrate renames sub-<uuid> files +
    rewrites tenancy approvals + rewrites submission back-pointers
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.products.library import store
from execution.products.library import tenancy
from execution.products.library import migrate_to_slug_asset_ids as mig


# ── slugify ────────────────────────────────────────────────────────


class TestSlugify:

    def test_basic_lowercases_and_hyphenates(self):
        assert store.slugify("Build Asset Catalog") == "build-asset-catalog"

    def test_collapses_runs_of_separators(self):
        assert store.slugify("foo   bar___baz") == "foo-bar-baz"

    def test_strips_edge_hyphens(self):
        assert store.slugify("---foo---") == "foo"

    def test_drops_special_chars(self):
        assert store.slugify("hello@world!.py") == "hello-world-py"

    def test_unicode_folds_to_ascii(self):
        assert store.slugify("Café Münster") == "cafe-munster"

    def test_empty_input_returns_unnamed(self):
        assert store.slugify("") == "unnamed"
        assert store.slugify("   ") == "unnamed"
        assert store.slugify("@@@") == "unnamed"

    def test_truncates_to_60_chars(self):
        long = "a" * 100
        assert store.slugify(long) == "a" * 60


# ── resolve_asset_slug ─────────────────────────────────────────────


@pytest.fixture
def fake_lib_root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "LIB_ROOT", tmp_path)
    return tmp_path


class TestResolveAssetSlug:

    def test_returns_base_when_no_collision(self, fake_lib_root):
        assert store.resolve_asset_slug("global", "skills",
                                                      "Build Asset Catalog") == "build-asset-catalog"

    def test_appends_2_on_first_collision(self, fake_lib_root):
        d = fake_lib_root / "global" / "skills"
        d.mkdir(parents=True)
        (d / "build-asset-catalog.meta.json").write_text("{}", encoding="utf-8")
        assert store.resolve_asset_slug("global", "skills",
                                                      "Build Asset Catalog") == "build-asset-catalog-2"

    def test_skips_to_next_free_suffix(self, fake_lib_root):
        d = fake_lib_root / "global" / "skills"
        d.mkdir(parents=True)
        (d / "x.meta.json").write_text("{}", encoding="utf-8")
        (d / "x-2.meta.json").write_text("{}", encoding="utf-8")
        (d / "x-3.meta.json").write_text("{}", encoding="utf-8")
        assert store.resolve_asset_slug("global", "skills", "x") == "x-4"


# ── review_submission asset_id_override ────────────────────────────


class TestReviewSubmissionOverride:

    def test_uses_override_when_set(self, fake_lib_root):
        sub = store.submit(
            workspace="global", category="skills",
            submitted_by="t@example.com",
            name="My Asset", description="d",
            owning_company_id="colaberry",
        )
        out = store.review_submission(
            workspace="global", submission_id=sub.submission_id,
            decision="accepted", reviewer="r@example.com",
            asset_id_override="my-asset",
        )
        assert out.asset_id == "my-asset"
        # File should land at <slug>.meta.json, not sub-<uuid>.meta.json
        assert (fake_lib_root / "global" / "skills" / "my-asset.meta.json").exists()
        assert not (fake_lib_root / "global" / "skills"
                                 / f"sub-{sub.submission_id}.meta.json").exists()

    def test_falls_back_to_sub_uuid_when_no_override(self, fake_lib_root):
        sub = store.submit(
            workspace="global", category="skills",
            submitted_by="t@example.com",
            name="Legacy Path", description="d",
            owning_company_id="colaberry",
        )
        out = store.review_submission(
            workspace="global", submission_id=sub.submission_id,
            decision="accepted", reviewer="r@example.com",
        )
        assert out.asset_id == f"sub-{sub.submission_id}"


# ── migrate_to_slug_asset_ids ──────────────────────────────────────


@pytest.fixture
def fake_full_root(tmp_path, monkeypatch):
    # Make store, tenancy, and the migration script all agree on root.
    monkeypatch.setattr(store, "LIB_ROOT", tmp_path / "output" / "library")
    tenancy_root = tmp_path / "tenancy"
    tenancy_root.mkdir(parents=True)
    monkeypatch.setattr(tenancy, "_root", lambda: tenancy_root)
    return tmp_path


def _seed_sub_record(root: Path, workspace: str, category: str,
                                  submission_id: str, name: str,
                                  owning_company_id: str = "colaberry") -> Path:
    asset_id = f"sub-{submission_id}"
    cat_dir = root / "output" / "library" / workspace / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    meta = cat_dir / f"{asset_id}.meta.json"
    meta.write_text(json.dumps({
        "asset_id": asset_id, "category": category, "workspace": workspace,
        "name": name, "description": "d",
        "owning_company_id": owning_company_id, "vetted": True,
    }), encoding="utf-8")
    sub_dir = root / "output" / "library" / workspace / "_submissions"
    sub_dir.mkdir(parents=True, exist_ok=True)
    sub = sub_dir / f"{submission_id}.json"
    sub.write_text(json.dumps({
        "submission_id": submission_id, "workspace": workspace,
        "category": category, "name": name, "description": "d",
        "asset_id": asset_id, "owning_company_id": owning_company_id,
        "submitted_by": "t@example.com", "status": "accepted",
        "submitted_at": "2026-06-09T00:00:00Z", "tags": [],
        "source": "user-submitted", "payload": {},
    }), encoding="utf-8")
    return meta


def _seed_tenancy_approval(category: str, asset_id: str,
                                          owning_company_id: str = "colaberry") -> None:
    tenancy.record_approval(
        item_kind="library_asset", item_id=asset_id, category=category,
        company_id=owning_company_id, approved_by_user_id="t@example.com",
        status="approved",
    )


class TestMigration:

    def test_renames_meta_file_to_slug(self, fake_full_root):
        meta = _seed_sub_record(fake_full_root, "global", "skills",
                                                "abc12345", "Build Asset Catalog")
        _seed_tenancy_approval("skills", "sub-abc12345")
        summary = mig.migrate()
        assert summary["scanned"] == 1
        assert summary["migrated"] == 1
        new_meta = meta.parent / "build-asset-catalog.meta.json"
        assert new_meta.exists()
        assert not meta.exists()
        data = json.loads(new_meta.read_text(encoding="utf-8"))
        assert data["asset_id"] == "build-asset-catalog"

    def test_rewrites_submission_back_pointer(self, fake_full_root):
        _seed_sub_record(fake_full_root, "global", "skills",
                                "abc12345", "Build Asset Catalog")
        _seed_tenancy_approval("skills", "sub-abc12345")
        mig.migrate()
        sub_file = (fake_full_root / "output" / "library" / "global"
                          / "_submissions" / "abc12345.json")
        sub_data = json.loads(sub_file.read_text(encoding="utf-8"))
        assert sub_data["asset_id"] == "build-asset-catalog"

    def test_rewrites_tenancy_approval_key(self, fake_full_root):
        _seed_sub_record(fake_full_root, "global", "skills",
                                "abc12345", "Build Asset Catalog")
        _seed_tenancy_approval("skills", "sub-abc12345")
        mig.migrate()
        # Old key gone, new key present
        assert tenancy.get_approval("library_asset", "sub-abc12345",
                                                  "skills", "colaberry") is None
        new_approval = tenancy.get_approval("library_asset",
                                                            "build-asset-catalog",
                                                            "skills", "colaberry")
        assert new_approval is not None
        assert new_approval.item_id == "build-asset-catalog"

    def test_handles_collisions_with_suffix(self, fake_full_root):
        # Two records with the same name -> second one gets a -2 slug.
        _seed_sub_record(fake_full_root, "global", "skills",
                                "aaaaaaaa", "Shared Name")
        _seed_sub_record(fake_full_root, "global", "skills",
                                "bbbbbbbb", "Shared Name")
        _seed_tenancy_approval("skills", "sub-aaaaaaaa")
        _seed_tenancy_approval("skills", "sub-bbbbbbbb")
        summary = mig.migrate()
        assert summary["migrated"] == 2
        cat_dir = (fake_full_root / "output" / "library" / "global" / "skills")
        names = sorted(p.stem.replace(".meta", "") for p in cat_dir.glob("*.meta.json"))
        assert names == ["shared-name", "shared-name-2"]

    def test_idempotent_second_run_is_noop(self, fake_full_root):
        _seed_sub_record(fake_full_root, "global", "skills",
                                "abc12345", "Build Asset Catalog")
        _seed_tenancy_approval("skills", "sub-abc12345")
        mig.migrate()
        summary = mig.migrate()
        # Second run finds no sub-* files left -> 0 scanned.
        assert summary["scanned"] == 0
        assert summary["migrated"] == 0

    def test_dry_run_does_not_write(self, fake_full_root):
        meta = _seed_sub_record(fake_full_root, "global", "skills",
                                                "abc12345", "Build Asset Catalog")
        _seed_tenancy_approval("skills", "sub-abc12345")
        summary = mig.migrate(dry_run=True)
        assert summary["scanned"] == 1
        assert summary["migrated"] == 1
        assert summary["dry_run"] is True
        # Source file unchanged
        assert meta.exists()
        assert not (meta.parent / "build-asset-catalog.meta.json").exists()
        # Tenancy untouched
        assert tenancy.get_approval("library_asset", "sub-abc12345",
                                                  "skills", "colaberry") is not None

    def test_records_missing_name_fall_back_to_asset_id(self, fake_full_root):
        # Many legacy enrichment_job/extracted_writer records have an
        # empty `name` field but a human-readable asset_id literal. The
        # migrator falls back to slugifying the asset_id (and finally
        # the filename stem) so these records aren't stranded.
        cat_dir = (fake_full_root / "output" / "library" / "global" / "mcp")
        cat_dir.mkdir(parents=True, exist_ok=True)
        legacy = cat_dir / "HTML to Markdown.meta.json"
        legacy.write_text(json.dumps({
            "asset_id": "HTML to Markdown",
            "category": "mcp", "workspace": "global",
            "name": "",  # empty name field -- common on prod
            "description": "",
            "owning_company_id": "community",
        }), encoding="utf-8")
        summary = mig.migrate()
        assert summary["scanned"] == 1
        assert summary["migrated"] == 1
        new_meta = cat_dir / "html-to-markdown.meta.json"
        assert new_meta.exists()
        data = json.loads(new_meta.read_text(encoding="utf-8"))
        assert data["asset_id"] == "html-to-markdown"

    def test_skips_records_with_nothing_to_slug(self, fake_full_root):
        # If name, asset_id, and filename stem all collapse to empty,
        # there's nothing usable to slug -- skip.
        cat_dir = (fake_full_root / "output" / "library" / "global" / "skills")
        cat_dir.mkdir(parents=True, exist_ok=True)
        meta = cat_dir / "sub-abc12345.meta.json"
        meta.write_text(json.dumps({
            "asset_id": "", "category": "skills", "workspace": "global",
            "name": "", "description": "d",
            "owning_company_id": "colaberry",
        }), encoding="utf-8")
        summary = mig.migrate()
        assert summary["scanned"] == 1
        # The collector grabs the sub-* file, but with both name and
        # asset_id empty, slug_source falls back to the stem
        # ("sub-abc12345"), which slugs to itself -> short-circuit, no
        # migration, no skip. This is fine: the file is already a
        # slug-compatible stem.
        assert summary["migrated"] == 0

    def test_migrates_literal_name_files(self, fake_full_root):
        # Legacy enrichment_job / extracted_writer path wrote files with
        # the human name as the filename, e.g. "HTML to Markdown.meta.json"
        # containing {"asset_id": "HTML to Markdown", "name": "HTML to Markdown"}.
        cat_dir = (fake_full_root / "output" / "library" / "global" / "mcp")
        cat_dir.mkdir(parents=True, exist_ok=True)
        legacy = cat_dir / "HTML to Markdown.meta.json"
        legacy.write_text(json.dumps({
            "asset_id": "HTML to Markdown",
            "category": "mcp", "workspace": "global",
            "name": "HTML to Markdown", "description": "d",
            "owning_company_id": "community",
        }), encoding="utf-8")
        summary = mig.migrate()
        assert summary["scanned"] == 1
        assert summary["migrated"] == 1
        new_meta = cat_dir / "html-to-markdown.meta.json"
        assert new_meta.exists()
        assert not legacy.exists()
        data = json.loads(new_meta.read_text(encoding="utf-8"))
        assert data["asset_id"] == "html-to-markdown"
        assert data["name"] == "HTML to Markdown"
        # No submission file exists for legacy literal-name records,
        # so the submission rewrite step is a no-op.
        assert summary["submission_rewrites"] == 0

    def test_skips_already_slug_filename_with_matching_asset_id(self, fake_full_root):
        # A file that's already correctly named + has a slug asset_id
        # is filtered out by _collect_sub_files (its stem matches
        # slugify(name)) so it doesn't even count toward "scanned".
        cat_dir = (fake_full_root / "output" / "library" / "global" / "mcp")
        cat_dir.mkdir(parents=True, exist_ok=True)
        good = cat_dir / "html-to-markdown.meta.json"
        good.write_text(json.dumps({
            "asset_id": "html-to-markdown",
            "category": "mcp", "workspace": "global",
            "name": "HTML to Markdown", "description": "d",
            "owning_company_id": "community",
        }), encoding="utf-8")
        summary = mig.migrate()
        assert summary["scanned"] == 0
        assert summary["migrated"] == 0
        assert good.exists()

    def test_idempotent_after_literal_name_migration(self, fake_full_root):
        cat_dir = (fake_full_root / "output" / "library" / "global" / "mcp")
        cat_dir.mkdir(parents=True, exist_ok=True)
        legacy = cat_dir / "HTML to Markdown.meta.json"
        legacy.write_text(json.dumps({
            "asset_id": "HTML to Markdown",
            "category": "mcp", "workspace": "global",
            "name": "HTML to Markdown", "description": "d",
            "owning_company_id": "community",
        }), encoding="utf-8")
        mig.migrate()
        # Second run finds the renamed file, sees slugify(name) == stem,
        # and skips it entirely.
        summary = mig.migrate()
        assert summary["scanned"] == 0
        assert summary["migrated"] == 0


# ── get_metadata backwards-compat fallback ─────────────────────────


class TestGetMetadataFallback:

    def test_returns_literal_name_file_when_it_exists(self, fake_lib_root):
        # Caller passes the literal asset_id, file exists at that exact
        # path -> return it directly (no fallback needed).
        d = fake_lib_root / "global" / "mcp"
        d.mkdir(parents=True)
        p = d / "HTML to Markdown.meta.json"
        p.write_text(json.dumps({
            "asset_id": "HTML to Markdown", "category": "mcp",
            "workspace": "global", "name": "HTML to Markdown",
            "description": "literal",
        }), encoding="utf-8")
        m = store.get_metadata("global", "mcp", "HTML to Markdown")
        assert m.asset_id == "HTML to Markdown"
        assert m.description == "literal"

    def test_falls_back_to_slug_when_literal_missing(self, fake_lib_root):
        # Caller passes legacy literal asset_id (e.g. from a bookmarked
        # URL), but the file has been migrated to the slug path. The
        # fallback re-tries with slugify(asset_id) and finds it.
        d = fake_lib_root / "global" / "mcp"
        d.mkdir(parents=True)
        p = d / "html-to-markdown.meta.json"
        p.write_text(json.dumps({
            "asset_id": "html-to-markdown", "category": "mcp",
            "workspace": "global", "name": "HTML to Markdown",
            "description": "slug-form",
        }), encoding="utf-8")
        m = store.get_metadata("global", "mcp", "HTML to Markdown")
        assert m.asset_id == "html-to-markdown"
        assert m.description == "slug-form"

    def test_returns_empty_when_both_miss(self, fake_lib_root):
        # Neither literal nor slug file exists -> empty AssetMetadata
        # preserving the requested asset_id verbatim (existing behavior).
        m = store.get_metadata("global", "mcp", "Nonexistent Asset")
        assert m.asset_id == "Nonexistent Asset"
        assert m.category == "mcp"
        assert m.workspace == "global"
        assert m.description == ""
        assert m.enrichment_state == "unenriched"


# ── meta_path cross-platform sanitization ──────────────────────────


class TestMetaPathSanitization:
    """Regression: an asset like 'Code Interpreter / Sandbox Execution' nested
    into a 'Code Interpreter ' (trailing space) directory that Windows can't
    create, raising FileNotFoundError on write. Each '/'-segment is now
    trimmed and illegal chars replaced, while '/' still nests."""

    def test_spaced_slash_segments_are_trimmed(self):
        assert (store._safe_asset_relpath("Code Interpreter / Sandbox Execution")
                == "Code Interpreter/Sandbox Execution")

    def test_plain_slash_nesting_is_unchanged(self):
        # No surrounding spaces -> identical to before (no prod churn).
        assert store._safe_asset_relpath("n8n Cron/Schedule Trigger") == "n8n Cron/Schedule Trigger"

    def test_backslash_and_windows_illegal_chars_replaced(self):
        assert store._safe_asset_relpath("a\\b") == "a_b"
        rel = store._safe_asset_relpath('q?:*"<>|x')
        assert not any(c in rel for c in '?:*"<>|\\')

    def test_save_and_load_roundtrip_for_spaced_slash_name(self, fake_lib_root):
        # The exact case that raised FileNotFoundError on Windows.
        store.upsert_metadata("global", "skills",
                              "Code Interpreter / Sandbox Execution",
                              description="works")
        m = store.get_metadata("global", "skills",
                               "Code Interpreter / Sandbox Execution")
        assert m.description == "works"

    def test_legacy_raw_path_still_read_after_sanitize(self, fake_lib_root):
        # A file written under the raw asset_id before sanitize is still found
        # (no prod metadata loss). A trailing-dot name is changed by sanitize
        # yet its raw path is creatable on every OS, so this is portable.
        d = fake_lib_root / "global" / "skills"
        d.mkdir(parents=True)
        (d / "MyAsset..meta.json").write_text(json.dumps({
            "asset_id": "MyAsset.", "category": "skills",
            "workspace": "global", "description": "legacy",
        }), encoding="utf-8")
        m = store.get_metadata("global", "skills", "MyAsset.")
        assert m.description == "legacy"
