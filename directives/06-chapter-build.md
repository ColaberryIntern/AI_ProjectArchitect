# Directive: Chapter-by-Chapter Build

## Purpose

Build project documentation one chapter at a time with review gates. Each chapter matches one outline section. Nothing is built in bulk.

## Inputs

- Locked outline (from `state.outline`, status must be `approved`)
- Chapter entries (from `state.chapters`)
- Current phase must be `chapter_build`

## Steps

### Step 1: Identify Next Chapter
Find the next chapter in `state.chapters` with status `pending`. Build chapters in index order — never skip ahead.

### Step 2: Generate Chapter Content
Each chapter must contain these 3 required elements, in order:

**1. Purpose**
- Why this chapter exists
- What decision, behavior, or build step it supports
- How it fits into the overall system

**2. Design Intent**
- Why this approach was chosen
- What tradeoffs were considered
- What constraints shaped the design

**3. Implementation Guidance**
- Practical steps
- Conceptual sequencing
- File or component-level guidance (where applicable)
- Assumptions about tools and environment
- Detailed enough for an intern to act without guesswork

### Step 3: Save and Record
Save chapter content as a Markdown file. Update state via `state_manager.record_chapter_status(state, chapter_index, "draft", content_path)`.

### Step 4: Present for Review
Present the chapter. Pause for feedback.

Acceptable feedback:
- **Approve** — "Approved", "Chapter approved", "Continue"
- **Revise** — Request targeted changes to specific sections
- **Clarify** — Ask for more detail on a specific point
- **Flag** — Identify missing context

### Step 5: Handle Revisions
- Revisions apply ONLY to the current chapter
- Maximum 2 revisions per chapter (tracked via `state_manager.get_revision_count()`)
- Update status to `revision_1` or `revision_2` via `state_manager.record_chapter_status()`
- Broad dissatisfaction triggers a reset or outline unlock, NOT endless edits
- Revisions never retroactively change prior chapters

### Step 6: Run Quality Gates
Run `execution/quality_gate_runner.py` on the chapter. Record results via `state_manager.record_chapter_quality()`.

If quality gates fail:
- Flag specific issues
- Revise the chapter
- Re-run quality gates

### Step 7: Approve and Proceed
On explicit approval, update status to `approved` via `state_manager.record_chapter_status()`.

Proceed to the next pending chapter. Repeat Steps 1-7 until all chapters are approved.

### Step 8: Complete Chapter Build Phase
When `state_manager.all_chapters_approved()` returns `True`, advance to `quality_gates`.

## Outputs

- Each chapter has a Markdown file at its `content_path`
- Each chapter status is `approved`
- Each chapter has a quality report
- Phase advanced to `quality_gates`

## Edge Cases

- User requests a 3rd revision: Explain the limit. Suggest resetting the chapter or unlocking the outline.
- User wants to change a previously approved chapter: This requires outline unlock — not a revision.
- Chapter content contradicts a prior chapter: Flag the inconsistency. Resolve before approving.

## Safety Constraints

- Never generate partial chapters
- Never dump multiple chapters at once
- Never skip ahead to a later chapter
- Never include placeholder content
- Never quietly change scope
- If uncertain, pause and ask

## Verification

- Each chapter contains Purpose, Design Intent, and Implementation Guidance
- Revision count does not exceed 2
- Quality gates have been run on each chapter
- All chapters have status `approved`
- `state_manager.all_chapters_approved()` returns `True`

## Auto-Build Mode

When the user approves the outline, the system can automatically generate all chapters
without human intervention. This uses:

- `execution/chapter_writer.py` — LLM generates chapter content with VS Code + Claude Code assumptions baked in
- `execution/auto_builder.py` — Orchestrates the full pipeline (generate → gate → retry → assemble)
- Quality gates are run automatically after each chapter
- Failed chapters are retried up to 2 times with gate failure feedback
- Final quality gates and document assembly happen automatically
- Progress is streamed to the browser via Server-Sent Events (SSE)

The manual chapter-by-chapter flow remains available as a fallback via `/chapter-build`.

## Build Depth Modes

The system supports four build depth modes that control chapter length, detail level, and
quality thresholds. The depth mode is selected on the Outline Approval page before building.

| Mode | Target Pages | Min Words/Chapter | Min Subsections | Max Tokens |
|------|-------------|-------------------|-----------------|------------|
| **Lite** | 20-30 | 800 | 3 | 4096 |
| **Standard** | 40-70 | 1500 | 4 | 6144 |
| **Enterprise** (default) | 80-150 | 2500 | 6 | 8192 |
| **Architect-Level** | 150+ | 3500 | 8 | 12288 |

Configuration is stored in `execution/build_depth.py`.

### Enterprise Chapter Requirements

Each of the 10 enhanced outline sections has chapter-specific subsection requirements
defined in `CHAPTER_REQUIREMENTS` (in `execution/build_depth.py`). For example:

- **Executive Summary** (Enterprise): Vision & Strategy, Business Model, Competitive Landscape,
  Market Size Context, Risk Summary, Technical High-Level Architecture, Deployment Model,
  Assumptions & Constraints
- **Technical Architecture & Data Model** (Enterprise): Service Architecture, Database Schema,
  API Design, Infrastructure & Deployment, Security Architecture, Performance Design

The enterprise chapter writer (`generate_chapter_enterprise()`) injects these required
subsections into the LLM prompt, ensuring each chapter has the right level of detail.

## Quality Scoring System

Chapters are scored on a 0-100 scale across four dimensions (25 points each):

1. **Word Count** — percentage of target word count met
2. **Subsection Coverage** — percentage of required subsections found as headings
3. **Technical Density** — presence of code blocks, file paths, CLI commands, tables, env vars
4. **Implementation Specificity** — execution order, I/O definitions, dependencies, env config

Score status thresholds:
- **Incomplete** (< 40): Chapter is severely lacking
- **Needs Expansion** (40-74): Chapter needs more detail
- **Complete** (>= 75): Chapter meets quality bar

Scoring functions are in `execution/quality_gate_runner.py` (`score_chapter()`, `score_document()`).

### Post-Build Validation

After all chapters are generated, a validation pass identifies chapters scoring below 75.
These are auto-regenerated (one additional attempt) with specific feedback about missing
subsections and low-scoring dimensions. This happens within `execution/auto_builder.py`.

### Auto-Complete Navigation

If all chapters pass quality thresholds and the document is assembled successfully,
the system automatically advances through `quality_gates → final_assembly → complete`
without requiring manual navigation through those phases.
