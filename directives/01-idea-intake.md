# Directive: Idea Intake

## Purpose

Accept a raw idea in any form and preserve it as a reference point. This is the entry point for every project.

## Inputs

- A user-provided idea in any format: sentence, paragraph, rant, half-thought, comparison ("something like X but for Y")

## Steps

1. Accept the idea without judgment or modification
2. Do NOT rephrase prematurely — understand intent first
3. Record the original idea verbatim using `execution/state_manager.py record_idea()`
4. Advance phase to `feature_discovery` using `execution/state_manager.py advance_phase()`
5. Redirect to Feature Discovery page

## Outputs

- State file updated with `idea.original_raw` (verbatim text)
- State file updated with `idea.captured_at` (timestamp)
- Phase advanced to `feature_discovery`

## Edge Cases

- User provides multiple ideas: Ask which one to focus on first. Defer others explicitly.
- User provides a very long description: Capture it fully. Do not truncate.
- User says "I'm not sure yet": Acknowledge uncertainty. Ask one clarifying question to help them articulate the idea.

## Safety Constraints

- Never modify the original idea text
- Never assume additional context that was not provided

## Verification

- State file contains a non-empty `idea.original_raw` field
- State file contains a valid `idea.captured_at` timestamp
- Phase is now `feature_discovery`
