# Architecture: Four Layers of System Design

<img src="images/system-mind-map.png" alt="System Architecture Mind Map" width="700">

---

## Overview

The AI Project Architect follows an **Agent-First, Deterministic-Execution** model. The system is divided into four layers, each with a clear boundary and responsibility. No layer is allowed to cross into another's domain.

This separation ensures that:
- Directives define intent without embedding logic
- Orchestration reasons without executing
- Execution scripts are repeatable and testable without making decisions
- Verification proves correctness without assuming it

---

## Layer 1: Directives

**Location:** `/directives`

Human-readable SOPs that describe what to do, not how to code it. Each directive covers one pipeline phase and defines:
- Goals and purpose
- Inputs and outputs
- Edge cases and safety constraints
- Verification expectations

| Directive | Phase |
|-----------|-------|
| `01-idea-intake.md` | Idea capture and guided questioning |
| `03-feature-discovery.md` | Catalog-based feature selection |
| `04-outline-generation.md` | Structured outline creation |
| `05-outline-approval.md` | Approval gating and scope lock |
| `06-chapter-build.md` | Incremental chapter construction |
| `07-quality-gates.md` | 5-gate validation |
| `08-final-assembly.md` | Document compilation |
| `manual-workflow.md` | End-to-end pipeline playbook |
| `state-management.md` | State file lifecycle rules |

Directives are living documents — they are updated as the system learns.

---

## Layer 2: Orchestration

**Location:** Claude / Chat Engine (`app/chat_engine.py`)

This is where reasoning happens. The orchestration layer:
- Reads the relevant directive before acting in any phase
- Plans changes and designs tests before logic
- Drives the conversation through the Question → Response → Refinement loop
- Decides which tools, scripts, and validations are required
- Updates directives with learnings

The orchestration layer **never** executes business logic or tests directly. It reasons, then delegates to Layer 3.

---

## Layer 3: Execution

**Location:** `/execution`

Deterministic Python scripts — one script, one clear responsibility. These are the workhorses that perform:
- State management and phase transitions
- Feature catalog generation and classification
- Outline generation and validation
- Chapter writing and quality gate checks
- Document assembly and version management

| Script | Responsibility |
|--------|---------------|
| `state_manager.py` | Project state lifecycle, phase transitions, immutability enforcement |
| `feature_catalog.py` | Feature generation, catalog grouping, mutual exclusion rules |
| `feature_classifier.py` | Feature-to-problem mapping, intern explainability checks |
| `ambiguity_detector.py` | Vague language detection and flagging |
| `outline_generator.py` | Outline creation from approved features |
| `outline_validator.py` | Outline structure validation |
| `chapter_writer.py` | Chapter generation (Purpose, Design Intent, Implementation Guidance) |
| `quality_gate_runner.py` | 5-gate validation execution |
| `document_assembler.py` | Final document compilation |
| `template_renderer.py` | Markdown template application |
| `version_manager.py` | Version tracking and tagging |
| `intelligence_goals.py` | AI depth analysis and goal generation |
| `llm_client.py` | OpenAI API integration (single responsibility) |

All execution code must be: **repeatable, testable, auditable, and safe to rerun.**

---

## Layer 4: Verification

**Location:** `/tests`

The automated test suite mirrors the execution and app structure. Tests are first-class citizens, not afterthoughts.

```
tests/
├── execution/          # Unit tests for all execution scripts
├── app/                # Route and integration tests for the web app
├── directives/         # Directive structure validation
├── integration/        # Full pipeline tests
└── conftest.py         # Shared fixtures (temp directories, mock LLM client)
```

### The Five Quality Gates

Every document passes through five mandatory validation checks:

| Gate | What It Validates |
|------|-------------------|
| **Completeness** | All sections present, no placeholders ("TBD", "we'll decide later") |
| **Clarity** | Each chapter summarizable in one sentence, consistent terminology |
| **Build Readiness** | Clear execution order, defined inputs/outputs, component boundaries |
| **Anti-Vagueness** | Forbidden phrases flagged and replaced with specifics |
| **Intern Success Test** | "Could an intern execute this project using only this document?" |

---

## Agent Personas

Four specialized agents operate within the orchestration layer, each activated at specific pipeline phases:

| Agent | Role | Active During |
|-------|------|---------------|
| **Project Architect** | Senior product thinker, technical architect, execution guide | All phases |
| **Ideation Coach** | Structured thinking partner for idea refinement | Idea Intake, Feature Discovery |
| **Quality Gatekeeper** | Systematic quality enforcer with checklist-driven evaluation | Chapter Review, Final Validation |
| **Document Assembler** | Mechanical compilation specialist | Final Assembly |

Agent definitions live in `/agents` and describe behavior, communication style, decision authority, and guardrails. Agents do not contain executable logic — they define personas that guide the orchestration layer's behavior.

---

## Web Application

**Location:** `/app`

A FastAPI web application provides the user interface for the pipeline:

| Component | Location | Purpose |
|-----------|----------|---------|
| Routers | `app/routers/` | HTTP endpoints for each pipeline phase |
| Templates | `app/templates/` | Jinja2 HTML templates (base layout, phase pages, partials) |
| Static Assets | `app/static/` | CSS, JavaScript (chat, layout, wizard) |
| Chat Engine | `app/chat_engine.py` | LLM-backed conversation logic |
| Dependencies | `app/dependencies.py` | Shared request helpers (state loading, phase info) |

The app follows an SSR (server-side rendering) pattern with Bootstrap 5 for styling and vanilla JavaScript for interactivity.

---

## State Management

Project state is stored as JSON files in the `/output` directory (gitignored). The state file tracks:

- Current pipeline phase
- Raw idea and refined context
- Project profile (technology, AI depth, scale, etc.)
- Feature catalog and selections
- Outline structure and approval status (SHA256 integrity hash)
- Chapter content and approval status
- Quality gate results
- Version history

Phase transitions are strictly ordered and enforced by `execution/state_manager.py`. No phase can be skipped or revisited without explicit unlock.
