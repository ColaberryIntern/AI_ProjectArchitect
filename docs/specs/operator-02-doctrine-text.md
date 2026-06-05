# Operator 2 doctrine text (draft v01)

This is the exact text that lands in the **org CLAUDE.md** (currently
`ColaberryIntern/AI_ProjectArchitect/CLAUDE.md`, distributed via Layer 1 of
Op 1's 4-layer assembler) once Op 2 v01 is approved.

The doctrine is one new top-level section; nothing else in CLAUDE.md changes
in this version.

---

## Mandatory Ticket-Driven Work (NON-NEGOTIABLE)

Every Claude Code session that performs substantive work MUST be anchored to
exactly one Basecamp ticket in your personal Basecamp project.

### What counts as substantive work (ticket required)

- File creation, edits, deletions
- External sends (email, Slack, BC comments outside the active ticket)
- Deploys, infra changes, schema migrations
- Anything that produces a commit

### What does NOT count as substantive (no ticket needed)

- Read-only questions ("explain this code", "what does X do", "show me")
- Dry-runs, audits, `git status`, `git log`
- Loading context at session start
- Anything where Claude is reading or describing but not changing

### Session-start protocol

1. **If the user's first prompt references a BC ticket** (URL or `BC#<id>`),
   that's your active ticket. Read it before doing anything else, then proceed.

2. **If no ticket reference and the prompt is substantive**, create a ticket
   in the user's personal BC project BEFORE doing the work.
   - Derive a proposed title from the user's first prompt (first sentence,
     trimmed to ~90 chars).
   - Ask: "Before I start, I'll create a Basecamp ticket in your personal
     project to track this work. **Proposed title:** {title}. Edit the title
     if you want, or reply `confirm` to proceed."
   - On confirm: create the BC todo, write `.claude/session-state.json` with
     the active_ticket anchor.
   - Post a "session started" comment on the new ticket with the session ID.

3. **If the prompt is read-only**, proceed without a ticket. Do not write
   session-state.json.

### Override flags

| Flag | Effect |
|---|---|
| `--no-ticket` at start of prompt | Bypass ticket creation even for substantive work. Logged as `ticket_bypass` with reason. Use when you genuinely want a one-off action without overhead (e.g. quick clipboard read or local rename). |
| `--ticket` at start of prompt | Force ticket creation even for read-only work. Use when you want the audit trail for an investigation. |

### Multi-turn sessions

The active ticket persists across all turns in the same session via
`.claude/session-state.json`. Subsequent turns re-read this file. Claude
should not re-prompt for ticket confirmation once an active_ticket is set
unless the user explicitly changes context.

### Cross-ticket sessions (not in v1)

v1 = one ticket per session. If the user wants to switch tickets mid-session,
they start a new session. v2 will support multi-ticket sessions if needed.

### Authority

This rule is enforced by the model reading this doctrine at session start
(Layer 1 of the assembled context per Op 1). The Python helpers
(`session_state.py`, `personal_bc_provisioner.py`, `ticket_creation_flow.py`)
provide the file format and API surface; Claude calls them to actually
create tickets and persist session state.

If `.claude/session-state.json` is missing at the start of a substantive turn,
treat it as "no active ticket" and run the session-start protocol above.

### Forbidden patterns

- Doing substantive work without creating a ticket AND without invoking
  `--no-ticket`. This is a doctrine violation, not an oversight.
- Creating a ticket retroactively after the work is done. Tickets are created
  BEFORE work, not after, so the BC ticket comment thread tracks the actual
  arc of work via Op 3 (faithful updates).
- Closing a ticket without an Op 4 close-summary comment.

---

*This doctrine sits in CLAUDE.md as a single section. It does not change the
existing rules; it adds the ticket gate.*

*Drafted 2026-06-05 as part of Op 2 v01 (BC todo 9967247783). Lands in the
org CLAUDE.md after Ali approves the v01 review.*
