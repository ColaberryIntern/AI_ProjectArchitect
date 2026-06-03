# Karun — Persona PRD

**Ticket:** [Karun 1](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889231) · due 2026-06-07
**Status:** DRAFT (scaffold awaiting 30-min session with Ali)
**Owner of this doc:** Ali Muwwakkil
**Pressure-tester:** Karun
**Unblocks:** [Karun 2 — build karun-agent + /karun-dash skill](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889261) (due 2026-06-12)

---

## Source of truth

| Source | Location | What it provides |
|---|---|---|
| Rubric workbook | `docs/ai-architect-rubrics-2026-06-02.xlsx` rows tagged `Karun` | Authoritative draft of the 5 numbers + skill list. Ali refines in place. |
| Weekly cadence | [`directives/pilot-weekly-cadence.md`](../../directives/pilot-weekly-cadence.md) | Standing agenda for Monday 09:00 ET Ali↔Karun 1:1 |
| Approver matrix | [`config/library_approvers.json`](../../config/library_approvers.json) | Per-category approver mapping including Karun's scope |
| Sync target | [Infra 2 sync](../specs/infra-01-colaberry-approved-classification.md) | On sign-off, this doc flags Colaberry-approved + auto-syncs to canonical repo |

---

## 1. Identity

| Field | Value |
|---|---|
| Name | Karun |
| Role | _[Ali to confirm — sales-side DRI per cadence directive 2026-06-04 example]_ |
| Reports to | Ali Muwwakkil |
| Peers in pilot | Kes (tech-side DRI — symmetric pilot) |
| Scope of approval authority | _[fill from `config/library_approvers.json` Karun rows]_ |
| Pilot start | Week of 2026-06-09 (Monday 09:00 ET 1:1) |
| Pilot length | 4 weeks (cadence directive) |

---

## 2. Systems Karun touches

The systems Karun uses or affects in his weekly motion. Filled from Ali's mental model + observed weekly activity.

| System | Read / Write / Approve | Cadence | Why it matters |
|---|---|---|---|
| _[e.g. HubSpot CRM]_ | _[R/W]_ | _[daily]_ | _[which numbers it feeds]_ |
| _[e.g. Basecamp `Sales / Outreach` list]_ | _[R/W/Approve]_ | _[weekly]_ | _[which numbers it feeds]_ |
| _[e.g. advisor.colaberry.ai Library `sales-use-cases` category]_ | _[Approve]_ | _[weekly]_ | _[rubric per cadence directive]_ |
| _[continue rows]_ | | | |

---

## 3. The 5 numbers Karun owns

The numbers that, when they move, mean Karun is doing his job. Each one needs a defined source, baseline, target, and review cadence.

| # | Number | Source query / system | Baseline (week 0) | Target (week 4) | Review cadence |
|---|---|---|---|---|---|
| 1 | _[e.g. Decisions made per week]_ | _[query]_ | _[N]_ | _[N]_ | weekly 1:1 |
| 2 | _[e.g. Time-in-meeting per week]_ | _[query]_ | _[N hours]_ | _[N hours]_ | weekly 1:1 |
| 3 | _[e.g. Retention signal]_ | _[query]_ | _[N]_ | _[N]_ | weekly 1:1 |
| 4 | _[fill]_ | | | | |
| 5 | _[fill]_ | | | | |

> Candidate seed numbers from `directives/pilot-weekly-cadence.md`: decisions/week, time-in-meeting/week, retention signal. The other 2 come from the XLSX.

---

## 4. The 10–12 skills that produce those numbers

Library assets (skills, agents, prompts, MCP servers) that, when Karun runs them weekly, drive the 5 numbers above. Each skill maps to exactly one or more of the 5 numbers.

| # | Skill name | Type (skill / agent / prompt / MCP) | Produces which number(s) | Status (built / planned / inherited) | Library `kind/cat/id` |
|---|---|---|---|---|---|
| 1 | _[fill from XLSX]_ | _[type]_ | #_ | _[status]_ | _[library ref]_ |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |
| 6 | | | | | |
| 7 | | | | | |
| 8 | | | | | |
| 9 | | | | | |
| 10 | | | | | |
| 11 | | | | | |
| 12 | | | | | |

> Min 10, max 12. The cap is intentional — if Karun needs 15 skills, two of them are doing the same job and one should be retired.

---

## 5. Per-number rubric

For each of the 5 numbers, a one-line rule that decides whether a skill counts as producing that number. The rubric is what Ali ratifies in the weekly 1:1; refinements get written into `output/library/_pilot/karun/{date}.jsonl` per the cadence directive's rubric-delta log.

| # | Number | Rubric (one line — "a skill counts when…") |
|---|---|---|
| 1 | _[number 1 name]_ | _[e.g. "the skill writes a row to HubSpot AND the row carries a measurable saved-N-minutes claim"]_ |
| 2 | _[number 2 name]_ | _[e.g. "the skill replaces ≥30 min of a weekly Karun-owned meeting"]_ |
| 3 | _[number 3 name]_ | |
| 4 | | |
| 5 | | |

> Seed example from `directives/pilot-weekly-cadence.md` (2026-06-04): "A use-case that lacks a measurable 'saved-N-minutes' claim cannot be approved as a sales-side use case." That phrasing is the rubric shape: a one-line falsifiable test.

---

## 6. /karun-dash content contract

What [Karun 2](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889261) must surface 5 minutes before each Monday 09:00 ET 1:1, per `directives/pilot-weekly-cadence.md`:

- [ ] Items moved through state machine this week (submitted / approved / rejected / changes_requested counts — needs [Workflow 1] queue counts API)
- [ ] Rubric scores for each approved item (`rating_avg`, `comment_count`)
- [ ] Approvals fired to GitHub via [Infra 2] sync PRs
- [ ] Outstanding follow-ups from the prior week
- [ ] Score deltas on the 5 numbers (week-over-week)
- [ ] Top-1 question to open the meeting: "Of these score deltas, which one surprised you?"

---

## 7. Open questions (for the 30-min session)

These are the gaps the session must close before this doc can be flagged Colaberry-approved + synced.

- [ ] **Karun's role title + scope:** confirm "sales-side DRI" framing
- [ ] **The 5 numbers:** which 2 round out the cadence-mentioned 3 (decisions/week, time-in-meeting/week, retention signal)
- [ ] **Source queries per number:** CRM column, BC list, Library category — exact pointer per number
- [ ] **Baseline week-0 measurements:** does Ali already have these in the XLSX or do they need a week-0 capture
- [ ] **Target setting method:** absolute targets vs % delta from baseline
- [ ] **The 10–12 skills:** ratify the list from the XLSX
- [ ] **Per-skill ownership:** which skills Karun builds vs which he inherits from the existing Library
- [ ] **Rubric per number:** confirm the one-line falsifiable test for each
- [ ] **Approval scope:** which Library categories does Karun have approve authority on (`config/library_approvers.json`)
- [ ] **Out-of-scope:** what is Karun explicitly NOT on the hook for

---

## 8. Ratification

When the 30-min session completes:

1. Ali edits this file in place (commits + pushes)
2. Set the `Status:` line at top to `Colaberry-approved`
3. The [Infra 2] sync watches this file's approval flag and PRs it to the canonical AI_ProjectArchitect repo automatically
4. [Karun 2] is now unblocked — start the `karun-agent` build using sections 3, 4, 6 as the spec

---

## Changelog

| Date | Author | Change |
|---|---|---|
| 2026-06-03 | Claude (session continued from auth-2 work) | Initial scaffold per BC ticket 9953889231 spec — empty placeholder rows; awaiting 30-min Ali↔Karun session |
