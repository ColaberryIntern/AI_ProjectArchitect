# [Admin 3] Add-new-company flow (super-admin tenant onboarding)

**Ticket:** Basecamp [9956776428](https://app.basecamp.com/3945211/buckets/7463955/todos/9956776428) · due 2026-06-14
**Status:** Shipped
**Depends on:** [Auth 1] ✅, [Admin 1] ✅ (shipped in same PR)

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Route `/admin/companies/new`, gated to super_admin (Ali only for v1) | `admin.py::company_new_form` + `_require_super_admin` |
| 2 | Form fields: company name, primary domain, default visibility, tenant slug | `admin/company_new.html` |
| 3 | On submit: row in companies + auto-mapping to library_tenant_domains | `admin.py::company_new` writes to companies.json + appends to tenant_domains config |
| 4 | List view at `/admin/companies` | `admin.py::companies_list` |
| 5 | Edit view with visibility-defaults + approver list | `/admin/companies/{slug}` detail page (basic version; full edit-form in follow-up) |
| 6 | Soft-delete only (suspend) | `tenancy.deactivate_company()` + `POST /admin/companies/{slug}/suspend` |
| 7 | Audit log: create / edit / suspend stamped with actor + timestamp | `admin._audit()` |
| 8 | Domain-to-tenant routing in SSO callback | Already in `auth_google.resolve_company_for_email()` |

## What's shipped

- Route `/admin/companies/new` accepts company_id (slug), display_name,
  plan, default_visibility, primary_domain (optional), notes
- Auto-appends a `{domain → company_id}` entry to `library_tenant_domains.json`
  when `primary_domain` is provided — so SSO logins from that domain
  route to this new tenant automatically
- Soft-suspend via `is_active=False`
- Per-tenant detail page shows users, approval counts, lets super-admin
  suspend

## Defaults applied (Ali, override in PR review if needed)

| # | Question | Default | Why |
|---|---|---|---|
| 1 | Auto-add domain mapping when primary_domain is set | **Yes** | If you set a primary domain, you almost certainly want auto-routing |
| 2 | Hard-delete | **Never** — soft-delete only | per acceptance criterion 6 |
| 3 | Edit-company UI | **Basic display now; full edit in follow-up** | Out of scope for Admin 3 acceptance; create row + suspend is the core flow |

## Hand-off

Once Ali creates a tenant via this UI:
- The tenant row exists in `companies.json`
- (If primary_domain set) SSO callback auto-provisions users from that domain
- Provisioning the first admin user happens via `/admin/users/new`
  (preselect this company)
