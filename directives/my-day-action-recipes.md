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
| SessionStart hook that assembles doctrine context (full vs limited mode) | `execution/products/library/session_start_hook.py` |
| Tests | `tests/execution/products/test_ops_suggestions.py` |

## Action recipes

`suggestions.py` keys a recipe off a deterministic title/description regex
(no LLM call). Recipe kinds: `decision`, `reply`, `meeting`, `research`,
`build`, `review`, `schedule`, and a `default` fallback. Each recipe carries
`one_line`, `steps`, `resources`, and `stop_conditions`. `build_suggestion`
assembles the structured suggestion; `generate_prompt` wraps it in the
ready-to-paste Claude Code prompt.

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
