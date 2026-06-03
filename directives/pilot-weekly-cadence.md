# Weekly Pilot Rubric Cadence — Ali ⇄ DRI

**Owner:** Ali Muwwakkil
**Audience:** Pilot DRIs (Karun, Kes), and any future per-DRI pilot
**Frequency:** Weekly during pilot weeks 1-4, monthly thereafter, quarterly post-rollout
**Ticket:** [Infra 4](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889190) · due 2026-06-09

---

## Purpose

A 30-minute weekly cadence between Ali and each pilot DRI to:

1. **Refine the rubric** — what counts as a Colaberry-approved skill is a living definition. Each week's evidence sharpens it.
2. **Recalibrate targets** — pilots aim at moving numbers (decisions/week, time-in-meeting/week, retention signal). Targets adjust based on first-week observed baselines.
3. **Ratify Colaberry-approved commits** — anything the DRI promoted from draft → approved this week gets a sanity-check ack before the [Infra 2] sync fires it to the canonical repo.

This is not a status meeting. It is a **calibration loop**.

---

## When + format

| When | Who | Length | Required |
|---|---|---|---|
| Every Monday 09:00 ET | Ali ⇄ Karun | 30 min | Yes (weeks 1-4) |
| Every Monday 09:30 ET | Ali ⇄ Kes | 30 min | Yes (weeks 1-4) |

Pre-fire (5 min before): `/karun-dash` / `/kes-dash` runs and surfaces:

- Items moved through state machine this week (submitted, approved, rejected, changes_requested counts — see [Workflow 1] queue counts API)
- Rubric scores for each approved item (rating_avg, comment_count)
- Approvals fired to GitHub via [Infra 2] sync PRs
- Outstanding follow-ups from the prior week

The dashboard is the agenda. The meeting starts with: "Of these score deltas, which one surprised you?"

---

## Standing agenda (30 min)

| Time | Topic | Output |
|---|---|---|
| 0:00–0:05 | Score deltas surfaced by dashboard | Both parties caught up — no recap |
| 0:05–0:15 | Rubric edge cases | "Does this item count as a 5/5? Why?" — 1-3 specific items, 3 min each |
| 0:15–0:25 | Approve-ratify pass | Walk through the items the DRI moved to approved this week; Ali ratifies or sends back |
| 0:25–0:30 | Next-week targets | One number to move, by what amount |

If any agenda item runs over, the meeting overruns — these are upstream of every other decision the DRI makes that week.

---

## Decisions captured

Each cadence produces three append-only artifacts:

1. **Rubric-delta log** — every edit to the approval rubric, dated, with the catalyst item
2. **Target-delta log** — every target adjustment, dated, with the observed evidence
3. **Ratified-approvals log** — every item Ali ratified, with the rubric score

These live under `output/library/_pilot/{dri_name}/{date}.jsonl`.

---

## What "rubric" means here

The rubric is the rules-of-thumb that gate Colaberry-approved status — codified in `config/library_approvers.json` for WHO can approve, and in the per-item review notes for WHAT counts as good. Each week's cadence may produce a tightening of either dimension.

Example concrete refinements that have come up in the first two pilot conversations:

- **2026-06-04 (Karun):** A use-case that lacks a measurable "saved-N-minutes" claim cannot be approved as a sales-side use case. Reason: ROI claims that don't survive a 30-second sanity check destroy customer trust.
- **2026-06-05 (Kes):** An MCP server skill needs a working `install_command` + `homepage_url` to be approved tech-side. Reason: anything that requires a 20-min setup expedition for the next intern is not "approved", it's "shelf-ware."

---

## Quarterly cadence (post-rollout)

After the pilot ramps down (mid-Aug 2026 per BUILD_INDEX), this cadence drops to once per quarter per DRI. Same format. The pre-fired dashboard now uses the rolling 13-week window instead of the rolling 7-day window.

---

## Anti-patterns to avoid

- **Becoming a status meeting:** if the dashboard is doing its job, the first 5 minutes don't require Ali asking "what did you do?" — that's already on the screen.
- **Skipping the ratification:** an approved item that never gets ratified is a slow-bleed corruption of the rubric. If Ali can't make a slot, the DRI defers the approval to next week, not the other way around.
- **Recreating the rubric verbally:** every refinement gets written into the rubric-delta log in the meeting. Same week, before the next sync fires. Otherwise it's gossip.

---

## Calendar invites

To be sent by Ali from `ali@colaberry.com` to `karun@colaberry.com` + `kes@colaberry.com` with:

- Recurring weekly, Monday 09:00 ET / 09:30 ET
- 30 min length
- Description: link to this directive + link to the relevant dashboard
- Cancel rule: Ali only; DRI may move but not cancel
