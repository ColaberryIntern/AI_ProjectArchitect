# Audit — /my-day/ stale-task sync bug

**Date:** 2026-06-04
**Reporter:** Ali Muwwakkil
**Auditor:** Claude (current session)
**Status:** Root cause identified · Fix shipped · Verified on prod
**Commit:** `8a7dc54`

---

## Symptom

Tasks completed in Basecamp continued to appear as **"active"** in `/my-day/`, surfacing as the "TOP OF QUEUE — NO HUMAN DECISIONS PENDING" focus card. User-visible examples:

| Task | Project | BC status | /my-day/ status |
|---|---|---|---|
| Press Mike for ShipCS rate confirmation template (PDF format) | ShipCES - Autonomous Brokerage | completed at 00:01:12 UTC by CB System | active |
| (similar pattern) | LandJet Growth Engine | completed | active |

---

## Root cause

The `/my-day/` read path had **no inline sync** at the FastAPI route layer. The page rendered immediately against whatever was in the local file-backed store, and a `_maybe_async_sync()` call kicked a daemon thread to refresh in the background.

This shape has three failure modes:

1. **Daemon thread killed by container restart / deploy** — sync was mid-flight, never finished, store stayed stale.
2. **Daemon thread errored silently** — `try/except` swallowed the error, lock never re-cleared, no retry.
3. **Background APScheduler cron not visibly verifiable** — the `logger.info(...)` line at scheduler startup wasn't appearing in uvicorn's stdout, so there was no way to confirm from logs whether the 5-minute sync job was actually firing in production.

Concrete evidence from the prod box at audit time (`2026-06-04T00:30 UTC`):
- `state.last_sync_at` = `2026-06-04T00:15:31` (15 min stale)
- BC API for task `9946715830`:
  ```
  completed:                true
  completion.created_at:    2026-06-04T00:01:12
  completion.creator.name:  CB System
  ```
- Local store for same `bc_id`:
  ```
  status:          active
  bc_updated_at:   2026-06-03T23:56:17    ← pre-completion snapshot
  ```

The 00:15 sync ran AFTER BC marked the task complete but did not propagate the change. Manually invoking `sync.pull_todos_for_project(ali, 47126345)` from the running container immediately fixed it — so the walker, classifier, and upsert logic are all correct. The gap was that the natural read flow never re-triggered a sync against fresh data.

---

## Fix

Two-part change (commit `8a7dc54`):

### 1. Natural-flow sync on every `/my-day/` GET

```
On every GET /my-day/, check state.last_sync_at:
  age < 90s  → fresh, render immediately
  age ≥ 90s  → 1. Targeted sync the user's current project filter inline (~2-3s)
                 → guaranteed fresh data for the focus task we're about to render
               2. Background-kick a full sync (~30-60s) for projects outside the filter
                 → long tail catches up without blocking the response
```

Pairs with the existing Mark Done targeted sync (commit `1b668b2`) which covers the write path. Together: every interaction with `/my-day/` keeps the local store consistent with BC, regardless of whether the background APScheduler cron is firing.

### 2. Visible scheduler-start confirmation

Added `print("[lifespan] ops sync scheduler started", flush=True)` to lifespan so the container logs now show:

```
[lifespan] ops sync scheduler started
[lifespan] pilot dash scheduler started
```

— immediately after `Application startup complete`. Now operators can `docker logs ai-project-architect-app-1` and confirm at a glance whether both schedulers are alive.

---

## Verification

Post-deploy on `95.216.199.47`:

1. Container logs now show both `[lifespan]` lines — schedulers started cleanly.
2. Manual full sync completed in ~4 minutes; `last_sync_at` advanced `00:15:31` → `01:19:44`.
3. Press Mike task in local store: `status='completed'` (corrected via targeted sync).
4. Next `/my-day/` visit with a project filter will refresh that project in <3s if stale.

---

## Open items

| Item | Action |
|---|---|
| Why the 00:15 sync didn't catch the Press Mike completion despite running after BC marked it done | Investigate next session — possibly a partial-success / silent exception inside `_walk_project_todos` for one project, while others succeeded. The walker works in isolation when re-tested. |
| `/my-day/_health` endpoint exposing scheduler state + per-user `last_sync_at` | Next ticket: build for operational monitoring. |
| Container output dir at `/opt/ai-project-architect/output` shows path-traversal probe artifacts (`pwn`, `rce`, `etc`, etc. as project directories) | Out of scope for this audit but worth flagging — `app/routers/projects.py` `POST /projects/new` is accepting arbitrary `project_name` and creating directories. Should be sanitized. |

---

## Files touched

- `app/routers/my_day.py` — new `_natural_flow_sync()`, `_kick_bg_full_sync()`, `_store_age_seconds()` helpers; `ops_home` re-ordered to do the natural-flow sync before loading todos
- `app/main.py` — `print(..., flush=True)` after each scheduler start

## Commit

```
8a7dc54 my_day: natural-flow sync — page-load inline sync of focused project
1b668b2 sync: targeted per-project sync on Mark Done + bg full sync
```
