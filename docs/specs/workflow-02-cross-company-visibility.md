# [Workflow 2] Cross-company visibility + follow-author

**Ticket:** Basecamp [9956731127](https://app.basecamp.com/3945211/buckets/7463955/todos/9956731127) · due 2026-07-01
**Status:** Shipped
**Depends on:** [Workflow 1] ✅, [Auth 2] ✅, [Library 1+2] ✅

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Same-company: every approved item appears in every same-tenant user's library, badged + searchable | Already enforced by `filter_for_company` (Library 1). Tests: `test_shared_public_item_appears_in_other_tenant_library`, `test_same_co_item_does_not_leak_across_tenants` |
| 2 | Cross-company opt-in: admin can mark Colaberry's approved items as `shared-public` and they appear in other tenants' libraries | New `upgrade_item_visibility()` helper + `POST /admin/{company}/queue/{item}/share` route. Guarded by `Company.allow_cross_company_shares` flag (off by default) |
| 3 | Cross-company allowlist: `visibility=shared-with-allowlist` | Already in [Auth 1] data model; now reachable via `upgrade_item_visibility(new_visibility="shared-with-allowlist", shared_with=[...])` |
| 4 | Provenance: "Authored by {user @ company}" + "Approved by {Company X, Company Y}" on detail view | `library_asset_detail` route builds `provenance` dict; new "🧾 Provenance" card in `asset.html` |
| 5 | "Follow this author" affordance | `follow_author` / `unfollow_author` / `is_following` / `followers_of` in `tenancy.py`. POST `/library/follow` route. Follow button in `asset.html` provenance card |
| 6 | Default for new customer companies: same-company-only; sharing publicly is explicit opt-in in admin settings | `Company.allow_cross_company_shares=False` default. `Company.allow_inbound_follows=True` default (less risky). Per-company toggle UI in `company_detail.html` |

## Authorization model

| Action | Same-co viewer | Cross-co viewer |
|---|---|---|
| See an item with `visibility=same-company-only` | ✓ if owner's tenant | ✗ |
| See an item with `visibility=shared-public` | ✓ | ✓ |
| See an item with `visibility=shared-with-allowlist` | ✓ if in allowlist | ✓ if in allowlist |
| Follow an author | ✓ always (same-company) | ✓ only if author's company has `allow_inbound_follows=True` |
| Upgrade an item to `shared-public` | ✓ if admin AND company has `allow_cross_company_shares=True` | ✗ |

## Files shipped

| File | Change |
|---|---|
| `execution/products/library/tenancy.py` | Added `Company.allow_cross_company_shares` + `Company.allow_inbound_follows`; new helpers: `can_publish_cross_company`, `can_follow_author`, `follow_author`, `unfollow_author`, `is_following`, `followers_of`, `upgrade_item_visibility`; `FollowEvent` dataclass + `follows.jsonl` append-only log |
| `app/routers/library.py` | Asset detail now builds `provenance` + `follow_state` dicts. New `POST /library/follow` route |
| `app/routers/admin.py` | New `POST /admin/companies/{slug}/sharing` (policy toggles) + `POST /admin/{company}/queue/{item}/share` (bulk visibility upgrade) |
| `app/templates/library/asset.html` | New "🧾 Provenance" card with author + approving-companies list + visibility badges + follow button |
| `app/templates/admin/company_detail.html` | New "🌐 Sharing policy" card with the two toggles |

## Defaults (intentional)

- `allow_cross_company_shares = False` — conservative. New tenants cannot accidentally publish to the world; the company admin must explicitly opt in via Settings.
- `allow_inbound_follows = True` — less risky. Following is read-only signal, no data exfiltration. Toggle to False if a tenant wants total opacity to outsiders.

## Tests

`tests/execution/products/test_workflow_visibility.py` — 16 tests:

- 2 on new-tenant defaults
- 4 on `upgrade_item_visibility` (allowed/blocked/unapproved/downgrade-always-ok)
- 3 on end-to-end visibility via `filter_for_company`
- 3 on `can_follow_author` permission rules
- 4 on follow event-log behaviour (collapse, case-insensitive, followers_of)

## Trade-offs / deferred

- **Author opt-out of follows** — currently a follow request always succeeds when the company allows inbound follows. Per-user "don't follow me" deferred to a [Workflow 2.1] follow-up.
- **Follow-publishes notifications** — `follow_author` records the follow + emits a one-time "X followed you" event. Auto-notification of followers when the author ships a new approved item is not yet wired (the hook would go in `decide_review` when status=approved). Easy follow-up.
- **Cross-company audit visibility** — when Colaberry promotes an item to `shared-public`, Patriot sees it but currently has no in-app indication of WHEN it appeared in their feed. A "New cross-tenant publications this week" digest section would help; deferred to Infra 4.
- **Bulk upgrade UI in the moderation queue** — the route exists (`POST /{company}/queue/{item}/share`) but the queue template hasn't been wired with a dropdown to call it inline. Authors can call it directly via API or from the company_detail page; UI polish for the queue is a small follow-up.
