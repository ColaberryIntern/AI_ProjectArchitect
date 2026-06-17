# Directive: My Day Basecamp Sync

## Purpose

Document the contract for how Basecamp todos land in `/my-day/` for each operator: the four triggers that start a sync, how mutual exclusion works, what counts as "fresh enough", how the UI tells the user a sync is in progress, and how to debug a stuck or stale sync.

This directive is the source of truth for the sync chain. Any change to a piece of code referenced here should update the corresponding section so the document and the code can never disagree silently.

## Background

`/my-day/` is a per-operator view of their Basecamp todos: assigned tasks, due-watching items, unassigned-but-active tasks they should claim, and watching items others own on the same projects. The data is mirrored into a local file-backed store (`output/ops/<email>/todos.json`) so the page can render without round-tripping to Basecamp's API on every request.

The sync chain is the code path that keeps that local store consistent with Basecamp. Historically the chain had four independent triggers with overlapping but uncoordinated locking, which caused [the 2026-06-04 stale-task incident](../docs/audits/2026-06-04-stale-task-sync-audit.md) and was systematically refactored in Phase 2/3 of the [2026-06-09 sync chain audit](../docs/audits/2026-06-09-my-day-sync-chain-audit.md).

## What the sync does

For one operator, one sync run:

1. Resolve the operator's Basecamp OAuth token via `tokens.get_user_token` (vault-OAuth preferred, vault-legacy bare-string fallback, CCPP shared token as last resort).
2. Discover the projects the token can see via `/projects.json` (BC API v3).
3. Add any extra buckets configured on the operator's tenancy record (`bc_extra_buckets`) plus `ali_legacy_bucket=7463955` when Ali is the operator (Phase A escape hatch).
4. For each project: walk the dock → todoset → todolists → active + completed todos. **For each todolist, also descend into its todo *groups*** (`/todolists/{id}/groups.json`) and walk each group's todos — a grouped todo is NOT returned by the parent list's `/todos.json`. Grouped todos are attributed to a `"<list>: <group>"` name. (Without this, every task filed under a "Week 01" style group is invisible — the 2026-06-17 Swati incident, where a Curriculum list with an empty top level hid 48 assigned tasks across 12 week-groups.)
5. For each todo, classify whether it belongs in this operator's queue (see *Classification* below). Drop anything not relevant.
6. Apply the freshness filter (see *Freshness window* below). Drop anything stale with no future due date.
7. Upsert the survivors into the local store (preserving operator-local fields — see *Store invariants*).
8. **Reconcile disappeared rows.** For each bucket walked without error, mark any locally-`active` row BC no longer returns in that bucket's active set as `completed`/`archived`, confirmed by a direct per-row GET (`sync._reconcile_walked_buckets`; see *Store invariants* #4). This catches completions the walk's best-effort completed-fetch missed, without waiting for the daily purge.
9. Persist `state.json` with `last_sync_at`, `last_sync_status`, `last_sync_error`, counts.

A sync that errors on one or more projects (but completes at least one) records `last_sync_status = "partial"`. A sync that fails before any walk (token missing, BC user id missing) records `last_sync_status = "failed"`. Otherwise `last_sync_status = "ok"`.

## The four triggers

Every sync starts from one of these four code paths. All four funnel through `SyncCoordinator.try_start_sync` so only one sync per operator runs at a time.

| # | Trigger | Code path | Behavior |
|---|---|---|---|
| 1 | Manual ↻ Sync button | `POST /my-day/sync` → spawns thread → `sync.pull_todos_for_user` | Redirects immediately with `?sync_started=1`; banner polls real status |
| 2 | Page load (natural-flow) | `GET /my-day/` → `_natural_flow_sync` → background thread → `sync.pull_todos_for_user` | Non-blocking; only fires when stale or last status ≠ `ok` |
| 3 | Mark Done | `POST /my-day/todo/{id}/complete` → `_sync_with_budget` → `sync.pull_todos_for_user` | Blocks up to 6 seconds; targeted re-sync of the project Mark Done touched lands first via `pull_todos_for_project` |
| 4 | 5-min APScheduler cron | `execution/products/ops/scheduler._sync_all_users` → for each user with a vault token → `sync.pull_todos_for_user` | Background; backstop when none of the other triggers have fired |

If a trigger fires while another sync is already running for that operator, `try_start_sync` returns `False` and the thread short-circuits with `{"status": "already_running"}`. The user-visible banner still polls and reloads when the in-flight sync completes.

## Mutual exclusion: `SyncCoordinator`

Lives at `execution/products/ops/sync_coordinator.py`. One process-wide singleton via `get_coordinator()`.

Contract:

| API | Behavior |
|---|---|
| `try_start_sync(email) -> bool` | Atomic check-then-set under one `threading.Lock`. Returns `True` exactly once per active slot. Caller MUST call `finish_sync` in a `finally` block. |
| `finish_sync(email)` | Idempotent slot release. Safe to call even if `try_start_sync` returned `False`. |
| `is_sync_in_flight(email)` | Non-blocking check. Honors TTL — an expired lock reads as not-in-flight. |
| `in_flight_age_seconds(email)` | Diagnostic for `/my-day/_health`. Returns `None` when no sync is in flight or the lock has expired. |
| `wait_for_sync(email, timeout)` | Block up to `timeout` seconds for any in-flight sync to clear. Used by Mark Done's 6-second budget. |

**TTL backstop.** A sync slot older than `LOCK_TTL_SECONDS_DEFAULT` (120 seconds) is treated as crashed/abandoned and is silently replaced by the next caller. Without this, a SIGKILL'd worker would strand the operator indefinitely. Real syncs run ~30-60s wall clock; 120s leaves comfortable margin without exposing genuinely-slow syncs to pre-emption.

**Per-process scope.** Each uvicorn worker has its own `SyncCoordinator`. The current prod deploy uses a single worker; scaling to multi-worker would require moving slot state to Redis (currently flagged as audit M2, not yet addressed).

## Store invariants

`execution/products/ops/store.py` owns the local file-backed mirror. Concurrency is enforced internally via a per-user `threading.Lock`, so callers do not need to acquire any lock to call store APIs.

Invariants the sync engine relies on:

1. **Read-modify-write atomicity.** Every mutation (`upsert_todos`, `upsert_projects`, `update_todo`, `save_state`) holds the per-user write lock for its entire load-merge-save span. Two concurrent writers cannot lose updates to each other at the file level.

2. **Local-only fields survive re-sync.** When `upsert_todos` finds an existing local row, it preserves: `is_dismissed`, `dismissed_at`, `dismissed_by`, `dismissed_reason`, `urgency_score`, `category`, `score_breakdown`. Operator overrides survive the next Basecamp walk.

3. **Operator project overrides survive re-sync.** `upsert_projects` preserves `is_managed` and `weight` across re-walks so an operator's "un-manage this project" choice doesn't get reverted every 5 minutes.

4. **The walk never deletes; reconciliation mirrors BC.** `upsert_todos` is upsert-only — items present locally but absent in a fresh BC walk are kept (a task moved between projects may briefly appear in both; subsequent walks converge). Down-grading a row that has left BC's open set (completed/trashed/deleted) is the job of two reconcilers that share one per-row checker, `purge.reconcile_active_row` (re-fetch the row, mirror BC: completed→`completed`, gone/trashed→`archived`, still-active→leave):
   - **Per-sync** (`sync._reconcile_walked_buckets`, 2026-06-17): the walk records the set of todo ids BC returned in each fully-walked bucket's *active* fetch. Right after the upsert, any locally-`active` row in one of those buckets whose id is **not** in that set is confirmed via a direct GET and reconciled. This is what makes a completion the walk's best-effort completed-fetch missed clear on the **next sync** instead of waiting for the daily purge — the 2026-06-17 "Integrate website % tracking" incident, and the still-open 2026-06-04 Press-Mike question. Only buckets walked **without error** are eligible (a bucket that errored or was budget-deferred is never reconciled on incomplete data), and the confirm GET prevents false positives (a row beyond the active-fetch page cap that's still active in BC is left alone). Bounded by `OPS_WALK_RECONCILE_CAP` (60) and the overall sync budget; kill-switch `OPS_WALK_RECONCILE=0`.
   - **Hourly/24h backstop** (`purge.purge_stale_active_rows`, see "Reconciliation purge" below): re-checks **every** active row, catching the long tail the per-sync pass doesn't reach — rows in buckets that keep erroring, completions outside the freshness window, and lists deleted while the walk is failing. Without either, a deleted Basecamp todolist (e.g. the old "approval queue") would leave its rows rendering as `active` forever, because the walk simply stops returning them.

5. **Atomic file writes.** Every write uses `tempfile.mkstemp` + `Path.replace` so a process crash mid-write can never produce a partially-written JSON file. The reader's `corrupt JSON → return empty` fallback is a defense-in-depth, not the primary protection.

## Classification: who gets which todo?

`sync._classify_for_user(todo, bc_user_id)` returns one of four `inclusion_reason` values, or `None` to drop the todo:

| Reason | Rule | Why |
|---|---|---|
| `assigned` | The user's BC id OR the CB System clone (37708014) is in the assignees list | These are tasks the operator (or their AI clone) is explicitly on the hook for |
| `due` | Not assigned to the user, but has a future due date in a project the token sees | The operator is watching for the due date even though someone else owns the task |
| `unassigned` | Empty assignees list + recent activity within freshness window | Someone needs to claim this; it's actively being discussed |
| `watching` | Assigned to someone else + recent activity within freshness window | Cross-team visibility — the operator follows because their AI clone is on the project |
| `None` (drop) | Anything else | Not enough signal to surface |

The CB System clone id (37708014) is hardcoded in `_classify_for_user`. Removing or changing this literal will silently drop every task assigned to the CB System clone — verify against `tests/execution/products/test_ops_sync.py::test_cb_system_clone_returns_assigned` before touching.

### Which `bc_user_id` the classifier uses (self-heal, 2026-06-16)

The classifier does **not** blindly trust the cached `User.bc_user_id`. The id that todos assigned to an operator actually carry is their **account-scoped person id** (from `/my/profile.json`) — that is ground truth. `bc_user_id` is only a cache of it. So `_pull_todos_for_user_inner` resolves the live account-person id (`token_person_id`) once per run and classifies against **that**, falling back to the cached `bc_user_id` only if the profile call fails. When the cache has drifted from the live id, it is **healed** (written back via `tenancy.upsert_user`) so subsequent offline reads are correct.

**Why this exists:** the self-serve connect flow used to cache the wrong value — the **Launchpad identity id** from `/authorization.json` instead of the account-person id (see the id-namespace gotcha below). The two never match, so a correctly-connected human matched **zero** assignees and saw none of their own tasks ("assigned" → 0; everything fell to the `due`/`watching` noise tiers). This was the **2026-06-16 Swati incident** (466 todos in queue, 0 assigned). Two fixes landed together:
- **Source:** `basecamp_connect.py` now stores the account-person id via `_resolve_account_person_id` (`/my/profile.json`), not `granted_id`.
- **Net:** sync classifies against / heals to the live account-person id, so even already-broken records self-correct on the next run.

**Clone exception:** the heal is **skipped** for AI-clone connections (Ali's CB System, anyone's `+ai` persona — detected via `basecamp_provisioning.is_ai_account_for_user`). The clone-by-design model deliberately authenticates as the clone while classifying against the **human's** id; overwriting the human id with the clone's id would break it. A genuine `+ai` mis-connect is surfaced by `connection_identity_suspect` (reconnect as human), not silently cached. Regression guards: `test_ops_sync.py::TestBcUserIdSelfHeal` (3 cases) and `test_basecamp_ai_detection.py::test_resolve_account_person_id_*`.

## Freshness window

Two environment-tunable knobs:

| Env var | Default | Effect |
|---|---|---|
| `OPS_FRESHNESS_DAYS` | 30 | A todo whose `updated_at` is older than this AND has no future due date is dropped at sync time |
| `OPS_PROJECT_FRESHNESS_DAYS` | 30 | Vestigial — `pull_todos_for_user` no longer skips projects by their own `updated_at`. Kept in code for backward compat. |

The freshness window deliberately excludes quietly-completed tasks (>30 days, no future due) from sync. That created audit M6 — zombie `active` rows for tasks completed outside the window — which the reconciliation purge below now retires.

## Reconciliation purge

`execution/products/ops/purge.py` runs out-of-band of the walk as the **24h backstop** reconciler. It is no longer the *only* place rows get down-graded — the per-sync `sync._reconcile_walked_buckets` (store invariant #4) handles the common case promptly; the purge catches the long tail. Both call the same per-row checker, `purge.reconcile_active_row`, so "gone from the queue" means the same thing in both. The scheduler fires `_purge_all_users` hourly (`OPS_PURGE_CRON_MINUTES=60`); each user is then gated by `is_purge_due` to run at most once per `OPS_PURGE_INTERVAL_HOURS` (24).

`purge_stale_active_rows` re-checks **every** active, non-dismissed local row (not just stale ones — see below) by re-fetching it one-at-a-time from BC, and mirrors BC's truth:

| BC response | Action |
|---|---|
| `completed: true` | Local row → `completed` with completion metadata (retires the M6 zombie) |
| `400`/`404` (None) | Task or its whole todolist was deleted → local row → `archived`, so the report stops surfacing it |
| `completed: false` | Left alone; re-checked next sweep |

Dismissed rows are never touched (a deliberate operator signal). Rows are checked oldest-`bc_updated_at`-first and bounded by `OPS_PURGE_CAP_PER_USER` (50) per run; the unswept tail rolls over to the next sweep.

**Why it re-checks all active rows, not just the stale tail (Option 2, 2026-06-10):** the original M6 sweep only looked at rows already past the 30-day freshness cutoff. A todolist deleted in BC *while its rows were still fresh* fell into a blind spot — the walk stopped returning it, and the cutoff-gated purge wouldn't look at it until it aged past 30 days. Dropping the cutoff gate closes that gap, at the cost of one BC GET per active row per 24h (bounded by the cap). Regression guard: `test_ops_purge.py::test_fresh_deleted_list_row_archived`.

An operator who wants a row gone immediately (rather than waiting for the next sweep) can still Mark Done or Skip it locally.

## Freshness gate: when does the page-load sync fire?

`_natural_flow_sync` (in `app/routers/my_day.py`) decides whether to spawn a background sync on every `GET /my-day/`. The gate requires BOTH:

- `age = now - state.last_sync_at < 90 seconds`, AND
- `state.last_sync_status == "ok"` (not `"partial"` and not `"failed"`).

If both are true, no sync is kicked. Otherwise a background sync starts; the page renders against the current store (which may be slightly stale) and the next interaction picks up the fresh data.

The `last_sync_status == "ok"` requirement is the audit H4 fix. Before this, a partial sync that errored on the operator's most-active project still bumped `last_sync_at`, so the gate said "fresh enough" and the operator sat on stale data for that project until the next page load. Now `partial` and `failed` are treated as retry signals regardless of age.

`_natural_flow_sync` NEVER blocks. It always returns immediately. The 30-second page freeze the natural-flow refactor was created to remove must not be re-introduced.

## UI: the in-progress banner

When the user clicks the manual ↻ Sync button, the handler:

1. Spawns a background sync thread (which goes through `SyncCoordinator` and may no-op via `already_running`).
2. Redirects to `/my-day/?<filters>&sync_started=1`.

The home page renders a blue banner with a spinner and the message "Sync started. Pulling latest from Basecamp in the background. This page will refresh automatically in 25s."

The JS embedded in the banner does NOT trust the 25s countdown. It polls `GET /my-day/sync-status.json` every 2.5 seconds and reloads when the server tells it the sync truly finished:

| Server says | JS does |
|---|---|
| `in_flight: true` (under 25s elapsed) | Tick the visible countdown |
| `in_flight: true` (over 25s elapsed) | Switch banner copy to "Sync still running… (Ns elapsed)" |
| `in_flight: false` AND saw-in-flight=true previously | Reload (sync completed) |
| `in_flight: false` AND `last_sync_at` advanced past baseline | Reload (sync was so fast we missed seeing it in flight) |
| `in_flight: false` for >8s with no state advance | Reload (likely `already_running` short-circuit; nothing to wait for) |
| Anything beyond 60s | Hard reload (timeout, give up waiting) |

Filter preservation: the polling URL builder strips `sync_started=1` and preserves every other query parameter, so the operator lands back on the same filtered view they triggered the sync from. The Sync `<form>` itself encodes the active filters as hidden inputs so the POST handler can reconstruct them; the Referer query string is a defensive fallback if the form body is empty (proxy strip, direct curl, stale template).

## How to debug a stuck or stale sync

In order of cost/effort:

1. **`/my-day/_health` (admin only).** JSON or HTML. Shows: both scheduler states with next-run times, per-user `last_sync_at`/`last_sync_status`/`last_sync_error`, the silent-error ring buffer (last 50 errors), Pilot dash delivery config. This is the first thing to check.

2. **Container logs.** `ssh root@95.216.199.47 'docker logs ai-project-architect-app-1 2>&1 | tail -100'`. Look for `[lifespan] ops sync scheduler started` (must appear once on boot) and `ops_sync cron:` lines summarizing each cron run.

3. **The per-operator state file.** `cat /opt/ai-project-architect/output/ops/<email>/state.json` on the prod box gives the raw `last_sync_at`, `last_sync_status`, `last_sync_error`, counts.

4. **Force a sync from a Python shell.** On the prod box: `docker exec -it ai-project-architect-app-1 python -c "from execution.products.ops import sync; print(sync.pull_todos_for_user('ali@colaberry.com'))"`. Bypasses the UI and prints the result dict directly.

5. **Inspect the BC API response.** When sync result includes errors mentioning a specific bucket id, GET that bucket directly with the operator's token via the BC API docs to see if the bucket is reachable, the response is malformed, or BC is returning 429/500.

Common patterns:

- **`last_sync_status: "failed"` and `last_sync_error: "token_missing"`.** The operator has no Basecamp OAuth grant. Send them to `/profile/connect-basecamp`.
- **`last_sync_status: "failed"` and `last_sync_error: "bc_user_id_missing"`.** Token present but the BC user id couldn't be resolved. Usually a re-connect fixes it.
- **`last_sync_status: "partial"` with errors in `recent_errors`.** Some specific bucket(s) errored. The rest of the data is fresh; the errored buckets will retry on the next sync.
- **`last_sync_status: "partial"` with `project_forbidden` errors (HTTP 403).** A *membership gap*, not a transient failure. The Basecamp identity the operator's token authenticates as (their AI clone — the OAuth grant resolved by `tokens.get_user_token`, see *Inputs and outputs*) can list the project in `/projects.json` but is not a member, so reading its todos returns 403. The fix is in Basecamp, not in code: add that clone identity to the project (**People → Add people**). The error message names the identity and the remediation; `result["forbidden_buckets"]` lists the affected bucket ids. **Do not "fix" this by swallowing the 403** — that would silently freeze the project's data at its last-synced state (a closed-in-BC task would render `overdue` forever). It stays `partial` on purpose until access is granted, after which the next walk clears it.
- **`connection_identity_suspect: true` (or a `connection_identity_suspect` ring-buffer entry).** The wrong-account self-annealing guard fired: the token's **account-scoped person id** (from `/my/profile.json`) differs from the id the classifier expects (`get_user_bc_id`), AND projects 403'd. That combination means the operator's BC OAuth grant is bound to the **wrong Basecamp account**. The banner leads with one root-cause line naming both ids; the fix is to **reconnect** (`/profile/connect-basecamp`) as the correct account.
  - **Critical id-namespace gotcha (2026-06-10).** Basecamp gives one human TWO ids: a global **Launchpad identity id** (in the OAuth grant metadata) and a per-**account person id** (returned by `/my/profile.json`, and what project memberships + the classifier use). They are different namespaces and never equal for the same person — e.g. Ali is Launchpad `16988292` but account-person `17454835`. The guard MUST compare the *account* person id, not the Launchpad id; an earlier version compared the Launchpad id and false-flagged every healthy connection. The guard is also gated on 403s being present so it can never fire on the legitimate AI-clone model (a granted clone reads cleanly with zero forbidden buckets). Regression guards: `test_ops_sync.py::test_widespread_403_with_wrong_account_flags_connection_suspect` and `::test_same_person_two_id_namespaces_not_flagged`.
- **`in_flight_age_seconds > 90` in `/my-day/_health`.** A sync is running unusually long — probably BC is slow or returning 429 frequently. The TTL backstop will free the slot at 120s.
- **`already_running` returned but no UI banner.** The operator's sync slot is held by another trigger (likely the cron). The page will refresh and show fresh data once it completes.

## Inputs and outputs

**Inputs:**
- Operator's Basecamp OAuth grant (vault entry `basecamp_ai_clone`)
- Operator's tenancy record (`bc_user_id`, `bc_extra_buckets`)
- Basecamp API at `https://3.basecampapi.com/{BC_ACCOUNT_ID}` (default 3945211)
- Env vars: `OPS_FRESHNESS_DAYS`, `OPS_PROJECT_FRESHNESS_DAYS`, `OPS_HTTP_TIMEOUT`, `OPS_HTTP_THROTTLE_SECONDS`, `OPS_MAX_RETRY_AFTER`, `OPS_SYNC_INTERVAL_MINUTES`, `OPS_WALK_RECONCILE` (default `1`; set `0` to disable the per-sync disappeared-row reconciliation), `OPS_WALK_RECONCILE_CAP` (default `60`; max confirm-GETs per sync)

**Outputs:**
- `output/ops/<email>/todos.json` — local mirror, file-locked per user
- `output/ops/<email>/projects.json` — operator's discovered projects + their `is_managed`/`weight` overrides
- `output/ops/<email>/state.json` — `last_sync_at`, `last_sync_status`, `last_sync_error`, counts
- Silent-error ring buffer (in-process, last 50) surfaced via `/my-day/_health`

## Edge cases

- **Token rotation mid-sync.** The walker holds one access token for the duration of the walk. If the token expires during a long sync, individual `_bc_get` calls start returning 401 and propagate as project-walk errors; the sync ends `partial`. The natural-flow gate retries on the next page load because `status != "ok"`.
- **403 vs 401 — authorization vs authentication.** These look similar but mean opposite things and have opposite fixes. A `401` means the *token* is bad (expired/revoked) — it hits **every** call, including `/projects.json`, so discovery itself fails and nothing syncs; fix is a token re-grant. A `403` means the token is *valid* but the identity it represents lacks membership on a **specific** project — discovery succeeds and only the un-granted bucket(s) error; fix is a Basecamp People→Add grant for that identity. `sync.py` special-cases the 403 branch in the project-walk loop to emit the actionable message and populate `forbidden_buckets`; a 401 falls through the generic branch. Regression guard: `test_ops_sync.py::test_403_forbidden_bucket_is_actionable_not_generic`.
- **BC API 429.** `_bc_get` honors `Retry-After` (capped at `MAX_RETRY_AFTER`=30s) and retries once. A second 429 propagates. Tests in `test_ops_sync.py::TestBcGet` lock in this contract.
- **Operator with zero BC projects visible.** `discover_projects` returns `[]`. The sync runs to completion with no todos walked. State is `ok`. No error.
- **Concurrent Mark Done + cron sync.** Both go through `SyncCoordinator`, so only one proceeds; the other returns `already_running`. The store-level lock additionally serializes the file writes if for some reason both reach the upsert path.
- **`sync_started=1` URL shared between tabs.** Each tab polls independently. Both reload when their poll sees the sync complete. Harmless.

## Verification

Code in this sync chain is covered by:

- `tests/execution/products/test_ops_sync.py` — classifier, freshness gate, 429 retry, paginator, `pull_todos_for_user` / `pull_todos_for_project` orchestration, coordinator integration, and the per-sync disappeared-row reconciliation (`TestWalkDisappearedReconciliation`: completion missed by the walk is reconciled; still-active rows and errored/budget-deferred buckets are left untouched; dismissed rows skipped; kill-switch)
- `tests/execution/products/test_ops_sync_coordinator.py` — atomic single-flight, TTL, wait_for_sync semantics
- `tests/execution/products/test_ops_store.py` — preserve-local-fields, concurrent-writer safety
- `tests/app/test_my_day_sync_redirect.py` — POST /sync filter preservation
- `tests/app/test_my_day_natural_flow_sync.py` — H4 gate semantics + bg-thread error logging
- `tests/app/test_my_day_sync_status.py` — polling endpoint contract
- `tests/app/test_my_day_health.py` — health snapshot shape + ring buffer

Before changing anything in this chain, run all six suites and add a test for the new behavior. CLAUDE.md is strict about this: deterministic-execution + test-first verification.

## Related

- [docs/audits/2026-06-04-stale-task-sync-audit.md](../docs/audits/2026-06-04-stale-task-sync-audit.md) — first stale-task incident
- [docs/audits/2026-06-09-my-day-sync-chain-audit.md](../docs/audits/2026-06-09-my-day-sync-chain-audit.md) — comprehensive audit that drove Phases 2 and 3
- [reference_deployment.md](../../../../.claude/projects/c--Users-ali-m-OneDrive-Business-Colaberry-Novedea-AI-Projects-AI-Project-Architect---Build-Companion/memory/reference_deployment.md) — prod deploy procedure (memory)
