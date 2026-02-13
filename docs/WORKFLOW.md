# Workflow: From Idea to Final Document

## Pipeline Overview

```
idea_intake → feature_discovery → outline_generation → outline_approval → chapter_build → quality_gates → final_assembly → complete
```

Each phase has its own directive in `/directives`. This document describes how they connect end-to-end.

---

## Prerequisites

- Working in VS Code with Claude Code
- Project repository initialized
- Dependencies installed (`pip install -r requirements.txt`)
- `.env` configured with your OpenAI API key

---

## The 8 Phases

### Phase 1: Idea Intake

**Directive:** `directives/01-idea-intake.md`

| | |
|---|---|
| **What happens** | The raw idea is captured verbatim in any form — a sentence, a rant, a half-thought |
| **What the user does** | Describes their idea naturally |
| **What the system does** | Records the idea, asks guided clarification questions, explores business model, user problem, differentiation, and AI leverage |
| **Artifact produced** | Stored idea with refined context in `project_state.json` |

### Phase 2: Feature Discovery

**Directive:** `directives/03-feature-discovery.md`

| | |
|---|---|
| **What happens** | A feature catalog is generated from the idea, grouped by product and architecture layers |
| **What the user does** | Selects features via checkboxes, classifies as core (MVP) or optional |
| **What the system does** | Applies anti-overengineering guardrails, enforces mutual exclusion rules, validates feature-to-problem mapping |
| **Artifact produced** | Approved feature set with build order |

### Phase 3: Outline Generation

**Directive:** `directives/04-outline-generation.md`

| | |
|---|---|
| **What happens** | A structured outline is generated with 7 required sections in strict order |
| **What the user does** | Reviews the generated outline |
| **What the system does** | Enforces section ordering (Why → Who → What → How → How to Build → What Not to Do → What's Next), runs outline validation |
| **Artifact produced** | Draft outline ready for approval |

### Phase 4: Outline Approval & Locking

**Directive:** `directives/05-outline-approval.md`

| | |
|---|---|
| **What happens** | The outline is locked as an immutable contract for execution |
| **What the user does** | Approves, revises, expands, or reduces the outline |
| **What the system does** | On approval: locks outline (SHA256 integrity hash), creates chapter entries, prevents further scope changes without formal unlock |
| **Artifact produced** | Locked outline with version tag |

### Phase 5: Chapter-by-Chapter Build

**Directive:** `directives/06-chapter-build.md`

| | |
|---|---|
| **What happens** | Chapters are built incrementally — one at a time, never in bulk |
| **What the user does** | Reviews and approves each chapter before the next begins |
| **What the system does** | Generates each chapter with three required sections: Purpose, Design Intent, and Implementation Guidance |
| **Artifact produced** | Approved chapters (max 2 revisions each) |

### Phase 6: Quality Gates

**Directive:** `directives/07-quality-gates.md`

| | |
|---|---|
| **What happens** | The full document is validated against 5 quality gates |
| **What the user does** | Reviews the quality report |
| **What the system does** | Runs: Completeness, Clarity, Build Readiness, Anti-Vagueness, and the Intern Success Test |
| **Artifact produced** | Quality validation report (all gates must pass) |

### Phase 7: Final Assembly

**Directive:** `directives/08-final-assembly.md`

| | |
|---|---|
| **What happens** | Approved chapters are compiled into a single professional document |
| **What the user does** | Receives the final artifact |
| **What the system does** | Mechanical compilation in exact outline order — no content is rewritten or added during assembly |
| **Artifact produced** | `{ProjectName}_Build_Guide_v{N}.md` |

### Phase 8: Complete

The project is marked complete. The final document is a durable, reusable, version-controlled asset.

---

## Interaction Pattern (Every Phase)

At every phase, the system follows this cycle:

1. **Read** the relevant directive to know the rules
2. **Read** the state file to understand context (what is decided, open, locked)
3. **Act** according to the directive (ask questions, generate content, run validation)
4. **Update** the state through execution scripts
5. **Pause** for user approval at every gate

---

## Key Rules

- **Nothing advances without explicit user approval** — "looks good" is not approval
- **Ambiguity is a blocking condition** — the system pauses and asks, never guesses
- **One chapter at a time** — no bulk generation, no skipping ahead
- **Scope is locked after outline approval** — changes require formal unlock and re-approval
- **Quality gates are mandatory** — all 5 must pass before final assembly
- **The Intern Success Test is the final validation** — could an intern execute this project using only the document?

---

## Manual Process vs. Future AI Agent Capability

| Manual Process (Today) | Future AI Agent (Tomorrow) |
|------------------------|---------------------------|
| Idea Intake & Questioning | Intent Extraction & Dynamic Questioning |
| Feature Discovery & Structuring | Automated Scope Analysis & Structured Generation |
| Chapter Build & Quality Gates | Controlled Expansion & Automated Validation |

The manual process defines the automation blueprint. Every rule in this workflow is designed to be human-usable today and machine-enforceable tomorrow.
