# Directive: My Day Action Recipes & Copy-Prompt

## Purpose

Document the contract for the per-todo **action recipe** and the **"Copy prompt"**
text an operator pastes into Claude Code. This is the surface that turns a
Basecamp todo into a runnable Claude Code session. Two rules here are
non-negotiable because they govern what the AI is *allowed* to do on the
operator's behalf:

1. The AI produces a **recommendation** on human-owned decisions, never an
   authoritative verdict.
2. The copied prompt must tell the operator how to confirm they are running it
   in the **right environment**, because the doctrine/ticket context only loads
   in the provisioned workspace repo.

This directive is the source of truth for that surface. Any change to the code
referenced here must update the corresponding section so the document and the
code can never disagree silently.

## Where this lives in code

| Concern | File |
|---|---|
| Action-recipe table + recommendation reframing + prompt template | `execution/products/ops/suggestions.py` |
| Delivery personas (the "How I want you to work" block) + selector save | `execution/products/ops/personas.py`, `app/routers/my_day.py` (`/persona`), `tenancy.User.prompt_persona`, `app/templates/my_day/workspace.html` |
| "How to run this prompt" runbook — single source `_mdSetupBlock` — + the card/list Copy button | `app/templates/my_day/_my_day_styles.html` (`_mdSetupBlock`, `copyPrompt`) |
| Workspace-page notes box + Copy button (reuses `_mdSetupBlock`) | `app/templates/my_day/workspace.html` (`rebuildFullPrompt`, `copyWorkspacePrompt`) |
| Shared button classes + 5-band heat colors + scatter panel CSS | `app/templates/my_day/_my_day_styles.html` |
| Health bands + per-person rollup + BC deep-link derivation | `execution/products/ops/rollup.py` (`score_band`, `per_person`, `_bc_list_url`) |
| SessionStart hook that assembles doctrine context (full vs limited mode) | `execution/products/library/session_start_hook.py` |
| Tests | `tests/execution/products/test_ops_suggestions.py`, `tests/execution/products/test_ops_rollup.py` |

## Button standard (consistent across EVERY My Day surface)

There are exactly two action buttons on a task and they always look the same so
the operator learns them once:

- **📋 Prompt** — class `md-btn-prompt` (on Kanban: `ka-btn-prompt`). **Always
  black.** Copies the Claude Code prompt to the clipboard inline (via the shared
  `copyPrompt(promptId, btnEl)` in `_my_day_styles.html`, which prepends the
  setup runbook). On dense briefing-table rows and Heat map cards the prompt
  text is precomputed deterministically in the router (`row_prompts` for each
  list's next-blocking step, `seq_prompts` for every PROJECT TIMELINE row,
  `heat_prompts` for Heat map cards, `kanban_prompts` for Kanban cards) so the
  copy is a no-round-trip clipboard write.
- **⚙ Workspace** — class `md-btn-workspace`. **Always indigo (`#6639ba`).**
  Links to `/my-day/todo/{bc_id}` (or, for a Library suggestion card, the
  asset's library page). The workspace page is where the operator adds context
  before copying.

**Pairing invariant — every ⚙ Workspace ships with a 📋 Prompt.** A lone
Workspace button is a bug: the operator should never have to open the workspace
page just to get a prompt. This applies to *every* surface — the focus card,
the feasibility row's next-blocking step, **each PROJECT TIMELINE row** (these
were prompt-less before 2026-06-11; now bound to `seq_prompts`), Heat map cards,
Kanban cards, and the Library-suggestion card. The Library suggestion guarantees
a non-empty `claude_prompt` (router falls back to a minimal "use this asset"
prompt if `build_claude_prompt` yields nothing) so its Workspace is never lone.
`tests/app/test_my_day_timeline_prompt_pairing.py` renders the briefing page and
asserts `count(md-btn-workspace) == count(md-btn-prompt)`.

Any new surface that copies a prompt MUST use this pair and these colors. Other
buttons (Mark done = green, Skip = quiet) are unaffected.

## Workspace page is notes-first

`workspace.html` leads with the operator's **context box + Copy prompt** button
(the primary reason to open the page): drop a decision already made, work
already done, a constraint, or a steer, and it is prepended to the copied
prompt. The heavy reading — Suggested approach, Basecamp description, and the
full prompt preview — sits in collapsed `<details>` below. The JS hook IDs
(`#userNotes`, `#promptBuilder`, `#promptText`, `#copyPromptBtn`,
`rebuildFullPrompt`, `copyPrompt`) are unchanged; only the visual order moved.

## Heat map: 5 health bands, three groups, scatter

`rollup.score_band(score)` is the single source of truth for Heat map color: a
5-bucket red→green scale (b1 critical `<40`, b2 at-risk `40-54`, b3 watch
`55-69`, b4 steady `70-84`, b5 on-track `85+`). The `heat_class()` macro in
`heatmap.html` mirrors these exact thresholds — change one, change both. The
Heat map renders three groups (Projects, Lists, **People** via `per_person`)
plus a Plotly scatter (X = score, Y = ticket-late %, bubble size = open count,
color = band, shape = group). Each card carries only the Prompt/Workspace pair
for its next blocking task; the title drills into Briefing (using the
background-load overlay) and the BC deep-link opens Basecamp.

## Action recipes

`suggestions.py` keys a recipe off a deterministic title/description regex
(no LLM call). Recipe kinds: `decision`, `reply`, `meeting`, `research`,
`build`, `review`, `schedule`, and a `default` fallback. Each recipe carries
`one_line`, `deliverable`, `steps`, `resources`, and `stop_conditions`.
`build_suggestion` assembles the structured suggestion; `generate_prompt` wraps
it in the ready-to-paste Claude Code prompt.

**`deliverable` is mandatory and answers "what am I producing?"** It is one
sentence naming the concrete artifact AND where it goes (e.g. the decision
recipe: "a recommendation — verdict · reason · next action — saved with
`colaberry_remember` and posted to the BC ticket"). It renders as the prompt's
`## You hand back` section. `test_every_recipe_declares_a_deliverable` enforces
that no recipe leaves it blank — an empty deliverable is the vagueness this
surface exists to kill.

**The prompt is BLUF (Bottom Line Up Front).** `_PROMPT_TEMPLATE` leads with the
title as an H1, the one-line ask, `## You hand back`, the `## Ownership` line
(when human-owned), and `## Stop & escalate if` — the questions a newcomer asks
before starting. Only then, below a divider, come the heavier `## Task details`,
`## Description`, `## Suggested steps`, `## Lean on`, and the working protocol.
This order is load-bearing; `test_generate_prompt_is_bluf_ordered` asserts the
title is the first line and `## You hand back` precedes `## Task details`. The
copied-prompt assembler (`workspace.html` `rebuildFullPrompt`) does NOT wrap the
task in a `## Task` heading — the template self-titles with `# {title}`, and a
wrapper produced a confusing double heading.

**One renderer for every surface, including the LLM focus card.**
`generate_prompt` is the single prompt assembler. The deterministic surfaces
call it directly; the **LLM-enhanced focus card** (and the autopickup worker)
call `llm_suggest.enhance` for ticket-specific *fields* (`goal_line`,
`specific_steps`, `stop_conditions`), fold them into the deterministic
suggestion with `merge_llm_suggestion`, and render through the **same**
`generate_prompt`. `llm_suggest.py` no longer writes a `claude_code_prompt` — it
returns fields only (cache `PROMPT_VERSION` bumped to `v5`). This is the runbook
lesson applied again: a second prompt-assembler drifts; one template can't. The
focus path passes `comments=` so recent BC thread text renders as a
`## Recent comments` block, and the router appends `standing_orders` to the
focus prompt on both the LLM and deterministic branches. `merge_llm_suggestion`
keeps the deterministic ownership note, resources, and HTML-clean description.

**Delivery personas — the operator picks how information reaches them.** The
`## How I want you to work` block is not fixed: each operator chooses one of six
personas (`personas.py`) — Co-pilot (paced, the default), Just the answer (BLUF),
Visual-first (builds a **professional data-storytelling decision sheet**: a
self-contained HTML page auto-opened in the browser, styled like a clean
business one-pager — slate executive palette, white panels, Segoe UI,
one restrained accent (teal), minimal emoji; explicitly *not* the old colorful/neon
look. As of the 2026-06-26 BC Reference-Kit merge it also **tells the story in
the data**: it models the task into one embedded source-of-truth object and picks
visuals from the *shape* of that data — KPI cards for headline numbers, Chart.js
bar/doughnut/grouped-bar/line for counts and trends, Mermaid gantt/flowchart/
sequence for time and process, and a conditional-color heatmap table for
coverage/matrix — each with a one-line "so what" caption, drilling from headline
to detail. It leads with a plain-English brief — **What this is** / **What you need
to do** — plus a facts row (project / list / due / urgency / owner). It surfaces
only the **few** decisions that change the outcome, each with the recommended
answer pre-selected *and* an "Other" write-your-own text box, and states the rest
as an overridable "Assumed defaults" line — so the operator isn't asked every
little question. The Basecamp-actions checkbox panel — complete / comment /
@mention / add people / due date / move / follow-ups — comes **pre-ticked and
pre-filled** for the moves the task implies (drafted comment, people to tag), so
the operator adjusts rather than decides each one. A "Copy Claude Code prompt"
button at top and bottom assembles a ready-to-run prompt from the filled-in form,
so the operator reviews, clicks, and pastes back to execute everything at once;
dyslexia-friendly),
Explain it to me (reasoning + teaching), Checklist doer, and **Plain & friendly**
(for the many non-technical people going through the program who just want to
"vibe" and build: plain language only, no jargon/acronyms/code, the AI handles
all the technical work rather than asking the operator to run commands or make
technical choices, decisions framed as plain outcome questions, and a warm,
conversational tone instead of a coding session). The choice is stored
server-side on `tenancy.User.prompt_persona` (set via `POST /my-day/persona` from
the selector at the top of the workspace page) and passed to `generate_prompt`
at every call site, so it applies to **every** surface and device until changed.
`persona=None` resolves to `copilot`, so an operator who never picks sees today's
behavior (no regression). The block always starts with the same
`## How I want you to work` header so the BLUF structure is stable; only the
guidance under it changes. To add a persona, append to `PERSONAS` — no other code
change. Tests: `test_personas.py`, the persona cases in `test_ops_suggestions.py`,
and the selector/endpoint cases in `test_my_day_workspace_copy_prompt.py`.

**"Create a new project" — idea → AI org → Basecamp build plan, in the background.**
Entry points: a "🚀 New project" item in the top "+ Add" menu and a button on the
My Day briefing, both linking `/advisory/?myday_build=1`. The flow reuses the full
advisory discovery (idea → 10 questions → design → capabilities), then diverts at a
**build-setup** step (`GET/POST /advisory/{sid}/build-setup` → `start-build`) that
collects the **target Basecamp project** (dropdown from `store.load_projects`) and a
**pace** (Sprint ≈1wk / Standard ≈1mo / Relaxed ≈3mo). `start-build` runs the 9-stage
advisory generation synchronously (so the org page renders + the project slug exists),
then kicks a background daemon (`myday_build_orchestrator.kick_build`) and redirects to
the AI-organization page with a live progress banner (polls
`/advisory/{sid}/build-status.json`). The background sequence is **joined and automatic**:
generate the full **Build Guide** (`full_pipeline`) → parse its chapter spine
(`build_guide_parser`) + generate BUILD/BREAK/HARDEN tasks per feature
(`feature_task_generator`) into a desired-state **`project-plan.json`**
(`plan_builder`, schema `cb-project-plan/v1`) → **verify** it against the fail-loud
validation gate (`project_plan.validate_plan`) → **reconcile** it into Basecamp
(`project_plan_reconciler`: initiative→todolist, feature→group, todo→todo; every todo
assigned to the creator + due-dated by pace; idempotent via `bc_manifest`) → resync My
Day. AI-vs-human tasks are encoded in the todo (🤖/🧑 + `[AI]`/`[Human]`) and read by
`scorer.task_kind` so they land in the right `tier=human`/`tier=ai` split. Doc edits are
applied as a bounded delta (`project_plan_reparse`: added→`proposed`, removed→`retired`,
unchanged→verbatim). Design specs: `docs/specs/myday-project-build-*.md`. Tests under
`tests/execution/advisory/test_project_plan*.py`, `test_plan_builder.py`,
`test_bc_manifest.py`, `test_build_status.py`, and `tests/app/test_myday_build_routes.py`.

**The CONTEXT block links the list and project, not just names them.**
`generate_prompt` emits the **task URL, the list URL, and the project URL** in
the `## Task details` block, derived from the todo's own app URL via
`execution/products/ops/bc_urls.py` (`OpsTodo.list_url` / `OpsTodo.project_url`;
the rollup reuses the same module). A title is not a pointer: a fresh session
must be able to open the list to see sibling tasks and judge project scale. When
the description carries `Depends-on:` / `Artifact:` markers (an approval/review
task), `generate_prompt` lifts them into a **## Dependency** block, and an
`Artifact: PENDING` is stated plainly as "not an approver delay." The full
contract for those markers, the generation gate, and the runtime scorer reroute
lives in [approval-task-dependency-linking.md](approval-task-dependency-linking.md).

**Resources must reference things that actually exist.** A `resources` entry of
`kind == "skill"` must name a skill that ships in the harness (currently
`deep-research` and `code-review`). Pointing an operator at a skill that does
not exist (the historical `decision-record` / `email-tone-check` /
`agenda-tight` / `cb-context-walker` bug) sends them chasing a tool that isn't
there. For decision capture, recipes point at the real MCP tools
`colaberry_remember` (operator memory) and `colaberry_save_doc_to_bc` (durable
BC artifact). `test_no_recipe_references_a_dead_skill` enforces this.

## Rule 1 — Recommendation, not verdict, on human-owned decisions

A todo is **human-owned** when either:

- the scorer tagged `category == "human_required"`, OR
- the PMO stamped a `HUMAN TASK` marker in the description.

For these, the AI cannot satisfy a Definition of Done like "Ali confirms the
decision" — only the named owner can. So a recipe may declare
`human_required_step_overrides`, a map of `{step_index: replacement_text}`.
`build_suggestion` applies the overrides only when the todo is human-owned,
interpolating `{owner}` (pulled from the `Owner:` marker, or the generic
"the owner" when no name is present).

Today only the `decision` recipe declares an override: its "Write a 3-line
decision: verdict, reason, next action" step becomes "Draft a recommendation
... for {owner} to confirm. Do NOT post it as the final decision; the call is
theirs." When an override fires, `generate_prompt` also emits an `## Ownership`
section restating that final confirmation rests with the owner. That section
sits in the BLUF header (directly under `## You hand back`), not at the bottom —
who owns the call modifies the deliverable, so the reader needs it up front.

Genuinely delegated decisions (no human marker, not `human_required`) keep the
original verdict wording and get no ownership note. To extend this rule to
another recipe kind (e.g. `review`), add a `human_required_step_overrides` map
to that recipe — no other code change is needed.

## Rule 2 — No clone: the prompt runs against the Colaberry MCP

The per-operator workspace repo (`{name}-workspace`) is a **behind-the-scenes
sync artifact** — operators never clone or touch it directly. The doctrine
layers, the mandatory-ticket protocol, and the Basecamp + memory tools all
reach the operator's Claude Code session through the **Colaberry MCP**
(`colaberry://doctrine/*` resources, served by `mcp_doctrine.py` and read at
session start), NOT through a cloned repo's SessionStart hook.

So the copied prompt's runbook carries **no clone / cd / git pull / PREFLIGHT**
steps. The only setup it states: open Claude Code (any folder) with the
Colaberry MCP connected, then paste the prompt. The MCP connection is the single
source of context — if it's connected the doctrine is loaded; if not, the
Basecamp tools are simply absent and the operator reconnects at
`/profile/welcome`.

**The runbook has exactly one definition: `_mdSetupBlock()` in
`_my_day_styles.html`.** Every copy surface uses it — the card/list/kanban/
heatmap/focus buttons via `copyPrompt()`, and the workspace page via
`rebuildFullPrompt()` (which calls the same function). Do NOT add a second copy.
History (2026-06-18): a duplicated `_setupBlock` in `workspace.html` was updated
to the MCP model while `_mdSetupBlock` kept the **old clone-based runbook**, so
every card and list row shipped stale "git clone / cd / git pull / SessionStart
hook" setup for days after the workspace page was fixed. Collapsed to one source;
`test_setup_runbook_is_mcp_model_not_clone_based` guards against the drift.

**Neither copy path wraps the task in a `## Task` heading.** The generated
prompt self-titles with `# {title}` (BLUF), so `copyPrompt` /
`rebuildFullPrompt` concatenate the runbook + the prompt directly. A `## Task`
wrapper produced a confusing double heading;
`test_no_double_task_heading_in_copy_paths` guards it.

History: an earlier design delivered context via a SessionStart hook that only
fired inside the cloned workspace repo, guarded by a PREFLIGHT "STOP if not in
the repo" instruction. That required operators to clone a private repo and hold
a personal GitHub account as a collaborator — which was never the intended
model (the repo is server-side only). The MCP-resources path supersedes it;
`session_start_hook.py` remains for the server-side scaffold, not the operator.

## Inputs and outputs

**Inputs:** an `OpsTodo` (title, description, category, owner marker, due date,
BC URL) from the local store; the operator's workspace repo URL from their
tenancy record.

**Outputs:** a structured suggestion dict (`build_suggestion`) and a
ready-to-paste Claude Code prompt string (`generate_prompt`) rendered in
`/my-day/` and copied by the Copy button.

## Edge cases

- **Decision keyword inside a build/research task.** Recipes are matched
  most-specific-first and `decision` is early in the table, so a description
  containing "confirm"/"approve" can win over a later kind. This is intentional
  (the decision framing is the safer default), but test data must avoid
  decision keywords when asserting a non-decision kind.
- **`human_required` with no owner name.** Override still fires; `{owner}`
  falls back to "the owner".
- **Owner marker but no category yet** (PMO stamped, scorer hasn't run).
  Still treated as human-owned via the `HUMAN TASK` marker.

## Verification

- `tests/execution/products/test_ops_suggestions.py` — recipe matching, the
  recommendation reframe (per-name and generic-owner), the `## Ownership`
  section, non-human decisions keeping verdict wording, and the no-dead-skill
  guards.

Before changing the recipe table, the override mechanism, or the runbook, run
this suite and add a test for the new behavior. CLAUDE.md is strict:
deterministic execution + test-first verification.

## Related

- [my-day-bc-sync.md](my-day-bc-sync.md) — how todos land in the local store this surface reads from
- `execution/products/library/session_start_hook.py` — the doctrine context the preflight checks for
