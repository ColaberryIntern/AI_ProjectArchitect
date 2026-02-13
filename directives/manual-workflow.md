# Directive: Manual Workflow (End-to-End)

## Purpose

This is the master playbook for operating the AI Project Architect system manually through Claude Code. It describes the complete pipeline from idea to final document.

## Prerequisites

- Working in VS Code with Claude Code
- Project repository initialized
- Dependencies installed (`pip install -r requirements.txt`)

## The Pipeline

```
idea_intake → feature_discovery → outline_generation → outline_approval → chapter_build → quality_gates → final_assembly → complete
```

Each phase has its own directive. This document describes how they connect.

## Step 0: Project Initialization

When a user says "I have a new project idea" (or similar):

1. Read this directive and `directives/01-idea-intake.md`
2. Run: `execution/state_manager.py initialize_state("Project Name")`
3. Confirm: "I've set up a new project workspace. Tell me your idea in any form."

## Step 1: Idea Intake

**Directive**: `directives/01-idea-intake.md`

- Capture the raw idea verbatim
- Advance to `feature_discovery`

## Step 2: Feature Discovery

**Directive**: `directives/03-feature-discovery.md`

- Present a feature catalog generated from the user's idea (one-time LLM call)
- User selects features via checkboxes (form-based, deterministic)
- Classify selected as core (MVP)
- Apply anti-overengineering guardrails
- Establish build order
- Get approval, advance to `outline_generation`

## Step 3: Outline Generation

**Directive**: `directives/04-outline-generation.md`

- Generate outline with 7 required sections in strict order
- Run outline validator
- Present for approval

## Step 4: Outline Approval

**Directive**: `directives/05-outline-approval.md`

- User approves, revises, expands, or reduces
- On approval: lock outline, create chapter entries
- Advance to `chapter_build`

## Step 5: Chapter Build

**Directive**: `directives/06-chapter-build.md`

- Build one chapter at a time (Purpose, Design Intent, Implementation Guidance)
- Present for review after each chapter
- Max 2 revisions per chapter
- Run quality gates per chapter
- Repeat until all chapters approved
- Advance to `quality_gates`

## Step 6: Quality Gates

**Directive**: `directives/07-quality-gates.md`
**Agent**: `agents/quality_gatekeeper.md`

- Run all 5 gates on the full document
- Fix any failures
- All gates must pass
- Advance to `final_assembly`

## Step 7: Final Assembly

**Directive**: `directives/08-final-assembly.md`
**Agent**: `agents/document_assembler.md`

- Compile chapters in order
- Apply formatting and naming
- Add version tag
- Export to Markdown
- Advance to `complete`

## Interaction Pattern (Every Step)

At every phase, Claude follows this pattern:

1. **Read** the relevant directive to know the rules
2. **Read** the state file to understand context (decided, open, locked, phase)
3. **Act** according to the directive (ask questions, generate content, run validation)
4. **Update** the state through execution scripts
5. **Pause** for user approval at every gate

## Key Rules

- Nothing advances without explicit user approval
- Ambiguity is a blocking condition
- One chapter at a time — no bulk generation
- Scope is locked after outline approval
- Quality gates are mandatory, not optional
- The Intern Success Test is the final validation

## Verification

- The user can go from "I have a new project idea" to a complete, build-ready document
- Every phase transition is recorded in the state file
- The final document passes all 5 quality gates
- An intern could execute the project using only the final document
