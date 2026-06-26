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
numbers. The framing (per Ali 2026-06-17): it is about WHO is using the AI-paired system
and whether that usage produces more than they did BEFORE the change - not ticket hygiene.
Colour is driven by AI Share. **Scope: employees + Gov Contracts only** - `EXCLUDE_PROJECTS`
drops Power BI / Center of Excellence / RMG. The email is visual: per-person completion
sparklines, AI-share conditional formatting, overdue badges, vs-before trend arrows.

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

## Inputs + attribution (the three-bucket model)

Per operator under `output/ops/<email>/todos.json`: completions (`completed_at`,
`completed_by_name`, `cycle_seconds`), open/overdue/stale, `category`, `assignee_names`,
`bc_created_at`.

Attribution model (the key correctness rule). The earlier version counted a completion as
AI-assisted **only** when `completed_by_name == "CB System"`. That scored the heaviest AI
users at 0% / "Low AI use", because when someone works through Claude Code the todo is
completed under their **own** identity. The fix:

- Todos are **deduped by `bc_id`** first - the same Basecamp task appears in every
  operator's mirror for a shared project, so per-mirror counting multi-counts.
- A completion is attributed to **whoever actually closed it** (`completed_by_name`).
- Every completion lands in exactly **one of three buckets** (`aggregate._classify`):
  - **`ai_assisted`** - any AI signal present:
    - *actor close* - `completed_by_name` is an AI actor (default `CB System`; override
      `PRODUCTIVITY_AI_ACTORS`);
    - *session join* - the task's `bc_id` was a Claude Code session `active_ticket`
      (`.claude/session-state.json`, the design-doc Pillar-1 signal);
    - *task AI marker* - a `[via … Claude Code]` progress prefix / `@CB` answer /
      auto-pickup / `ai_signals.json` entry for that `bc_id`.
  - **`human_only`** - positive evidence of an unaided manual close (a human-authored
    progress post / manual close with no AI signal anywhere near it).
  - **`attribution_unknown`** - no signal either way. A measurement gap, **not** a verdict.
- **`attribution_confidence`** per operator = `(ai_assisted + human_only) / completions`.
- AI share is reported as a point share (`ai_assisted / completions`), an "of attributable
  work" share (`ai_assisted / (ai_assisted + human_only)`), and an upper bound that folds in
  the unknown slice - never collapsed to a single confident number when confidence is low.
- A person is **`ai_active`** if they have AI sessions/commits/`@CB` answers in the window
  (person-level prior). An `ai_active` operator's `attribution_unknown` work is **never**
  silently counted as human.
- The runner's I/O edge (`runner.gather_ai_signals`) harvests these signals from disk
  (`.claude/session-state.json`, the `_cb_mentions` cursor, git `Co-Authored-By: Claude` /
  Session-ID provenance, optional `output/ops/_productivity/ai_signals.json`) and injects
  them as a pure `AiSignals` object; the math stays I/O-free and unit-tested. Every source
  is optional - a missing source yields more `attribution_unknown`, shown honestly.

## The KPI catalog (5 pillars)

1. **Adoption** - syncs, active days.
2. **Throughput** - completed today / 7d / prior 7d, open, overdue, stale, net flow.
3. **AI leverage** - per person: AI share over the three buckets (point / attributable /
   upper), the AI / human / unknown split, delegatable vs human-required mix, estimated $
   saved. **Team headline = MEDIAN of per-operator shares** (with p25-p75), not the
   completion-weighted ratio, so one heavy operator cannot own the number; the old
   volume-weighted figure is kept as a secondary read.
4. **Speed** - median **new-work** cycle time (long-dormant backlog cleanup is split out as
   a separate `backlog_cycle_days`, so clearing old todos does not read as "slower"),
   cycle vs pre-launch baseline.
5. **Quality** - overdue rate, stale rate (the productivity-paradox guard).

## Verdict rubric (`aggregate._verdict`) - gated on attribution confidence

The colour is driven by **AI Share**, but only once the work is **attributable**. A
measurement gap must never be rendered as a behavioural verdict:
- **UNKNOWN "Attribution incomplete"** - `attribution_confidence < PRODUCTIVITY_ATTRIB_CONF_MIN`
  (default 50%). Reads "baseline-building", and when the operator is `ai_active` it says so
  explicitly ("not a low-use call"). This is the case the old report mislabelled as red.
- **GREEN "Heavy AI use"** - confident AND attributed AI share >= `PRODUCTIVITY_AI_HIGH` (50%).
- **AMBER "Partial AI use"** - confident AND attributed share >= `PRODUCTIVITY_AI_LOW` (20%).
- **RED "Low AI use"** - confident AND genuinely low attributed share (real `human_only`
  work dominates). Reached **only** with high confidence, never from unknowns.
- **NODATA** - no completed work in scope this week.

The reason line appends the productivity read vs before: "producing more / about the same /
less than before" (throughput vs baseline, or faster cycle; +/-10% `TREND_BAND` to count).
**Outlier-robust math:** "vs before" is **winsorized** to +/-`PRODUCTIVITY_TREND_CAP`%
(default 300) and the baseline is **floored** - a baseline under `PRODUCTIVITY_MIN_BASELINE_SAMPLE`
completions renders "n/a, baseline too small" instead of a runaway percentage (kills the
+30775% artifact). Operators are bucketed into volume tiers (heavy / core / occasional) and
anyone closing >= `PRODUCTIVITY_OUTLIER_SHARE` of all completions is flagged an **outlier**.
Overdue/quality is shown (red badges) but does NOT set the colour.

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

`PRODUCTIVITY_AI_ACTORS` (`CB System`), `PRODUCTIVITY_AI_HIGH` (0.50),
`PRODUCTIVITY_AI_LOW` (0.20), `PRODUCTIVITY_ATTRIB_CONF_MIN` (0.50 - the verdict gate),
`PRODUCTIVITY_TREND_CAP` (300 - winsorize cap %), `PRODUCTIVITY_MIN_BASELINE_SAMPLE` (3 -
baseline floor), `PRODUCTIVITY_DORMANT_DAYS` (30 - new-work vs backlog cycle split),
`PRODUCTIVITY_OUTLIER_SHARE` (0.40 - outlier flag), `PRODUCTIVITY_SIGNAL_WINDOW_DAYS` (7),
`PRODUCTIVITY_MIN_SAVED_PER_TASK` (15 min), `PRODUCTIVITY_DOLLARS_PER_HOUR` ($60),
`PRODUCTIVITY_MIN_SAMPLE` (3), `PRODUCTIVITY_BASELINE_WEEKS` (8). The key assumptions are
printed in the report footer.

## Verification

- Unit tests: `pytest tests/execution/products/ops/productivity -v` (math, baseline,
  verdict incl. the paradox case, email-safe HTML).
- End-to-end: run the CLI; open the generated HTML; confirm every pillar populates,
  both AI ratios render, the verdict matches the numbers.
- One supervised live send to Ali only before scheduling.

## Edge cases / honest limits

- **Thin "after" window.** For ~2-3 weeks post-launch the sample is small; verdicts stay
  in BASELINE / low-confidence rather than over-claiming a trend.
- **AI attribution is a measurement, with an explicit unknown bucket.** Signals are
  inferred from session-state joins, git provenance, the `@CB` cursor, and the optional
  `ai_signals.json`. Where no signal fires the work is `attribution_unknown` and shown as
  such - the report never fabricates AI credit, and never reads an unknown as "Low AI use".
  Accuracy over flattery: an honest "we could not attribute this" beats a confident wrong
  verdict. To firm up attribution in prod, populate
  `output/ops/_productivity/ai_signals.json` (`session_ticket_ids`, `ai_marked_task_ids`,
  `human_marked_task_ids`, `ai_active_operators`) or ensure operator workspaces keep their
  `.claude/session-state.json`.
- **Baseline depth.** The local mirror retains recently-completed todos; a deeper walk of
  full Basecamp completion history is a Phase-3 enrichment behind `baseline.compute_baseline`.

## Scheduling

`scheduler.py` registers a daily weekday job (**05:30 America/Chicago = 5:30 AM CST/CDT**)
and is started from `app/main` lifespan alongside the other schedulers. Tune via
`PRODUCTIVITY_REPORT_TZ`, `PRODUCTIVITY_REPORT_HOUR`, `PRODUCTIVITY_REPORT_MINUTE`,
`PRODUCTIVITY_REPORT_DOW`. It writes the HTML every run; it only emails when
`PRODUCTIVITY_REPORT_DELIVERY=1` (set in prod `.env.prod`).

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
