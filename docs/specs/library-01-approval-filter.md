# [Library 1] Per-tenant approval filter + badges

**Ticket:** Basecamp [9956731079](https://app.basecamp.com/3945211/buckets/7463955/todos/9956731079) · Library category page
**Status:** Shipped
**Depends on:** [Auth 1] ✅ (tenancy model + `filter_for_company`), [Admin 1] ✅ (admin can stamp approvals)

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Default view filters items to those the viewer's company can see | `library.py::library_category` calls `inventory.filter_for_company(items, cat.key, viewer_company_id)` before per-item enrichment |
| 2 | Anon viewer sees the open inventory (no narrowing) | `_viewer_company_id(None, scope)` returns `None`; `filter_for_company` short-circuits on `None` |
| 3 | "Other company approved …" lets a viewer narrow to items approved by a specific named company | Query param `?approved=company:patriot,colaberry` → router builds set of `company_id`s, narrows to items where at least one `ItemApproval.company_id` matches |
| 4 | Each item lists every company that has approved it | Per-item enrichment populates `_approving_companies = [{company_id, company_name, approved_at}, ...]`; template stacks one badge per company |
| 5 | Active filter is visible + clearable | When `approved_filter_companies` set, a strip renders above the inventory: `"Filtering by approval: <chip> <chip> [clear ✕]"` |

## Visibility model (inherited from [Auth 1])

An item is visible to `viewer_company_id` if **any** of:

1. `owning_company_id == viewer_company_id` (the asset is theirs)
2. The viewer's company has a row in `item_approvals` for this asset
3. Some company approved with `visibility="shared-public"`
4. Some company approved with `visibility="shared-with-allowlist"` AND viewer is in `shared_with`

Withdrawn approvals (`status="withdrawn"`) do not grant visibility.

## Files

- `app/routers/library.py::library_category` — applies filter, parses `?approved=`, enriches items
- `app/templates/library/category.html` — chip + dropdown + active-filter strip + per-item badge stack
- `execution/products/library/inventory.py::filter_for_company` — the chokepoint
- `execution/products/library/tenancy.py::list_approvals` — query backing the badges + filter

## Tests

`tests/execution/products/test_library_filter.py` — 7 tests covering:
- anon → no filter
- approval-gated narrowing
- shared-public inclusion
- shared-with-allowlist gating
- owning-company always sees own
- list_approvals returns multiple approving companies
- revoked approval removes visibility

## Trade-offs / deferred

- Filter is OR-of-companies, not AND. To find items approved by BOTH X and Y, follow-up would intersect the two sets — not in scope.
- Approving-company badges show all companies that have approved, capped only by the on-disk approval count. If the list grows long (>5 companies), a future ticket can collapse to "+N more".
- Per-item rationale (`ItemApproval.notes`) is captured but not surfaced in the badge tooltip — easy follow-up.
