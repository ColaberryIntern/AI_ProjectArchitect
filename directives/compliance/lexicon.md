# Directive — Canonical Lexicon (GOALS-Lexicon enforcement)

**Status:** active · **Owner:** Ali Muwwakkil · **Framework:** `TBI-2025.12.0`
**Closes:** TBI fleet-audit systemic gap #2 (Lexicon).

## Why this exists

The Trust Before Intelligence framework defines **Lexicon** (the "L" in GOALS) as
*"a consistent, shared vocabulary; terminology does not drift across artifacts."*
Before this directive, every attestation merely *asserted* `goals.lexicon: satisfied`
with prose — there was no canonical glossary and nothing checked for drift. This
directive makes Lexicon a real, enforced signal.

## What is enforced

A single source of truth — [`config/lexicon.json`](../../config/lexicon.json) — defines:

- **`terms`** — the preferred vocabulary (term + definition, with optional
  `aliases`). An alias is a *non-canonical synonym*; if it appears in an artifact the
  checker raises a **drift** warning suggesting the canonical term. Drift is advisory
  (does not block).
- **`forbidden`** — banned / deprecated terms. A match is a **block**-level violation
  that fails the gate. Each entry carries a `reason` and an `allow_in` list of
  repo-relative paths where a literal mention is legitimate (e.g. CLAUDE.md naming
  "Moltbot" only to say the project does not use it).
- **`scan_globs`** — which files are in scope: AI artifacts (`agents/*.md`,
  `docs/personas/*.md`, `config/blueprints/*.json`, `config/skill_registry.json`) and
  every `*.tbi.json` attestation (only the evidence/notes prose is scanned there).

## How it is checked

- **Deterministic checker** (LLM-free, never raises):
  [`execution/ops_platform/lexicon.py`](../../execution/ops_platform/lexicon.py) —
  `check_text` / `check_file` / `scan_artifacts` / `summary`. This is "the semantic
  layer checks the glossary" from the audit remediation.
- **CI gate:** [`scripts/lexicon_check.py`](../../scripts/lexicon_check.py), wired into
  [`.github/workflows/tbi-compliance-check.yml`](../../.github/workflows/tbi-compliance-check.yml).
  Forbidden term in a changed artifact → exit 1 (PR blocked); drift → printed, exit 0.
- **Live signal:** the **Lexicon** card on the Trust Command Center (`/admin/trust`,
  super-admin) shows term count, forbidden count, and a live violation scan; the
  drill-down lists the full glossary and any violations.

## How to maintain the glossary

1. **Add a canonical term** — append `{term, abbr?, definition, aliases?}` to `terms`.
   Add `aliases` only for synonyms you want flagged as drift; keep them conservative to
   avoid noise (drift is advisory, but it shows on the dashboard).
2. **Ban a term** — append `{term, reason, allow_in?}` to `forbidden`. Run a full scan
   (`python -c "from execution.ops_platform import lexicon; print(lexicon.summary())"`)
   and seed `allow_in` with any legitimate existing mentions so the gate starts green.
3. **Verify** — `python scripts/lexicon_check.py <changed artifacts>` and the
   `tests/execution/ops_platform/test_lexicon.py` suite must pass.

## Self-annealing

A lexicon gate failure is a first-class trigger for the CLAUDE.md self-annealing loop:
fix the artifact's wording (or, if the rule itself was wrong, the glossary), extend
`test_lexicon.py`, and re-run the gate. Changing the glossary's *intent* (adding/removing
forbidden terms, raising the gate from advisory to blocking on drift) is an
approval-gated change to the compliance baseline.
