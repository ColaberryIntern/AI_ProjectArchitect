# [Karun 3 + Kes 3] Pilot dash scheduler — calendar-driven 1:1 dashboard

**Tickets:** Basecamp [Karun 3](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889285) (due 2026-06-14) + [Kes 3](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889413) (due 2026-06-14)
**Status:** Shipped (harness); rendering is placeholder until [Karun 1 PRD](../personas/karun-prd.md) + [Kes 1 PRD](../personas/kes-prd.md) are signed
**Depends on:** [Karun 2 SKILL](../../.claude/skills/karun-agent/SKILL.md) + [Kes 2 SKILL](../../.claude/skills/kes-agent/SKILL.md) (scaffolds shipped)
**Source:** the Alden plan email's "4-hour MVP" framing — *"Approve this and v1 lives by EOD next day"*

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Dashboard auto-fires 30 min before each Ali ↔ Karun 1:1 | `execution/products/pilot/scheduler.py::start_scheduler()` registers a `CronTrigger(day_of_week='mon', hour=8, minute=30, timezone=ET)` — fires every Monday 08:30 ET |
| 2 | Dashboard auto-fires 30 min before each Ali ↔ Kes 1:1 | Mirror: `CronTrigger(day_of_week='mon', hour=9, minute=0, timezone=ET)` — fires every Monday 09:00 ET |
| 3 | Both Ali + DRI receive identical HTML | Render step writes one file; delivery (when enabled) sends the same file to both recipients via Gmail MCP |
| 4 | 3 consecutive 1:1s land with dashboard delivered on schedule + zero misfires | Scheduler logs every run with status + path; cron in BackgroundScheduler is single-instance per job (`max_instances=1`) preventing double-fires; idempotency: same scheduler instance is reused on lifespan re-entry |

---

## What ships in this ticket

- **`execution/products/pilot/scheduler.py`** — APScheduler `BackgroundScheduler` with two `CronTrigger` jobs (`pilot_dash_karun`, `pilot_dash_kes`), idempotent `start_scheduler()` / `stop_scheduler()`, environment-gated delivery via `PILOT_DASH_DELIVERY=1`
- **`execution/products/pilot/dash_runner.py`** — End-to-end pipeline: `_load_sources()` → `_score()` → `_critic()` → `_render_html()` → write HTML + JSON sidecar. Returns a `DashResult` dataclass. Stub mode (current) renders a placeholder banner pointing at the unsigned PRDs.
- **`execution/products/pilot/__init__.py`** — package marker + module docstring
- **`app/main.py`** — lifespan hooks call `pilot.scheduler.start_scheduler()` on startup, `stop_scheduler()` on shutdown
- **`tests/execution/products/test_pilot_scheduler.py`** — 11 tests covering cron registration, stub render, sidecar JSON, critic short-circuit, banned-phrase detection, idempotent start/stop

## Cron schedule (per [`directives/pilot-weekly-cadence.md`](../../directives/pilot-weekly-cadence.md))

| DRI | Meeting time (ET) | Dashboard fires (ET) | Cron field |
|---|---|---|---|
| Karun | Mon 09:00 | Mon 08:30 | `day_of_week='mon', hour=8, minute=30` |
| Kes | Mon 09:30 | Mon 09:00 | `day_of_week='mon', hour=9, minute=0` |

Timezone resolved via `zoneinfo.ZoneInfo("America/New_York")` (stdlib, no pytz dep needed). DST transitions are handled automatically by `zoneinfo`.

## Output paths

```
output/library/_pilot/karun/{YYYY-MM-DD}.html   — dashboard HTML
output/library/_pilot/karun/{YYYY-MM-DD}.json   — structured run sidecar
output/library/_pilot/kes/{YYYY-MM-DD}.html     — dashboard HTML
output/library/_pilot/kes/{YYYY-MM-DD}.json     — structured run sidecar
```

The sidecar JSON carries the run's `dri`, `ran_at`, `sources_stub`, scored numbers, and `critic_failures` — used by the [Workflow 1] queue counts view (and by future retrospective tooling per Karun 5 / Kes 5).

## Delivery (currently OFF)

Gmail push to Ali + DRI inboxes is gated by `PILOT_DASH_DELIVERY=1`. When enabled, `_deliver()` POSTs the HTML to the Gmail MCP server using the `gmail_pilot_dash` vault entry.

Until Ali confirms:
1. Recipient list (`ali@colaberry.com` + `karun@colaberry.com` / `kes@colaberry.com`)
2. Gmail OAuth token is stored in the vault under `gmail_pilot_dash`

…the scheduler runs the full render + critic pipeline + writes to disk, but logs the would-deliver call without sending. Safe default for prod-before-recipient-confirmation.

## Critic loop

Six base checks per [`karun-agent SKILL.md`](../../.claude/skills/karun-agent/SKILL.md#critic-loop-mandatory-before-ship):

1. **Completeness** — all 5 numbers have current values
2. **Citation integrity** — every score tile references a source row that exists
3. **Banned phrases** — no hedging in headline numbers (`might`, `could`, `approximately`, `around`, `roughly`)
4. **Stale data** — no source's last-pulled timestamp > 60 min old
5. **Rubric coverage** — every flagged item has the rubric clause it tripped, verbatim
6. **Format** — HTML validates + inboxes render the same

On stub mode (current — PRDs unsigned), the critic short-circuits with a single failure: `"scoring stub — PRD §3 + §5 not yet signed; placeholder HTML rendered"`. The HTML still writes (with the pre-ratification banner) so a human can inspect — but `delivery` is gated on a clean critic pass, so nothing actually gets emailed until the PRDs sign.

## Activation steps (Ali, after PRD signature)

1. Sign [Karun 1 PRD](../personas/karun-prd.md) — flip Status to `Colaberry-approved`, fill §3-5
2. (Equivalent for Kes 1 PRD)
3. Implement `_load_sources(dri)` + `_score(dri, sources)` per the now-known 5 numbers (the 4-hour build)
4. Add Gmail OAuth token to vault: `vault.store_secret(user_id='ali', tool_name='gmail_pilot_dash', token=...)`
5. Set `PILOT_DASH_DELIVERY=1` in `.env.prod`
6. Verify on next Monday 08:30 ET: dashboard renders, critic passes (zero failures), delivery confirmed in logs, both inboxes receive the HTML

## Idempotency + safety

- `start_scheduler()` is idempotent — calling it twice returns the same `BackgroundScheduler` instance (lifespan re-entry safe)
- Each cron job is `max_instances=1` — a slow run never gets double-fired on the next minute
- `dash_runner.run()` is the only place that writes to `output/library/_pilot/` — easy to redirect for tests (see `OUTPUT_ROOT` constant)
- All exceptions in the dashboard pipeline are caught and surfaced via `DashResult.status='error'` + `DashResult.error` — the scheduler never crashes from a bad render

## Out of scope (deferred)

- Real source loading (`_load_sources` is stubbed — gated on Karun/Kes 1)
- Real scoring (`_score` is stubbed — gated on PRD §3 + §5)
- Calendar-aware misfire detection (if Ali reschedules the 1:1; v1 assumes the standing time holds)
- Retrospective analytics over the sidecar JSON files (Karun 5 / Kes 5 ticket)
