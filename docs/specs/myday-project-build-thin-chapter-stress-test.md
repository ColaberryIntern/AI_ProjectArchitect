# My Day Project Build — Thin/Boilerplate Chapter Stress-Test (Build Guide → project-plan parser)

**Status:** Design / spec (parser not yet built)
**Layer:** Layer 2 design for a Layer 3 deterministic parser (`/execution`), per `CLAUDE.md`
**Scope:** Stress-test the (not-yet-written) Build Guide → `project-plan.json` parser + the ID Law + the validation gate against a chapter that is *all prose* — headings, no explicit tasks, no acceptance criteria, no failure path.

---

## 0. What is real today vs. what this doc specs

This matters because the prompt describes a doc→plan parser as if it ships; it does **not**. Three layers exist, with a gap in the middle:

| Piece | File | State |
|---|---|---|
| Build Guide generator (LLM chapters) | `execution/chapter_writer.py` | **Real.** Legacy 3-field + enterprise full-markdown chapters. |
| Build Guide assembler (mechanical concat) | `execution/document_assembler.py` + `execution/auto_builder.py` | **Real.** Writes `output/{slug}/{Name}_Build_Guide_{version}.md`. |
| Desired-state plan: schema `cb-project-plan/v1`, the **ID Law**, content hash, **validation gate** | `execution/advisory/project_plan.py` (tests: `tests/execution/advisory/test_project_plan.py`) | **Real and tested.** `assign_ids()`, `validate_plan()`, `content_hash()`. |
| **Build Guide markdown → `project-plan.json` parser** | — | **DOES NOT EXIST.** This is the subject of this doc. |
| Shipped "Create a new project from My Day" build path | `execution/advisory/myday_build_orchestrator.py` → `build_task_generator.py` → `basecamp_build_writer.py` | **Real, but bypasses the Build Guide.** Goes `feature_catalog → smart_selector → requirements.json → one Basecamp todo per requirement`. It never parses chaptered markdown and never calls `project_plan.validate_plan()`. |

So this stress-test has two payoffs: (1) it specifies the missing parser, and (2) it documents why feeding the *current* enterprise Build Guide into the *existing* `project_plan` validation gate fails loudly on essentially every chapter — because Build Guides today carry **zero** machine-readable tasks, phases, or acceptance criteria. The thin/boilerplate chapter is not an edge case; **it is the shape of every chapter the generator produces today.**

---

## 1. How Build Guides are actually produced and structured

### 1a. Generation (`execution/chapter_writer.py`)
- One chapter per outline section. Two formats:
  - **Legacy** (`generate_chapter`): LLM returns JSON `{purpose, design_intent, implementation_guidance}`.
  - **Enterprise** (`generate_chapter_enterprise`, default for all non-`light` depth modes): LLM returns `{content: "<full markdown body with ## subsection headings>"}`.
- The prose is steered by quality gates (word count, forbidden vague phrases, "execution order" signals like *first/then/next*). **None of these gates require an explicit task list, a phase tag, an acceptance criterion, or a failure path.** Build readiness is judged lexically, not structurally.
- LLM-unavailable fallbacks (`_fallback_chapter`, `_fallback_chapter_enterprise`) emit generic prose with a stock `## {subsection}` per required subsection — pure boilerplate by construction.

### 1b. Heading conventions a parser MUST rely on
Rendered per chapter via templates (`execution/template_renderer.py`):

- Legacy (`templates/chapter_template.md`):
  ```
  # Chapter {{index}}: {{title}}
  ## Purpose
  ## Design Intent
  ## Implementation Guidance
  ```
- Enterprise (`templates/enterprise_chapter_template.md`):
  ```
  # Chapter {{index}}: {{title}}
  > **Chapter purpose**: …blockquote…
  {{content}}        ← LLM body, whose subsections are ## headings
  ```
- Subsection vocabulary (the `##` headings) is a closed list per `(section_title, depth_mode)` in `execution/build_depth.py` → `CHAPTER_REQUIREMENTS` (e.g. Functional Requirements/enterprise: *Feature Specifications, Input/Output Definitions, Workflow Diagrams, Acceptance Criteria, API Endpoint Definitions, Error Handling & Edge Cases, Feature Dependency Map, …*). These are **section names, not features** — "Acceptance Criteria" is one `##` subsection of prose, not a per-task field.

### 1c. Assembly (`execution/document_assembler.py`, `assemble_full_document`)
- **Mechanical only** — `compile_document()` concatenates approved chapter files in outline order, joined by `\n\n---\n\n`; `apply_formatting()` normalizes whitespace and guarantees a blank line before every `#{1,6}` heading; `add_version_header()` prepends `# {name} — Build Guide` + version block + `---`. **No content is rewritten or introduced** (directive `08-final-assembly.md`).

**Net heading grammar a parser sees in the assembled guide:**
```
# {Project} — Build Guide        ← H1 doc title (version header)
---
# Chapter {N}: {Title}           ← H1 per chapter  (INIT spine = N)
> blockquote (enterprise only)
## {Subsection}                  ← H2 = candidate "feature section" / list
…prose, bullets, code fences, tables…
---                              ← chapter separator
# Chapter {N+1}: …
```
There is **no H3-per-task convention, no `[BUILD]`/`[BREAK]` markers, no acceptance line, no due offset** anywhere in generator output. The parser must therefore *derive* `cb-project-plan/v1` structure from prose, and that derivation is exactly what the thin-chapter walk below stresses.

### 1d. The target schema, ID Law, validation gate (`execution/advisory/project_plan.py`)
- Mapping intent: chapter → **initiative**; `##` feature section → **list**; action item → **todo** with `phase ∈ {BUILD,BREAK,HARDEN}`, `acceptance`, `dueOffsetDays`.
- **ID Law (pure function of doc position):** `INIT.ch{NN}-slug(chapterTitle)`, `LIST.ch{NN}.slug(featureTitle)`, `TODO.<list>.slug(titleSansPhaseTag)`; collisions get `-2/-3` in document order (`resolve_collisions`). The chapter number is the spine (survives title renames); the phase tag is stripped before slugging so re-tagging a todo keeps its id (`strip_phase_tag`).
- **Validation gate** `validate_plan()` (fail-loud, before any Basecamp write) enforces, verbatim from the code:
  1. every id == its computed value (no hand-edited ids)
  2. every id globally unique
  3. every todo has `phase ∈ {BUILD,BREAK,HARDEN}` AND non-empty `acceptance`
  4. every `list.designs[]` / `todo.deps[]` references an existing node id
  5. every `docAnchor` exists in the source doc (only when `doc_anchors` supplied)
  6. no active node references a retired node
  7. every **active** feature/list has ≥1 active **BUILD** AND ≥1 active **BREAK** todo (Failure-First Design)

---

## 2. The thin chapter, walked line-by-line

A representative boilerplate chapter — the *normal* output shape, e.g. the LLM-unavailable fallback or any low-effort prose chapter. Chapter 4, depth=enterprise:

```markdown
# Chapter 4: Functional Requirements

> **Chapter purpose**: This chapter provides the design intent and implementation guidance for Functional Requirements.

## Feature Specifications

The system supports role management. Users can be assigned roles and the
application enforces them across the relevant screens. This is foundational
to the product and should be implemented early.

## Acceptance Criteria

The team will know this section is done when the role features behave as
described above and the experience is consistent.
```

Walk (parser pass = lexical structure extraction, then ID Law, then gate):

1. `# Chapter 4: Functional Requirements` → matches `^#\s+Chapter\s+(\d+):\s+(.+)$`. **Initiative**, `order=4`, `title="Functional Requirements"` → `INIT.ch04-functional-requirements`. ✔ spine extracted.
2. `> blockquote` → boilerplate; ignored.
3. `## Feature Specifications` → an H2. Is it a *feature/list* or just a *prose subsection*? It is in the closed `CHAPTER_REQUIREMENTS` vocabulary (a section-structure heading, not a feature name). Naively treating every H2 as a list → `LIST.ch04.feature-specifications`. **First fork: subsection-vs-feature ambiguity** (Edge G).
4. Prose under it: free text. Parser hunts for action items (imperative bullets, "Step N", numbered lists, todo markers). **None found** — it is a paragraph. → list with **zero todos**. (Edge A.)
5. `## Acceptance Criteria` → another H2 in the vocabulary. As a *list* it again has zero action-item todos. As a *field*, it is section-wide narrative ("done when … consistent"), not tied to any todo. **No per-task acceptance exists.** (Edge C.)
6. No `[BUILD]`/`[BREAK]`/`[HARDEN]` tag appears anywhere → every todo (if any were synthesized) has **no phase**. (Edge F.)
7. No failure/negative-path language ("reject", "invalid", "must not", "422") → **no BREAK candidate**. (Edge B.)
8. ID Law on whatever was extracted: `assign_ids()` is total — it never throws; it slugs empty/duplicate titles deterministically (empty slug → `INIT.ch04-`, duplicates → `-2/-3`). So **the ID Law always "succeeds"**; correctness is the gate's job, not the ID Law's.
9. `validate_plan()` outcome on the literal parse (lists with no todos): Rule 7 fires for **every** list — `feature 'LIST.ch04.feature-specifications' has no active BUILD todo` and `… no active BREAK todo (Failure-First Design)`. If the parser had instead synthesized happy-path-only todos with no phase/acceptance, Rules 3 + 7 fire. **Either way the gate rejects — which is the desired fail-loud behavior, not a bug.**

**Conclusion:** a thin chapter cannot yield a valid plan. The only safe outcomes are (a) emit nothing for that chapter and let the gate report the empty initiative, or (b) emit `status:proposed` scaffolding plus explicit `TODO:` markers so a human promotes/fills before any Basecamp write. **Never fabricate green acceptance or a phantom passing BREAK.**

---

## 3. Edge cases → deterministic parser RULE for each

| # | Edge case (from a thin chapter) | Deterministic parser RULE |
|---|---|---|
| A | **Heading-only / prose-only section** (no action items) | Emit the list with `todos: []` and `status: "proposed"`, plus a single placeholder todo `[BUILD] TODO: define tasks for "{section}"` with `status:"proposed"` and `acceptance:"PROPOSED — no acceptance authored"`. The gate still rejects (Rule 7 wants a BREAK), surfacing the gap. Do **not** drop the section silently — a dropped section = a silently lost requirement. |
| B | **Feature with happy-path tasks but no BREAK/failure task** (violates Rule 7) | **Fail-loud-with-scaffold**, never silent green. Synthesize a default BREAK todo `[BREAK] TODO: define the failure path for "{section}"`, `status:"proposed"`, `acceptance:"PROPOSED — author the negative-path assertion"`. Because it is `proposed`, Rule 7 (which only counts **active** todos) *still fires* → gate rejects until a human promotes it to `active`. This is the deliberate design: the scaffold guides the human; the gate refuses to pass until they act. (Rationale: `project_plan.py` Rule 7 counts only `status=="active"`.) |
| C | **Missing per-task acceptance** | Synthesize `acceptance: "PROPOSED — meets the section's stated intent: {one-line section summary}"` and set the owning todo `status:"proposed"`. Rule 3 requires *non-empty* acceptance only, so this string passes Rule 3 *lexically* — therefore acceptance synthesis MUST be paired with `status:"proposed"` so the human-promotion gate (active-only) is what actually blocks. Marker prefix `PROPOSED —` is mandatory and greppable. Never emit a confident acceptance the author did not write. |
| D | **Duplicate section titles within a chapter** | Rely on `resolve_collisions()` exactly as the ID Law specifies: first occurrence keeps the bare id, repeats get `-2/-3` **in document order**. Parser must preserve source document order so suffixing is reproducible. No parser-side dedup, no merging — two `## Acceptance Criteria` headings become two distinct lists `LIST.ch04.acceptance-criteria` and `…-2`. |
| E | **Un-anchorable heading** (slug empty: emoji-only, punctuation-only, non-Latin that NFKD-strips to nothing) | `slug()` returns `""`, yielding ids like `LIST.ch04.` / `INIT.ch04-`. RULE: detect empty-slug headings during parse and (1) keep the node (position is real) but (2) attach a parse warning `unanchorable-heading` and force `status:"proposed"`. Do **not** invent a title. If you also emit a `docAnchor`, it MUST be a real anchor present in the source doc, else Rule 5 rejects — so prefer emitting **no** `docAnchor` over a guessed one. |
| F | **Task with no obvious phase** | Default to `BUILD` **only** when the line is clearly an action item (imperative verb / "Step N" / checkbox). Tag the todo with a parse note `phase-defaulted`. Do **not** default phase for prose that is not an action item — that would manufacture a task. A defaulted-BUILD todo still leaves the list without a BREAK → Rule 7 fires → human prompted. (Never default to BREAK/HARDEN — those assert a tested failure/hardening path that prose did not state.) |
| G | **H2 that is a structure subsection, not a feature** (the common case — every enterprise `##` is from `CHAPTER_REQUIREMENTS`) | Maintain the closed `CHAPTER_REQUIREMENTS`/`CHAPTER_REQUIREMENTS_DEFAULT` vocabulary (already in `execution/build_depth.py`) as a **non-feature heading set**. An H2 whose normalized title is in that set is treated as a *prose container*, not a list, **unless** it contains ≥1 detected action item. This stops the parser from minting a meaningless `LIST.ch04.workflow-diagrams` from a diagram-description paragraph. |
| H | **Chapter with zero features** (no H2 yields a list) | Emit the initiative with `lists: []` and `status:"proposed"`, plus a parse warning `empty-initiative`. The gate is silent on empty initiatives (Rule 7 is per-list), so the parser MUST add an explicit advisory error of its own and refuse to advance to Basecamp write. An empty initiative is the single most dangerous silent case — it looks valid to `validate_plan()`. |

---

## 4. Recommended handling policy (for the parser implementer)

Concrete, in priority order. Target module: `execution/advisory/build_guide_parser.py` (new, deterministic, no I/O beyond reading the markdown string; unit-tested in `tests/execution/advisory/test_build_guide_parser.py`).

1. **Two-pass, position-faithful parse.** Pass 1: tokenize headings/blocks preserving document order (never reorder — the ID Law's collision suffixing and `dueOffsetDays` derivation depend on order). Pass 2: classify each H2 via the `CHAPTER_REQUIREMENTS` vocabulary (Edge G) and extract action items by an explicit, testable grammar (markdown checkboxes `- [ ]`, `Step N`, numbered lists, leading imperative verb). Document the grammar; nothing implicit.

2. **`proposed` over silent green — the core invariant.** Anything the parser had to *synthesize* (a phase, an acceptance, a BREAK, a placeholder todo, an unanchorable node) is emitted with `status:"proposed"` and a `PROPOSED —` / `TODO:` text marker. The `project_plan.py` gate counts only `active` todos for Rule 7, so proposed scaffolding is **visible but non-passing** — the human promotion step is the real gate. This is the requirement from `CLAUDE.md` ("prefer deterministic verification; fail loud") realized: the parser never produces a plan that *looks* shippable but isn't.

3. **Call the existing pure functions; do not reimplement IDs.** After building the tree, call `project_plan.assign_ids(plan)` then `project_plan.validate_plan(plan, doc_anchors=anchors_from_doc)`. Collect `validate_plan()` errors AND parser-side advisories (`empty-initiative`, `unanchorable-heading`, `phase-defaulted`, `acceptance-synthesized`, `break-synthesized`) into one report.

4. **Gate hard before any Basecamp write.** `basecamp_build_writer.publish_to_basecamp()` must only run when `validate_plan()` returns `[]` **and** zero unresolved `proposed` nodes remain (or an explicit operator override is recorded). Today's `myday_build_orchestrator` skips this entirely — wiring the parser in means inserting `validate_plan` as a blocking step between `generate_build_tasks` and `publish_to_basecamp`.

5. **`docAnchor` only when real.** Compute the set of GitHub-style heading anchors actually present in the source doc and pass it as `doc_anchors`; emit a node's `docAnchor` only if it is in that set. Prefer omitting `docAnchor` to guessing — Rule 5 fails loud on a dangling anchor, which is good, but a *guessed-correct* anchor that drifts on the next regen is worse (silent mis-link).

6. **Never default to BREAK/HARDEN, never write a confident acceptance.** BUILD is the only safe phase default and only for genuine action items (Edge F). Acceptance and BREAK are always `proposed` + marked. The asymmetry is intentional: a missing happy-path step is recoverable; a fabricated "we tested the failure path" claim is a correctness lie the Failure-First gate exists to prevent.

7. **Generator gap to log alongside the parser.** Because today's Build Guides contain no machine-readable tasks/phases/acceptance, the parser will mark essentially everything `proposed`. The durable fix is upstream: extend `chapter_writer.py` (or a structured sidecar emitted next to each chapter, e.g. `output/{slug}/chapters/ch{N}.tasks.json`) so tasks, `[BUILD]/[BREAK]` phase, and acceptance are authored at generation time rather than reverse-engineered from prose. Recommend the sidecar route — it keeps the prose Build Guide human-readable while giving the parser a deterministic, gate-ready source, and avoids brittle prose-mining. Until then, the parser-with-`proposed`-scaffolding above is the safe bridge.

---

## 5. Source references

- Generator: `execution/chapter_writer.py` (`generate_chapter`, `generate_chapter_enterprise`, `_fallback_chapter*`)
- Heading templates: `templates/chapter_template.md`, `templates/enterprise_chapter_template.md`, `templates/final_document_template.md`
- Subsection vocabulary: `execution/build_depth.py` (`CHAPTER_REQUIREMENTS`, `CHAPTER_REQUIREMENTS_DEFAULT`, `get_chapter_subsections`)
- Assembler: `execution/document_assembler.py` (`compile_document`, `apply_formatting`, `assemble_full_document`); directive `directives/08-final-assembly.md`; orchestration `execution/auto_builder.py`
- Schema / ID Law / hash / gate: `execution/advisory/project_plan.py`; tests `tests/execution/advisory/test_project_plan.py`
- Shipped My Day build path (bypasses the guide today): `execution/advisory/myday_build_orchestrator.py`, `execution/advisory/build_task_generator.py`, `execution/advisory/basecamp_build_writer.py`
- Chapter-build directive: `directives/06-chapter-build.md`
