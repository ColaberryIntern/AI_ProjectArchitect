# Directive: Acceptance Criteria

## Purpose

For every `must`-priority Requirement, generate testable acceptance criteria (AC) in Given/When/Then form. ACs are the contract between "the requirement says so" and "a developer can write a runnable test from this alone."

This directive is invoked from inside [03-feature-discovery.md](03-feature-discovery.md) Step 6.

## Inputs

- A Requirement object with `id`, `actor`, `action`, `value`, `priority`, and `requirement_type` set
- The approved ideation summary (for vocabulary and context)

## Steps

### Step 1: One AC per success path

For each Requirement, identify each distinct success path the actor can take. One AC per path. Three is a healthy baseline for `must`-priority functional requirements; fewer is acceptable only if the action is genuinely unary.

### Step 2: Write each AC in Given/When/Then form

```
Given <a precise precondition / state>
When  <a single triggering action>
Then  <a measurable, observable outcome>
```

**`given`** — must name a concrete state, not a vague setting. ✅ "an authenticated broker with one open invoice for $5,200" — ❌ "the user is on the page".

**`when`** — exactly one action. If the AC needs to chain actions, it's two ACs.

**`then`** — must be measurable. The outcome should be a value, threshold, observable state change, or persistent record. ✅ "the invoice status transitions to `disputed` and an audit row is written within 2s" — ❌ "the system handles it appropriately".

### Step 3: Add at least one negative or edge-case AC

For each `must`-priority Requirement, include at least one AC that covers either:
- A failure mode (invalid input, missing precondition, downstream service down)
- An edge case (boundary value, empty state, max load)

A spec without negative paths is not yet a spec.

### Step 4: Mark `measurable: true` honestly

Set `measurable: true` only if the `then` clause is mechanically verifiable from logs, API responses, or persisted state. If a human must subjectively decide, it is not measurable — rewrite the `then` clause.

### Step 5: Assign IDs

`AC-<reqid-numeric>-N`. For Requirement `REQ-007`, ACs are `AC-007-1`, `AC-007-2`, …

### Step 6: NFR ACs

Non-functional Requirements (`requirement_type: nonfunctional`) MAY have ACs that reference NFR thresholds, but the threshold itself must live in the `nfr` array (with `category`, `metric`, `threshold`, `verification`). Do not duplicate the threshold in the AC body — reference it.

Example:
- `nfr`: `{category: performance, metric: "p95 invoice-list latency", threshold: "< 500ms", verification: "k6 load test"}`
- AC: "Given 1000 invoices in the database, When the broker requests the list, Then p95 latency stays below the documented threshold (NFR-perf-001)."

## Anti-Patterns (auto-rejected by the AC Testability Gate)

| ❌ Pattern | ✅ Replacement |
|---|---|
| "The system handles errors appropriately" | "Then the API returns 422 with `error_code = INVALID_BOL` and the audit log records `{action, actor, payload}`." |
| "Performance is good" | NFR with concrete threshold + `verification` method |
| "The user sees a confirmation" | "Then the response body contains `status: confirmed` AND a `confirmation_id` matching `^conf_[a-z0-9]{12}$`" |
| "It's secure" | NFR `{category: security, metric: ..., threshold: ..., verification: ...}` |

## Outputs

- Each `must`-priority Requirement has `acceptance_criteria` array with ≥ 1 entry (≥ 2 recommended, with at least one negative/edge case)
- Each AC has `id`, `given`, `when`, `then`, and an honest `measurable` flag
- Persisted into `state.features.core[*].acceptance_criteria` and `state.features.optional[*].acceptance_criteria`
- Re-emitted into `output/{slug}/specs/requirements.json` by `requirements_writer.write_requirements()`

## Safety Constraints

- Never auto-mark `measurable: true` without explicit verification of the `then` clause
- Never accept an AC for a `must`-priority Requirement that lacks a measurable outcome
- Never auto-generate ACs without surfacing them to the user for approval (LLM drafts must be reviewed)

## Verification

- For every `must`-priority Requirement, `len(acceptance_criteria) >= 1`
- The AC Testability Gate (in `quality_gate_runner.py`) scores each AC ≥ 2 on a 0–3 scale
- No two ACs share an `id`
