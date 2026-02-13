# Directive: State Management

## Purpose

Explain how the project state file works, where it lives, and how it is maintained throughout the pipeline.

## What the State File Is

A single JSON file per project that tracks the entire project lifecycle. It is the single source of truth for:
- What phase the project is in
- What has been decided
- What is still open
- What is locked (immutable)
- All approval history

## Where It Lives

```
output/{project-slug}/project_state.json
```

The slug is derived from the project name (lowercase, hyphens for spaces, alphanumeric only).

## How It Works

### Claude reads state to understand context
Before acting in any phase, Claude loads the state to know:
- Current phase
- Prior decisions
- Locked content
- Outstanding items

### Scripts write state to record decisions
State is never edited manually or by Claude directly. All updates go through `execution/state_manager.py` functions:
- `initialize_state()` — Create a new project
- `record_idea()` — Capture the raw idea
- `add_feature()` — Add a classified feature
- `approve_features()` — Approve feature discovery
- `set_outline_sections()` — Set outline structure
- `lock_outline()` — Lock outline (immutable)
- `unlock_outline()` — Unlock for changes (increments version)
- `record_chapter_status()` — Track chapter progress
- `record_chapter_quality()` — Store quality gate results
- `record_final_quality()` — Store final validation results
- `record_document_assembly()` — Record final document output
- `advance_phase()` — Move to next pipeline phase

### State is validated before every write
The state file is validated against `config/schemas/project_state.schema.json` to ensure structural integrity.

## Phase Transition Rules

Phases must follow this strict order — no skipping, no going backward:

```
idea_intake → feature_discovery → outline_generation → outline_approval → chapter_build → quality_gates → final_assembly → complete
```

Each transition requires specific preconditions to be met (see individual directives).

## Immutability Rules

Once the outline is locked:
- `outline.locked_hash` stores a SHA256 hash of the content
- Any modification can be detected via `verify_outline_integrity()`
- Changes require an explicit unlock, which resets approval and increments version

## Safety

- State is written atomically (temp file + rename) to prevent corruption
- State is never manually edited
- The `updated_at` timestamp is refreshed on every save
- Invalid state cannot be saved (schema validation enforced)

## Verification

- State file exists at the expected path
- State passes schema validation (`execution/schema_validator.py`)
- Phase transitions follow the defined order
- Outline integrity can be verified after locking
