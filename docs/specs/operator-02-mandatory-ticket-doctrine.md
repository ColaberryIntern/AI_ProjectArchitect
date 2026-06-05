# [Operator 2] Mandatory ticket-driven work doctrine

**Ticket:** Basecamp [9967247783](https://app.basecamp.com/3945211/buckets/7463955/todos/9967247783)
**Status:** Designed; not yet built
**Depends on:** [Operator 1] (per-user scaffold), [Auth 1] ✅ (user identity), [Provision 1] ✅ (personal BC project)

---

## Why this exists

Ali's exact words: *"every single request will have a ticket created in their personal BC page. You will NOT do work if it's not a new ticket or part of an existing ticket."*

Today Claude Code will happily do unbounded substantive work in any session — edit files, send emails, deploy code — without any persistent record tying that work to a tracked unit. PROGRESS.md captures it after the fact, but there's no ticket up front, no shared visibility for managers, and no auditable thread per task.

The doctrine: every Claude Code session is anchored to exactly one Basecamp ticket. No ticket → Claude Code refuses substantive work and creates one before proceeding.

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Doctrine encoded in **org-level CLAUDE.md** (so it applies to every Colaberry user, not just Ali) | New section in `ColaberryIntern/colaberry-policy/CLAUDE.md` — "Mandatory Ticket-Driven Work" |
| 2 | Each user has a **personal Basecamp project** auto-provisioned at onboarding | `personal_bc_provisioner.provision(user_email)` — creates `{First Last} Personal` BC project, grants user access, returns project_id |
| 3 | Session-start protocol: Claude detects ticket reference in prompt OR creates one | New session-start helper reads prompt, looks for `BC#<id>` or URL, otherwise prompts to create |
| 4 | "Substantive work" defined explicitly | Code edits / file creation / external sends / deploys → ticket required. Read-only Q&A / dry-runs / `git status` → no ticket needed. |
| 5 | Ticket creation flow: 1 BC API call, user does not have to type the title | `bc_ticket.create(project_id, title, description, due_on?)` — title auto-derived from user's first prompt, user can edit before confirm |
| 6 | Explicit override path: user can prefix prompt with `--no-ticket` | Logged as `ticket_bypass` event with reason; surfaces in weekly audit. Decision rationale below. |
| 7 | Ticket reference persists across multi-turn session | Stored in `.claude/session-state.json` at session start; re-read on every turn |

## Architecture

### Org CLAUDE.md addition (the doctrine itself)

New top-level section in the org CLAUDE.md, layered above everything else:

```markdown
## Mandatory Ticket-Driven Work (NON-NEGOTIABLE)

Every Claude Code session that performs substantive work MUST be anchored
to exactly one Basecamp ticket in your personal Basecamp project.

**Substantive work** (ticket required):
- File creation, edits, deletions
- External sends (email, Slack, BC comments outside the active ticket)
- Deploys, infra changes, schema migrations
- Anything that produces a commit

**Not substantive** (no ticket needed):
- Read-only questions ("explain this code", "what does X do")
- Dry-runs, audits, `git status`, `git log`
- Loading context at session start

**Session-start protocol:**
1. If the user's first prompt references a BC ticket (URL or `BC#<id>`),
   that's your active ticket. Read it before doing anything else.
2. If no ticket reference, create one in their personal BC project
   BEFORE doing substantive work. Use the user's first prompt as the
   ticket title (let them edit it). Post a confirmation comment with
   the session ID once created.
3. If the user says "no ticket needed, just X" — respect the override,
   but log it as `ticket_bypass` with reason.
```

### Personal Basecamp project provisioning

Extend the existing onboarding flow (in `admin.py::user_new`) with one more step:

```python
def provision_user_personal_bc(user_email: str, display_name: str) -> dict:
    """Create the user's personal Basecamp project + invite them."""
    project = bc_api.create_project(
        account_id=COLABERRY_ACCOUNT_ID,
        name=f"{display_name} Personal",
        description=f"Personal workspace for {display_name}. "
                    f"All Claude Code work flows through this project."
    )
    bc_api.grant_project_access(project["id"], user_email, role="owner")
    record_audit("personal_bc_provisioned", user_email, project_id=project["id"])
    return project
```

Project ID is stored on the user row: `users.personal_bc_project_id`.

### Session-state file

Per-session, Claude Code writes `.claude/session-state.json`:

```json
{
  "session_id": "CC-20260605-4w8q",
  "active_ticket": {
    "bucket_id": "7463955",
    "todo_id": "9999999999",
    "url": "https://app.basecamp.com/...",
    "title": "Investigate stale-task sync bug",
    "started_at": "2026-06-05T14:30:00Z"
  },
  "ticket_bypass": false
}
```

Every Claude Code turn reads this file first. If `active_ticket` is missing and the user is asking for substantive work, Claude must create a ticket or invoke the bypass path before proceeding.

## Workflows

### Workflow A: User starts a new session, no ticket reference

```
User: "Can you help me fix the inbox COS alert spam?"

Claude:
  1. Reads session-state.json → no active ticket
  2. Detects substantive work intent ("fix")
  3. Replies: "Before I start — I'll create a Basecamp ticket in your
     personal project to track this. Proposed title:
     'Fix inbox COS alert spam'. Edit it or confirm?"
  4. On confirm: bc_api.create_todo(user.personal_bc_project_id, title, ...)
  5. Writes the new ticket info to session-state.json
  6. Posts a comment on the new ticket: "Session CC-20260605-4w8q started."
  7. Proceeds with the work
```

### Workflow B: User starts a new session referencing an existing ticket

```
User: "Continue work on BC#9999999999 — there were 3 deferred items"

Claude:
  1. Reads session-state.json → no active ticket
  2. Parses BC#9999999999 from prompt
  3. bc_api.get_todo(9999999999) — verifies user has access
  4. Writes ticket to session-state.json as active_ticket
  5. Reads the ticket's full comment history for context
  6. Proceeds with the work
```

### Workflow C: User invokes override

```
User: "No ticket needed, just tell me what files reference X"

Claude:
  1. Detects override phrase
  2. Logs ticket_bypass with reason="user-explicit-readonly-query"
  3. Proceeds without a ticket
```

## What's intentionally NOT in v1

- **Ticket-type taxonomy** (bug / feature / chore / spike) — defer to v2. v1 = one flat ticket per session.
- **Cross-ticket sessions** (working on 2 tickets in 1 session) — defer. v1 = 1 ticket per session. If user needs to switch, start a new session.
- **Auto-link to existing tickets by keyword match** — e.g., "this is related to the work on BC#XXX" — defer. v1 = user passes URL/ID explicitly or creates new.
- **Mandatory ticket due-date** — v1 = optional. Operator-04 (auto-close) handles the lifecycle.

## Open questions

| # | Question | Decision |
|---|---|---|
| 1 | Override mechanism | ✅ **Resolved 2026-06-05 (Claude decided per Ali's "you decide"):** Explicit `--no-ticket` flag at the start of the prompt. Rationale: (a) clearly intentional, no one types it by accident; (b) familiar CLI-flag pattern; (c) doesn't conflict with natural English; (d) symmetric `--ticket` flag forces ticket creation even on read-only-shaped prompts. Logged with reason in `ticket_bypass` audit event. |
| 2 | Personal BC project for legacy users | Default: auto-provision lazily on first need. |
| 3 | Where does Karun's personal BC live | Default: Colaberry's BC account (work on the Colaberry platform is Colaberry work). |

## Hand-off

Builds on this:
- [Operator 3] uses the `active_ticket` from session-state to post faithful updates
- [Operator 4] uses the `active_ticket` to auto-close when work completes

This spec turns CLAUDE.md from a passive policy doc into an enforced workflow gate.
