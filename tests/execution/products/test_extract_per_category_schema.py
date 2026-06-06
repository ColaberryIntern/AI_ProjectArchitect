"""Schema-conformance test for extracted assets.

Question being answered: if the user clicks "Commit to library branch"
on the Extract page for each of the 15 output_types we support, does
the resulting AssetMetadata satisfy the per-category schema defined in
category_schemas.SCHEMAS?

Pass = all required fields populated for the destination category.
Fail = at least one required field empty -- the extracted asset would
render an incomplete library page and would fail a tightened
submit-form schema check.

The test is intentionally noisy: it prints a per-category pass/fail
table at the bottom of the output so the gap is visible at a glance.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from execution.products.library import (
    category_schemas, extracted_writer, store,
)


class _FakeSrc:
    """Mimics what skill_extractor.extract_from_bc_ticket builds: enough
    fields that any of the 15 .j2 templates can render without raising.

    The 'body' is realistic-but-generic so we exercise the per-category
    fallback population, not a particular ticket's contents."""
    def __init__(self, output_type: str):
        self.source_kind = "bc_ticket"
        self.source_id = "9999000000"
        self.title = f"Test {output_type} from extract pipeline"
        self.body = (
            f"This is a test {output_type} extracted from a BC ticket. "
            "Goal: cover the full source body so the categorizer / writer "
            "have plenty of text to derive how_to_use, what_its_for, and "
            "example fields. Sub-steps: produce a clean rendered preview, "
            "then call _register_as_library_asset and inspect the saved "
            "AssetMetadata for schema conformance."
        )
        self.metadata = {
            "bc_url": ("https://app.basecamp.com/3945211/buckets/"
                              "7463955/todos/9999000000"),
            "comments": [],
            "tools": ["pytest"],
        }


def _grade(meta, category: str):
    """Return (missing_required, empty_optional_excluding_universally_optional)."""
    schema = category_schemas.schema_for(category)
    payload = {f: getattr(meta, f, None) for f in schema["required"] + schema["optional"]}
    missing = category_schemas.validate_payload(category, payload)
    universally_skipped = {"tags", "source"}
    empty_optional = [f for f in schema["optional"]
                                  if not getattr(meta, f, None) and f not in universally_skipped]
    return missing, empty_optional


@pytest.fixture
def cleanup_test_assets():
    """Remove any synthetic .meta.json files we wrote so the catalog
    isn't polluted by test runs."""
    written: list[Path] = []
    yield written
    for p in written:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


# Parametrize over every output_type the extractor knows about.
@pytest.mark.parametrize("output_type",
                                            list(extracted_writer.OUTPUT_TYPE_TO_CATEGORY.keys()))
def test_extract_per_output_type_satisfies_schema(output_type, cleanup_test_assets):
    """For each output_type the extractor supports, simulate the full
    commit path and assert the resulting AssetMetadata satisfies the
    destination category's schema."""
    category = extracted_writer.OUTPUT_TYPE_TO_CATEGORY[output_type]
    src = _FakeSrc(output_type)
    slug = f"test-extract-{output_type}-schema-check"
    artifact = extracted_writer.ExtractedArtifact(
        slug=slug,
        output_type=output_type,
        source_kind=src.source_kind,
        source_bc_id=src.source_id,
        branch=f"skill-extracted/{slug}",
        file_path=f"library/extracted/{output_type}/{slug}.md",
        raw_url=(f"https://raw.githubusercontent.com/X/Y/skill-extracted/"
                          f"{slug}/library/extracted/{output_type}/{slug}.md"),
        created_at="2026-06-06T00:00:00Z",
        created_by="test@colaberry.com",
    )
    # Render template (mirrors what write_and_commit does).
    rendered_body = extracted_writer.render(src, output_type, slug,
                                                                          created_at=artifact.created_at)
    # Same call _register_as_library_asset gets after write_and_commit.
    with contextlib.redirect_stdout(io.StringIO()):
        extracted_writer._register_as_library_asset(
            artifact, src, rendered_body, output_type,
            owning_company_id="colaberry",
            created_by="test@colaberry.com",
        )
    cleanup_test_assets.append(store.meta_path("global", category, slug))

    meta = store.get_metadata("global", category, slug)
    missing, empty_optional = _grade(meta, category)

    # The actual assertion: required fields must all be populated.
    assert not missing, (
        f"\n  output_type={output_type!r} -> category={category!r}\n"
        f"  REQUIRED fields still empty after extract+register: {missing}\n"
        f"  (optional fields also empty: {empty_optional})"
    )


def test_report_summary_table(cleanup_test_assets, capsys):
    """Render a human-readable pass/fail table across all output_types.

    This is technically a duplicate of the parametrized test above, but
    it produces a SINGLE legible block at the bottom of the test output
    so the gap is visible at a glance rather than scattered across 15
    parametrize cases.
    """
    rows = []
    for output_type, category in extracted_writer.OUTPUT_TYPE_TO_CATEGORY.items():
        src = _FakeSrc(output_type)
        slug = f"test-extract-{output_type}-summary"
        artifact = extracted_writer.ExtractedArtifact(
            slug=slug,
            output_type=output_type,
            source_kind=src.source_kind,
            source_bc_id=src.source_id,
            branch=f"skill-extracted/{slug}",
            file_path=f"library/extracted/{output_type}/{slug}.md",
            raw_url=("https://raw.githubusercontent.com/X/Y/skill-extracted/"
                              f"{slug}/library/extracted/{output_type}/{slug}.md"),
            created_at="2026-06-06T00:00:00Z",
            created_by="test@colaberry.com",
        )
        try:
            rendered_body = extracted_writer.render(src, output_type, slug,
                                                                                  created_at=artifact.created_at)
            with contextlib.redirect_stdout(io.StringIO()):
                extracted_writer._register_as_library_asset(
                    artifact, src, rendered_body, output_type,
                    owning_company_id="colaberry",
                    created_by="test@colaberry.com",
                )
            cleanup_test_assets.append(store.meta_path("global", category, slug))
            meta = store.get_metadata("global", category, slug)
            missing, empty_opt = _grade(meta, category)
            status = "PASS" if not missing else "FAIL"
        except FileNotFoundError as e:
            # A .j2 template is missing for this output_type.
            status = "TEMPLATE-MISSING"
            missing = [f"template_missing: {Path(str(e)).name}"]
            empty_opt = []
        rows.append((output_type, category, status, missing, empty_opt))

    # Render the table to stdout so the user sees it.
    with capsys.disabled():
        print()
        print("=" * 88)
        print(f"  {'OUTPUT_TYPE':<13} {'CATEGORY':<15} {'STATUS':<18} MISSING_REQUIRED")
        print("-" * 88)
        for output_type, category, status, missing, _ in rows:
            miss_str = ", ".join(missing) if missing else "-"
            print(f"  {output_type:<13} {category:<15} {status:<18} {miss_str}")
        print("-" * 88)
        passes = sum(1 for r in rows if r[2] == "PASS")
        fails = sum(1 for r in rows if r[2] == "FAIL")
        templ = sum(1 for r in rows if r[2] == "TEMPLATE-MISSING")
        print(f"  {passes} pass / {fails} fail / {templ} template-missing  "
                    f"({len(rows)} output_types total)")
        print("=" * 88)
        print()
        if fails > 0 or templ > 0:
            print("  Per-category required fields the extractor doesn't yet populate:")
            for output_type, category, status, missing, _ in rows:
                if status != "PASS":
                    schema = category_schemas.schema_for(category)
                    print(f"    {output_type} -> {category}: required={schema['required']}")
            print()


@pytest.mark.parametrize("output_type",
                                            list(extracted_writer.OUTPUT_TYPE_TO_CATEGORY.keys()))
def test_extract_always_sets_owning_company_and_provenance(output_type, cleanup_test_assets):
    """Independent of schema conformance: every extracted asset MUST be
    tagged to the operator's company and carry submitted_by. These are
    non-negotiable -- without them the asset is unattributable and the
    'auto-extracted' rollout would create orphans no one can audit."""
    category = extracted_writer.OUTPUT_TYPE_TO_CATEGORY[output_type]
    src = _FakeSrc(output_type)
    slug = f"test-extract-{output_type}-provenance"
    artifact = extracted_writer.ExtractedArtifact(
        slug=slug, output_type=output_type,
        source_kind=src.source_kind, source_bc_id=src.source_id,
        branch="", file_path="", raw_url="<raw>",
        created_at="2026-06-06T00:00:00Z",
        created_by="test@colaberry.com",
    )
    try:
        rendered = extracted_writer.render(src, output_type, slug,
                                                                      created_at=artifact.created_at)
    except FileNotFoundError:
        pytest.skip(f"template missing for {output_type}")
    with contextlib.redirect_stdout(io.StringIO()):
        extracted_writer._register_as_library_asset(
            artifact, src, rendered, output_type,
            owning_company_id="colaberry",
            created_by="test@colaberry.com",
        )
    cleanup_test_assets.append(store.meta_path("global", category, slug))
    meta = store.get_metadata("global", category, slug)
    assert meta.owning_company_id == "colaberry"
    assert meta.submitted_by == "test@colaberry.com"
    assert "auto-extracted" in (meta.tags or [])
