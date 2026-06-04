---
name: karun-agent
description: Pre-1:1 dashboard generator for Ali ↔ Karun weekly cadence. Reads BC + Gmail + HubSpot + Apollo + CCPP via MCP, scores Karun's 5 numbers against the PRD rubric, emits a clean HTML dashboard within 60s.
---

# karun-agent

**Ticket:** [Karun 2](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889261) · due 2026-06-12
**Status:** SCAFFOLD (blocked on [Karun 1 PRD](../../../../docs/personas/karun-prd.md) signature)
**Fires:** [Karun 3](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889285) wires this skill to run 30 min before each Monday 09:00 ET 1:1
**Critic loop:** mandatory before "ship" per ticket acceptance criteria

---

## What this skill does

Generates an HTML dashboard for the standing Ali ↔ Karun weekly 1:1. Inputs come from five data sources; output is one self-contained HTML file delivered to both inboxes 30 minutes before the meeting.

```
                    karun-agent (this skill)
                          │
        ┌─────────┬──────────┬──────────┬──────────┬──────────┐
        ▼         ▼          ▼          ▼          ▼          ▼
   Basecamp    Gmail     HubSpot    Apollo      CCPP    advisor.colaberry.ai
   (BC todos,  (threads  (CRM rows  (sequence   (SQL    /library/ approvals
    comments,   tagged   per         per          rows   for Karun categories)
    closures)   Karun)   account)   contact)    DB)
        │         │          │          │          │          │
        └─────────┴──────────┴──────────┴──────────┴──────────┘
                          │
                  ─────score against─────
                          │
         docs/personas/karun-prd.md §3-5
         (the 5 numbers · rubric per number)
                          │
                          ▼
              HTML dashboard (60s budget)
                          │
                   critic loop pass
                          │
                          ▼
                   ship to both inboxes
```

---

## Data sources (MCP servers consumed)

| Source | MCP server | What it reads | Used for which of the 5 numbers |
|---|---|---|---|
| Basecamp | `mcp_basecamp` _(planned — see [Workflow 1] queue counts API)_ | Active + completed todos, comments, approvals fired this week | _[fill from karun-prd.md §3]_ |
| Gmail | `mcp_claude_ai_Gmail` _(in registry)_ | Threads where Karun is To/CC + Karun-tagged conversation outcomes | _[fill]_ |
| HubSpot | `mcp_hubspot` _(planned)_ | CRM rows owned by Karun, deal stages, last activity | _[fill]_ |
| Apollo | `mcp_apollo` _(planned)_ | Outbound sequence performance, contact-level engagement | _[fill]_ |
| CCPP | `mcp_ccpp` _(via SSH+docker bridge per [reference_basecamp_auth](../../../../C%3A%5CUsers%5Cali_m%5C.claude%5Cprojects%5Cc--Users-ali-m-OneDrive-Business-Colaberry-Novedea-AI-Projects-AI-Project-Architect---Build-Companion%5Cmemory%5Creference_basecamp_auth.md))_ | Customer signal table, training-cohort retention | _[fill]_ |

Read order: BC → Gmail → HubSpot → Apollo → CCPP. Anything that 429s or times out is logged and the dashboard renders without that source (with a missing-source banner — never silently dropped).

---

## Output contract

`/karun-dash` emits ONE file: `output/library/_pilot/karun/{YYYY-MM-DD}.html`

Structure (per [`directives/pilot-weekly-cadence.md`](../../../../directives/pilot-weekly-cadence.md) standing agenda):

| Section | Content | Source |
|---|---|---|
| Top banner | Date · "30 min before Ali ↔ Karun 1:1" · Week N of 4 | runtime |
| Score deltas | The 5 numbers, week-over-week (per `karun-prd.md §3`) | computed |
| Rubric edge cases | 1-3 items where the score is on the fence — used for the 0:05-0:15 calibration block | computed |
| Approve-ratify pass | Items Karun moved to `approved` this week (Library state machine) | [Workflow 1] queue counts API |
| Next-week target proposal | One number to move, by what amount, based on this week's delta | computed |
| Outstanding follow-ups | Open commitments from the prior week's rubric-delta log | `output/library/_pilot/karun/{prior-week}.jsonl` |

Bottom of dashboard always closes with the standing opening question:
> "Of these score deltas, which one surprised you?"

---

## Scoring contract (per-number)

For each of the 5 numbers in `karun-prd.md §3`:

```python
score_for_number(number: PRDNumber, raw_data: dict) -> ScoreRow:
    return ScoreRow(
        number=number.name,
        current=current_value(number.source_query, raw_data),
        prior_week=lookup_prior(number.name),  # from last week's jsonl
        delta=current - prior_week,
        target_for_this_week=number.target,
        on_track=(current >= target_for_this_week),
        rubric_pass=apply_rubric(number.rubric, raw_data),  # per §5
        flagged_items=collect_flagged(number, raw_data),  # for edge-case section
    )
```

Each `ScoreRow` becomes one tile in the HTML output. Tiles render green (on track), amber (within 10% of target), red (off track) per the [Infra 1] approval classification visual language.

---

## Critic loop (mandatory before ship)

Per the BC ticket acceptance criteria, the generated dashboard MUST pass a critic loop before it's delivered. The critic checks for:

| Check | Hard fail if… |
|---|---|
| Completeness | Any of the 5 numbers missing a current value (would silently mislead Ali) |
| Citation integrity | A score tile references a source row that doesn't exist in the raw data dict |
| Banned phrases | Hedging language ("might", "could", "approximately") in headline numbers |
| Stale data | Any source's last-pulled timestamp is > 60 min old |
| Rubric coverage | Every flagged item has the rubric clause it tripped, written verbatim |
| Format | HTML validates · open inboxes render it the same way · no broken image refs |

If the critic fails on any check, the dashboard is NOT shipped. The critic emits a `output/library/_pilot/karun/{YYYY-MM-DD}-critic-failure.md` describing exactly which check failed and how to fix. The Monday 1:1 then runs without the pre-fire — the meeting opens with "the dashboard didn't ship; here's why" instead of the standing question. Honest failure beats misleading delivery.

---

## Performance budget

| Phase | Budget |
|---|---|
| All 5 MCP reads | 35s |
| Score computation | 5s |
| HTML render | 5s |
| Critic loop | 10s |
| Inbox delivery | 5s |
| **Total wall clock** | **60s** (per ticket acceptance) |

If any phase blows budget, the skill emits a partial dashboard + a budget-overrun row at the bottom. Acceptable degradation: 1 missing source. Unacceptable: degraded score (better to ship 4 numbers honestly than 5 numbers half-imagined).

---

## What's still blocking ship

This skill cannot run until [Karun 1 PRD](../../../../docs/personas/karun-prd.md) is signed:

- [ ] §3 of the PRD — the 5 numbers must have source queries and targets
- [ ] §5 of the PRD — per-number rubric must have one-line falsifiable tests
- [ ] §4 of the PRD — the 10-12 skills feeding the numbers must be enumerated

Until then, this directory holds the contract (this file), a stub `dash.py` (planned), and the rubric for the critic loop. When Karun 1 lands, the implementation of `score_for_number` + `apply_rubric` becomes a 4-hour build (per [Karun 3](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889285) "4-hour MVP from the Alden plan email").

---

## Changelog

| Date | Author | Change |
|---|---|---|
| 2026-06-03 | Claude (post Auth 2 ship) | Scaffold landed; awaiting Karun 1 PRD signature for implementation. Skill description, data source matrix, output contract, scoring contract, critic loop, and performance budget defined; per-number scoring + rubric application stubbed pending PRD. |
