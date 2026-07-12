"""Canonical Trust-Before-Intelligence primer for the deep-plan generator.

This is the SINGLE grounding source for trust in generated build plans. Its
content is the pinned, vendored TBI framework snapshot
(``directives/compliance/trust-before-intelligence.md`` @ ``TBI-2025.12.0``,
vendored from github.com/colaberry/trust-before-intelligence-book at commit
a296fe9). It is used two ways:

  1. ``prompt_primer()`` is injected into the generator prompts so the model
     grounds every trust requirement in the canonical INPACT / GOALS / 7-layer
     framework instead of improvising from its own priors.
  2. ``primer_markdown()`` is shipped as a "Trust (TBI) Primer" document with
     every plan, so whoever builds a story (a non-technical founder OR the
     build-loop AI) has the definition INLINE — no access to the private book
     repo required.

The framework version is pinned. ``test_tbi_primer`` asserts it matches
``tbi_compliance.CURRENT_FRAMEWORK_VERSION`` so a framework refresh forces this
primer to be re-reviewed (CLAUDE.md self-annealing loop).
"""
from __future__ import annotations

# Must track the vendored snapshot. A mismatch with CURRENT_FRAMEWORK_VERSION
# fails test_tbi_primer → the primer must be re-derived on a framework refresh.
FRAMEWORK_VERSION = "TBI-2025.12.0"
SOURCE = ("directives/compliance/trust-before-intelligence.md "
          "(vendored from github.com/colaberry/trust-before-intelligence-book @ a296fe9)")

THESIS = ("Trust before intelligence: stand up the trust + data foundation BEFORE you deploy "
          "agent intelligence. ~95% of AI agent projects fail because teams bolt a clever model "
          "onto an untrustworthy foundation — no governance, no observability, no grounded context, "
          "no permissions. Intelligence without trust is liability.")

# INPACT — six needs every agent must fulfil. (dimension, requirement, trust layer)
INPACT = [
    ("Instant", "Responsiveness appropriate to the use; a defined latency expectation; never hangs the user.", "L2 Real-Time"),
    ("Natural", "Natural-language / conversational; meets users in their own words via a shared semantic layer.", "L3 Semantic"),
    ("Permitted", "Role-based access + authorization; only takes permitted actions, for permitted users, within explicit autonomy bounds.", "L5 Governance"),
    ("Adaptive", "Learns and improves from feedback and outcomes over time, not frozen at build time.", "learning loop"),
    ("Contextual", "Decisions grounded in unified, retrieved knowledge (RAG / memory) — not the model's parametric guesswork.", "L4 RAG"),
    ("Transparent", "Decisions and actions are explainable and auditable after the fact.", "L6 Observability"),
]

# GOALS — five operational-excellence targets. (target, requirement)
GOALS = [
    ("Governance", "Policy, approval gates, access control, and autonomy bounds are in force."),
    ("Observability", "Every action is audited, monitored, and traceable."),
    ("Availability", "The capability is reliably reachable / up when needed."),
    ("Lexicon", "A consistent, shared vocabulary; terminology does not drift across the system."),
    ("Solid", "Reliability, correctness, and safe rollback."),
]

# 7-Layer Architecture of Trust. (layer, name, purpose)
LAYERS = [
    (1, "Multi-Modal Storage", "Durable, multi-format storage foundation."),
    (2, "Real-Time Data Fabric", "Low-latency movement and freshness of data."),
    (3, "Unified Semantic Layer", "Shared meaning and vocabulary across data."),
    (4, "Intelligent Retrieval (RAG)", "Grounds answers in retrieved knowledge."),
    (5, "Agent-Aware Governance", "Permissions, policy, and autonomy bounds."),
    (6, "Observability & Feedback", "Audit, monitoring, explainability, feedback capture."),
    (7, "Self-Service Data Products & Multi-Agent Orchestration", "Composition of trusted products into multi-agent systems."),
]

# How the abstract framework shows up as concrete things to BUILD in a product.
BUILD_PATTERNS = [
    ("Audit log", "An append-only record of every meaningful action (who/what/when/before-after). Satisfies Transparent + Observability."),
    ("Approval gate", "High-stakes actions are HELD for a human to approve ('AI proposes, human approves'). Satisfies Permitted + Governance."),
    ("Escalation", "When confidence is low or an anomaly appears, the agent routes to a human instead of acting. Satisfies Permitted + Adaptive."),
    ("Trust dashboard", "One screen of system health, pending approvals, recent actions, and anomalies. Satisfies Observability + Availability."),
    ("Governance score", "A live 0–100 score (e.g. % actions audited, % approvals honoured, failure rate); below a threshold it recommends a fix. Satisfies Governance + Solid."),
]


def prompt_primer() -> str:
    """Compact, authoritative TBI definition for injection into generator prompts."""
    inpact = "\n".join(f"  - {d}: {req} [{layer}]" for d, req, layer in INPACT)
    goals = "\n".join(f"  - {t}: {req}" for t, req in GOALS)
    layers = "\n".join(f"  - L{n} {name}: {purpose}" for n, name, purpose in LAYERS)
    patterns = "\n".join(f"  - {n}: {d}" for n, d in BUILD_PATTERNS)
    return (
        f"CANONICAL TRUST-BEFORE-INTELLIGENCE FRAMEWORK ({FRAMEWORK_VERSION}). "
        "Ground EVERY trust requirement and trust scenario in this; do NOT improvise a different trust model.\n"
        f"Thesis: {THESIS}\n"
        f"INPACT — six needs every agent must satisfy (or justify n/a):\n{inpact}\n"
        f"GOALS — five operational-excellence targets:\n{goals}\n"
        f"7-Layer Architecture of Trust (the agent sits ON TOP of L1–L6):\n{layers}\n"
        "Express trust in the product as these concrete, buildable controls:\n"
        f"{patterns}\n"
        "Each story's trust scenario must assert one of these controls with concrete values."
    )


def primer_markdown(project: str = "") -> str:
    """The founder-facing 'Trust (TBI) Primer' document shipped with every plan."""
    title = f"{project} — Trust (TBI) Primer" if project else "Trust (TBI) Primer"
    lines = [f"# {title}", "",
             f"*The trust foundation this build must stand up. Framework: **{FRAMEWORK_VERSION}** "
             f"(pinned). Read this before building any 'trust' task — it is the source of truth for "
             f"what \"trust\" means here, so you don't have to guess.*", "",
             "## Why trust comes first", "", THESIS, "",
             "## INPACT — six needs every AI assistant must satisfy", "",
             "| Need | What it requires | Trust layer |", "|---|---|---|"]
    lines += [f"| **{d}** | {req} | {layer} |" for d, req, layer in INPACT]
    lines += ["", "## GOALS — five operational-excellence targets", "",
              "| Target | What it requires |", "|---|---|"]
    lines += [f"| **{t}** | {req} |" for t, req in GOALS]
    lines += ["", "## The 7-Layer Architecture of Trust", "",
              "The agent intelligence sits *on top of* L1–L6.", "",
              "| Layer | Name | Purpose |", "|---|---|---|"]
    lines += [f"| L{n} | {name} | {purpose} |" for n, name, purpose in LAYERS]
    lines += ["", "## How to BUILD trust (the concrete controls)", "",
              "Every 'trust' task in this plan is one of these. Build them with the same minimal-code "
              "tools as the rest of the product (e.g. an audit-log table in Supabase, an approval queue in Retool).", ""]
    lines += [f"- **{n}** — {d}" for n, d in BUILD_PATTERNS]
    lines += ["", "---",
              f"*Source: {SOURCE}. Pinned for deterministic compliance; refreshing the snapshot is "
              "approval-gated (see CLAUDE.md). Every AI artifact in this build must ultimately carry a "
              "passing TBI attestation.*"]
    return "\n".join(lines)
