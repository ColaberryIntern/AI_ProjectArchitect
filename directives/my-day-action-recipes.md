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
| "How to run this prompt" runbook + preflight + the Copy button | `app/templates/my_day/workspace.html` (`_setupBlock`, `copyPrompt`) |
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
`one_line`, `steps`, `resources`, and `stop_conditions`. `build_suggestion`
assembles the structured suggestion; `generate_prompt` wraps it in the
ready-to-paste Claude Code prompt.

**The CONTEXT block links the list and project, not just names them.** Both
prompt paths — `generate_prompt` (deterministic) and the `claude_code_prompt`
spec in `llm_suggest.py` — emit the **task URL, the list URL, and the project
URL** in the CONTEXT block, derived from the todo's own app URL via
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
section restating that final confirmation rests with the owner.

Genuinely delegated decisions (no human marker, not `human_required`) keep the
original verdict wording and get no ownership note. To extend this rule to
another recipe kind (e.g. `review`), add a `human_required_step_overrides` map
to that recipe — no other code change is needed.

## Rule 2 — Environment preflight in the copied prompt

The runbook (`_setupBlock`) targets the operator's **provisioned workspace
repo** (the GitHub URL on their tenancy record), NOT the central
`AI_ProjectArchitect` build repo. The doctrine layers, shared KB, tenant
policy, and the mandatory-ticket protocol are assembled by the SessionStart
hook **only inside the workspace repo**, and only in *full mode* when the
central repo is installed side-by-side; otherwise the hook runs in *limited
mode* (short banner + ticket protocol only — KB and tenant-policy layers
missing). See `session_start_hook.py` `main()`.

Because nothing enforces *where* the operator runs `claude`, the copied prompt
carries a **PREFLIGHT** instruction: confirm the terminal is inside the
workspace repo and that the "Colaberry SessionStart hook" banner printed, and
STOP if running in `AI_ProjectArchitect` or any other directory. This is the
agreed fix for the environment-mismatch class of bug (operator pastes a
workspace prompt into a session running in the build repo, so none of the
promised context is loaded). The runbook also no longer overstates the 5-layer
assembly as guaranteed; it describes both full and limited hook modes.

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
