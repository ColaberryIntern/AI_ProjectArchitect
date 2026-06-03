# [Library 2] Identity badge + workspace/scope switcher

**Ticket:** Basecamp [9956731092](https://app.basecamp.com/3945211/buckets/7463955/todos/9956731092) · Library header
**Status:** Shipped
**Depends on:** [Auth 2] ✅ (Google SSO + JWT session cookie), [Library 1] ✅ (per-company filter is the data path)

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Header shows who is logged in | `_library_base.html` renders avatar + display name + `{company_id}` chip when `current_session_user` is set; otherwise a "Sign in with Google" button |
| 2 | Scope switcher: All / My company / Mine | 3 chips in the header; active chip resolved from `?scope=` or default (logged-in → my-company, anon → all) |
| 3 | "All" shows everything the user is authorised to see (open inventory if anon, full per-company union if logged in) | `_scope == "all"` → `_viewer_company_id` returns `None` → `filter_for_company` no-ops |
| 4 | "My company" narrows to items the user's company can see | `_scope == "my-company"` → viewer_co = user.company_id → tenant filter active |
| 5 | "Mine" narrows to items the user submitted | `_scope == "mine"` → category route filters by `store.get_metadata(...).submitted_by == user.email` |
| 6 | Sign out is one click | Dropdown on the avatar → `/auth/logout` clears the JWT cookie |

## Resolvers (all in `app/routers/library.py`)

```python
def _session_user(request) -> tenancy.User | None
def _scope(request, session_user) -> "all" | "my-company" | "mine"
def _viewer_company_id(session_user, scope) -> str | None
```

These three functions are the entire identity surface — every other library route can opt in by calling them and passing the result through `_ctx`.

## Context fields surfaced to templates

- `current_session_user` — the resolved `tenancy.User` or `None`
- `scope` — the resolved string
- `viewer_company_id` — the company_id used for filtering (or `None`)

## Files

- `app/routers/library.py` — resolvers + `_ctx` plumbing + `scope=mine` filter on `library_category`
- `app/templates/library/_library_base.html` — identity badge + scope switcher + sign-in/out menu
- `app/routers/auth.py` — `/auth/login`, `/auth/logout`, `/auth/whoami` ([Auth 2])

## Tests

`tests/execution/products/test_library_filter.py` — 7 resolver tests:
- anon defaults to scope=all
- logged-in defaults to scope=my-company
- explicit ?scope= overrides default
- unknown ?scope= falls back to default
- viewer_company_id is None for anon
- viewer_company_id is None for scope=all (even when logged in)
- viewer_company_id == user.company_id for my-company / mine

## Trade-offs / deferred

- "Mine" filter currently iterates store.get_metadata per item. For categories with hundreds of items this is O(n). If it becomes hot, an index of `submitted_by → asset_ids` can be added — out of scope.
- Switching scope does a full-page reload (link, not JS). Acceptable for v1; if/when the team adopts htmx, swap to a partial.
- Sign-in button only shows Google. Microsoft / GitHub deferred until a tenant requests it.
- Avatar is initials-only — Google profile pictures deferred to avoid third-party image hotlinking.
