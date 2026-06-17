# Directive: Daily Productivity & AI-Leverage Report

## Goal

Email Ali a daily report that answers five questions about the AI-assisted
operating system (live since **2026-06-14**):

1. **Usage** - how much are people using the new system?
2. **Throughput** - how many tasks are they completing?
3. **AI leverage** - what is the ratio of AI usage to total usage?
4. **Speed** - does the process speed work up?
5. **Effectiveness** - are people genuinely *more productive*, or just *faster*?

The report ends with an **assessment** (a per-operator and team verdict), not just
numbers. It directly answers "are they slowing down because they can work faster?"

## Where it lives

Deterministic module `execution/products/ops/productivity/` (CLAUDE.md layering):

| File | Responsibility |
|---|---|
| `aggregate.py` | Pure KPI math + verdict. No I/O. Unit-tested. |
| `baseline.py` | Pre-launch "before" reference (median cycle + weekly throughput). |
| `render.py` | Email-safe HTML (inline CSS, zero em-dashes). |
| `delivery.py` | SMTP send, gated OFF by default. |
| `runner.py` | Orchestration + CLI. Discover operators -> aggregate -> render -> deliver. |
| `scheduler.py` | Daily weekday APScheduler job (07:30 ET), wired into `app/main` lifespan. |

Output: `output/ops/_productivity/{YYYY-MM-DD}.html` (+ `.json` sidecar).
Baseline: `output/ops/_productivity/baseline.json`.
Recipients/branding: `config/report_recipients.json`.

## Inputs + attribution (all already on disk; no extra instrumentation needed)

Per operator under `output/ops/<email>/todos.json`: completions (`completed_at`,
`completed_by_name`, `cycle_seconds`), open/overdue/stale, `category`, `assignee_names`,
`bc_created_at`.

Attribution model (the key correctness rule):
- Todos are **deduped by `bc_id`** first - the same Basecamp task appears in every
  operator's mirror for a shared project, so per-mirror counting multi-counts.
- A completion is attributed to **whoever actually closed it** (`completed_by_name`),
  not to everyone who mirrors the project.
- **AI signal = `completed_by_name == "CB System"`** (the bot account; override via
  `PRODUCTIVITY_AI_ACTORS`). This is the real, already-stored "AI did this" marker - no
  log-harvesting or new instrumentation. Per person: throughput = tasks they personally
  closed; AI leverage = of their assigned tasks completed this week, the share the AI closed.
  Team headline AI leverage = AI completions / all completions.

## The KPI catalog (5 pillars)

1. **Adoption** - syncs, active days.
2. **Throughput** - completed today / 7d / prior 7d, open, overdue, stale, net flow.
3. **AI leverage** - team: AI share of all completions (`CB System` completions / total);
   per person: AI share of their assigned tasks completed this week; delegatable vs
   human-required mix; estimated $ saved from AI-completed tasks.
4. **Speed** - median cycle time, AI cohort vs human cohort, cycle vs pre-launch baseline.
5. **Quality** - overdue rate, stale rate (the productivity-paradox guard).

## Verdict rubric (`aggregate._verdict`)

Precedence, per operator and team:
- **BASELINE** - fewer than `PRODUCTIVITY_MIN_SAMPLE` (default 3) completions, or no
  pre-launch baseline. Says so; does not over-claim.
- **RED** - overdue share above `PRODUCTIVITY_OVERDUE_RED` (default 30%), or slower per
  task AND completing less than baseline. Speed is costing quality.
- **GREEN** - completing more than baseline without slowing down. Genuinely more productive.
- **AMBER** - faster per task but NOT completing more (the paradox), or any mixed signal.

A move must clear +/-10% (`TREND_BAND`) to count as up/down/faster/slower.

## How to run

```
# manual / OS-cron, writes HTML to disk, delivery OFF
python -m execution.products.ops.productivity.runner

# force-rebuild the pre-launch baseline first
python -m execution.products.ops.productivity.runner --rebuild-baseline
```

Enable email (supervised first send):
```
PRODUCTIVITY_REPORT_DELIVERY=1 \
GMAIL_SMTP_USERNAME=... GMAIL_SMTP_APP_PASSWORD=... \
python -m execution.products.ops.productivity.runner
```
Recipients default to `ali@colaberry.com` only; widen via `config/report_recipients.json`.

## Tunable assumptions (env)

`PRODUCTIVITY_MIN_SAVED_PER_TASK` (15 min), `PRODUCTIVITY_DOLLARS_PER_HOUR` ($60),
`PRODUCTIVITY_MIN_SAMPLE` (3), `PRODUCTIVITY_OVERDUE_RED` (0.30),
`PRODUCTIVITY_BASELINE_WEEKS` (8). All assumptions are printed in the report footer.

## Verification

- Unit tests: `pytest tests/execution/products/ops/productivity -v` (math, baseline,
  verdict incl. the paradox case, email-safe HTML).
- End-to-end: run the CLI; open the generated HTML; confirm every pillar populates,
  both AI ratios render, the verdict matches the numbers.
- One supervised live send to Ali only before scheduling.

## Edge cases / honest limits

- **Thin "after" window.** For ~2-3 weeks post-launch the sample is small; verdicts stay
  in BASELINE / low-confidence rather than over-claiming a trend.
- **AI attribution is estimated.** Workflow runs do not store `user_id`; AI-touched is
  inferred from auto-pickup + @CB-mention signals (and the optional `ai_signals.json`).
  The report labels this explicitly. Activity-based ratio shows `n/a` until instrumented.
- **Baseline depth.** The local mirror retains recently-completed todos; a deeper walk of
  full Basecamp completion history is a Phase-3 enrichment behind `baseline.compute_baseline`.

## Scheduling

`scheduler.py` registers a daily weekday job (07:30 ET by default) and is started from
`app/main` lifespan alongside the other schedulers. Tune via `PRODUCTIVITY_REPORT_HOUR`,
`PRODUCTIVITY_REPORT_MINUTE`, `PRODUCTIVITY_REPORT_DOW`. It writes the HTML every run;
it only emails when `PRODUCTIVITY_REPORT_DELIVERY=1`.

## Deploy (prod, Docker)

HTTP-facing change, so it ships through the Docker rebuild (NOT systemd:8080):
1. Land on `main` via PR.
2. On the prod box: `git pull origin main && docker compose up -d --build`.
3. Confirm in container logs: `[lifespan] productivity report scheduler started`.
4. Supervised first email: set `PRODUCTIVITY_REPORT_DELIVERY=1` (+ `GMAIL_SMTP_*`) and run
   the CLI once; confirm it arrives in Ali's inbox; then leave the daily job to run.

## Phasing

- **Phase 1 (done):** modules + tests + this directive; HTML to disk, delivery gated OFF.
- **Phase 2 (done):** daily scheduler wired into `app/main` lifespan; delivery module +
  gating + tests. Pending operational step: prod Docker rebuild + supervised first send.
- **Phase 3:** rework/reopen detection, feedback pulse, per-operator capability tokens,
  deeper BC baseline backfill.
