# Directive: TBI Compliance Gate

## Purpose

Prove that every AI artifact follows the **Trust Before Intelligence** framework
([trust-before-intelligence.md](./trust-before-intelligence.md)) before it ships. This
gate is **mandatory and non-negotiable** per [CLAUDE.md](../../CLAUDE.md). It blocks
any pull request that adds or changes an AI artifact without a passing attestation.

## When this gate runs

- Locally, whenever you create or modify an AI artifact (before opening a PR).
- In CI, on every PR that touches an AI artifact path (see `.github/workflows/tbi-compliance-check.yml`).
- As **Gate 9** inside the document pipeline ([07-quality-gates.md](../07-quality-gates.md)).

## What counts as an AI artifact

Anything that defines, generates, or governs AI behavior:

| Kind | Paths |
|------|-------|
| agent | `agents/**/*.md` |
| persona | `docs/personas/**/*.md` |
| blueprint | `config/blueprints/*.json` |
| skill_registry | `config/skill_registry.json` |
| library_asset | `library/**/*.md` (AI assets) |
| workflow / advisory | advisory + workflow agent code |

## The attestation

Each artifact carries a sidecar attestation file: **`<artifact>.tbi.json`**
(e.g. `agents/project_architect.md.tbi.json`). It is validated against
`config/schemas/ops/tbi_attestation.schema.json` and scored by
`execution/ops_platform/tbi_compliance.py`.

> **Why a sidecar JSON** (not YAML frontmatter): uniform across markdown and JSON
> artifacts, robust to parse in a bare CI runner (no YAML dependency), and keeps the
> attestation out of human-facing persona prose. One file per artifact.

## Checklist (per artifact)

1. **Read** the canonical framework ([trust-before-intelligence.md](./trust-before-intelligence.md)).
2. **INPACT** — for each of Instant, Natural, Permitted, Adaptive, Contextual, Transparent:
   mark `satisfied` (with evidence pointing at the control that satisfies it) or `n_a`
   (with written justification). A bare `n_a` fails.
3. **GOALS** — same for Governance, Observability, Availability, Lexicon, Solid.
4. **Layers** — list which of the 7 trust layers the artifact relies on and how (≥1).
5. **Map to existing controls** — prefer `execution/ops_platform/` (trust_engine,
   audit_log, agent_registry, governance_scorecards, reputation_scorer) over new code.
6. **Runtime artifacts** — set `trust_score_ref` to the capability_id so the scorer can
   pull the live `trust_engine` profile.
7. **Approver + verified_at** — record who signed off and when.

## Pass / fail criteria (computed by the scorer)

- **`non_compliant`** (gate FAILS) if any of:
  - the attestation is missing or fails schema validation;
  - any INPACT dimension or GOALS target has status `gap`;
  - any dimension/target is `n_a` **without** non-empty evidence (unjustified);
  - `framework_version` does not match the current vendored snapshot;
  - no layers are mapped;
  - a referenced `trust_engine` profile recommends `DO_NOT_DEPLOY`.
- **`conditional`** (gate PASSES, flagged) if compliant but caveated — e.g. any justified
  `n_a`, or a referenced trust profile at `REQUIRES_REVIEW` / `LIMITED_ROLLOUT`.
- **`compliant`** (gate PASSES) otherwise.

The CI gate fails the PR only on `non_compliant`. `conditional` passes but is surfaced.

## Steps

1. Run the scorer / CI check locally: `python scripts/tbi_compliance_check.py <artifact-path>`.
2. Fix any blocking issues (close the gap, or justify the `n_a` with real evidence).
3. Re-run until the verdict is `compliant` or `conditional`.
4. The scorer emits a `tbi.evaluated` audit entry per evaluation (Transparent/Observability).

## Failure handling (self-annealing)

A gate failure is a first-class trigger for the self-annealing loop (CLAUDE.md §5):
fix the artifact or attestation → extend the scorer test if a rule was wrong → update
this directive if the rule was unclear → re-run the gate.

## Safety constraints

- Never mark the gate passed when a `non_compliant` verdict exists.
- Never accept an `n_a` without written justification.
- Never edit the vendored framework snapshot to make an artifact pass — that is an
  approval-gated change (CLAUDE.md) and defeats the purpose.

## Verification

- `python scripts/tbi_compliance_check.py <changed artifacts>` exits 0.
- `pytest tests/execution/ops_platform/test_tbi_compliance.py` passes.
- Every changed AI artifact has a schema-valid `<artifact>.tbi.json` with a non-`non_compliant` verdict.
