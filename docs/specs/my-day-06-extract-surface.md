# My Day - Phase 6: Extract surface (wires Phase 4 engine to a user-facing tab)

Phase 4 shipped the extraction engine: `skill_extractor.extract(source_kind, bc_id, output_type, slug)`, the per-output-type templates under `app/templates/extracted/`, `extracted_writer.py` (file write + branch push), and the library sync hook. Onboarding (`admin.user_new()`) already calls it for fresh users.

Phase 6 puts that same engine behind a manual surface in My Day so any user can turn any past BC todo or todolist into a reusable skill / agent / directive / cron / script on demand.

## Repo + branch

- `c:\Users\ali_m\OneDrive\Business\Colaberry Novedea\AI Projects\AI Project Architect & Build Companion\` (advisor)
- Branch from main after Phase 5 dogfood lands: `feature/my-day-extract-surface`
- Deploy: `./deploy.sh` to `root@95.216.199.47` on Ali's greenlight

## Read first

| Source | Why |
|---|---|
| Phase 4 PR / commit | The engine you're wiring. Open the merged PR, read what `skill_extractor.extract()` accepts + returns. Don't re-derive; the contract is set. |
| `app/routers/my_day.py` | Existing My Day routes. Mirror the auth + session pattern. |
| `app/templates/my_day/workspace.html` | Visual pattern to mirror in `extract.html`. |
| `app/templates/my_day/_my_day_base.html` | Nav structure; add the Extract link here. |
| `execution/products/ops/sync.py` - `pull_todos_for_user()` | Existing BC pull pattern. Extend it for the completed/archived case. |
| `execution/products/ops/bc_comments.py` | For the BC echo on the source ticket; use the existing `<bc-attachment>` mention helper. |
| BC todo **9946499448** (Kes Anthropic_ContentRegistry) | The dogfood case; extract a skill from this and verify content quality. |

## Concrete tasks (small)

### 1. Completed-work source

Add to `sync.py`: `pull_completed_for_user(user_id, days=30) -> list[OpsTodo]`. Hits `/projects/recordings.json?type=Todo&status=archived`, paginates, filters to user-assigned. Cache 60s in `OpsState.completed_cache` (extend the dataclass in `store.py`). Pattern mirrors `pull_todos_for_user`.

### 2. Three new routes in `app/routers/my_day.py`

- `GET /my-day/extract/` -> list of recently-completed work, filter by time range + project
- `GET /my-day/extract/preview` (query: `source_kind`, `bc_id`, `output_type`) -> calls `skill_extractor.extract()` with no commit, returns rendered preview markdown for the modal
- `POST /my-day/extract/commit` (body: same + optional `slug`) -> calls `skill_extractor.extract()` + `extracted_writer.write_and_commit()`, persists `OpsExtractedArtifact` record, posts BC echo comment via `bc_comments.post()`, returns `{ branch, file_path, raw_url, slug }`

All three use `_require_user(request)` like the existing routes.

### 3. Template

`app/templates/my_day/extract.html` - extends `_my_day_base.html`. Left pane: completed-work list. Right pane: preview area + output_type dropdown + Generate Preview button + Commit button. After commit, success card with branch link + raw URL + copy-to-clipboard. Reuse `_my_day_styles.html`.

Add nav link in `_my_day_base.html`: "Extract" tab between Home and Workspace (or wherever feels right in the existing nav).

### 4. Storage record

Add `OpsExtractedArtifact` dataclass to `store.py`: `slug`, `output_type`, `source_kind`, `source_bc_id`, `branch`, `file_path`, `created_at`, `created_by`, `use_count` (default 0). Persist to `output/ops/users/<user_id>/extracted.json`. If Phase 4 already added this dataclass, reuse it.

### 5. BC echo on source ticket

After successful commit, post a comment on the source BC todo:

```
[Extracted] This ticket was converted to a <output_type> on <date>.
Branch: skill-extracted/<slug>
File: <file_path>
```

Tag the original assignee with proper `<bc-attachment>` mention HTML so they get the email.

### 6. Tests

- `tests/app/routers/test_my_day_extract.py` - auth, preview happy path, commit happy path with mocked git, BC echo with mocked HTTP
- `tests/execution/products/ops/test_sync_completed.py` - `pull_completed_for_user` happy path with mocked BC pagination + cache TTL behavior

Engine tests stay where Phase 4 put them; don't duplicate.

## Definition of done

1. Branch `feature/my-day-extract-surface` against `main`
2. `pytest tests/app/routers/test_my_day_extract.py tests/execution/products/ops/test_sync_completed.py` passes
3. E2E manual: open `/my-day/extract/`, pick BC todo **9946499448** (Kes Anthropic_ContentRegistry), output_type = skill, preview shows correct YAML frontmatter + body referencing the actual watcher service + cron schedule, commit creates `skill-extracted/anthropic-content-registry` branch with the file
4. BC echo comment landed on todo 9946499448 with the Kes mention
5. PR description includes: screenshot of the Extract tab, branch link, raw URL of the extracted SKILL.md
6. Ali reviews + merges + deploys via `./deploy.sh`

## Anti-scope

- Don't touch `skill_extractor.py`, `extracted_writer.py`, or the `.j2` templates; that's Phase 4's surface, and the onboarding flow uses it. Read-only from Phase 6.
- Don't build the replay loop (incrementing `use_count` when an extracted artifact is reused in the agent path). Future phase.
- Don't extract from Message Board, chats, or campfire. Todos and todolists only.
- Don't auto-merge `skill-extracted/<slug>` branches.

Start by reading the Phase 4 PR, then `sync.py`, then `_my_day_base.html`. The whole build should be ~300 LOC.
