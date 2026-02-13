# Directive: Final Document Assembly

## Purpose

Compile all approved chapters into one coherent, durable execution artifact. The final document is the single source of truth for the project.

## Inputs

- All chapters approved
- All quality gates passed (`quality.final_report.all_passed` is `True`)
- Current phase must be `final_assembly`

## Steps

### Step 1: Compile Chapters
Call `execution/document_assembler.py compile_document()` to:
- Read all approved chapter files
- Compile in exact outline order
- No content rewriting
- No new content introduction
- Preserve cross-references

### Step 2: Apply Formatting
Call `execution/document_assembler.py apply_formatting()`:
- Clear chapter headings
- Consistent heading hierarchy
- Numbered sections where sequencing matters
- Bullet points for lists and constraints
- Explicit labels for optional vs required content

### Step 3: Apply Naming Convention
Generate filename: `{ProjectName}_Build_Guide_v{N}.md`
- Project name from `state.project.name`
- Version from `state.document.version`

### Step 4: Add Version Tag
Include at the top of the document:
- Version number
- Date of assembly
- Brief change summary (from version history)

### Step 5: Final Validation
Before delivery, confirm:
- All chapters are present and approved
- Quality gates have passed
- Formatting is consistent
- Naming and versioning are correct

### Step 6: Export
- Write the final Markdown file to `output/{project_slug}/`
- Record via `state_manager.record_document_assembly(filename, output_path)`
- Advance phase to `complete`

### Step 7: Deliver
Present to the user: "The final document is ready: `{filename}`. It has been saved to `{output_path}`."

## Outputs

- Final Markdown document written to output directory
- `document.filename` set
- `document.output_path` set
- `document.assembled_at` has timestamp
- Phase is `complete`

## Edge Cases

- A chapter file is missing or corrupted: Fail assembly. Report the specific chapter.
- Cross-references are broken: Flag during compilation. Fix before completing assembly.
- User requests PDF output: Generate PDF from the canonical Markdown source.

## Safety Constraints

- Assembly is mechanical — never rewrite content
- Never introduce new content during assembly
- Never compile with missing or unapproved chapters
- The Markdown file is the canonical source; other formats are derived

## Verification

- Output file exists at the recorded path
- File contains all chapters in correct order
- Formatting is consistent throughout
- Version tag is present and correct
- `state.current_phase` is `complete`
