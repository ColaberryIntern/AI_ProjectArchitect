# Directive: Approval / Review Task Dependency Linking & Generation Gate

## Purpose

Define the contract for **how an approval or review task references the thing it
is supposed to approve**, and **when such a task is allowed to exist at all**.

This directive exists because of a concrete failure: a generated approval task
("Draft sales call script for outreach to alumni" → approve it) named its
upstream deliverable by **title only**, with no link to the drafting task, the
artifact, or the parent list. A fresh session — human or AI — could not navigate
to the thing it was asked to approve. The gate stalled, and the scorer escalated
it as an **approver delay** when the real state was "artifact reachable but
unlinked." This produced **8 days of false CRITICAL RISK escalation** on a task
that was actionable in minutes.

The root cause is that a **title is not a pointer**. A task template that lists
its dependency as plain text assumes the reader already knows where the
dependency's output lives. For a fresh session with no prior context, that
assumption is always false.

## The binary acceptance test (the whole point)

> A fresh Claude Code session, given **only** the generated approval task, can
> open the artifact and review it **without asking the operator for a single
> link**. If it has to ask "where is the thing I'm approving," the template
> failed.

Every rule below exists to make this test pass. Any change that does not move
toward this test is out of scope.

## Implementation status (2026-06-14)

- **Done (this repo).** The per-todo prompt now emits the task/list/project URLs
  and lifts `Depends-on:` / `Artifact:` markers into a **## Dependency** block
  (`suggestions.py`, `llm_suggest.py`, `bc_urls.py`, `store.py`). The runtime
  generation gate is live: the scorer reclassifies any task carrying
  `Artifact: PENDING` to `waiting_dependency` so it never enters the
  human_required/CRITICAL approver-delay band (`scorer.py`). Tests in
  `test_ops_scorer.py` and `test_ops_suggestions.py`.
- **Pending (Accelerator repo).** `generateLaunchTasks.js` must start stamping
  the `Depends-on:` / `Artifact:` / `List:` markers and sgid-bearing upload links
  when it generates approval/review pairs (the "Generation side" half below).
  Until then the runtime gate and prompt surfacing are inert for legacy tasks
  that carry no markers — they degrade to the prior behavior, no regression.

## Definitions

- **Drafting task** — the upstream todo that *produces* the artifact (the draft,
  doc, upload, or decision input).
- **Approval/review task** — the downstream todo whose job is to approve,
  review, sign off on, or reject the drafting task's artifact. Matched today by
  the `decision` and `review` recipes in
  `execution/products/ops/suggestions.py`.
- **Artifact** — the concrete deliverable to be reviewed: a BC vault upload, a
  Google Doc, a BC todo description, or a comment body.
- **Approver** — the human or AI session that acts on the approval task.

## Required links — every generated approval/review task must carry all three

A title is never sufficient. The generator must embed, in the task description:

1. **Dependency task link.** A direct BC URL to the drafting todo that produces
   the artifact, not just its title.
   Format: `https://3.basecamp.com/<acct>/buckets/<proj>/todos/<id>`.
2. **Artifact link.** A direct link to the deliverable itself. Format rules
   below differ by artifact type — a bare `/uploads/<id>` is **banned** (see
   "AI-reachability").
3. **List link.** The BC todolist URL the task belongs to, so the reader can see
   sibling tasks and judge the project's scale.
   Format: `https://3.basecamp.com/<acct>/buckets/<proj>/todolists/<id>`.

These are embedded with an explicit machine-readable marker so downstream code
(scorer, escalation, prompt renderers) can find them deterministically — the
same marker style as the existing `Owner:` / `HUMAN TASK` markers parsed in
`suggestions.py`:

```
Depends-on: <drafting todo URL>
Artifact: <artifact URL or "PENDING">
List: <todolist URL>
```

`Artifact: PENDING` is the explicit "not attached yet" state and is what the
generation gate keys on. It must never be silently omitted.

## Generation gate — prevents the false-escalation loop

**Do not generate an approval/review task as active and queue-eligible until its
dependency is done AND its artifact is attached and linkable.**

- If the dependency is incomplete, any reminder or escalation belongs on the
  **drafting task and its owner** — never on the approval gate or the approver.
- An approval gate whose `Artifact:` is `PENDING` (no attached, linkable
  artifact) **must not enter an approval queue and must not escalate** as an
  approver delay.

### Two halves, because a one-shot generator can't see the future

The launch PMO generator runs **once, up front**, before any artifact exists. It
therefore cannot itself observe "artifact now attached." The gate is enforced in
two places:

- **Generation side (`generateLaunchTasks.js`, Accelerator repo).**
  - Create the **drafting task first**, capture its returned BC todo id/URL, then
    create the approval task with `Depends-on:` and `List:` already populated and
    `Artifact: PENDING`.
  - Never emit an approval task whose dependency it did not also create (no
    dangling title-only references).
- **Runtime side (this repo, `execution/products/ops`).** The scorer and
  escalation must treat an approval task with `Artifact: PENDING` (or an
  incomplete `Depends-on` dependency) as **blocked-on-drafter, not
  approver-delayed**: redirect urgency to the drafting task + owner, and suppress
  the CRITICAL approver-delay escalation on the gate itself. This is the runtime
  backstop for the 8-day false escalation.

## AI-reachability — links alone are not enough for an AI session

A human can follow a link. An AI session also needs the artifact's **content**
reachable. Two hard requirements:

1. **BC vault uploads must link with the blob `sgid`.** `colaberry_attachment_fetch`
   retrieves an upload by its `attachment_sgid`; the bare `.../uploads/<id>` form
   fails with `basecamp_http_400` (empty attachment_sgid). The generator must
   emit the sgid-bearing form so the artifact is fetchable, not just clickable.
   See the `reference_attachment_fetch` and `bc_token_scope_limit` operator
   notes.
2. **BC todo descriptions / comments must be pasted inline.** There is currently
   no MCP tool that reads BC todolist or todo *contents*. If the artifact is a BC
   todo description or a comment body, the generator must paste that text into
   the approval task's prompt verbatim — a link to it is not retrievable by an AI
   session.

## Inputs and outputs

**Inputs:** an approval/review `OpsTodo` whose description carries the generator
markers `Depends-on:` / `Artifact:` / `List:`; the drafting task's BC todo
id/URL captured at generation time; the artifact's sgid (for uploads) or inline
text (for BC todo/comment bodies).

**Outputs:** a per-todo prompt whose CONTEXT block links the task, list, project,
drafting task, and artifact (the binary acceptance test passes); and a scorer
verdict that routes a `PENDING`-artifact gate to `waiting_dependency` instead of
the human_required/CRITICAL approver-delay band.

## Where this lives in code

| Concern | File | Repo |
|---|---|---|
| Generate drafting-then-approval pairs; embed `Depends-on:` / `Artifact:` / `List:`; sgid upload links; inline BC text | `generateLaunchTasks.js` | Accelerator (`ColaberryEnterprise_AI_LeadershipAccelerator`) |
| List/project URL derivation reused by the prompt | `execution/products/ops/rollup.py` (`_bc_list_url`, `_bc_project_url`) | this repo |
| Prompt CONTEXT block must surface task + list + project + dependency links | `execution/products/ops/suggestions.py` (`generate_prompt`, `_PROMPT_TEMPLATE`), `execution/products/ops/llm_suggest.py` (`SYSTEM_PROMPT`, `_build_user_message`) | this repo |
| Runtime gate: reroute escalation from approval gate to drafter when `Artifact: PENDING` | `execution/products/ops/scorer.py`, `execution/products/ops/rollup.py` | this repo |
| Marker parsing (mirror the `Owner:` / `HUMAN TASK` pattern) | `execution/products/ops/suggestions.py` | this repo |

## Verification

- Generator: a unit test asserting every emitted approval/review task contains a
  resolvable `Depends-on:` URL, a `List:` URL, and either a sgid-bearing
  `Artifact:` URL or `Artifact: PENDING` — never a title-only reference.
- Runtime gate: a test asserting an approval task with `Artifact: PENDING` is
  **not** scored into the CRITICAL approver-delay band, and that its urgency is
  attributed to the drafting task instead.
- Prompt: a test asserting `generate_prompt` and the LLM CONTEXT spec include the
  list URL and (when present) the `Depends-on:` artifact link.
- The binary acceptance test above is the end-to-end gate: a session given only
  the approval task can reach and read the artifact.

Per CLAUDE.md these tests are written **before** the logic and gate the change.

## Related

- [my-day-action-recipes.md](my-day-action-recipes.md) — the prompt surface that renders these links; its CONTEXT block is extended by Fix A.
- [my-day-bc-sync.md](my-day-bc-sync.md) — how todos (and their descriptions, where the markers live) land in the local store.
- Operator memory: `reference_attachment_fetch` (sgid fetch), `bc_token_scope_limit` (why customer-project BC writes 404), `reference_launch_pmo_generator` (the generator is in the Accelerator repo and destructive to re-run).
