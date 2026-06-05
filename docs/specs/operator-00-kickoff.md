# [Operator 0] Kickoff orchestration — build the per-operator experience layer

**Ticket:** Basecamp [9967247739](https://app.basecamp.com/3945211/buckets/7463955/todos/9967247739)
**Status:** Active — kicked off 2026-06-05
**Owns:** Operator 1, 2, 3, 4, 5 (the 5 child specs)

---

## Mandate

Ali asked for the per-operator experience layer to be built end-to-end without manual orchestration on his side. This spec tells the autonomous Claude Code agent that takes the build forward (a) what to build in what order, (b) how to verify each build, (c) how to loop with Ali via email when visual review is needed, (d) what terminates the loop.

This is the **single source of truth for the build process**. Future sessions read this file at session start and continue from where the prior session left off.

---

## Build order (sequential, with one parallel pair)

```
Op 1 (per-user scaffold)
    │
    ▼
Op 2 (mandatory ticket doctrine)
    │
    ├──────────────┐
    ▼              ▼
Op 3 (faithful   Op 4 (auto-close
updates)         with confidence)
    │              │
    └──────┬───────┘
           ▼
Op 5 (operator memory + shared KB)
```

| Order | Spec | Why this slot | Hard dependencies |
|---|---|---|---|
| 1 | Operator 1 | Foundation — workspace scaffold + personal BC project + the 3-layer CLAUDE.md mechanism. Nothing else works until this exists. | [Auth 1] ✅, [Provision 1] ✅ |
| 2 | Operator 2 | The doctrine. Adds the "mandatory ticket" rule on top of Op 1's per-user CLAUDE.md. Touches the prompt-handling layer. | Op 1 |
| 3a | Operator 3 | Faithful BC comments. Independent of Op 4. Can run in parallel. | Op 2 |
| 3b | Operator 4 | Auto-close with confidence. Independent of Op 3. Can run in parallel. | Op 2 |
| 4 | Operator 5 | Operator memory + shared KB. Reads from the comment history Op 3 produces; reads from session-state Op 2 owns. | Op 1, Op 2, Op 3 |

---

## Process model — per Operator N

Each child Operator follows the same 7-step cycle:

```
1. PLAN     → write implementation plan inline in the spec (Acceptance criteria → concrete steps)
2. BUILD    → make code changes
3. TEST     → tsc --noEmit + relevant unit tests; for visual surfaces, render a preview
4. REVIEW   → email Ali with the visual + HTML reply form (only for surfaces Ali sees)
5. ITERATE  → incorporate Ali's reply, send follow-up; loop until Ali stops replying or approves
6. SHIP     → commit + push + deploy if applicable
7. CLOSE    → mark BC ticket complete via auto-close (per Op 4 logic, even before Op 4 ships)
```

The cycle is the same shape as the Build-Break-Harden loop from CLAUDE.md but with the **visual review** step formalized.

---

## Verification by build kind

Different specs produce different kinds of artifacts. Verification matches the artifact:

| Spec | Primary artifact | Verification |
|---|---|---|
| Op 1 | New scaffold files (per-user CLAUDE.md template, session-start hook script) + workspace-provisioning extension | Provision a test user end-to-end; confirm their workspace repo has the 4 expected files; confirm session-start hook fetches and concatenates the 4 layers correctly. **Visual review:** screenshot of the concatenated context banner shown to operator at session start. |
| Op 2 | New session-state.json mechanism + ticket-creation prompt + override flag handling | Run 3 test prompts: substantive prompt without ticket reference (must trigger ticket creation), substantive prompt with existing BC URL (must reuse), `--no-ticket` prompt (must bypass + log). **Visual review:** screenshot of the "creating ticket" confirmation Claude shows. |
| Op 3 | New `ticket_updater` module + 10 step-kind handlers + rate limiter | Run a synthetic 5-file-edit session against a test BC ticket; confirm 5 comments posted with correct cards + idempotent on re-run. **Visual review:** the live BC ticket comment thread. |
| Op 4 | New `auto_close` decision logic + confidence scorer integration + 2 comment templates | Synthetic session: complete a low-blast change (must auto-close); complete a high-blast change (must ask-to-confirm). **Visual review:** the 2 BC comments (auto-close summary + ask-to-confirm). |
| Op 5 | New `OPERATOR_MEMORY.md` template + capture triggers + KB scraper (3 colaberry.com sites + GitHub CLAUDE.md fetch) | Provision a test user; trigger 3 capture events (stated preference, correction, pattern observed 3x); confirm memory file updated. **Visual review:** rendered preview of the assembled context with the 5 layers visible. |

---

## Email-review loop — how visual review actually works

For every spec whose acceptance criteria include a visual review:

### Step A: Render the artifact

Claude produces the visual (PNG screenshot, rendered HTML, or BC ticket comment thread) and saves it to `tmp/operator-N-review-vYY.png` (or .html).

### Step B: Send the review email

A reusable helper `scripts/sendOperatorReviewEmail.js` (see [Op 0 ticket payload](#bc-ticket-payloads) for build) takes the spec ID + version number + artifact path + a structured set of review questions. It produces an HTML email with:

- Subject: `[Op N Review v{YY}] {spec title}`
- Body: artifact embedded as inline image (for screenshots) or rendered HTML
- A review form with **mailto:-link buttons** (one-click responses):
  - 🟢 **Approve this version** → mailto:claude+approve@colaberry.com with subject `Re: [Op N Review v{YY}] APPROVED`
  - 🟡 **Approve with comments** → mailto: with prefilled subject; body has a textarea-style instruction Ali fills in
  - 🔴 **Request changes** → mailto: with prefilled subject; body has a checklist of common changes + free-text field
  - ⚪ **Stop the loop** → mailto: with subject `Re: [Op N Review v{YY}] STOP` — terminates iteration without approval

- Includes the active BC ticket URL and the spec file URL in the footer

### Step C: Wait for reply

Claude polls Ali's inbox (via Gmail MCP, the existing read-only inbound capability) for replies matching the review's subject line. If a reply lands with one of the 4 categorical responses → categorize and act:

| Response category | Next action |
|---|---|
| APPROVED | Mark this review iteration complete. Proceed to next step in cycle. |
| COMMENTS | Read Ali's notes; make the requested changes; render new artifact; send follow-up review email (v{YY+1}). Loop. |
| CHANGES | Same as COMMENTS but expect bigger revisions. |
| STOP | Halt iteration. Post a BC comment on the ticket: "Review loop halted by Ali at v{YY}." Leave ticket open; surface to Ali in next session. |

### Step D: No reply within 48h

If no reply within 48 hours of the latest review email:
- Post a BC comment on the ticket: "No reply received in 48h on v{YY} review. Treating as 'awaiting Ali' state. Will not auto-proceed."
- Halt the loop for this spec; continue with parallelizable specs if any.

This is the **back-and-forth until replies are stopped by the user** loop from Ali's requirements, with a 48h safety net so the system doesn't auto-decide.

---

## Termination

The kickoff is **complete** when ALL of these are true:

1. Operator 1, 2, 3, 4, 5 are shipped (committed, pushed, deployed if applicable)
2. Each has its acceptance criteria checked off
3. Each has had its visual review approved OR explicitly halted by Ali
4. All 6 BC tickets (Op 0-5) are closed

The kickoff is **partially complete and paused** if any of:
- Ali sends STOP on a review → that spec halts; others can continue
- 48h no-reply on a review → that spec pauses; future session can resume
- An external dependency surfaces (e.g., need a new API key) → escalate via Op 0's parent BC ticket

The kickoff **never silently halts**. Every pause posts to BC.

---

## BC ticket payloads

When the kickoff first runs, it creates 6 BC tickets in list [9953889092](https://app.basecamp.com/3945211/buckets/7463955/todolists/9953889092):

| Spec | BC title | BC description |
|---|---|---|
| Op 0 | `[Operator 0] Kickoff orchestration — build per-operator experience layer` | Parent ticket for the 5-spec build. Tracks meta-progress. Closes when all 5 child tickets close. |
| Op 1 | `[Operator 1] Per-user CLAUDE.md + PROGRESS.md scaffold` | Per spec at `docs/specs/operator-01-per-user-scaffold.md`. |
| Op 2 | `[Operator 2] Mandatory ticket-driven work doctrine` | Per spec at `docs/specs/operator-02-mandatory-ticket-doctrine.md`. |
| Op 3 | `[Operator 3] Faithful ticket progress updates` | Per spec at `docs/specs/operator-03-faithful-ticket-updates.md`. |
| Op 4 | `[Operator 4] Auto-close tickets with confidence gate` | Per spec at `docs/specs/operator-04-auto-close-tickets.md`. |
| Op 5 | `[Operator 5] Operator memory + shared KB` | Per spec at `docs/specs/operator-05-operator-memory-system.md`. |

Each child ticket links to its spec file URL on GitHub and to the parent ticket.

---

## Files this kickoff creates

| File | Purpose |
|---|---|
| `docs/specs/operator-00-kickoff.md` | This file. |
| `scripts/sendOperatorReviewEmail.js` | The reusable email-review-loop helper. |
| `scripts/createKickoffBCTickets.js` | One-shot script that creates the 6 BC tickets at the start of the kickoff. |
| `PROGRESS.md` (root) | Updated per CLAUDE.md doctrine; one entry per kickoff step. |

---

## Future-session instructions

A Claude Code session opens this repo. The doctrine says: read CLAUDE.md, read PROGRESS.md, summarize current state.

In the context of this kickoff, the session must also:

1. Read `docs/specs/operator-00-kickoff.md` (this file)
2. Determine current build state by checking PROGRESS.md for `[Operator N]` entries and checking BC ticket status for each child ticket
3. Pick up at the first incomplete spec in build order
4. Follow the 7-step PLAN→BUILD→TEST→REVIEW→ITERATE→SHIP→CLOSE cycle
5. Do not stop the cycle for a single spec until either: shipped + closed, OR Ali sends STOP, OR 48h no-reply pause

Multiple sessions over multiple days can complete the kickoff. Each session resumes cleanly from PROGRESS.md + BC ticket state.

---

## How verification works end-to-end (the user-facing experience)

From Ali's perspective:

1. He gets the kickoff email today. It says "I'm starting Op 1. Here's the plan. I'll send you a review when there's something to look at."
2. A few hours / a day later, he gets `[Op 1 Review v01]` with a screenshot of the assembled context banner. He clicks 🟢 Approve.
3. Claude proceeds to commit + push + deploy Op 1, then auto-closes the Op 1 BC ticket. He gets a 1-line BC notification.
4. Same loop for Op 2: he gets `[Op 2 Review v01]`, clicks Approve or fills in changes.
5. Op 3 and Op 4 may overlap (parallel), so he might get two reviews in one window.
6. Op 5 wraps it up. The Op 0 parent ticket auto-closes when all 5 child tickets are closed.
7. He gets a final wrap-up email summarizing what shipped and pointing at the new system.

If at any point he wants to stop or change direction, he replies STOP on any open review. The loop pauses on that spec; he can resume by responding with new direction.
