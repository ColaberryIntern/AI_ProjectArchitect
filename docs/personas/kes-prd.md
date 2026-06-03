# Kes — Persona PRD

**Ticket:** [Kes 1](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889366) · due 2026-06-07
**Status:** DRAFT (scaffold awaiting 30-min session with Ali)
**Owner of this doc:** Ali Muwwakkil
**Pressure-tester:** Kes
**Unblocks:** [Kes 2 — build kes-agent + /kes-dash skill](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889389) (due 2026-06-12)

> Structure mirrors [`karun-prd.md`](karun-prd.md). Same shape, tech-side scope.

---

## Source of truth

| Source | Location | What it provides |
|---|---|---|
| Rubric workbook | `docs/ai-architect-rubrics-2026-06-02.xlsx` rows tagged `Kes` | Authoritative draft of the 5 numbers + skill list. Ali refines in place. |
| Weekly cadence | [`directives/pilot-weekly-cadence.md`](../../directives/pilot-weekly-cadence.md) | Standing agenda for Monday 09:30 ET Ali↔Kes 1:1 |
| Approver matrix | [`config/library_approvers.json`](../../config/library_approvers.json) | Per-category approver mapping including Kes's scope |
| Sync target | [Infra 2 sync](../specs/infra-01-colaberry-approved-classification.md) | On sign-off, this doc flags Colaberry-approved + auto-syncs to canonical repo |

---

## 1. Identity

| Field | Value |
|---|---|
| Name | Kes |
| Role | _[Ali to confirm — tech-side DRI per cadence directive 2026-06-05 example]_ |
| Reports to | Ali Muwwakkil |
| Peers in pilot | Karun (sales-side DRI — symmetric pilot) |
| Scope of approval authority | _[fill from `config/library_approvers.json` Kes rows]_ |
| Pilot start | Week of 2026-06-09 (Monday 09:30 ET 1:1) |
| Pilot length | 4 weeks (cadence directive) |

---

## 2. Systems Kes touches

| System | Read / Write / Approve | Cadence | Why it matters |
|---|---|---|---|
| _[e.g. GitHub `ColaberryIntern/*`]_ | _[R/W]_ | _[daily]_ | _[which numbers it feeds]_ |
| _[e.g. advisor.colaberry.ai Library `mcp-servers` category]_ | _[Approve]_ | _[weekly]_ | _[rubric per cadence directive]_ |
| _[continue rows]_ | | | |

---

## 3. The 5 numbers Kes owns

| # | Number | Source query / system | Baseline (week 0) | Target (week 4) | Review cadence |
|---|---|---|---|---|---|
| 1 | _[fill from XLSX]_ | | | | weekly 1:1 |
| 2 | | | | | weekly 1:1 |
| 3 | | | | | weekly 1:1 |
| 4 | | | | | |
| 5 | | | | | |

> Cadence directive seeds (shared with Karun): decisions/week, time-in-meeting/week, retention signal. Tech-side variants likely differ — Ali fills.

---

## 4. The 10–12 skills that produce those numbers

| # | Skill name | Type | Produces which number(s) | Status | Library `kind/cat/id` |
|---|---|---|---|---|---|
| 1 | _[fill from XLSX]_ | | #_ | | |
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

---

## 5. Per-number rubric

| # | Number | Rubric (one line — "a skill counts when…") |
|---|---|---|
| 1 | | _[e.g. "the MCP server has a working `install_command` AND `homepage_url` AND a passing install dry-run"]_ |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |

> Seed example from `directives/pilot-weekly-cadence.md` (2026-06-05): "An MCP server skill needs a working `install_command` + `homepage_url` to be approved tech-side." Same shape: one-line falsifiable test.

---

## 6. /kes-dash content contract

Surfaces 5 minutes before each Monday 09:30 ET 1:1 ([Kes 3](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889413)):

- [ ] State-machine counts this week (per Workflow 1)
- [ ] Rubric scores per approved item
- [ ] Approvals fired to GitHub via Infra 2
- [ ] Outstanding follow-ups
- [ ] Score deltas on the 5 numbers
- [ ] Top-1 opening question

---

## 7. Open questions (for the 30-min session)

- [ ] **Kes's role title + scope:** confirm "tech-side DRI" framing
- [ ] **The 5 numbers:** tech-side specifics
- [ ] **Source queries per number**
- [ ] **Baseline week-0 measurements**
- [ ] **The 10–12 skills:** ratify XLSX list
- [ ] **Per-skill ownership:** build vs inherit
- [ ] **Rubric per number**
- [ ] **Approval scope:** which Library categories
- [ ] **Out-of-scope**

---

## 8. Ratification

Same flow as Karun PRD: edit in place → set `Status: Colaberry-approved` → Infra 2 syncs.

---

## Changelog

| Date | Author | Change |
|---|---|---|
| 2026-06-03 | Claude | Initial scaffold per BC ticket 9953889366 — mirrors karun-prd.md structure; awaiting 30-min Ali↔Kes session |
