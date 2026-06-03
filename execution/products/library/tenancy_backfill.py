"""Auth 1 backfill — assigns every existing library asset + use case to
the "colaberry" tenant, and creates ItemApproval rows for items the legacy
`vetted=True` flag marked as Colaberry-approved.

Idempotent — running it twice is safe. Run via:

    python -c "from execution.products.library.tenancy_backfill import run; \
                    print(run(dry_run=False))"
"""

from __future__ import annotations

from typing import Any

from . import inventory, store, tenancy, use_cases


COLABERRY_COMPANY_ID = "colaberry"


def _find_ali_user_id() -> str:
    u = tenancy.get_user("ali@colaberry.com")
    return u.user_id if u else "ali-legacy-import"


def run(dry_run: bool = False) -> dict[str, Any]:
    """Returns counts: {companies_seeded, users_seeded, assets_assigned,
                              approvals_created, use_cases_assigned}."""
    # 1. Seed companies + users (idempotent)
    seeded = tenancy.seed_initial_companies_and_users()

    approver_id = _find_ali_user_id()

    # 2. Walk every library category + assign owning_company
    assets_assigned = 0
    approvals_created = 0

    for cat in inventory.CATEGORIES:
        rows = inventory.load_category(cat.key) or []
        for row in rows:
            asset_id = row.get("name") or row.get("id") or ""
            if not asset_id:
                continue
            meta = store.get_metadata("global", cat.key, asset_id)
            if getattr(meta, "owning_company_id", None) != COLABERRY_COMPANY_ID:
                if not dry_run:
                    store.upsert_metadata("global", cat.key, asset_id,
                                                  owning_company_id=COLABERRY_COMPANY_ID)
                assets_assigned += 1
            if getattr(meta, "vetted", False):
                # Create / refresh the ItemApproval row
                existing = tenancy.get_approval(
                    "library_asset", asset_id, cat.key, COLABERRY_COMPANY_ID)
                if existing and existing.status == "approved":
                    continue   # already there
                if not dry_run:
                    tenancy.record_approval(
                        item_kind="library_asset",
                        item_id=asset_id,
                        category=cat.key,
                        company_id=COLABERRY_COMPANY_ID,
                        approved_by_user_id=meta.vetted_by or approver_id,
                        status="approved",
                        visibility="same-company-only",
                        notes=f"Backfill: legacy vetted_at={meta.vetted_at}",
                    )
                approvals_created += 1

    # 3. Walk use cases + same treatment
    uc_assigned = 0
    for uc in use_cases.list_all(workspace="global"):
        # Note: use_cases lacks owning_company_id today; not modifying the
        # use-case file format here. Backfill creates an approval row when
        # uc.vetted=True; per-tenant ownership of use cases is a Workflow 1
        # concern (when submit-to-mod-queue is wired).
        if uc.vetted:
            existing = tenancy.get_approval(
                "use_case", uc.use_case_id, "use_cases", COLABERRY_COMPANY_ID)
            if existing and existing.status == "approved":
                continue
            if not dry_run:
                tenancy.record_approval(
                    item_kind="use_case",
                    item_id=uc.use_case_id,
                    category="use_cases",
                    company_id=COLABERRY_COMPANY_ID,
                    approved_by_user_id=uc.vetted_by or approver_id,
                    status="approved",
                    visibility="same-company-only",
                    notes=f"Backfill: legacy vetted_at={uc.vetted_at}",
                )
            uc_assigned += 1

    return {
        "dry_run": dry_run,
        "companies_seeded": seeded["companies"],
        "users_seeded": seeded["users"],
        "assets_assigned_to_colaberry": assets_assigned,
        "asset_approvals_created": approvals_created,
        "use_case_approvals_created": uc_assigned,
    }


if __name__ == "__main__":
    import json, sys
    dry = "--dry-run" in sys.argv
    print(json.dumps(run(dry_run=dry), indent=2))
