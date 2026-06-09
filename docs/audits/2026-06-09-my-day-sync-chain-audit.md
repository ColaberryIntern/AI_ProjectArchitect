# Audit — /my-day/ BC sync chain (end-to-end)

**Date:** 2026-06-09
**Requester:** Ali Muwwakkil
**Auditor:** Claude (read-only)
**Scope:** Full My Day Basecamp sync chain — manual `↻ Sync` button, 5-min auto-refresh cron, `?sync_started=1` banner + auto-reload, filter preservation across the round-trip.
**Mode:** Read-only — no code changes proposed yet. This is a findings report; remediation plan to follow on request.

---

## 1. Map of the chain

```
┌─────────────────────────────────────────────────────────────────────────┐
│ User triggers                                                           │
│                                                                         │
│   (a) Click ↻ Sync                                                      │
│       └─> POST /my-day/sync  (hidden inputs: view/tier/project/list/    │
│                               person; Referer fallback)                 │
│                                                                         │
│   (b) Page-load on /my-day/   (natural-flow sync)                       │
│                                                                         │
│   (c) Mark Done                                                         │
│       └─> POST /my-day/todo/{id}/complete  (inline _sync_with_budget,   │
│                                             ~6s budget)                 │
│                                                                         │
│   (d) APScheduler cron, every 5 min                                     │
│       └─> _sync_all_users → for each user w/ vault token,               │
│           pull_todos_for_user + scorer                                  │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Coordination layer                                                      │
│                                                                         │
│   _maybe_async_sync._locks  ←─── per-user dict[email→bool]              │
│                                  attached to a function attribute       │
│                                  (in-process, in-worker, no TTL)        │
│                                                                         │
│   (a),(b),(c) consult the lock. (d) does NOT.                           │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Backend pull   execution/products/ops/sync.py                           │
│                                                                         │
│   1. resolve token + bc_user_id (tokens.py)                             │
│   2. discover_projects() → /projects.json (paginated, ~50 for CB Sys)   │
│   3. + extra_buckets from user record + ali_legacy_bucket               │
│   4. for each project: dock → todoset → todolists → todos               │
│   5. _classify_for_user → assigned | due | unassigned | watching        │
│   6. _todo_is_relevant freshness filter (default 30 days)               │
│   7. throttled at 0.22s/call, 429 → Retry-After (capped 30s), 1 retry   │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Write layer  execution/products/ops/store.py                            │
│                                                                         │
│   upsert_todos: load → mutate dict by bc_id → atomic write              │
│   - keys: file-backed JSON, atomic via tempfile+replace                 │
│   - preserves local-only fields (is_dismissed, urgency_score, ...)      │
│   - does NOT delete rows absent from fresh (no purge)                   │
│   state.json: last_sync_at, last_sync_status, last_sync_error           │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ UI refresh                                                              │
│                                                                         │
│   POST /sync → 303 → /my-day/?…&sync_started=1                          │
│   home.html banner: 25s JS countdown → reload (URL minus sync_started)  │
│   filter preservation: form hidden inputs OR Referer fallback           │
│   status strip: "Synced X min ago · auto every 5 min" + stale flag      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Findings

Ordered by severity. Severity rubric:
- **High** — silent data corruption, lost work, or invisible failure modes the user can't diagnose
- **Medium** — incorrect behavior under specific conditions; UX-confusing; tests would catch it
- **Low** — code-hygiene, layer-mixing, future-risk

### High-severity

#### H1. Lock TTL gap — stale-lock can strand a user permanently
[my_day.py:295-301](app/routers/my_day.py#L295-L301), [my_day.py:744-755](app/routers/my_day.py#L744-L755), [my_day.py:879-892](app/routers/my_day.py#L879-L892)

`_locks[user_email] = True` is set before spawning the background thread; `finally: locks[user_email] = False` clears it. If the worker is SIGKILL'd mid-sync (OOM, `docker compose down`, deploy), the `finally` does **not** run because the process is gone — but if the same lock dict survives a restart, no… wait, locks are in-process, so a process restart resets them. **OK in that case.**

The real risk: a Python-level crash that bypasses `finally` (a `os._exit()` somewhere, or a C-extension segfault) leaves the lock True until process restart. There is also no timestamp on the lock — a sync hung on a slow BC call holds the lock indefinitely; the `?sync_started=1` banner reloads after 25s and silently returns early because "a sync is already running" — user sees the same stale data and assumes Sync is broken.

The 2026-06-04 audit explicitly flagged this failure-mode class (item 1: "Daemon thread killed by container restart / deploy") but the fix only added the natural-flow sync; it did not address lock TTL.

**Repro:** intentionally make `pull_todos_for_user` block on a `time.sleep(600)`. Click Sync → lock True → reload page → natural-flow sync skipped → status says "Synced X min ago" forever.

#### H2. Lock check is racy
[my_day.py:298-300](app/routers/my_day.py#L298-L300), [my_day.py:744-745](app/routers/my_day.py#L744-L745), [my_day.py:873-879](app/routers/my_day.py#L873-L879)

```python
if locks.get(user_email):
    return
locks[user_email] = True
```

Two concurrent requests can both observe `False` and both proceed to launch BC walks. The GIL covers each individual dict op but not the check-then-set sequence. Under FastAPI + ASGI this is more theoretical than in true multi-threaded code, but `_natural_flow_sync` runs inside the async handler thread pool and a double-click on Sync produces real concurrent dispatch. The next layer down (`store.upsert_todos`) is not safe under concurrent writes either — see H3.

**Fix shape (when remediation lands):** `threading.Lock` around the check-and-set, or replace the dict with a `dict[str, threading.Lock]` whose `acquire(blocking=False)` is the atomic primitive.

#### H3. `upsert_todos` is not safe under concurrent writers — lost-write race
[store.py:125-155](execution/products/ops/store.py#L125-L155)

```python
def upsert_todos(user_id, fresh):
    by_id = {t.bc_id: t for t in load_todos(user_id)}   # READ
    for f in fresh: ...                                  # MUTATE in memory
    save_todos(user_id, list(by_id.values()))            # WRITE
```

Scheduler-triggered `_sync_all_users` (runs without consulting `_locks`) can collide with a user-triggered sync or a Mark Done targeted sync. Concrete loss scenario:

1. Scheduler: load_todos → {1,2,3}, walking projects, hasn't written yet
2. User clicks Mark Done on bc_id=4 → `store.update_todo` writes {1,2,3,4-completed}
3. Scheduler finishes: writes its own dict, which never had bc_id=4 — Mark Done effectively reverted, status flips back to "active"

The data converges on the next pull (bc_id=4 will come back from BC marked completed), but **for a window of seconds the user sees the just-completed task back in their queue**, which matches the exact symptom class the 2026-06-04 audit was about. Note: this could be the unidentified cause behind "open item: why the 00:15 sync didn't catch the Press Mike completion" in that audit.

File-write atomicity (tempfile + replace) protects against partial-write corruption but does **not** protect against last-write-wins. Either:
- Coordinate all writers through a single per-user lock (cleanest), or
- Switch to a per-row update primitive that re-reads inside the lock

#### H4. Partial-sync masks true freshness — `last_sync_at` lies
[sync.py:446-451](execution/products/ops/sync.py#L446-L451), [my_day.py:323-344](app/routers/my_day.py#L323-L344)

`pull_todos_for_user` always sets `state.last_sync_at = _now_iso()` and `state.last_sync_status = "partial" if partial else "ok"`. The natural-flow sync reads only `last_sync_at` to decide freshness (`age < 90s → fresh`). **A partial sync that errored on the user's most-active project still updates last_sync_at**, suppressing the next inline sync attempt.

Consequence: if BC API flakes on one project repeatedly (timeout, 500), the user sits on stale data for that project indefinitely. The status strip says "Synced 30s ago" — green — when in reality the data they care about hasn't refreshed.

**Better signal:** track per-project `last_walked_at`, or gate the freshness check on `state.last_sync_status == "ok"` rather than time alone.

#### H5. Backend pull has zero unit tests — entire module uncovered
[execution/products/ops/sync.py](execution/products/ops/sync.py), [tests/app/](tests/app/)

Grep for tests touching `pull_todos_for_user`, `pull_todos_for_project`, `_natural_flow_sync`, `_kick_bg_full_sync`, `_classify_for_user`, `_walk_project_todos`, `_todo_is_relevant`, `_is_fresh`:

- `tests/app/test_my_day_sync_redirect.py` — tests **only** the filter-preservation redirect, mocks `pull_todos_for_user` to a no-op.
- `tests/app/test_my_day_health.py` — tests the health snapshot, not the sync engine.

That means:

- The 4-way classifier (`assigned | due | unassigned | watching`) — the thing that decides whether a BC task appears in the queue at all — has **no regression coverage**. The CB-System-clone special case (`37708014`) is one literal away from a silent regression that hides every assigned task.
- The freshness gate (`_is_fresh`, `_todo_is_relevant`) is uncovered. A timezone-handling regression would silently drop every BC task.
- Pagination (`_paginate`) and 429 retry (`_bc_get`) are uncovered.
- Lock interaction (`_natural_flow_sync` thresholds, `_kick_bg_full_sync` no-double-fire) is uncovered.

Per CLAUDE.md: *"All non-trivial execution logic must have unit tests"* and *"Unit tests must be fast, deterministic, run locally"*. This module is the most non-trivial code in the queue surface and it has the least coverage. **This is the single biggest gap.**

### Medium-severity

#### M1. Scheduler bypasses the per-user lock
[scheduler.py:42-65](execution/products/ops/scheduler.py#L42-L65), [my_day.py:740-755](app/routers/my_day.py#L740-L755)

`_sync_all_users` calls `sync.pull_todos_for_user(u.email)` directly with no lock consultation. So:
- T=0: cron fires, scheduler starts walking projects for Ali (~30s)
- T=2s: Ali clicks Sync — lock dict says False (scheduler doesn't set it), handler kicks ANOTHER full sync
- Two threads walking ~50 BC projects in parallel for the same user. BC 429s, both halve speed, one or both end "partial".

Compounds H3 (the race) and H1 (lock TTL) — they're all symptoms of the lack of a single SyncCoordinator that every trigger goes through.

#### M2. Multi-worker scheduler duplication risk
[main.py:65-76](app/main.py#L65-L76), [scheduler.py:76-103](execution/products/ops/scheduler.py#L76-L103)

`start_scheduler()` runs in the FastAPI lifespan. If the prod container is configured with `uvicorn --workers N` (or gunicorn with N workers), **each worker process starts its own scheduler instance**. With N=4, the 5-min sync cron fires 4× per user per cycle, multiplying BC API load and amplifying H3 (race).

This needs a quick check of the prod compose/Procfile/Dockerfile CMD line. If `--workers 1` (the FastAPI default), no current issue but future scaling will silently introduce it.

#### M3. `?sync_started=1` countdown can finish before the actual sync
[home.html:148-170](app/templates/my_day/home.html#L148-L170)

Hard-coded 25s countdown, but actual sync time observed up to 60s+ per the comments. After 25s, JS auto-reloads → page renders against still-stale store → user sees the same data they did before clicking Sync, with no indication the sync is still running in the background. Looks like a no-op.

Two cheap improvements (for the future remediation):
- Reload at 25s but check a sync-status endpoint; show "Sync still running…" if state hasn't advanced.
- Have the backend write a synthetic timestamp at sync-START as well as sync-END, so the UI can tell "in progress" from "stale".

#### M4. Status strip can't distinguish "fresh but partial" from "fresh and complete"
[_view_chrome.html:5-19](app/templates/my_day/_view_chrome.html#L5-L19)

`sync_stale` is only set when `'hr ago' in sync_relative or 'days ago' in sync_relative` — i.e., >1 hour. A partial sync that errored 30 seconds ago still reads "Synced 30s ago · auto every 5 min" with no warning. The "Sync failed" banner only shows when `last_sync_status == 'failed'`; `'partial'` is treated as success.

#### M5. Silent error swallowing in user-triggered BG threads
[my_day.py:302-310](app/routers/my_day.py#L302-L310), [my_day.py:747-755](app/routers/my_day.py#L747-L755), [my_day.py:961-967](app/routers/my_day.py#L961-L967)

The router wraps each background thread body in `try/except Exception: pass`. The 2026-06-04 audit explicitly called this out as a failure mode (item 2). The fix added `recent_errors` ring buffer in `sync.py` for per-project errors — but **only `_record_error` calls inside `_walk_project_todos` populate it**. Top-level exceptions from `tokens.get_user_token`, `scorer.score_all_todos`, or anything before the per-project loop disappear silently.

Concrete gap: if `tokens.get_user_token` raises (vault unreachable, secrets store rate-limited), the user-triggered sync silently no-ops, `last_sync_at` never advances, lock never clears (H1 again), and there is **no trace anywhere** — not in logs, not in `/my-day/_health`, not in state.json.

#### M6. Stale "active" rows for tasks completed outside the freshness window
[sync.py:132-143](execution/products/ops/sync.py#L132-L143), [store.py:125-155](execution/products/ops/store.py#L125-L155)

`_todo_is_relevant` requires recent activity OR future due date. A task completed 31 days ago with no future due never gets re-walked, so its local row stays at whatever status it had at last walk. If the prior walk classified it as active and BC then completed it on day 31, the local store keeps it active forever (until the user manually Marks Done or the task is touched in BC).

Low-frequency, but accumulates over time — every quiet completion ages out of the walker. The store has no purge or "older than N days, mark archived" sweep.

### Low-severity

#### L1. Locks-as-function-attribute is clever-not-clear
[my_day.py:295-296](app/routers/my_day.py#L295-L296)

`_maybe_async_sync._locks` is a hidden global stuck on a function object, mutated from 4+ call sites (`_kick_bg_full_sync`, `_natural_flow_sync`, `ops_sync`, `_sync_with_budget`). CLAUDE.md: *"Prefer systems over cleverness"*. A small `SyncCoordinator` class would (a) make the contract explicit, (b) make it testable, and (c) be where H1/H2 fixes naturally land.

#### L2. Layer-mixing in `app/routers/my_day.py`
CLAUDE.md Layer 2 (Orchestration / Claude) vs Layer 3 (Execution / scripts). The router contains real orchestration logic: threading, lock management, budget waits, BC HTTP calls for completion write-back. Per the project contract these belong in `/execution/products/ops/` as a deterministic, importable, testable interface. The router should be a thin HTTP layer.

This is a long-term refactor target, not urgent — but it's the architectural reason H5 (no tests) is so hard to fix incrementally: the threading lives in a router which is harder to unit-test than a plain module.

#### L3. `ALI_LEGACY_BUCKET` is hardcoded in two places
[my_day.py:34](app/routers/my_day.py#L34), [sync.py:402-403](execution/products/ops/sync.py#L402-L403)

The legacy bucket gets injected via the router (`ali_legacy_bucket=legacy`) AND would also get injected if it were added to the user's `bc_extra_buckets` (which is the real Phase-B path). Two paths for the same data → drift risk. Phase-B remediation should move Ali's record to `bc_extra_buckets` and delete the hardcoded constant + router branch.

#### L4. Token-missing failure UX is opaque
[_view_chrome.html:21-26](app/templates/my_day/_view_chrome.html#L21-L26)

`Sync failed: token_missing` is a string the operator must decode. The banner could detect that specific value and surface a link to `/profile/connect-basecamp` directly.

#### L5. `pull_todos_for_project` doesn't update `state.last_sync_at`
[sync.py:294-329](execution/products/ops/sync.py#L294-L329)

By design (it's a write-time touch-up, not a freshness signal). But a user whose only sync activity is Mark Done sees "Not synced yet" in the status strip even though their just-touched project is current. Minor confusion; either show "Last touched X min ago" or have the targeted sync write a separate `last_targeted_sync_at` field.

#### L6. No wall-clock budget on the full sync
[sync.py:349-465](execution/products/ops/sync.py#L349-L465)

If BC API hangs on every call, HTTP_TIMEOUT=20s × ~150 calls = ~50min worst-case before the sync gives up. Throttle 0.22s helps on the happy path but doesn't help under outage. An overall budget (e.g., 90s) with graceful exit would prevent runaway threads that lock H1 indefinitely.

#### L7. No E2E coverage for the sync banner + countdown + reload
[home.html:148-170](app/templates/my_day/home.html#L148-L170)

Per CLAUDE.md ("End-to-end & UI testing… browser automation tools, e.g. Playwright, are preferred"): no Playwright test covers the click-Sync → banner-appears → countdown-fires → reload-preserves-filters flow. The redirect test covers backend filter preservation but not the JS countdown or the URL-strip-and-reload behavior.

---

## 3. Test coverage gap matrix (per CLAUDE.md Layer 4)

| Component | Unit | Integration | E2E | Status |
|---|---|---|---|---|
| `_classify_for_user` (sync.py) | ❌ | ❌ | ❌ | **Critical gap (H5)** |
| `_is_fresh` / `_todo_is_relevant` (sync.py) | ❌ | ❌ | ❌ | **Critical gap (H5)** |
| `_paginate` / `_bc_get` 429 retry (sync.py) | ❌ | ❌ | ❌ | **Critical gap (H5)** |
| `_walk_project_todos` (sync.py) | ❌ | ❌ | ❌ | **Critical gap (H5)** |
| `pull_todos_for_user` end-to-end | ❌ | ❌ | ❌ | **Critical gap (H5)** |
| `pull_todos_for_project` end-to-end | ❌ | ❌ | ❌ | **Critical gap (H5)** |
| `_natural_flow_sync` 90s threshold | ❌ | ❌ | ❌ | Gap |
| `_kick_bg_full_sync` lock semantics | ❌ | ❌ | ❌ | Gap (H1/H2) |
| `_sync_with_budget` budget timeout | ❌ | ❌ | ❌ | Gap |
| `store.upsert_todos` preserve-local-fields | ❌ | ❌ | ❌ | Gap |
| `store.upsert_todos` concurrent writers | ❌ | ❌ | ❌ | Gap (H3) |
| POST /my-day/sync filter-preservation | ✅ | — | ❌ | **OK** |
| POST /my-day/sync Referer fallback | ✅ | — | ❌ | **OK** |
| `/my-day/_health` snapshot shape | ✅ | — | ❌ | **OK** |
| `recent_errors` ring buffer | ✅ | — | ❌ | **OK** |
| Sync banner + countdown + reload | ❌ | ❌ | ❌ | Gap (L7) |
| Filter preservation across JS reload | ❌ | ❌ | ❌ | Gap (L7) |
| Scheduler cron actually fires (prod) | ❌ | ❌ | ❌ | Gap |

---

## 4. Directive gap

Per CLAUDE.md the `/directives` layer is supposed to be the human-readable contract for what the system does. Searching the repo for a directive that describes the BC sync chain found none — only the `2026-06-04-stale-task-sync-audit.md` post-mortem, which documents *a fix* but not the steady-state contract.

A `directives/ops/bc-sync.md` should exist covering at minimum:
- The four triggers (manual, page-load, Mark Done, cron) and which paths they go through
- The lock semantics (currently undocumented)
- The 90s freshness gate, the 25s UI countdown, the 5-min cron interval
- The classification rules (`assigned | due | unassigned | watching`) and the CB-System-clone special case
- The freshness window (`OPS_FRESHNESS_DAYS=30`) and what it intentionally excludes
- Failure modes and how to detect them (link to `/my-day/_health`)

Without this directive, every change to the sync chain risks contradicting an unwritten contract.

---

## 5. Summary

| # | Severity | Class | One-liner |
|---|---|---|---|
| H1 | High | Reliability | Lock has no TTL — a hung sync can strand a user indefinitely |
| H2 | High | Concurrency | `if not lock: lock = True` is a check-then-set race |
| H3 | High | Data loss | `upsert_todos` is last-write-wins under concurrent writers |
| H4 | High | UX/data | Partial sync still updates `last_sync_at`, masking staleness |
| H5 | High | Coverage | Entire sync engine has zero unit tests |
| M1 | Med | Concurrency | Scheduler bypasses the per-user lock — duplicates work |
| M2 | Med | Reliability | Multi-worker uvicorn would multiply scheduler firings |
| M3 | Med | UX | 25s UI countdown can fire before the sync actually finishes |
| M4 | Med | UX | Status strip can't show "partial" — only "fresh" vs "stale" |
| M5 | Med | Observability | Background-thread exceptions swallowed silently above the per-project layer |
| M6 | Med | Data drift | Quietly-completed tasks (>30d) stay "active" forever locally |
| L1 | Low | Hygiene | Locks-as-function-attribute is a hidden global |
| L2 | Low | Architecture | Threading + lock orchestration lives in the HTTP router |
| L3 | Low | Drift risk | `ALI_LEGACY_BUCKET` injected via two paths |
| L4 | Low | UX | Token-missing failure shows opaque string, no remediation link |
| L5 | Low | UX | Targeted-only syncs read as "Not synced yet" |
| L6 | Low | Reliability | No overall wall-clock budget on the full sync |
| L7 | Low | Coverage | No E2E coverage of the sync banner + reload flow |

The high-severity findings cluster around **one root cause**: there is no single SyncCoordinator. Four triggers each spawn threads on their own terms, the lock is a function-attribute dict shared by three of them but bypassed by the fourth, the storage layer assumes single-writer, and the status field can't distinguish degraded from healthy. Each item is reachable in isolation, but the cluster fix is structural.

The single biggest improvement-per-effort: **H5 (unit tests for sync.py)**. Even before any code change, a test suite covering `_classify_for_user`, `_todo_is_relevant`, and the `upsert_todos` preservation rules would catch a future regression of the 2026-06-04 stale-task class.

---

## 6. Not in scope (flagged for separate triage)

- `pilot_dash_scheduler` — second cron in the same process, similar shape, not audited here
- `cb_mention_worker` — depends on `_maybe_async_sync` (named import — see `my_day.py:316`) and would be affected by any lock refactor
- The `scorer.score_all_todos` step that piggybacks every sync — not audited; could be the source of partial silent failure mentioned in 2026-06-04 audit open item

## 7. Files referenced

- [app/routers/my_day.py](app/routers/my_day.py) — router, lock dict, all 4 sync triggers
- [execution/products/ops/sync.py](execution/products/ops/sync.py) — BC API walker, classifier, upsert orchestration
- [execution/products/ops/scheduler.py](execution/products/ops/scheduler.py) — 5-min APScheduler cron
- [execution/products/ops/store.py](execution/products/ops/store.py) — file-backed JSON store, upsert primitives
- [app/templates/my_day/home.html](app/templates/my_day/home.html) — `?sync_started=1` banner + 25s JS countdown
- [app/templates/my_day/_view_chrome.html](app/templates/my_day/_view_chrome.html) — status strip + sync_stale signal
- [app/main.py](app/main.py) — lifespan wiring of `ops_start`
- [tests/app/test_my_day_sync_redirect.py](tests/app/test_my_day_sync_redirect.py) — the only sync-adjacent test (covers redirect only)
- [tests/app/test_my_day_health.py](tests/app/test_my_day_health.py) — health snapshot tests
- [docs/audits/2026-06-04-stale-task-sync-audit.md](docs/audits/2026-06-04-stale-task-sync-audit.md) — prior audit (referenced throughout)
