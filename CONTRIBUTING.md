# Contributing

## Getting Started

```bash
# Clone the repository
git clone https://github.com/ColaberryIntern/AI_ProjectArchitect.git
cd AI_ProjectArchitect

# Create a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# Install all dependencies (including dev)
pip install -r requirements.txt
pip install pytest pytest-cov pytest-mock httpx

# Copy environment config
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

# Run the test suite
pytest
```

---

## Architecture

This project follows a strict 4-layer architecture. Before contributing, read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) to understand how the layers interact.

| Layer | Location | Rule |
|-------|----------|------|
| Directives | `/directives` | Human-readable SOPs only — no code, no logic |
| Orchestration | Claude / Chat Engine | Reasoning and planning only — never executes business logic |
| Execution | `/execution` | Deterministic scripts only — no orchestration, no prompts |
| Verification | `/tests` | Automated tests — mirrors execution and app structure |

**The cardinal rule:** No layer crosses into another's domain.

---

## Development Rules

### Layer Boundaries

- No business logic in directives
- No orchestration logic in execution scripts
- No execution or testing inside Claude responses
- One script = one clear responsibility

### Test-First Validation

- All non-trivial execution logic must have unit tests
- Pure logic tested without I/O
- External dependencies mocked
- Tests must be fast, deterministic, and run locally

### Approval-Gated Changes

The following changes require explicit approval before merging:
- Large refactors
- Schema changes (JSON schemas in `/config/schemas`)
- Deleting files
- Production-impacting logic
- Modifying safety, compliance, or testing baselines

---

## Adding a New Directive

Directives live in `/directives` and must include:
- **Purpose** — what this phase accomplishes
- **Inputs** — what data is available
- **Outputs** — what artifacts are produced
- **Rules** — constraints and guardrails
- **Verification** — how success is measured

Reference existing directives (e.g., `directives/01-idea-intake.md`) for the expected format.

---

## Adding a New Execution Script

Scripts in `/execution` must:
- Have a clear, single responsibility
- Be importable and testable (no side effects on import)
- Include a corresponding test file in `tests/execution/`
- Not contain orchestration logic or LLM prompts
- Be safe to rerun (idempotent where applicable)

---

## Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/execution/test_state_manager.py

# Run with coverage report
pytest --cov=execution --cov=app --cov-report=term-missing

# Coverage threshold is 80% (enforced in pyproject.toml)
```

---

## The Intern Standard

Every contribution must be understandable by a junior developer. This means:

- Clear variable and function names
- No clever shortcuts that obscure intent
- Comments where logic is not self-evident
- Test names that describe what is being validated
- Documentation that explains "why", not just "what"

> "If an intern must ask clarifying questions repeatedly, the system has failed."

This applies to documentation **and** code.
