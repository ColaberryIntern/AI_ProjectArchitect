# [Workflow 1] Per-company publish workflow + moderation queue

**Ticket:** Basecamp [9956731113](https://app.basecamp.com/3945211/buckets/7463955/todos/9956731113) · due 2026-07-01
**Status:** Shipped
**Depends on:** [Auth 1] ✅ (item_approvals model), [Infra 1] ✅ (approval classification)

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | State machine: `draft → submitted → under_review → approved/rejected/changes_requested` | `tenancy.SUBMISSION_TRANSITIONS` graph + `can_transition()` guard + `submit_for_review` / `claim_for_review` / `decide_review` helpers |
| 2 | Submissions land in author's company queue, not a shared queue | `queue_for_company(company_id)` filters by `ItemApproval.company_id`. Per-company isolation enforced at the data layer |
| 3 | Moderation queue view at `/admin/{company}/queue` | `admin.py::moderation_queue` route + `admin/moderation_queue.html` template — claim/approve/reject/changes inline |
| 4 | Reviewer role config per company (Ali for Colaberry v1) | `tenancy.can_review(user)` — `admin` role gates v1. Category-aware sub-approvers (`config/library_approvers.json`) wire in as a follow-up |
| 5 | On approve: `ItemApproval` row written; sync fires; author notified | `decide_review(decision='approved')` writes row; [Infra 1] auto-sync hook fires per approval; `notifications.notify_decision()` pings author |
| 6 | On reject/changes_requested: comment thread back to author | `decide_review(notes=...)` captures rationale on the row; `notifications.notify_decision()` delivers it |
| 7 | Notifications: in-library bell + daily email digest (not per-event) | Bell counter in `_library_base.html` via `unread_count_for_user`; `render_daily_digest(company, date)` builds one HTML email per company per day |
| 8 | One item approved by multiple companies independently | Per-`(item, company)` join key is the unit. Test `test_one_item_approved_independently_by_two_companies` proves it |

## State machine

```
                           (author)
            ┌──────────────────────────────┐
            ↓                              │
   ┌──── draft ──submit──→ submitted ──claim──→ under_review
   │                          ↑                   │
   │                          │                   ├─ approve ──→ approved ──withdraw──→ withdrawn
   │     (resubmit) ──────────┤                   │
   │                          │                   ├─ reject  ──→ rejected ──┐
   │                          │                   │                          │
   │                          │                   └─ changes_requested ──┐  │
   │                          │                                            │  │
   │                          └────────────────────────────────────────────┘  │
   │                                                                          │
   └──────────────────────────────────────────────────────────────────────────┘
                              (author revises and resubmits)
```

All transitions are append-only — every state change writes a row to `output/library/_tenants/approval_transitions.jsonl` for audit. The "current" status lives on the `ItemApproval` row that `record_approval()` mutates in place.

## Files shipped

| File | Purpose |
|---|---|
| `execution/products/library/tenancy.py` | Extended `APPROVAL_STATUSES`, added `SUBMISSION_TRANSITIONS`, `can_transition`, `submit_for_review`, `claim_for_review`, `decide_review`, `queue_for_company`, `queue_counts`, `can_review`, `list_transitions` |
| `execution/products/library/notifications.py` | NEW — per-company JSONL inbox + `notify_submission` / `notify_decision` / `unread_for_user` / `mark_all_read` / `render_daily_digest` |
| `app/routers/admin.py` | NEW routes: `GET /admin/{company}/queue`, `POST /admin/{company}/queue/{item_id}/claim`, `POST /admin/{company}/queue/{item_id}/decide`, `POST /admin/{company}/queue/submit` |
| `app/templates/admin/moderation_queue.html` | NEW — queue table with inline claim/approve/reject/changes forms + transition history |
| `app/templates/admin/home.html` + `_admin_base.html` | Added queue link + sidebar nav |
| `app/routers/library.py` | Added `/library/notifications` GET + `/library/notifications/mark-read` POST. Extended `_ctx` with `bell_count`, `queue_count`, `is_reviewer` |
| `app/templates/library/_library_base.html` | Added 📥 reviewer queue badge + 🔔 unread-notification badge in header |
| `app/templates/library/notifications.html` | NEW — inbox view |

## Authorization model

- **Author** (any logged-in `contributor` in company X) can `submit_for_review` items into X's queue.
- **Reviewer** = `admin` role in company X — can claim + decide on X's queue.
- **Super-admin** (Colaberry-tenant admin) can review for ANY company (cross-tenant moderation, for support cases). Guarded in `_require_reviewer_for`.

## Tests

`tests/execution/products/test_workflow_publish.py` — 20 tests:

- State machine: 2 (legal + illegal transitions)
- submit_for_review: 3 (creates row, idempotent, resubmission cycle)
- claim/decide: 4 (claim, blocked re-claim, approve default visibility, approve shared-public)
- Queue isolation: 3 (per-company isolation, sorted, counts breakdown)
- Multi-company approval: 1 (independent approval rows)
- Authorization: 2 (admin gating, None handling)
- Notifications: 4 (fan-out, no-self, decision ping, mark-read)
- Daily digest: 1 (group by target)

## Trade-offs / deferred

- **Category-aware sub-approvers** — `config/library_approvers.json` has sales/tech delegation rules but Workflow 1 v1 treats every admin as a reviewer. To activate sub-approvers, extend `can_review(user, category)` to consult the JSON config.
- **SMTP send for daily digest** — `render_daily_digest()` writes HTML to disk. The actual send wires in once [Admin 2] credentials for Gmail or Mandrill are set. Stubbed safely.
- **In-app comment threads** — current model captures one note per transition. A multi-turn comment thread on a single review cycle would need a separate `review_comments` table — deferred.
- **Bell mark-read is "all or nothing"** — individual notification dismissal is a UX polish; v1 is "Mark all as read."
- **Drafts don't persist** — there's no `draft` autosave UI yet. Authors compose via the existing Submit form, which goes straight to `submitted`. The `draft` state in the machine is there for the future autosave flow.
