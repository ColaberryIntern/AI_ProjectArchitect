# Hand-off to AI_ProjectArchitect Claude Code agent

Hand-off from session CC-20260603-v7da (Colaberry Enterprise AI Leadership Accelerator repo).

## What happened in the originating session

A standing-orders BC ticket (9956775973) asked the prior agent (in the Colaberry Enterprise AI Leadership Accelerator repo, NOT this one) to "fully build the AI_ProjectArchitect system."

That agent invoked SCOPE GUARD: the build is 5 weeks, 33 tickets, in a different git repo. Build Index itself states "A Claude Code agent in the ColaberryIntern/AI_ProjectArchitect repo should be able to read this list cold and build the system end to end." The prior agent posted a focused-question verdict offering 3 options (A: use intended path / B: cross-repo bootstrap / C: coordinate-only) on BC comment 9961267608.

Ali chose Option A + asked for the kickoff prompt for THIS (current) Claude Code session, running in the AI_ProjectArchitect repo.

## What you (this Claude Code session) own

The full buildout, per the Build Index. Read spec/BUILD_INDEX.md first.

## Spec files in this directory (committed at the start of this session)

- `spec/TICKET_DESCRIPTION.md` — the BC ticket description verbatim
- `spec/BUILD_INDEX.md` — the Build Index content from BC comment 9956776017 (the primary spec)
- `spec/BUILD_INDEX_ATTACHMENTS_NOTE.md` — BC comment 9956801994 explaining the original BUILD_INDEX.md + current_list_snapshot.json attachments
- `spec/ADVISOR_CLAUDE_CODE_PROMPT.md` — BC comment 9956813365 referencing the kickoff prompt (note: the original .md file was a BC attachment; if its body is needed beyond this comment, refetch from BC)
- `spec/CURRENT_LIST_SNAPSHOT.json` — the 15 current todos in the BC parent list (id 9953889092) with full descriptions + metadata

## Source-of-truth BC URLs

- Master Build Index todo: https://app.basecamp.com/3945211/buckets/7463955/todos/9956775973
- Parent list ("AI_ProjectArchitect company-wide rollout"): https://app.basecamp.com/3945211/buckets/7463955/todolists/9953889092
- Account: 3945211 / Bucket: 7463955 / List: 9953889092

## Standing Orders (from Ali, must follow in every action)

PROFESSIONAL OUTPUT — All work is executive-grade. Tone: confident, concise, decisive. No filler, no hedging.

COLABERRY QUALITY RUBRIC (self-check before shipping any output):
1. COMPLETENESS — all sections present, no TBDs, dependencies + assumptions explicit
2. CLARITY — purpose of each section in 1 sentence, terms used consistently
3. BUILD READINESS — execution order clear, inputs/outputs defined, dependencies stated, file boundaries described
4. ANTI-VAGUENESS — forbidden phrases include: "handle edge cases", "optimize later", "make it scalable", "use best practices", "where applicable", "circle back", "going forward", "low-hanging fruit", "just checking in", em-dashes
5. INTERN SUCCESS TEST — could a competent intern execute using only what you delivered?

WORKFLOW ORDERS:
- POST PROGRESS — after each meaningful step, post a 1-line comment on the active BC ticket
- POST YOUR ANSWER — final response is BC-paste-ready, verdict first
- CLOSE IF DONE — only if >85% confident + all 5 rubric gates pass
- ASK IF UNSURE — focused question + STOP
- NEVER NARRATE — do, then report
- SCOPE GUARD — STOP if scope expands >2x or touches outside ticket

## Memory + doctrine references (from the originating repo)

The originating session followed memory rules including:
- Every BC todo created must have due_on set at creation (PUT requires full body)
- Ali Personal: every outbound email + produced document attaches to its originating ticket via sendWithBcAttach
- No em-dashes anywhere in any communication
- Branded HTML + plain-text signature on every outbound email
- Production deploys after hours unless Ali explicitly greenlights

Apply equivalent discipline here.

## Recommended first 3 actions for this session

1. Read `spec/BUILD_INDEX.md` end to end. Identify Week 1 ticket 1 ("Infra 1").
2. Read the repo's existing structure (`agents/`, `app/`, `config/`, `configschemas/`, `directives/`, `docs/`, `execution/`, `output/`, `CLAUDE.md`, `README.md`, `Dockerfile`, `docker-compose.yml`). Build a mental model of what already exists.
3. Post a 1-line progress comment on BC todo 9956775973 declaring readiness + naming the first concrete commit you'll make.

## Critical context: this is a multi-week project

The Build Index sequences 5 weeks of work. A single Claude Code session will not finish it. The right cadence: per-day or per-ticket session checkpoints, each one closing 1-3 tickets, each with a BC progress comment + a git push. The originating ticket (9956775973) is the orchestration meta-todo and stays open until the full 5-week cycle wraps.

Session start: 2026-06-03 evening. Build Index due 2026-06-08 (the meta-todo's own due date - 5 days from now).
