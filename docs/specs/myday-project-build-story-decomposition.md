# [My-Day Build] Story-driven, traceable, loop-buildable decomposition

**Status:** ✅ **BUILT** on `feat/story-driven-build-plans` (2026-06-28) — implemented in `deep_plan.py` (6-stage chain), `deep_plan_trace.py` (deterministic gate), `deep_plan_publisher.py` (dynamic releases + assigned/due-dated todos), `mcp_tools.py` (`create_ticket` due/assignee); generator declared a governed runtime entrypoint with a **compliant** TBI attestation. Tests + TBI gate green. PR open, not merged/deployed.
**Replaces the internals of:** [`deep_plan.generate_deep_plan`](../../execution/advisory/deep_plan.py) and the fixed-sprint model in [`deep_plan_publisher`](../../execution/advisory/deep_plan_publisher.py)
**Depends on / coordinates with:** [`myday_build_orchestrator`](../../execution/advisory/myday_build_orchestrator.py), [Operator 2 ticket doctrine](operator-02-mandatory-ticket-doctrine.md), the deep_plan TBI attestation
**Owner:** Ali (critique + harden) · Drafted by Claude

---

## Why this exists

Ali's diagnosis: the generated build plans "got shorter and shorter" (14 → 6 tickets), aren't detailed enough, and **don't coordinate the requirements document**. The fix must be structural, not cosmetic. Three root causes are confirmed in code:

| # | Root cause | Where |
|---|---|---|
| 1 | **Tickets are generated *blind* to the requirements doc** — `req`, `arch`, `tickets` run as 3 parallel threads off the same `ctx`; the ticket call never sees the generated requirements | [`deep_plan.py:162-166`](../../execution/advisory/deep_plan.py#L162-L166) |
| 2 | **A "story" is just a string field, no executable acceptance** — all sprints crammed into one 8K-token JSON; `story` + a freeform `test` string | [`deep_plan.py:84-89`](../../execution/advisory/deep_plan.py#L84-L89) |
| 3 | **The "≥12 tickets / no thin sprints" floor is not enforced** — `MAX_REFINE=2`; on any checker exception or empty `gaps` it keeps the thin draft; the *same generic rubric* grades docs and tickets | [`deep_plan.py:104-127`](../../execution/advisory/deep_plan.py#L104-L127) |
| 4 | **The 6 sprints are hard-coded** (`s0..s5`, fixed titles & `WEEK_SPAN`) regardless of the product's real capability journey | [`deep_plan.py:157-160`](../../execution/advisory/deep_plan.py#L157-L160), [`deep_plan_publisher.py:19`](../../execution/advisory/deep_plan_publisher.py#L19) |

**The principle (one sentence):** Each user story is an *executable, traceable, loop-buildable spec* — it cites the exact requirement IDs it fulfills, carries Gherkin acceptance criteria that double as the demo script and the build loop's stop condition, names its owning agent and trust controls, and ships a paste-ready build prompt.

---

## Locked framework decisions

"Split the requirements" is four decisions; each has a proven owner. Decisions confirmed with Ali (2026-06-26):

| Decision | Framework adopted | Resolution |
|---|---|---|
| **A. Atomic build unit** | **Event Modeling slice** (`Command → Event → Read-Model`) used *internally*, presented to the founder as a **User Story** | ✅ EM gives traceability-by-construction (Information Completeness Check) and a unit sized for an AI build loop; the founder never sees EM notation (decision 3) |
| **B. Agent carving** | **Event Storming + DDD Bounded Contexts** | ✅ The agent map is *derived* from requirements, not guessed; each slice's `owner_agent` comes from this map |
| **C. Sprint / demo sequencing** | **User Story Mapping** (Patton) — backbone → **walking skeleton** → release slices | ✅ Walking-skeleton-first, *reconciled* with the trust spine (the skeleton includes the audit-log + one approval gate, so "trust-first" is satisfied without a fixed s0) |
| **D. Acceptance detail** | **Example Mapping → Gherkin** | ✅ Rules + concrete Examples → executable acceptance = loop stop **and** demo script |
| **Loop** | **Loop Architect** (maker/checker, hard stop) | Per story, stop condition = "all acceptance scenarios pass" |

EM notation is **hidden** from the founder UI/Basecamp (decision 3): the generator reasons in slices; the founder reads a Story + Gherkin.

---

## The new pipeline — sequential & traceable (replaces the parallel model)

The single biggest change: **stages depend on each other** (kills root cause #1). Requirements come first *with stable IDs*; everything downstream cites those IDs.

```
1. Requirements (maker/checker)   → REQUIREMENTS.md + REQ-### catalog (each REQ has ≥2 acceptance criteria)
2. Agent map     (maker/checker)  → bounded contexts → agents (each owns a set of REQ-###); a Coordinator
3. Story slicing (maker/checker)  → STORY-### per capability; EACH cites fulfills:[REQ-###], Gherkin, owner_agent
4. Story map     (maker/checker)  → backbone + releases; release r0 = Walking Skeleton (thin end-to-end + trust spine)
5. Build guide   (maker)          → per-release educational walkthrough using each story's build-loop prompt
6. Traceability  (DETERMINISTIC)  → assert every REQ is fulfilled by ≥1 story & every story cites a valid REQ (fail-closed)
```

Stages 1–4 are maker/checker loops (separate rubrics, below). **Stage 6 is plain code, not an LLM** — a deterministic RTM gate that fails the build if traceability is broken. Per CLAUDE.md, determinism belongs in `/execution`, not in a model.

### Data shapes

**REQ catalog** (emitted alongside REQUIREMENTS.md so downstream stages can cite it):
```json
{ "requirements": [
  { "id": "REQ-004", "area": "Dispatch", "statement": "...",
    "acceptance": ["...", "..."], "priority": "must|should|could" } ] }
```

**Story slice** (the new atom — `story`-only field is gone):
```json
{
  "id": "STORY-012",
  "title": "Dispatcher approves a flagged load before booking",
  "fulfills": ["REQ-004", "REQ-011"],            // REQUIRED, non-empty, must resolve to real REQ ids
  "narrative": "As a dispatcher I want ... so that ...",
  "slice": { "command": "ApproveLoad", "event": "LoadApproved", "read_model": "ApprovalQueue" },
  "owner_agent": "Dispatch Coordinator",         // from the Stage-2 agent map
  "acceptance": [                                 // Gherkin = demo script + loop stop condition
    { "scenario": "Approval gate blocks auto-booking",
      "given": "a load the Risk agent flagged high-risk",
      "when": "the dispatcher has not yet approved it",
      "then": "booking is held and the load appears in the ApprovalQueue" },
    { "scenario": "Trust: every decision is audited",   // ≥1 trust scenario is MANDATORY per story
      "given": "the dispatcher approves the load",
      "when": "the approval is recorded",
      "then": "the audit log shows who approved it and when" }
  ],
  "trust": { "controls": ["approval gate", "audit log entry"], "inpact": ["Permitted", "Transparent"] },
  "build_loop": { "maker_prompt": "<paste-ready prompt>", "stop_when": "all acceptance scenarios pass",
                  "minimal_code_tools": ["Retool (queue UI)", "Supabase (audit table)"] },
  "estimate": "S|M|L", "release": "r0"
}
```

**Story map → releases** (replaces fixed `s0..s5`):
```json
{ "backbone": [ { "step": "Intake", "stories": ["STORY-001", "STORY-003"] } ],
  "releases": [
    { "key": "r0", "name": "Walking Skeleton",
      "goal": "thinnest end-to-end demo with the trust spine ON", "stories": ["..."], "weeks": [3,4] },
    { "key": "r1", "name": "...", "goal": "...", "stories": ["..."], "weeks": [5,6] } ] }
```
Release `weeks` are *derived* from the cohort calendar (program weeks 3–11), not a hard-coded `WEEK_SPAN` map.

---

## The prompt-chain (red-team this)

Shared system prompt keeps the existing depth doctrine (`_MAKER_SYS`) and the no-internal-code guard (`_guard`). New per-stage prompts:

**Stage 1 — Requirements maker** (unchanged intent, adds IDs):
> Write a DEEP REQUIREMENTS DOCUMENT for "{project}" … Number every functional requirement `REQ-001`, `REQ-002`, … Each REQ gets **≥2 concrete, testable acceptance criteria**. End with a machine-readable JSON `REQ catalog` block listing every id, area, statement, acceptance, priority.

**Stage 2 — Agent-map maker** (new):
> From the REQUIREMENTS below, derive the multi-agent organization using bounded contexts. Output JSON: each agent `{name, context, owns:[REQ-###…], commands_issued, events_reacted_to, autonomy, approval_gates, escalation}` plus one Coordinator. **Every REQ must be owned by exactly one agent** (list orphans explicitly).
> REQUIREMENTS: {reqs} · REQ CATALOG: {catalog}

**Stage 3 — Story-slicing maker** (new; sees reqs + agent map — *this closes root cause #1*):
> Slice the requirements into vertical-slice user stories, **one LLM call per capability cluster** (one cluster ≈ one backbone step from the agent map). For EACH story output the Story-slice schema above. **Hard rules:** `fulfills` must be non-empty and cite only ids from the REQ catalog; every story carries Gherkin acceptance with concrete values and **at least one trust scenario**; `owner_agent` must be a name from the agent map; one story = one demoable capability (INVEST). **Cover every `must` AND `should` REQ. There is no upper limit on story count — bias toward MORE depth, never fewer; minimum 12 stories total across the product.**
> REQUIREMENTS: {reqs} · REQ CATALOG: {catalog} · AGENT MAP: {agents} · CLUSTER: {cluster}

**Stage 4 — Story-map maker** (new):
> Arrange the stories into a story map: a capability backbone, then releases. **Release `r0` is the Walking Skeleton** — the thinnest path that runs end-to-end *with the trust spine on* (audit log + one approval gate). No release may contain a single story. Order later releases by demo value. Assign program weeks (3–11).
> STORIES: {stories}

**Stage 5 — Build-guide maker** (existing, now fed the full chain): unchanged except it consumes stories (with build-loop prompts) instead of raw tickets.

---

## The two rubrics (replaces the single shared `_rubric`)

**DOC rubric** (Stages 1, 5) — keep the current depth bar (≥1,500 words, no stubs, domain-specific, minimal-code external tools, TBI woven, multi-agent). Add: **Stage-1 fails if any functional REQ lacks an id or <2 acceptance criteria.**

**STORY rubric** (Stage 3) — *new, demanding, with explicit hard-fails. A story set fails (score <70) if ANY of:*

| Hard-fail | Rationale |
|---|---|
| Any story has empty/invalid `fulfills` | breaks traceability (root cause #1) |
| Any story lacks Gherkin acceptance with concrete values | no loop stop / no demo (root cause #2) |
| Any story has no trust scenario | TBI must be demoed per slice, not deferred |
| `owner_agent` not in the agent map | breaks the multi-agent map (decision B) |
| Any `must` REQ is fulfilled by zero stories | coverage gap |
| Fewer than 12 stories OR any release with a single story | depth floor (root cause #3) — now enforced on a structured set, not a soft word count. **No upper cap — "build out as much as possible"; the rubric never penalizes a *larger* story set.** |

Because Stage 3 emits **one story object at a time** (or per capability cluster) rather than one 8K-token blob, depth no longer competes for a shared token budget — the primary reason output drifted "shorter and shorter."

---

## Deterministic traceability gate (Stage 6 — code, not LLM)

A pure function in `/execution` (proposed `deep_plan_trace.validate(reqs, stories)`):
- every `STORY.fulfills[*]` resolves to a real `REQ.id` → else **fail-closed**
- every `must` `REQ.id` appears in ≥1 `STORY.fulfills` → else **fail-closed** (list orphan musts; the build does not publish)
- every `should` `REQ.id` uncovered → **warn + flag on the Basecamp Requirements doc**, does *not* block the build
- emit an **RTM** (`REQ-### ⇄ STORY-### ⇄ scenarios`) into `output/{slug}/` and onto the Basecamp Requirements doc

This is the "coordinate the requirements document" gap closed *by construction* — it cannot pass with floating stories. First-class unit tests cover it (CLAUDE.md `/tests` gate).

---

## What changes at publish time

[`deep_plan_publisher`](../../execution/advisory/deep_plan_publisher.py) today renders 6 fixed groups and a 7-row ticket table. New behaviour:
- Groups come from the **story map's releases** (dynamic count + names + week spans), not `WEEK_SPAN`.
- Each todo description gains a **"Fulfills: REQ-004, REQ-011"** line and the **Gherkin scenarios** (the demo script the student runs), in addition to design/build/vibe/trust.
- The RTM is appended to the Requirements document so a manager can see coverage at a glance.

---

## What's intentionally NOT in v1

- **Founder-facing Event Modeling notation** — hidden; internal only.
- **Auto-generated executable test code** from Gherkin — v1 stops at the scenarios as the stop condition; wiring them to a runner is v2.
- **Cross-project shared agents / a library of reusable slices** — defer.
- **Re-slicing on requirement change** (true living RTM with diffing) — v1 regenerates whole; incremental is v2.

---

## Resolved decisions (locked 2026-06-26 with Ali)

| # | Question | Resolution |
|---|---|---|
| 1 | Stage-3 granularity | ✅ **Per capability cluster** — one LLM call per backbone step. Balances depth vs. cost. |
| 2 | Minimum stories floor | ✅ **≥12 total, no upper cap.** "Build out as much as possible" — cover every `must` AND `should` REQ; the rubric never penalizes a larger set. |
| 3 | Walking skeleton scope | ✅ **Hard requirement for every product**, including tiny ones — the skeleton *is* the trust-spine demo and never collapses. |
| 4 | Sprint / release naming | ✅ **Fully dynamic** names derived from the story map; weeks 3–11 calendar anchoring retained. The old `s0..s5` vocabulary is dropped. |
| 5 | Trace-gate severity | ✅ **Fail-closed on an orphan `must` REQ** (build does not publish); **warn + flag** an uncovered `should` REQ on the Basecamp doc without blocking. Rationale: a missing must-have is a real coverage hole; a missing should-have is worth surfacing, not worth failing a student's build. |

---

## Governance / Definition of Done (when we build this)

Per CLAUDE.md, implementing this is a change to an **AI artifact** (the `deep_plan` prompt-chain governs AI behavior), so the build is not "done" until:
- the **deep_plan TBI attestation** (`<artifact>.tbi.json`) is refreshed and the CI gate is green;
- first-class **unit tests** cover the deterministic trace gate and each rubric's hard-fails (`tests/execution/advisory/`);
- an **end-to-end** run proves a real product produces a traceable, deep plan;
- the relevant **directive + PROGRESS.md** are updated;
- **no code is shipped from this spec without Ali's approval** (this doc is the critique surface first).
