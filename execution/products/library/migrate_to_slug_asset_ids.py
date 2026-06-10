"""One-shot migration: rename `sub-<uuid8>` AssetMetadata files to slug-
based asset_ids, in lock-step with their tenancy approval rows and the
originating Submission's asset_id pointer.

Background: pre-normalization, `_tool_propose_asset` -> `review_submission`
set `asset_id = "sub-<submission_id>"`, producing opaque files like
`output/library/global/skills/sub-22194d23.meta.json`. The library UI
and filter_for_company expected `asset_id == slugify(name)`, leading to
URL ugliness (/library/skills/sub-22194d23) and a two-convention lookup
in the filter. This script walks every `sub-*.meta.json` file on disk
and renames each to `<slug>.meta.json`, updating:

  1. The AssetMetadata file: rename + rewrite `asset_id` field
  2. The Submission JSON (output/library/<ws>/_submissions/<sid>.json):
     rewrite `asset_id` so the submission -> asset back-pointer is correct
  3. The tenancy item_approvals row: remove old key, write new key

Idempotent: a second run finds no `sub-*.meta.json` files left and exits
0 with `migrated: 0`. Safe to dry-run by passing `--dry-run`.

Usage (inside the prod container or any environment with write access to
output/library/):
    python -m execution.products.library.migrate_to_slug_asset_ids
    python -m execution.products.library.migrate_to_slug_asset_ids --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import store, tenancy


def _load_json(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json(p: Path, data: dict[str, Any]) -> None:
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _collect_sub_files() -> list[Path]:
    """Collect every `*.meta.json` whose filename stem doesn't already
    match `slugify(<name field>)`. That covers:

      - the original `sub-<uuid8>.meta.json` legacy (`_tool_propose_asset`
        path -> `review_submission` default), and
      - literal-name files written by `enrichment_job.enrich_batch` /
        `extracted_writer` paths that used `item["name"]` verbatim as
        the asset_id (e.g. `HTML to Markdown.meta.json`).

    The check is read-only: anything whose stem already equals the slug
    of its `name` field is skipped here so idempotence holds across
    repeated runs.
    """
    if not store.LIB_ROOT.exists():
        return []
    out: list[Path] = []
    for ws_dir in store.LIB_ROOT.iterdir():
        if not ws_dir.is_dir() or ws_dir.name.startswith("_"):
            continue
        for cat_dir in ws_dir.iterdir():
            if not cat_dir.is_dir() or cat_dir.name.startswith("_"):
                continue
            for p in cat_dir.glob("*.meta.json"):
                stem = p.name[: -len(".meta.json")]
                # Always pick up sub-<uuid8>.meta.json (legacy convention).
                if stem.startswith("sub-"):
                    out.append(p)
                    continue
                # For everything else, peek at the `name` field; if
                # slugify(name) != stem, this file is a legacy literal-
                # name write that needs renaming. Many prod legacy
                # records have an empty `name` field but a literal
                # asset_id (e.g. asset_id="HTML to Markdown"); fall
                # back to the asset_id field, then the stem itself.
                data = _load_json(p)
                if not data:
                    continue
                name_src = (
                    (data.get("name") or "").strip()
                    or (data.get("asset_id") or "").strip()
                    or stem
                )
                if not name_src:
                    continue
                if store.slugify(name_src) != stem:
                    out.append(p)
    return out


def _resolve_slug_for_migration(workspace: str, category: str, name: str,
                                                old_path: Path) -> str:
    # Mirror resolve_asset_slug but don't count `old_path` (we're about to
    # remove it). Otherwise running migration twice would always append "-2".
    base = store.slugify(name)
    cat_dir = old_path.parent
    target = cat_dir / f"{base}.meta.json"
    if not target.exists() or target == old_path:
        return base
    n = 2
    while True:
        cand = cat_dir / f"{base}-{n}.meta.json"
        if not cand.exists() or cand == old_path:
            return f"{base}-{n}"
        n += 1


def _find_submission_file(workspace: str, submission_id: str) -> Path | None:
    p = store.LIB_ROOT / workspace / "_submissions" / f"{submission_id}.json"
    return p if p.exists() else None


def migrate(dry_run: bool = False) -> dict[str, Any]:
    """Walk every sub-*.meta.json under LIB_ROOT, rename to slug-based id.

    Returns a summary dict with per-step counts + a list of any skipped
    records (name missing, collision unresolvable, etc.).
    """
    summary: dict[str, Any] = {
        "scanned": 0, "migrated": 0, "skipped": [],
        "tenancy_rewrites": 0, "submission_rewrites": 0,
        "dry_run": dry_run,
    }
    approvals_path = tenancy._approvals_path()
    approvals = tenancy._load_approvals() if approvals_path.exists() else {}
    approvals_dirty = False

    for old_meta in _collect_sub_files():
        summary["scanned"] += 1
        ws = old_meta.parent.parent.name
        category = old_meta.parent.name
        data = _load_json(old_meta)
        if not data:
            summary["skipped"].append({"file": str(old_meta),
                                                "reason": "could not parse meta json"})
            continue
        name = (data.get("name") or "").strip()
        old_asset_id = (data.get("asset_id") or old_meta.stem.replace(".meta", ""))
        # If `name` is missing, fall back to the asset_id field and
        # finally the file stem. Many legacy enrichment_job/
        # extracted_writer records have empty `name` but a literal
        # human-readable asset_id (e.g. "HTML to Markdown"), which is
        # plenty to slug. Only skip if we have nothing at all.
        slug_source = name or old_asset_id or old_meta.stem.replace(".meta", "")
        slug_source = slug_source.strip()
        if not slug_source:
            summary["skipped"].append({"file": str(old_meta),
                                                "reason": "no name field"})
            continue
        new_asset_id = _resolve_slug_for_migration(ws, category, slug_source, old_meta)
        old_stem = old_meta.name[: -len(".meta.json")]
        if new_asset_id == old_asset_id and new_asset_id == old_stem:
            # Already in the right shape (e.g. a manual previous rename):
            # both the on-disk filename and the in-JSON asset_id field
            # match the slug. Note: when only the asset_id field matches
            # but the filename is still legacy (literal name), we still
            # need to rename the file -- so don't short-circuit then.
            continue
        new_meta = old_meta.parent / f"{new_asset_id}.meta.json"

        # 1) Rewrite + rename the AssetMetadata file
        data["asset_id"] = new_asset_id
        if not dry_run:
            _save_json(old_meta, data)
            old_meta.rename(new_meta)

        # 2) Update the originating Submission's asset_id back-pointer.
        # Submission file is keyed by submission_id, which for legacy
        # sub-<uuid8> ids is everything after "sub-".
        sid = old_asset_id[len("sub-"):] if old_asset_id.startswith("sub-") else None
        if sid:
            sub_p = _find_submission_file(ws, sid)
            if sub_p:
                sub_data = _load_json(sub_p)
                if sub_data is not None:
                    sub_data["asset_id"] = new_asset_id
                    if not dry_run:
                        _save_json(sub_p, sub_data)
                    summary["submission_rewrites"] += 1

        # 3) Rewrite tenancy approval row.
        owning_company = (data.get("owning_company_id") or "").strip() or "community"
        old_key = tenancy._approval_key("library_asset", category,
                                                          old_asset_id, owning_company)
        new_key = tenancy._approval_key("library_asset", category,
                                                          new_asset_id, owning_company)
        if old_key in approvals:
            row = dict(approvals[old_key])
            row["item_id"] = new_asset_id
            approvals[new_key] = row
            del approvals[old_key]
            approvals_dirty = True
            summary["tenancy_rewrites"] += 1

        summary["migrated"] += 1

    if approvals_dirty and not dry_run:
        tenancy._save_approvals(approvals)

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                                help="Walk + report without writing.")
    args = parser.parse_args(argv)
    summary = migrate(dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
