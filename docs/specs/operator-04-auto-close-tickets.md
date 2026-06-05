# [Operator 4] Auto-close tickets with confidence gate

**Ticket:** Basecamp [9967247829](https://app.basecamp.com/3945211/buckets/7463955/todos/9967247829)
**Status:** Designed; not yet built
**Depends on:** [Operator 2] (active ticket), [Operator 3] (faithful updates), existing Definition of Done

---

## Why this exists

Ali's exact words: *"Tickets should be closed automatically and if you aren't confident the ticket should be closed, just ask to confirm."*

Today Claude finishes work and the ticket stays open until the operator manually goes to BC and checks the box. Manager visibility is wrong (open ticket = looks like work-in-progress). The operator has to context-switch to BC just to mark done.

The doctrine: when Claude finishes substantive work AND verification passes AND confidence is high → close the ticket automatically. When confidence is low → post a close-request comment and wait for human confirmation.

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | "Done" gate: shipped code + verification pass + PROGRESS.md updated | Reuses existing Definition of Done from CLAUDE.md root section |
| 2 | Confidence threshold: ≥ 0.85 → auto-close; < 0.85 → ask | Reuses the confidence scoring framework from CLAUDE.md root |
| 3 | Auto-close posts a final summary comment, then marks complete | `POST /buckets/{p}/todos/{id}/completion.json` with `enabled:true` |
| 4 | Ask-to-confirm posts a structured close-request comment, does NOT mark complete | Comment includes work summary + confidence reasoning + 1-click confirm link |
| 5 | Idempotent: re-running close is a no-op if already complete | Check `todo.status == 'completed'` before any action |
| 6 | Final summary cross-references PROGRESS.md + BuildManifest if emitted | Link both in the close comment |
| 7 | Operator can override: explicit "close this ticket" or "don't close" | Explicit phrases checked before auto-close logic runs |

## Architecture

### Done-gate logic

A ticket is "done" only when ALL of these are true:

1. The work the user originally asked for is shipped (Claude's own assessment)
2. Verification passed: `tsc --noEmit` clean OR tests pass OR user-confirmed
3. PROGRESS.md was updated in this session with this ticket's entry
4. No blockers in the last 5 step comments (i.e., the work isn't stalled)

Encoded in:

```python
def is_ticket_done(session_state, ticket_id) -> bool:
    return (
        session_state.work_shipped
        and session_state.verification_passed
        and progress_md_updated_for(ticket_id)
        and not has_recent_blocker(ticket_id, n=5)
    )
```

### Confidence scoring (reuse existing)

Per CLAUDE.md root, Claude already scores confidence in 5 dimensions:
- Directive clarity
- Test coverage strength
- Reversibility
- Architectural blast radius
- Compliance/security impact

Same scoring applies here. Aggregate score ≥ 0.85 → auto-close. < 0.85 → ask.

### Auto-close flow

```python
def auto_close_if_ready(session_state):
    ticket = session_state.active_ticket
    if not is_ticket_done(session_state, ticket.id):
        return
    if get_ticket_status(ticket.id) == 'completed':
        return  # idempotent

    confidence = compute_confidence(session_state)
    if confidence >= 0.85:
        post_close_summary_comment(ticket, session_state, confidence)
        bc_api.complete_todo(ticket.bucket_id, ticket.id)
        log_audit("ticket_auto_closed", ticket.id, confidence)
    else:
        post_close_request_comment(ticket, session_state, confidence)
        log_audit("ticket_close_requested", ticket.id, confidence)
```

### Close-summary comment (auto-close path)

```html
<!-- close:auto:abc123 -->
<div style="border: 2px solid #34a853; padding: 14px; background: #e6f4ea; border-radius: 6px;">
  <div style="font-weight: 700; font-size: 14px; color: #137333;">
    ✅ Closed — work complete
  </div>
  <div style="margin-top: 10px;">
    <strong>What shipped:</strong> {one-line summary}
  </div>
  <div style="margin-top: 6px;">
    <strong>Verification:</strong> {test name | tsc clean | user-confirmed}
  </div>
  <div style="margin-top: 6px;">
    <strong>Files touched:</strong> {N files, listed}
  </div>
  <div style="margin-top: 6px; font-size: 12px;">
    PROGRESS.md entry: <a>link</a> · Confidence: 0.92 · Session CC-...
  </div>
</div>
```

### Close-request comment (ask-to-confirm path)

```html
<!-- close:request:abc123 -->
<div style="border: 2px solid #f9ab00; padding: 14px; background: #fef7e0; border-radius: 6px;">
  <div style="font-weight: 700; font-size: 14px; color: #b06000;">
    ⚠️ Ready to close? Confidence is below auto-close threshold.
  </div>
  <div style="margin-top: 10px;">
    <strong>What shipped:</strong> {one-line summary}
  </div>
  <div style="margin-top: 6px;">
    <strong>Why I'm not auto-closing:</strong> {reason — e.g., "tests pass but blast radius is high (DB schema change)" or "user feedback not received"}
  </div>
  <div style="margin-top: 6px;">
    <strong>Verification status:</strong> {tsc clean / tests / pending}
  </div>
  <div style="margin-top: 10px;">
    Reply "close" on this ticket to mark complete, or "keep open" to defer.
  </div>
</div>
```

## Workflows

### Workflow A: High-confidence auto-close

```
End of session — work done, tests pass, PROGRESS.md updated.

Claude:
  1. is_ticket_done? → true
  2. confidence = 0.92 (clear directive, tests cover happy + sad path, fully reversible, low blast)
  3. 0.92 >= 0.85 → auto-close path
  4. post_close_summary_comment → BC comment posted with green border
  5. bc_api.complete_todo → ticket flips to completed
  6. PROGRESS.md final entry includes "ticket auto-closed (confidence 0.92)"
```

### Workflow B: Low-confidence ask-to-confirm

```
End of session — work done but it's a DB schema migration on prod.

Claude:
  1. is_ticket_done? → true (code shipped, tests pass)
  2. confidence = 0.72 (clear directive, tests OK, but irreversible schema change + high blast radius)
  3. 0.72 < 0.85 → ask path
  4. post_close_request_comment → BC comment posted with amber border
  5. ticket stays open
  6. PROGRESS.md final entry includes "ticket close-request posted (confidence 0.72) — awaiting human confirm"

Later:
  Ali replies "close" on the ticket.
  Next session reads the reply, runs the close-completion path, marks complete.
```

### Workflow C: User overrides

```
User mid-session: "Don't close this ticket — there's a follow-up I want to add"

Claude:
  - Sets session_state.close_override = "user_explicit_keep_open"
  - Skips auto-close logic at end of session
  - Final PROGRESS.md entry notes "ticket close suppressed by user override"
```

## What's intentionally NOT in v1

- **Re-open detection** — if a ticket was auto-closed and a new session asks about the same work, we don't auto-re-open. User has to start a new ticket. Defer to v2.
- **Sub-ticket spawn** — if confidence is low because of one specific dimension, we don't auto-create a follow-up ticket for "address X concern." User does that manually if they want. Sub-ticket auto-spawn = [Operator 4.1].
- **Manager approval workflow** for high-blast closes — e.g., "all schema changes need Ali's explicit approve" — defer. v1 = ask-to-confirm covers it because high-blast lowers confidence.
- **Confidence calibration over time** — i.e., if Claude's 0.9-confident closes turn out to be wrong, learn from that. Defer to v2 once we have data.

## Open questions for Ali

1. **Confidence threshold (0.85)**: is this right? Too high → too many ask-to-confirm comments (noisy). Too low → too many wrong auto-closes (worse). Recommend 0.85 to start, tune after 30 days of data.
2. **Should auto-close also trigger BC ticket-list re-ranking** (move to "Done" column on a Kanban view)? Default: only if user is using BC's Card Table view; otherwise just mark complete.
3. **What if PROGRESS.md was not updated** (rule violation per CLAUDE.md root)? Default: BLOCK auto-close. The rule "no `[x]` without verification" stays in force; auto-close can't paper over a PROGRESS.md miss.

## Hand-off

Builds on [Operator 2] (active ticket reference) and [Operator 3] (comment history is the input to "was there a blocker?").

Feeds back into:
- Manager reporting (future Operator 6+) can show "tickets closed today by Karun, with avg confidence 0.88"
- Auditability — every auto-close logs `confidence` so we can build a "Claude was wrong" feedback loop
