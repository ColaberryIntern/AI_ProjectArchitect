# Directive: Outline Generation

## Purpose

Produce a structured, numbered outline that locks scope before building begins. The outline becomes the contract between thinking and execution.

## Inputs

- Approved feature set (from `state.features`)
- Current phase must be `outline_generation`

## Steps

### Step 1: Generate Required Sections
Every outline must include these 7 sections in strict order:

1. **System Purpose & Context** (Why) — Why this project exists and what problem it solves
2. **Target Users & Roles** (Who) — Who interacts with the system and in what capacity
3. **Core Capabilities** (What) — What the system must do to deliver value
4. **Non-Goals & Explicit Exclusions** (What Not) — What the system intentionally does not attempt
5. **High-Level Architecture or Flow** (How) — How major components interact (conceptual, not code)
6. **Execution Phases or Modules** (How to Build) — How the build is broken into logical chunks
7. **Risks, Constraints, and Assumptions** — What could go wrong and what is being assumed

### Step 2: Add Optional Sections (if relevant)
Only include when justified:
- Data & Storage Model
- AI / Automation Logic
- Security & Access Considerations
- Integrations & External Dependencies
- Metrics & Success Measurement
- Future Enhancements
- Deployment & Maintenance Notes

Optional sections must be explicitly labeled as such.

### Step 3: Apply Naming Conventions
Section titles must be:
- Clear, descriptive, and unambiguous
- Action- or purpose-oriented
- No marketing language ("Magic Layer", "Secret Sauce")
- No internal shorthand

### Step 4: Validate Before Presenting
Run `execution/outline_validator.py` to check:
- All 7 required sections present
- Section ordering is correct
- No placeholder language ("TBD", "we'll figure out later")
- No significant section overlap
- Each section has a clear, distinct purpose

### Step 5: Run Clarity & Scope Validation Checklist
Before presenting to the user, confirm:
- [ ] Each section has a clear purpose
- [ ] No section overlaps significantly with another
- [ ] Scope feels achievable, not bloated
- [ ] Core vs optional elements are distinguishable
- [ ] An intern could explain the outline at a high level
- [ ] No section exists "just in case"

### Step 6: Present the Outline
Record sections via `state_manager.set_outline_sections()`. Present the numbered, hierarchical outline to the user for approval.

## Outputs

- `outline.sections` populated with section objects (index, title, type, summary)
- Each section maps 1:1 to a future chapter
- Outline passes validation checks
- Ready for approval phase

## Edge Cases

- User wants a section not in the required list: Add as an optional section if justified.
- Outline is too large: Suggest splitting or deferring sections. Protect core intent.
- Sections overlap: Merge or clarify boundaries before presenting.

## Safety Constraints

- Never omit required sections
- Never use placeholder content
- Never present an outline that fails validation
- Section ordering is non-negotiable

## Verification

- All 7 required sections are present
- Sections follow the strict ordering: Why → Who → What → What Not → How → Build → Risks
- `execution/outline_validator.py` reports all checks passing
- No placeholder language detected
- Each section maps to exactly one future chapter
