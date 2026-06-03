# [Admin 1] Admin console: user provisioning UI

**Ticket:** Basecamp [9956730991](https://app.basecamp.com/3945211/buckets/7463955/todos/9956730991) · due 2026-06-17
**Status:** Shipped
**Depends on:** [Auth 1] ✅, [Auth 2] ✅
**Companion tickets:** [Admin 2] tools-access matrix, [Admin 3] add-new-company, [Provision 1] workspace repo, [Provision 2] vault

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Route `/admin/users`, gated to role=admin | `app/routers/admin.py::users_list` + `_require_admin` |
| 2 | List view of all users in current tenant | Same — filters by current user's `company_id` (super_admin sees all) |
| 3 | Status column: provisioning state | `admin/users_list.html` renders Status pill |
| 4 | Create-user form: name, email, role, access-scope hooks | `/admin/users/new` form |
| 5 | Detail view per user: access scopes + workspace repo + tool statuses | `/admin/users/{id}` + `/admin/users/{id}/scopes` |
| 6 | Audit log: every admin action timestamped | `_audit()` → `output/library/_tenants/admin_audit.jsonl` |

## Routes

| Path | Purpose | Auth |
|---|---|---|
| `GET /admin/` | Console home | admin |
| `GET /admin/companies` | List tenants | admin |
| `GET /admin/companies/new` | Add-new-company form | super_admin |
| `POST /admin/companies/new` | Create company | super_admin |
| `GET /admin/companies/{slug}` | Tenant detail | admin |
| `POST /admin/companies/{slug}/suspend` | Soft-delete | super_admin |
| `GET /admin/users` | List users (filtered by tenant) | admin |
| `GET /admin/users/new` | Provisioning form | admin |
| `POST /admin/users/new` | Create user + optionally trigger Provision 1 | admin |
| `GET /admin/users/{id}` | User detail | admin |
| `POST /admin/users/{id}/role` | Update roles | admin |
| `GET /admin/users/{id}/scopes` | Tools-access matrix | admin |
| `POST /admin/users/{id}/scopes/grant` | Grant tool | admin |
| `POST /admin/users/{id}/scopes/revoke` | Revoke tool | admin |
| `POST /admin/users/{id}/scopes/credential` | Store credential in vault | admin |

## Dev-mode fallback

When SSO is disabled (env vars missing on the local dev box), the admin
auth gate falls back to acting as `ali@colaberry.com`. This lets the
admin console work pre-OAuth-registration without exposing it on prod
where SSO is enforced. The fallback is gated on
`auth_google.is_enabled() == False`.

## Audit

Every mutation (`company.create`, `company.suspend`, `user.create`,
`user.set_roles`, `scope.grant`, `scope.revoke`, `credential.set`,
`workspace.provision`) appends one row to:

```
output/library/_tenants/admin_audit.jsonl
{actor_id, action, target, notes, at}
```

Separate from per-user `audit.jsonl` (vault) and `access_scopes.jsonl`
(tenancy) so the admin perspective is queryable independently.

## Hand-off

- **[Admin 3]** uses the same UI scaffolding — already shipped here as
  `/admin/companies/new` (so this PR closes Admin 3 too)
- **[Admin 2]** uses the `/admin/users/{id}/scopes` route — already
  shipped here (so this PR closes Admin 2 too)
- **[Provision 1]** is triggered by the `create_workspace_repo`
  checkbox on the user-create form — shipped in `workspaces.py`
