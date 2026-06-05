# [Operator 3] Faithful ticket progress updates

**Ticket:** Basecamp [9967247804](https://app.basecamp.com/3945211/buckets/7463955/todos/9967247804)
**Status:** Designed; not yet built
**Depends on:** [Operator 2] (active ticket per session), existing BC API helpers in `backend/src/scripts/`

---

## Why this exists

Ali's exact words: *"As the ticket is worked on, they should be constantly and faithfully updated on the ticket without the user having to ask."*

Today PROGRESS.md captures the post-hoc summary, but the BC ticket itself is silent until the user manually posts a comment. A manager looking at Basecamp can't tell whether work is in progress, stalled, or done — they have to ask the operator or pull the codebase.

The doctrine: Claude Code posts a structured comment on the active ticket at every meaningful step, automatically. The ticket becomes the live progress log.

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Each "meaningful step" triggers exactly one BC comment | `ticket_updater.post_step(step_kind, details)` called at trigger points (see below) |
| 2 | Comments are append-only, never edit prior comments | BC API `POST /buckets/{p}/recordings/{r}/comments.json` |
| 3 | Comments are idempotent (same step twice = one comment) | Stable signature `<!-- step:{kind}:{evidence_hash} -->` checked in last 20 comments |
| 4 | Rate-limited to max 1 comment / 60s to avoid BC API throttling | In-process queue with debounce |
| 5 | Comment format is structured (timestamp · kind · evidence) | Markdown table or color-banded HTML, mirrors existing BC report style |
| 6 | Failures degrade gracefully (BC API down → log + continue) | Try/catch wrap, log to `tmp/ticket-updater-errors.jsonl` |
| 7 | Manager-readable: a non-technical reader can follow the work | Comments name the file in plain language ("Updated the lead-routing rules in `lead_router.py`") |

## Architecture

### Trigger points (when to post)

| Trigger | Step kind | Comment content |
|---|---|---|
| File edit (substantive — not whitespace) | `file_edit` | "Edited `<path>` — <one-line summary of change>" |
| New file created | `file_create` | "Created `<path>` — <one-line purpose>" |
| File deleted | `file_delete` | "Deleted `<path>` — <one-line reason>" |
| Deploy initiated | `deploy_started` | "Deploying to <env>. Commit: `<sha>`" |
| Deploy completed | `deploy_completed` | "Deployed. Verification: <url or test name>" |
| Test run | `test_run` | "Ran `<test name>` — <pass/fail count>" |
| External send (email, Slack, etc.) | `external_send` | "Sent <email/slack/etc> to <recipient> — <subject>" |
| Blocker hit (failure with no auto-recovery) | `blocker` | "Blocked: <one-line description>. Investigating." |
| Diagnostic-mode entered | `diagnostic_mode` | "Entered diagnostic mode — <reason>. Will retry up to 3 attempts." |
| End-of-step (logical pause in work) | `step_complete` | "Step complete: <one-line summary>. Next: <one-line>" |

The list is **closed** in v1. Adding a new step kind = new ticket. Don't let it expand organically.

### Comment format

Each comment is a small HTML card so BC renders it cleanly:

```html
<!-- step:file_edit:abc123def456 -->
<div style="border-left: 3px solid #1a73e8; padding: 8px 12px; background: #f1f3f4;">
  <div style="font-size: 11px; color: #666;">
    <strong>14:32:05 UTC · 2026-06-05</strong> · <code>file_edit</code> · session CC-20260605-4w8q
  </div>
  <div style="margin-top: 4px;">
    Edited <code>backend/src/services/leadRouter.ts</code> —
    added retry logic for stale lead state.
  </div>
  <div style="margin-top: 6px; font-size: 12px;">
    Evidence: <a href="https://github.com/.../commit/abc123">abc123</a>
  </div>
</div>
```

The HTML-comment signature `<!-- step:{kind}:{hash} -->` makes the comment idempotent — re-running the same step is a no-op.

### Idempotency check

Before posting a new comment, fetch the last 20 comments on the ticket via `GET /buckets/{p}/recordings/{r}/comments.json?page=1` and check if any contain the new comment's signature substring. If yes → skip. If no → post.

20 comments back is enough for any single session; older comments are not at risk of duplication because each session has its own signature space (session ID is part of the signature).

### Rate limiter

A simple in-process token bucket:

```python
class CommentRateLimiter:
    def __init__(self, max_per_minute=1):
        self.max_per_minute = max_per_minute
        self.queue = collections.deque()  # tuples of (step_kind, payload)
        self.last_post_at = 0

    def post(self, step_kind: str, payload: dict):
        self.queue.append((step_kind, payload))
        self._drain()

    def _drain(self):
        now = time.time()
        while self.queue and (now - self.last_post_at) >= 60:
            kind, payload = self.queue.popleft()
            bc_api.post_comment(payload)
            self.last_post_at = now
            now = time.time()
```

For bursty sequences (10 file edits in 30 seconds), comments queue up and post on the next minute boundary. The session-end `step_complete` always flushes the queue.

## Workflows

### Workflow A: 3-file edit then deploy

```
14:30:00 — User: "Add retry logic to lead router"
14:30:05 — Claude edits leadRouter.ts
14:30:05 — post_step("file_edit", { path: "leadRouter.ts", summary: "added retry logic" })
           → BC comment posted

14:30:15 — Claude edits leadRouterRetry.ts (new file)
14:30:15 — post_step("file_create", { path: "leadRouterRetry.ts", ... })
           → queued (within 60s of last comment)

14:30:25 — Claude edits test_lead_router.py
14:30:25 — post_step("file_edit", { path: "test_lead_router.py", ... })
           → queued

14:31:05 — Rate limiter drains: posts the 2 queued comments back-to-back

14:31:20 — Claude runs tests
14:31:20 — post_step("test_run", { name: "test_lead_router", ... })
           → queued

14:32:05 — Drain posts the test_run comment

14:32:30 — Claude deploys
14:32:30 — post_step("deploy_started", { env: "prod", commit: "abc123" })
           → queued

14:33:05 — Drain posts deploy_started

14:33:35 — Deploy completes
14:33:35 — post_step("deploy_completed", { env: "prod", verification_url: "..." })
           → queued

14:34:05 — Drain posts deploy_completed + end-of-session step_complete
```

Result: ticket has 6 timestamped comments, each one a card the manager can read at a glance.

### Workflow B: Blocker

```
14:45:00 — Claude tries to update DB schema
14:45:01 — Hits permission error
14:45:01 — post_step("blocker", {
              description: "Schema migration blocked — missing ALTER privilege",
              attempted_action: "ALTER TABLE leads ADD COLUMN ...",
              error: "permission denied"
           })
           → posted IMMEDIATELY (blockers bypass rate limit so manager sees it fast)
```

Blockers, `diagnostic_mode`, and `step_complete` are **never rate-limited** — they're high-signal.

## What's intentionally NOT in v1

- **Live progress streaming** (real-time updates as Claude is mid-tool-call) — defer. v1 posts at logical step boundaries only.
- **Comment threading / replies** — BC supports it; v1 just appends to the ticket.
- **Cross-ticket aggregation** ("show me all of Karun's blockers across his tickets this week") — defer to [Operator 6]-style reporting.
- **Sentiment / urgency tagging on blockers** — v1 = flat blocker kind. Severity classification is a v2 concern.
- **Manager @-mentions on blockers** — v1 = passive (blocker posts to ticket; manager has to be subscribed). Auto @-mention on blockers > 30 min = v2.

## Open questions for Ali

1. **Should comments use the Colaberry user's BC identity, or a service-account identity** ("Claude on behalf of {user}")? Recommend: service account, so the audit trail clearly distinguishes human vs agent comments.
2. **Comment retention**: do we want to compress old `file_edit` comments after the ticket closes? Default: no — BC handles its own storage.
3. **What about non-Basecamp tickets** (Jira, Linear, GitHub Issues)? Default: BC only in v1. Generic ticket adapter = [Operator 3.1] follow-up.

## Hand-off

This spec uses the `active_ticket` from session-state.json that [Operator 2] writes. If [Operator 2] isn't shipped first, this spec has no anchor.

Builds on this:
- [Operator 4] reads the comment history this spec produces to decide whether to auto-close
- Manager-facing reports (future Operator 6+) consume these structured comments as their data source
