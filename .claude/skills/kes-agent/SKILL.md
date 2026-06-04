---
name: kes-agent
description: Pre-1:1 dashboard generator for Ali ↔ Kes weekly cadence. Reads BC + GitHub + advisor.colaberry.ai Library + skill_registry + MCP catalog via MCP, scores Kes's 5 numbers against the PRD rubric, emits a clean HTML dashboard within 60s.
---

# kes-agent

**Ticket:** [Kes 2](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889389) · due 2026-06-12
**Status:** SCAFFOLD (blocked on [Kes 1 PRD](../../../../docs/personas/kes-prd.md) signature)
**Fires:** [Kes 3](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889413) wires this skill to run 30 min before each Monday 09:30 ET 1:1
**Critic loop:** mandatory before "ship" per ticket acceptance criteria

> Structure mirrors [karun-agent SKILL](../karun-agent/SKILL.md). Same shape, tech-side data sources + tech-side scoring.

---

## What this skill does

Generates an HTML dashboard for the standing Ali ↔ Kes weekly 1:1. Same flow as karun-agent but reads tech-side sources (GitHub, advisor.colaberry.ai Library, skill_registry) instead of CRM/outbound tools.

---

## Data sources (MCP servers consumed)

| Source | MCP server | What it reads | Used for which of the 5 numbers |
|---|---|---|---|
| Basecamp | `mcp_basecamp` _(planned)_ | Active + completed todos in tech-side BC lists, comments, approvals | _[fill from kes-prd.md §3]_ |
| GitHub | `mcp_github` _(in registry)_ | Commits, PRs, repos under `ColaberryIntern/*`, especially Kes-authored + Kes-reviewed | _[fill]_ |
| advisor.colaberry.ai Library | `mcp_library` _(planned — local FastAPI)_ | Skill / agent / MCP-server submissions awaiting Kes approval; approved items this week | _[fill]_ |
| skill_registry | local file read (`config/skill_registry.json`) | Tech-side skills count, last-verified freshness, source diversity | _[fill]_ |
| MCP server catalog | local file read | Per-server `install_command` + `homepage_url` presence (per cadence directive 2026-06-05 example) | _[fill]_ |

Read order: BC → GitHub → Library → skill_registry → MCP catalog.

---

## Output contract

`/kes-dash` emits ONE file: `output/library/_pilot/kes/{YYYY-MM-DD}.html`

Same section structure as karun-dash, scoped tech-side. Same closing question:
> "Of these score deltas, which one surprised you?"

---

## Scoring + critic + performance

Same contracts as [karun-agent SKILL §Scoring](../karun-agent/SKILL.md#scoring-contract-per-number), §Critic loop, §Performance budget — Kes-side data dictionary replaces Karun-side.

Tech-side critic-specific checks (in addition to karun-agent's six):

| Check | Hard fail if… |
|---|---|
| Install reproducibility | Any MCP server scored "approved" lacks a verified `install_command` |
| Homepage liveness | Any approved skill's `homepage_url` 404s at render time |
| Code-review trail | Any approved item lacks a linked PR review |

---

## What's still blocking ship

- [ ] §3 of [Kes 1 PRD](../../../../docs/personas/kes-prd.md) — 5 numbers with source queries + targets
- [ ] §5 — per-number rubric (one-line falsifiable tests)
- [ ] §4 — the 10-12 skills

---

## Changelog

| Date | Author | Change |
|---|---|---|
| 2026-06-03 | Claude (post Auth 2 ship) | Scaffold landed; mirrors karun-agent. Awaiting Kes 1 PRD signature for implementation. |
