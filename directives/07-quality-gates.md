# Directive: Quality Gates & Validation

## Purpose

Run the final quality validation suite on the complete document. All 5 gates must pass before final assembly.

## Inputs

- All chapters approved (from `state.chapters`)
- Current phase must be `quality_gates`

## Steps

### Step 1: Run Full Document Quality Gates
Execute `execution/quality_gate_runner.py run_final_gates()` across the entire document.

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

**Gate 5 — Intern Success Test**
Binary pass/fail:
"Could a competent intern, with no additional context, successfully execute this project using only this document?"

Evaluate against:
- Can the intern answer "What am I building?" from the document alone?
- Can the intern answer "What do I build first?" from the document alone?
- Can the intern answer "What does done look like?" from the document alone?
- Does the document eliminate the need for repeated clarification questions?

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

- Never skip any of the 5 gates
- Never mark a gate as passed when failures exist
- Partial success is not sufficient — all gates must pass
- The Intern Success Test is the final and most important gate

## Verification

- All 5 gates show `pass`
- `quality.final_report.all_passed` is `True`
- `quality.final_report.ran_at` has a valid timestamp
- No unresolved vagueness flags remain
- Phase is `final_assembly`
