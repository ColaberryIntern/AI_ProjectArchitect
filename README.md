# AI Project Architect & Build Companion

A chat-driven system that transforms raw business or application ideas into clear, execution-ready project documentation. Designed for use within VS Code with Claude Code.

## What This System Does

1. **Idea Intake** — Accepts raw ideas in any form
2. **Feature Discovery** — Catalog-based feature selection with anti-overengineering guardrails
3. **Outline Generation** — Produces structured outlines with required sections in strict order
4. **Outline Approval & Locking** — Approval gates with immutability enforcement
5. **Chapter-by-Chapter Build** — One chapter at a time with Purpose, Design Intent, and Implementation Guidance
6. **Quality Gates** — 5 validation checks (Completeness, Clarity, Build Readiness, Anti-Vagueness, Intern Test)
7. **Final Document Assembly** — Compiled, formatted, versioned Markdown output

## Architecture

Follows an **Agent-First, Deterministic-Execution** model:

- **Layer 1 — Directives** (`/directives`): Human-readable SOPs for each pipeline phase
- **Layer 2 — Orchestration**: Claude reads directives and drives the conversation
- **Layer 3 — Execution** (`/execution`): Deterministic Python scripts
- **Layer 4 — Verification** (`/tests`): Automated test suite

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Install dev dependencies
pip install pytest pytest-cov pytest-mock

# Copy environment config
cp .env.example .env
```

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=execution --cov-report=term-missing
```

## Folder Structure

| Folder | Purpose |
|--------|---------|
| `/agents` | Agent personas and behavioral descriptions |
| `/directives` | SOPs and runbooks for each pipeline phase |
| `/execution` | Deterministic Python scripts |
| `/config` | Settings and JSON Schema definitions |
| `/templates` | Markdown templates for document generation |
| `/tests` | Unit, directive, and integration tests |
| `/output` | Generated project documents (gitignored) |
| `/tmp` | Scratch space (gitignored) |
