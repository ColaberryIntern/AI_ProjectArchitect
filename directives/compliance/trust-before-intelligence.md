---
title: Trust Before Intelligence — Canonical Framework (Vendored Snapshot)
kind: compliance-standard
slug: trust-before-intelligence
version: 1.0.0
owner: ali@colaberry.com
framework_version: TBI-2025.12.0
source_repo: https://github.com/colaberry/trust-before-intelligence-book
source_commit: a296fe96910349b910655c3b6c2c857ca9d2ea23
synced_at: 2026-06-20
status: pinned
---

# Trust Before Intelligence — Canonical Framework

> **This is a vendored, pinned snapshot.** It is the single source of truth for TBI
> compliance in this repository. The upstream book is an in-development manuscript;
> we pin a specific commit so compliance is deterministic and does not shift under us.
> **Refreshing this snapshot is an approval-gated change** (see [CLAUDE.md](../../CLAUDE.md)).
> When you refresh it, bump `framework_version`, update `source_commit` / `synced_at`,
> and update `CURRENT_FRAMEWORK_VERSION` in [tbi_compliance.py](../../execution/ops_platform/tbi_compliance.py)
> so every existing attestation must be re-validated against the new version.

## The thesis

**Trust before intelligence.** You must stand up the trust and data architecture
*before* you deploy agent intelligence. The book's claim is that ~95% of AI agent
projects fail because teams bolt intelligence (a clever model + prompts) onto an
untrustworthy foundation — no governance, no observability, no grounded context,
no permissions. Intelligence without trust is liability. **In this repo, no AI
artifact ships until it can demonstrate the trust foundation underneath it.**

## INPACT™ — six needs every agent must fulfill

Each artifact must satisfy (or justify as not-applicable) all six.

| # | Dimension | What it requires | Trust layer |
|---|-----------|------------------|-------------|
| I | **Instant** | Responsiveness appropriate to the use (the book's bar is sub-second / real-time interaction). The artifact has a defined latency expectation and does not hang the user. | L2 Real-Time |
| N | **Natural** | Interaction is natural-language / conversational; the artifact meets users in their own words, grounded in a shared semantic layer. | L3 Semantic |
| P | **Permitted** | Role-based access and authorization. The artifact only takes actions it is permitted to take, for users who are permitted, within explicit autonomy bounds. | L5 Governance |
| A | **Adaptive** | Learns and improves from feedback and outcomes over time, rather than being frozen at build time. | learning loop |
| C | **Contextual** | Decisions are grounded in unified, retrieved knowledge (RAG / memory) — not the model's parametric guesswork. | L4 RAG |
| T | **Transparent** | Decisions and actions are explainable and auditable after the fact. | L6 Observability |

## GOALS™ — five operational-excellence targets

| Letter | Target (canonical) | Source wording variant | What it requires |
|--------|--------------------|------------------------|------------------|
| G | **Governance** | — | Policy, approval gates, access control, autonomy bounds are in force. |
| O | **Observability** | — | Every action is audited, monitored, and traceable. |
| A | **Availability** | *"Accessibility"* (v3.2 codex) | The capability is reliably reachable / up when needed. |
| L | **Lexicon** | *"Language"* (v3.2 codex) | A consistent, shared vocabulary; terminology does not drift across artifacts. |
| S | **Solid** | *"Soundness"* (v3.2 codex) | Reliability, correctness, and safe rollback. |

> **Source ambiguity (recorded deliberately).** The pinned manuscript expands
> G-O-A-L-S two ways: the *Architecture of Trust Quick Reference v1.1* uses
> **Availability / Lexicon / Solid**; the *Book Codex Master v3.2* uses
> **Accessibility / Language / Soundness**. The letters and intent are identical.
> We adopt the v1.1 wording as canonical and note the variant. This is the kind of
> drift the pin protects us from — do not silently "fix" it on the next sync.

## 7-Layer Architecture of Trust

Conceptual layers (canonical). The agent intelligence sits *on top of* L1–L6.

| Layer | Name | Purpose | Satisfies |
|-------|------|---------|-----------|
| L1 | Multi-Modal Storage | Durable, multi-format storage foundation. | — |
| L2 | Real-Time Data Fabric | Low-latency movement / freshness of data. | Instant |
| L3 | Unified Semantic Layer | Shared meaning and vocabulary across data. | Natural |
| L4 | Intelligent Retrieval (RAG) | Grounds answers in retrieved knowledge. | Contextual |
| L5 | Agent-Aware Governance | Permissions, policy, autonomy bounds. | Permitted |
| L6 | Observability & Feedback | Audit, monitoring, explainability, feedback capture. | Transparent |
| L7 | Self-Service Data Products & Multi-Agent Orchestration | Composition of trusted products into multi-agent systems. | — |

**Reference implementation** (Echo Health stack, from the codex, illustrative only):
L1 Databricks · L2 Redis+Trino · L3 dbt · L4 Neo4j · L5 Collibra · L6/L7 agent + LLM
(AIXcelerator, OpenAI + Anthropic).

## How this maps onto THIS repo (what already satisfies what)

The repo already has a strong substrate. New work should *reuse and map to* these,
not rebuild them.

| TBI element | Existing control to map to |
|-------------|----------------------------|
| Permitted / Governance | `execution/ops_platform/agent_registry.py` (autonomy policies), `auth_gate` SSO, approval gates, library identity-gating |
| Transparent / Observability | `execution/ops_platform/audit_log.py` (append-only/immutable), `compliance_reports.py` |
| Natural | `execution/ops_platform/semantic_analyzer.py`, `config/schemas/ops/semantic_enrichment.schema.json` |
| Contextual | library retrieval, `config/schemas/ops/intelligence_extract.schema.json`, memory |
| Adaptive | self-annealing loop (CLAUDE.md §5), `feedback_store`, `reputation_scorer.py` |
| Solid | `trust_engine.py` (reliability / rollback / prompt-stability), response contracts, `/tests` |
| Instant / Availability | **partial** — define a latency/health expectation per artifact |
| Lexicon | **partial** — keep terminology consistent; lean on the semantic layer |

## What "compliant" means here

An AI artifact is **TBI-compliant** when it carries a passing **TBI attestation**
(`config/schemas/ops/tbi_attestation.schema.json`) in which:

1. all six INPACT dimensions are `satisfied`, or marked `n_a` **with written evidence**;
2. all five GOALS targets are `satisfied`, or `n_a` **with written evidence**;
3. at least one of the 7 layers is mapped with how it is satisfied;
4. its `framework_version` matches this snapshot (`TBI-2025.12.0`);
5. if it is a runtime capability, its `trust_engine` deployment recommendation is not `DO_NOT_DEPLOY`.

The gate procedure, checklist, and pass/fail rules live in
[tbi-compliance-gate.md](./tbi-compliance-gate.md). The deterministic scorer is
[tbi_compliance.py](../../execution/ops_platform/tbi_compliance.py).
