"""AI Ops Command Center — multi-tenant per-user task triage surface.

Mirrors each user's Basecamp todos into a local file-backed store, scores them
deterministically (no LLM), surfaces a Claude Code-ready prompt per task, and
(in later phases) accepts decide-and-write-back via each user's AI clone
identity.

Data lives under output/ops/{user_id}/ — one directory per user. Sync runs
periodically and is idempotent. Scoring runs after every sync.

Phases shipped vs deferred — see docs/specs/ops-01-foundation.md.
"""
