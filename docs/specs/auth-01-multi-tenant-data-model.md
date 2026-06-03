# [Auth 1] Multi-tenant data model

**Ticket:** Basecamp [9956730953](https://app.basecamp.com/3945211/buckets/7463955/todos/9956730953) · due 2026-06-10
**Status:** Shipped
**Author:** Advisor Claude Code (autonomous), reviewed by Ali on merge
**Depends on:** [Infra 1] ✅
**Unblocks:** [Auth 2] SSO · [Admin 1] console · [Library 1] approval filter · [Workflow 1] publish

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Schema adds `companies` (tenant root) | `tenancy.Company` + `companies.json` |
| 2 | Schema adds `users` (FK company_id) | `tenancy.User` + `users.json` |
| 3 | Schema adds `roles` (admin / contributor / consumer) | `User.roles: list[str]` with `ROLES = ("admin", "contributor", "consumer")` constant |
| 4 | Schema adds `access_scopes` (per-tool grants) | `tenancy.AccessScope` + append-only `access_scopes.jsonl` |
| 5 | Every library row gets `owning_company_id` | Added to `AssetMetadata` (default `"colaberry"`) |
| 6 | Separate `item_approvals` join table — same item can be approved by multiple companies independently | `tenancy.ItemApproval` + `item_approvals.json` keyed `{kind}|{cat}|{id}|{company}` |
| 7 | Approval visibility ∈ {same-company-only, shared-public, shared-with-allowlist} | `VISIBILITIES` constant + `ItemApproval.visibility` + `ItemApproval.shared_with` |
| 8 | Backfill: today's library → Colaberry tenant + same-company-only approvals for currently-vetted items | `tenancy_backfill.py::run()` — idempotent |
| 9 | Backend query layer filters by tenant on every read; explicit join on item_approvals when filter is "{Company} approved" | `inventory.filter_for_company(rows, category, viewer_company_id)` |

## 1. Storage layout

```
output/library/_tenants/
├── companies.json          # JSON-array of Company rows
├── users.json              # JSON-array of User rows
├── item_approvals.json     # dict keyed "{kind}|{cat}|{id}|{company}"
└── access_scopes.jsonl     # append-only event log
```

This matches the rest of the Library product (JSON-file backed,
operator-friendly, easy to seed/inspect/back up). A future ticket can
migrate to SQLite/Postgres without changing the API surface — every
read goes through `tenancy.list_*` / `get_*` and every write goes
through `upsert_*` / `record_*`.

## 2. Schema

### Company
```python
@dataclass
class Company:
    company_id: str            # slug, e.g. "colaberry", "demo-tenant"
    display_name: str
    plan: str = "free"          # free | team | enterprise
    default_visibility: str = "same-company-only"
    primary_admin_user_id: str | None = None
    created_at: str = ""
    is_active: bool = True
    notes: str = ""
```

### User
```python
@dataclass
class User:
    user_id: str               # "usr-{10-hex}"
    email: str                 # unique; case-insensitive lookup
    company_id: str            # FK → Company.company_id
    display_name: str
    roles: list[str]           # subset of {"admin", "contributor", "consumer"}
    google_subject: str | None # OAuth sub (Auth 2 will populate)
    workspace_repo: str | None # Provision 1 will populate
    created_at: str
    last_login_at: str | None
    is_active: bool
```

### ItemApproval
```python
@dataclass
class ItemApproval:
    item_kind: str             # "library_asset" | "use_case"
    item_id: str               # asset_id or use_case_id
    category: str              # library category (or "use_cases")
    company_id: str            # which company is approving
    approved_by_user_id: str   # which user in that company
    approved_at: str
    status: str                # approved | rejected | pending | withdrawn | deprecated
    visibility: str            # same-company-only | shared-public | shared-with-allowlist
    notes: str
    shared_with: list[str]     # company_ids; only used when visibility="shared-with-allowlist"
```

### AccessScope (append-only)
```python
@dataclass
class AccessScope:
    scope_id: str
    user_id: str
    tool: str                  # "gmail" | "calendar" | "github" | "basecamp" | "ccpp" | ...
    grant_type: str            # "granted" | "revoked"
    granted_by_user_id: str
    granted_at: str
    notes: str
```

## 3. Per-company approval as first-class

The crucial Ali-called-out shape:

> Colaberry approves a skill → Patriot opts in to consume it → Patriot
> can ALSO mark it as their-own-approved without forking.

This works because `item_approvals` is keyed by **(item, company)**, not
by item alone. Two companies can have independent `approved` rows for
the same item with independent visibility settings. The asset is one
record; the approvals layer is many-to-many.

```python
# Colaberry approves "MCP Filesystem Server" for itself only
record_approval("library_asset", "MCP Filesystem Server", "mcp",
                  company_id="colaberry", approved_by_user_id=ali.user_id,
                  visibility="same-company-only")

# Demo-tenant ALSO approves it, independently, sharing it publicly
record_approval("library_asset", "MCP Filesystem Server", "mcp",
                  company_id="demo-tenant", approved_by_user_id=demo.user_id,
                  visibility="shared-public")

# Querying — both rows coexist
list_approvals(item_id="MCP Filesystem Server")
#   → [ItemApproval(company="colaberry", visibility="same-company-only"),
#       ItemApproval(company="demo-tenant", visibility="shared-public")]
```

## 4. Backend query filter

`inventory.filter_for_company(rows, category, viewer_company_id)` is
the single chokepoint:

```python
def filter_for_company(rows, category, viewer_company_id):
    if not viewer_company_id:
        return rows  # legacy / admin view
    out = []
    for row in rows:
        asset_id = row["name"]
        meta = store.get_metadata("global", category, asset_id)
        if meta.owning_company_id == viewer_company_id:
            out.append(row)                         # you own it
        elif tenancy.companies_with_access(
                "library_asset", asset_id, category, viewer_company_id):
            out.append(row)                         # someone approved it for you
    return out
```

`companies_with_access` walks the approval rows + checks visibility:
- own approval → see
- shared-public approval (by anyone) → see
- shared-with-allowlist approval whose allowlist contains viewer → see

[Library 1] will wire this into the routes:
- `/library/{cat}?ws={company_id}` filters by that company
- Filter chip "✓ {Company} approved" adds a `status=approved` constraint

## 5. Backfill

`tenancy_backfill.py::run(dry_run=False)`:

1. Seeds `colaberry` + `demo-tenant` companies (idempotent)
2. Seeds `ali / ram / karun / kes` users in `colaberry` + `demo` user in `demo-tenant`
3. For every existing library asset:
   - Sets `owning_company_id = "colaberry"` on the metadata
   - If `vetted=True`, creates an `ItemApproval` row for company `colaberry`
     with `visibility="same-company-only"` (per BUILD_INDEX default #3)
4. Same for use cases that are vetted

Returns a counts dict. Re-running is a no-op.

## 6. Backward compatibility

The existing `meta.vetted: bool` stays in the schema and continues to
work. The new `owning_company_id` defaults to `"colaberry"` so reads of
legacy metadata files transparently inherit. Code paths that don't
pass `viewer_company_id` to `inventory.filter_for_company` continue to
see all rows (admin / debugging view).

Migration path for clients:
- Today: `inventory.load_category("skills")` returns all rows
- After Library 1: routes start passing the logged-in user's company →
  `inventory.filter_for_company(rows, "skills", company_id)` narrows

`vetted_by` / `vetted_at` are still informational. Removing them is a
future cleanup, not blocking.

## 7. Open questions (defaults applied)

| # | Question | Default | Why |
|---|---|---|---|
| 1 | Is `roles` an enum table or list-of-strings on User? | **list[str] on User** | Simpler. Three roles, low cardinality. |
| 2 | Can a user belong to multiple companies? | **No** for v1 — one `company_id` per User | YAGNI. Cross-company collab uses `shared-with-allowlist` |
| 3 | What's the company "platform admin" — a separate concept or a role? | **Role** — `"admin"` on a Colaberry user with company_id="colaberry" | Avoids a 4th table |
| 4 | Hard-delete of a company? | **No** — soft-delete via `is_active=False` | Audit-grade |
| 5 | Email uniqueness scope — global or per-company? | **Global** | Same person can be a member of many companies later, but not v1 |

## 8. Files shipped

| File | Purpose |
|---|---|
| `execution/products/library/tenancy.py` | Data layer — companies, users, item_approvals, access_scopes |
| `execution/products/library/tenancy_backfill.py` | Idempotent backfill script |
| `tests/execution/products/test_tenancy.py` | 20 tests covering all 4 tables + visibility + backfill |
| `docs/specs/auth-01-multi-tenant-data-model.md` | This document |
| `execution/products/library/store.py` (modified) | `AssetMetadata.owning_company_id` added |
| `execution/products/library/inventory.py` (modified) | `filter_for_company()` helper |

## 9. Hand-off

**[Auth 2] Google SSO** — needs:
- `User.google_subject` field (already added)
- Login flow that creates/updates a User on first Google auth, looks up
  the matching company by domain (or routes to a "no company" admin
  approval queue if email domain doesn't match any known company)

**[Library 1] approval filter** — needs:
- Route-level `viewer_company_id` from session
- Pass to `inventory.filter_for_company()`
- Add filter chip "✓ {Company} approved" toggling status=approved query

**[Workflow 1] publish workflow** — needs:
- Per-company moderation queue: `list_approvals(company_id=X, status="pending")`
- State transitions through `record_approval(..., status=...)`

**[Provision 2] credentials vault** — needs:
- `access_scopes` plumbing (already in place)
- Encrypted token storage (separate concern; this ticket only models the grant/revoke history)
