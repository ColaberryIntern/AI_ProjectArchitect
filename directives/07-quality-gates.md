# Directive: Quality Gates & Validation

## Purpose

Run the final quality validation suite on the complete document. The five **lexical gates** must pass on every chapter and the assembled document. The three **spec-driven gates** (added in the spec-driven upgrade) must pass against the project's Requirement set.

## Inputs

- All chapters approved (from `state.chapters`)
- Current phase must be `quality_gates`
- `output/{slug}/specs/requirements.json` exists (written at outline approval)

## Gate Inventory

| # | Gate | Type | Source |
|---|---|---|---|
| 1 | Completeness | Lexical (regex) | `quality_gate_runner.check_completeness` |
| 2 | Clarity | Lexical (regex) | `quality_gate_runner.check_clarity` |
| 3 | Build Readiness | Lexical (regex) | `quality_gate_runner.check_build_readiness` |
| 4 | Anti-Vagueness | Lexical (regex) + measurability | `quality_gate_runner.check_anti_vagueness`, `ambiguity_detector.detect_measurability_issues` |
| 5 | Intern Success Test (document-level) | Lexical (regex) | `quality_gate_runner.check_intern_test` |
| 6 | **Requirement Coverage** | Structural | `quality_gate_runner.check_requirement_coverage` |
| 7 | **AC Testability** | LLM-judged | `quality_gate_runner.check_ac_testability` |
| 8 | **Chapter Intern Test (semantic)** | LLM-judged | `quality_gate_runner.check_chapter_intern_semantic` |
| 9 | **TBI Compliance** | Deterministic (per AI artifact) | `tbi_compliance.evaluate_attestation` via `scripts/tbi_compliance_check.py` |

Gates 1–5 run as `run_final_gates(document_text)`. Gates 6–8 run as `run_spec_gates(requirements, chapters)`. Both result objects expose an `all_passed` field. Gate 9 runs per AI artifact (not per document) — see [compliance/tbi-compliance-gate.md](compliance/tbi-compliance-gate.md).

## Steps

### Step 1: Run Full Document Quality Gates (lexical)
Execute `execution/quality_gate_runner.py run_final_gates()` across the entire document.

### Step 1b: Run Spec Gates
Execute `execution/quality_gate_runner.py run_spec_gates(requirements, chapters)`. The Requirements come from `requirements_writer.collect_requirements(state)`; chapters are `[{id, text}]` for every approved chapter.

### Step 2: Evaluate Each Gate

**Gate 1 — Completeness**
- All outline sections have corresponding approved chapters
- No placeholder language anywhere ("TBD", "we'll decide later", "to be determined")
- Core features are fully described
- Non-goals are explicitly stated
- Dependencies and assumptions are documented

**Gate 2 — Clarity**
- Each chapter's purpose can be summarized in one sentence
- Intended outcomes are clearly stated
- Terminology is consistent across all chapters
- Responsibilities are clearly assigned
- Constraints are explicit

**Gate 3 — Build Readiness**
- Execution order is clear across the full document
- Required inputs and outputs are defined for each component
- Dependencies between components are stated
- File or module boundaries are described
- "Done" criteria are included for key deliverables

**Gate 4 — Anti-Vagueness**
Run `execution/ambiguity_detector.py` on all chapter content.

Flag and require replacement of:
- "Handle edge cases" → specify which edge cases and how
- "Optimize later" → specify what, when, and criteria
- "Make it scalable" → specify scale targets and constraints
- "Ensure good UX" → specify UX requirements and success criteria
- "Use best practices" → specify which practices and why

**Gate 5 — Intern Success Test (document-level, lexical)**
Binary pass/fail. Lexical signal scan against the assembled document for "what we're building", "build order", and "definition of done" markers. This gate is *retained* as a fast safety net; the rigorous variant is Gate 8 below.

Evaluate against:
- Can the intern answer "What am I building?" from the document alone?
- Can the intern answer "What do I build first?" from the document alone?
- Can the intern answer "What does done look like?" from the document alone?
- Does the document eliminate the need for repeated clarification questions?

**Gate 6 — Requirement Coverage**
Every Requirement with `priority: must` must have at least one chapter referenced in `traces_to.chapter_ids`. This is a structural check (no LLM). Orphans block final assembly.

**Gate 7 — AC Testability (LLM-judged)**
For every acceptance criterion attached to a `must`-priority Requirement, an LLM judge scores testability on a 0–3 scale. The gate fails if any score is < 2 (i.e. the AC could not yield a runnable test without significant assumptions). If the LLM is unavailable, the gate is reported as `skipped` (advisory) rather than failing — so a CI environment without `OPENAI_API_KEY` does not block progress on this gate alone.

**Gate 8 — Chapter Intern Test (semantic, LLM-judged)**
Per chapter, the LLM judge is asked: given this chapter and the linked Requirements, can you answer (a) inputs, (b) outputs, (c) one runnable test scenario, (d) the definition of done? Each question must be answered "yes" with concrete evidence quoted from the chapter or its linked Requirements. This replaces the keyword-based Gate 5 for the per-chapter case; Gate 5 remains for the assembled-document scan.

**Gate 9 — TBI Compliance (deterministic, per AI artifact)**
If the build produced or changed any **AI artifact** (agent persona, skill, blueprint, advisory/workflow agent, library AI asset), each must carry a passing **Trust Before Intelligence** attestation (`<artifact>.tbi.json`). Run `python scripts/tbi_compliance_check.py <artifact paths>`; the gate fails on any `non_compliant` verdict. This is the document-pipeline surface of the repo-wide TBI mandate (CLAUDE.md). Full procedure: [compliance/tbi-compliance-gate.md](compliance/tbi-compliance-gate.md).

### Step 3: Generate Quality Report
Record results via `state_manager.record_final_quality()`.

### Step 4: Handle Failures
If any gate fails:
- Identify the specific chapters and sections that caused the failure
- Return to the relevant chapter(s) for targeted revision
- Re-run the failed gate(s)
- Repeat until all gates pass

### Step 5: Advance
When all 5 gates pass (`quality.final_report.all_passed` is `True`), advance to `final_assembly`.

## Outputs

- `quality.final_report.all_passed` is `True`
- Quality report contains per-gate results with details
- Phase advanced to `final_assembly`

## Edge Cases

- A single chapter causes multiple gate failures: Fix all issues in that chapter before re-running.
- Gate failure requires significant rewrite: This may trigger an outline unlock if scope is the issue.
- Anti-vagueness flags a term that is intentionally generic: Document the justification explicitly.

## Safety Constraints

- Never skip any gate (lexical or spec)
- Never mark a gate as passed when failures exist
- Partial success is not sufficient — all gates must pass (with one carve-out: Gate 7 is advisory when the LLM is unavailable)
- The Chapter Intern Test (Gate 8) is the most important gate — it is the rigorous successor to the lexical Gate 5

## Verification

- All 5 lexical gates show `pass`
- All 3 spec gates show `pass` (or Gate 7 is `skipped` due to no LLM and the orchestrator has explicitly accepted the advisory)
- `quality.final_report.all_passed` is `True`
- `quality.final_report.ran_at` has a valid timestamp
- No unresolved vagueness flags remain
- Phase is `final_assembly`
