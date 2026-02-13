# Directive: Outline Approval & Locking

## Purpose

Create a hard alignment checkpoint before execution begins. The approved outline becomes immutable and is the single source of truth for the chapter build.

## Inputs

- A validated outline (from `state.outline.sections`)
- Current phase must be `outline_approval`

## Steps

### Step 1: Present for Approval
Present the outline and ask the user for one of 4 responses:

1. **Approved** — Accept as-is. Scope is locked. Chapter build may begin.
2. **Revise** — Outline structure is sound but specific sections need modification. Revise only what is requested.
3. **Expand** — Outline is missing required sections or depth. Ask where expansion is desired before acting.
4. **Reduce** — Outline is too large or complex. Remove or defer non-essential sections.

Only "Approved" allows progression.

### Step 2: Handle Non-Approval Decisions
- If **Revise**: Record decision via `state_manager.record_outline_decision("revise", notes)`. Modify requested sections only. Re-present.
- If **Expand**: Record decision. Ask where expansion is needed. Add targeted sections. Re-present.
- If **Reduce**: Record decision. Identify sections to remove or defer. Re-present.

Iterate until the user explicitly approves.

### Step 3: Lock on Approval
When user says "Approved", "Approve outline", or "Lock this":

1. Call `state_manager.lock_outline()` to:
   - Set `outline.status` to `approved`
   - Store SHA256 hash of outline content
   - Record approval timestamp
   - Create chapter entries for each section
2. Advance phase to `chapter_build`

### Step 4: Post-Lock Constraints
After approval, the following are forbidden without re-approval:
- Adding new outline sections
- Removing approved sections
- Renaming sections
- Reordering sections
- Expanding scope beyond what was approved

## Outputs

- `outline.status` is `approved`
- `outline.locked_hash` contains integrity hash
- `outline.locked_at` contains timestamp
- `chapters` array created with one entry per section
- Phase advanced to `chapter_build`

## Edge Cases

- User says "Looks good" or "Seems fine": These are NOT approval. Ask for explicit approval language.
- User silence: NOT approval. Re-prompt.
- User wants changes after approval: Require explicit unlock via `state_manager.unlock_outline(reason)`. This resets approval and increments version.

## Safety Constraints

- Never assume approval from vague language
- Never modify the outline after locking without an explicit unlock
- Unlock resets the entire approval — there is no partial unlocking
- Each outline approval creates a new version (v1, v2, v3...)

## Verification

- State records approval status as `approved`
- Approval history contains the approval event with timestamp
- Outline hash is stored and matches current content
- All chapter entries created (one per section)
- Phase is `chapter_build`
- `state_manager.verify_outline_integrity()` returns `True`
