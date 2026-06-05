# [Operator 5] Operator memory system

**Ticket:** Basecamp [9967247849](https://app.basecamp.com/3945211/buckets/7463955/todos/9967247849)
**Status:** Designed; not yet built
**Depends on:** [Operator 1] (per-user scaffold), [Operator 3] (faithful updates produce learning signal)

---

## Why this exists

Ali's exact words: *"Also within the environment should be a way to share files across the company meaning if I add a file that explains how something works, that means every employee's Claude Code automatically gets that information so I can control the narrative by deciding what's inside the knowledge base and that prioritized over anything learned in their special account. The system will get to know the person well as they interact with it."*

This is two related but distinct asks:

1. **Shared knowledge base** — admin-controlled, distributed to every employee, **prioritized over per-user learning**. The narrative-control rail.
2. **Per-operator memory** — Claude learns each person over time (preferences, recurring patterns, anti-patterns). The personalization rail.

The priority order matters: org policy wins, then tenant policy, then per-user CLAUDE.md, then learned memory **last**. So learned memory never overrides explicit policy — it only fills in gaps and tailors style.

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Per-user memory file at `<workspace>/OPERATOR_MEMORY.md` | Auto-seeded at workspace creation (extends Operator 1's `seed()`) |
| 2 | Memory file is **never read before** org/tenant/per-user CLAUDE.md | Layered loader: `colaberry_policy → tenant_policy → user_claude_md → operator_memory` |
| 3 | Explicit policy always wins over learned memory on conflict | Loader concatenates with priority banners; Claude is instructed to prefer earlier layers |
| 4 | Memory captures: stated preferences, recurring patterns, corrections, anti-patterns | 4 sections in the memory file (see Architecture) |
| 5 | Updates are append-only with timestamps | New entries land at the bottom; older entries are not edited |
| 6 | Admin can read any operator's memory (for support / audit) | Memory file is committed to the workspace repo, which admin has access to |
| 7 | Operator can edit / delete their own memory entries | The file is plain markdown in their repo |
| 8 | A separate **shared knowledge base** lives at `ColaberryIntern/colaberry-knowledge` and is auto-pulled the same way as the org CLAUDE.md | Mirrors the Operator 1 distribution pattern |

## Architecture

### File hierarchy and priority

```
Read order (top wins on conflict):

1. .claude/colaberry/CLAUDE.md            ← org doctrine (admin-controlled)
2. .claude/colaberry/knowledge/**/*.md    ← shared KB (admin-controlled)  ★ NEW
3. .claude/tenant/CLAUDE.md               ← tenant policy (if set)
4. .claude/tenant/knowledge/**/*.md       ← tenant KB (if set)
5. ./CLAUDE.md                            ← per-user policy (user-editable)
6. ./OPERATOR_MEMORY.md                   ← learned memory (Claude-managed)
```

The shared KB at layer 2 is the **"control the narrative"** rail. Anything Ali drops into `colaberry-knowledge` is automatically distributed to every operator's Claude Code at session start, with higher priority than their personal CLAUDE.md and far higher than their learned memory.

### OPERATOR_MEMORY.md structure

```markdown
# Operator memory — {Display Name}

Last updated: 2026-06-05 14:30:00 UTC
Sessions captured: 47

## Stated preferences (verbatim from operator)

- 2026-04-12 — "I prefer one consolidated PR over many small ones for refactors in this area"
- 2026-05-03 — "Mandrill emails should always BCC me on outbound to external recipients"
- 2026-05-18 — "When writing Mandrill scripts, default to TEST_MODE=1 until I greenlight"

## Recurring patterns (Claude-observed, >= 3 occurrences)

- After every deploy, operator runs a Playwright smoke check before declaring done (observed 11x)
- When opening a new spec, operator wants to see the existing related spec first (observed 6x)
- For Basecamp comment formatting, operator prefers HTML cards over plaintext (observed 9x)

## Corrections (anti-patterns)

- 2026-05-08 — Don't use em-dashes in outbound emails (banned phrase rule)
- 2026-05-22 — Don't link to /portal/project/cory; use the floating widget instead
- 2026-06-01 — Don't ship SMS via T-Mobile email-to-SMS gateway; use Mandrill→Gmail push

## Open observations (not yet promoted to patterns)

- Operator may prefer Bloomberg/Salesforce visual style for executive surfaces
  (observed once, 2026-05-12 — needs 2 more occurrences to promote)
```

### Capture triggers (when memory gets written)

| Trigger | What gets written | Section |
|---|---|---|
| Operator explicitly says "I prefer X" / "always do X" / "from now on, X" | Verbatim quote + date | Stated preferences |
| Operator corrects Claude ("no, not that — do X instead") | Captured correction + date | Corrections |
| Same behavior pattern observed 3+ times across sessions | Auto-promoted from Open observations → Recurring patterns | Recurring patterns |
| Operator removes / edits memory file | Respected as-is, no auto-revert | n/a |

The "3 occurrences before promotion" rule prevents Claude from over-fitting to single-session noise.

### Shared knowledge base distribution

`colaberry-knowledge` is a private repo, deploy-key-distributed (same mechanism as Operator 1's `colaberry-policy`).

Structure:

```
colaberry-knowledge/
├── README.md                        ← what lives here, how to add files
├── product/
│   ├── how-cory-briefing-works.md
│   ├── advisor-system-overview.md
│   └── pilot-program-structure.md
├── ops/
│   ├── deploy-playbook.md
│   ├── basecamp-conventions.md
│   └── mandrill-send-helpers.md
├── people/
│   └── company-roster.md
└── decisions/
    └── 2026-q2-architecture-decisions.md
```

When Ali edits any file here and pushes, the next time any operator starts Claude Code, that file is part of their context. **No per-operator distribution work** — pull happens automatically.

This is the file that makes Ali's "control the narrative" requirement concrete: he writes a file once, every employee's Claude Code knows it.

### Priority enforcement at the prompt boundary

At session start, the loader assembles the full context as:

```markdown
# === Layer 1: Colaberry org policy (HIGHEST PRIORITY) ===
{colaberry-policy/CLAUDE.md}

# === Layer 2: Colaberry shared knowledge base ===
{colaberry-knowledge/**/*.md, concatenated}

# === Layer 3: Tenant policy ({Tenant Name}) ===
{tenant CLAUDE.md if any}

# === Layer 4: Your personal CLAUDE.md ===
{user CLAUDE.md}

# === Layer 5: What I've learned about you (LOWEST PRIORITY — never overrides anything above) ===
{OPERATOR_MEMORY.md}
```

The literal section headers tell Claude how to resolve conflicts: if learned memory says "I prefer X" but org policy says "do Y," Claude does Y.

## Workflows

### Workflow A: Ali adds a shared knowledge file

```
1. Ali edits ColaberryIntern/colaberry-knowledge/product/how-cory-briefing-works.md
2. git push
3. Next time Karun opens Claude Code:
   a. Session-start hook runs `git -C .claude/colaberry pull`
   b. New file is now in his context
   c. He asks "how does the Cory briefing work?" — Claude answers from the file
```

### Workflow B: Claude learns a preference

```
Session N:
  Operator: "Ugh, you used an em-dash again. I told you — no em-dashes in outbound emails."

Claude:
  1. Detects correction pattern
  2. Appends to OPERATOR_MEMORY.md → Corrections:
     "2026-06-05 — Don't use em-dashes in outbound emails (operator-corrected this session)"
  3. Commits the file to the workspace repo
  4. Future sessions read this and avoid em-dashes
```

### Workflow C: Pattern promotion

```
Session 1: Operator runs Playwright smoke after deploy.
Session 2: Same.
Session 3: Same.

Claude:
  - After session 3, pattern promoter sees 3 occurrences in the open-observations log
  - Promotes the pattern: moves from "Open observations" → "Recurring patterns"
  - Now this is treated as an expected default, not a single observation
```

### Workflow D: Org policy override

```
Operator memory says: "Operator prefers to deploy without Playwright smoke when in a rush."
Org policy says:      "Deploys to prod always run Playwright smoke."

Claude resolves: org policy wins. Runs Playwright smoke regardless of operator preference.
```

## What's intentionally NOT in v1

- **Cross-operator memory sharing** — Karun's memory file is NOT visible to other operators (only to Ali / admins). No "Karun learned X, so everyone benefits" mechanism. Defer to a future "team patterns" feature.
- **Auto-write memory mid-session** — v1 writes memory only at session end, after the user has clearly stated a preference or pattern is confirmed. Mid-session interrupting writes feel intrusive.
- **Memory expiration / decay** — preferences don't expire automatically. If Karun's preferences change, he edits the file.
- **Conflict UI** — if learned memory and per-user CLAUDE.md contradict, the priority order resolves it silently. We don't surface "these two disagree" yet.
- **Knowledge-base search** — the shared KB is read in full at session start (concatenated). A retrieval layer that pulls only relevant files = v2 once the KB grows past ~50 files.

## Open questions for Ali

1. **Shared KB size**: at ~50 files, the concat approach blows context. What's the upper bound before we need retrieval/embedding? Default: monitor, switch to retrieval at 50 files.
2. **Who can write to the shared KB?** Default: only Colaberry admins (Ali + designees). Tenant-admins can write to their tenant KB but not the Colaberry-wide one.
3. **Should memory be cross-device for the same operator?** (Karun on his laptop vs Karun on a different machine.) Default: yes — it lives in his workspace repo so it travels with him via git.
4. **Privacy**: should operators be able to mark a memory entry "private — don't share with admin"? Default: no in v1 — admin can always read the file. Privacy escape valve = v2 if needed.

## Hand-off

This spec depends on [Operator 1] (workspace scaffold + per-user CLAUDE.md) and [Operator 3] (signal source for what to learn — corrections come via the comment stream).

Feeds back into:
- All future sessions: every operator's Claude Code is shaped by org KB + their personal memory
- Manager visibility: admin can read any operator's memory file to understand how Claude is interacting with them
- Compliance: the memory file is auditable, version-controlled, and editable by both operator and admin
