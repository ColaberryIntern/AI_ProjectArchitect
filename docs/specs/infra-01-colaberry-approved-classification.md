# [Infra 1] Colaberry-approved classification + auto-sync spec

**Ticket:** Basecamp [9953889131](https://app.basecamp.com/3945211/buckets/7463955/todos/9953889131) · due 2026-06-08
**Status:** Shipped
**Author:** Advisor Claude Code (autonomous), reviewed by Ali on merge
**Depends on:** none (foundational)
**Unblocks:** `[Auth 1]` data model · `[Workflow 1]` publish workflow · `[Infra 2]` library→GH sync v2

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Classification flag exists in the library data model (UseCase / Skill / Agent) | `AssetMetadata.vetted` (already present) + `UseCase.vetted` (already present). See [§2](#2-classification-flag). |
| 2 | Approval workflow defined: who can mark (Ali, Ram, Karun = sales; Kes = tech) | `config/library_approvers.json` + `github_sync.can_approve()`. See [§3](#3-approver-matrix). |
| 3 | Once flagged, automated job syncs to `github.com/ColaberryIntern/AI_ProjectArchitect` at `library/{type}/{slug}.md` | `execution/products/library/github_sync.py::sync_asset()`. See [§4](#4-sync-mechanism). |
| 4 | Sync handles updates (re-flag) + deletions (unflag) | `sync_asset(..., operation="upsert")` and `operation="delete"`. Full reconciliation via `sync_all_approved()`. See [§5](#5-update--unflag). |
| 5 | Audit log: every sync event recorded with author + timestamp + artifact + commit SHA | `output/library/_github_sync/{date}.jsonl` — `SyncEvent` dataclass per row. See [§6](#6-audit-log). |

## 1. Scope

This ticket establishes the **classification + sync contract**. It does
NOT change the broader Library product UX, the Project Architect
pipeline, or any prod data on advisor.colaberry.ai. Subsequent tickets
(`[Auth 1]`, `[Workflow 1]`) layer on company-level scoping and submit
workflows — this one defines the foundation they sit on.

## 2. Classification flag

Already present (shipped with the Library product):

```python
# execution/products/library/store.py
@dataclass
class AssetMetadata:
    ...
    vetted: bool = False
    vetted_by: str | None = None
    vetted_at: str | None = None
    vetted_status: str = "unreviewed"  # unreviewed | pending | vetted | rejected
    vetted_notes: str = ""
```

```python
# execution/products/library/use_cases.py
@dataclass
class UseCase:
    ...
    vetted: bool = False
    vetted_by: str | None = None
    vetted_at: str | None = None
    vetted_notes: str = ""
```

Set today via the curator decision form on every asset detail page
(`/library/{category}/{asset_id}` → "Colaberry vetting" card).

**No schema change needed for this ticket.** `[Auth 1]` will extend
these records with `owner_company_id`, `approval_state` enum, and
`visibility` separately as part of the multi-tenant data model.

## 3. Approver matrix

Stored in `config/library_approvers.json`. The matrix encodes who can
stamp `vetted=True` on each asset category. The Library UI's curator
form does not (yet) enforce this — for v1, enforcement happens in the
sync pipeline via `can_approve(email, category)`. Unauthorized
approvers can still flip the in-app flag, but the sync job will refuse
to push their commits.

| Approver | Role | Can approve |
|---|---|---|
| ali@colaberry.com | lead | **all** categories |
| ram@colaberry.com | sales | `sales`-tagged (use_cases, prompts, agents) |
| karun@colaberry.com | sales | `sales`-tagged (use_cases, prompts, agents) |
| kes@colaberry.com | tech | tech categories (skills, mcp, capabilities, workflows, projections, recovery, chaos, governance, evals, connectors, adapters, templates, policies) |

Adding a new approver: edit `config/library_approvers.json`, no code
change. The file is human-readable JSON with a `_doc` field describing
the format.

API surface:

```python
from execution.products.library.github_sync import can_approve
ok, reason = can_approve("ram@colaberry.com", "use_cases")  # → (True, "Ram grants category 'use_cases'")
ok, reason = can_approve("ram@colaberry.com", "skills")     # → (False, "Ram cannot approve 'skills'")
```

## 4. Sync mechanism

`execution/products/library/github_sync.py::sync_asset(category, asset_id, approver_email, operation, …)`

Sync is **explicit-trigger**, not write-amplification. Three ways to fire:

| Trigger | Caller | Notes |
|---|---|---|
| **Manual** | Operator runs `python -m execution.products.library.github_sync sync <cat> <id>` (CLI to be wrapped) | Useful for one-off recovery / backfill |
| **Scheduled** | Daily reconciliation via the Library scheduler (next ticket: `[Infra 2]` extends APScheduler) | Catches drift |
| **Webhook** | Library UI's curator "Submit decision" form POSTs to `/library/{cat}/{id}/sync` when transitioning to vetted=True (wired in `[Workflow 1]`) | Real-time |

Per-call flow:

```
sync_asset(cat, id, email, op="upsert")
   │
   ├─ Load approver matrix → can_approve(email, cat)?
   │     └─ if no → fail-fast, audit row with status="failed"
   │
   ├─ Render markdown body:
   │     - Frontmatter: asset_id, category, version, owner, vetted_*, tags, source
   │     - Sections: What it's used for · Description · How to use · Example
   │                 · Install · README (snapshot) · Source link
   │
   ├─ Resolve target path: library/{type}/{slug}.md
   │     - slug = `re.sub(r"[^A-Za-z0-9._-]+", "-", asset_id)[:120]`
   │     - Repo: ColaberryIntern/AI_ProjectArchitect (configurable)
   │     - Branch: main (configurable)
   │
   ├─ Commit via gh CLI:
   │     gh api PUT /repos/{repo}/contents/{path} \
   │       -f message="[Library sync] Upsert {cat}/{id} (approver: {Name})" \
   │       -f content={b64} -f branch={branch} \
   │       -f committer[name]="Colaberry Library Sync" \
   │       -f committer[email]="library-sync@colaberry.com" \
   │       [-f sha={existing_sha} if file exists]
   │
   └─ Record SyncEvent → output/library/_github_sync/{date}.jsonl
         with commit_sha from gh response
```

**Why `gh` API and not `git` shell-out?** The `gh` CLI has cached auth
on the Hetzner prod box and uses the GitHub Contents API which is
atomic per-file. Avoids the "clone-edit-commit-push" cycle on the prod
box where we don't want a writable checkout of `main`. The fallback
path (`_sync_via_git()`) is reserved for future work if the `gh` CLI is
unavailable; v1 just fails loudly with a clear audit row.

## 5. Update + unflag

**Update (re-approval, edited content):** `sync_asset(cat, id, op="upsert")`
re-renders + re-pushes. The GitHub Contents API handles SHA-based
overwrite via the `sha=` parameter. Idempotent: re-running with no
content change produces a new commit but identical file.

**Unflag (`vetted=True → False` or rejected/withdrawn):**
`sync_asset(cat, id, op="delete")` deletes the file. The reconciler
(`sync_all_approved()`) walks every category, finds assets whose
`vetted=False` but have a prior `upsert` audit row, and issues
`delete`s for them.

The full reconciliation is the **source of truth** — if a single per-event
sync misses (network failure mid-write, etc.), the next nightly
reconciliation closes the gap. Operations are idempotent: re-delete of
an already-deleted file is a no-op (returns empty SHA, audit row status
`noop`).

## 6. Audit log

`output/library/_github_sync/{date}.jsonl` — one JSON object per line:

```json
{
  "event_id": "5b9e7c44a912",
  "operation": "upsert",
  "asset_kind": "library_asset",
  "category": "skills",
  "asset_id": "MCP Filesystem Server",
  "repo": "ColaberryIntern/AI_ProjectArchitect",
  "branch": "main",
  "target_path": "library/skills/MCP-Filesystem-Server.md",
  "author_email": "ali@colaberry.com",
  "author_display_name": "Ali Muwwakkil",
  "triggered_by": "manual",
  "commit_sha": "a3413a5f8c9e123…",
  "status": "success",
  "error": "",
  "bytes_written": 4827,
  "started_at": "2026-06-02T20:14:33Z",
  "finished_at": "2026-06-02T20:14:35Z"
}
```

Read via `github_sync.history(category=..., asset_id=...)` — returns
filtered `SyncEvent` list across all date files.

**Audit fields meet criterion 5:**
- ✅ `author_email` + `author_display_name` (Ali / Ram / Karun / Kes)
- ✅ `started_at` + `finished_at` (timestamps)
- ✅ `category` + `asset_id` + `target_path` (artifact identity)
- ✅ `commit_sha` (the GitHub SHA returned by the Contents API)

Plus diagnostic fields: `status`, `error`, `bytes_written`, `operation`,
`triggered_by`.

## 7. Open questions (with defaults — Ali, flip if needed)

| # | Question | Default | Why |
|---|---|---|---|
| 1 | Should Karun/Ram cross-approve into each other's categories? | **No** — `sales` role is one set. Either of them can approve any sales-tagged item. | Avoid finger-pointing about ownership |
| 2 | Should Kes be able to approve `use_cases` (since they're at the intersection of biz + tech)? | **No** — Kes can mark `vetted=True` in the UI, but the sync job refuses. Falls to Ali to break ties. | Keeps the lanes clean |
| 3 | Should the sync push to `main` directly, or via a sync branch + PR? | **Direct to `main`** for v1. The commits are clearly tagged `[Library sync]`; bypassing PR review is acceptable because the *content* was already curator-reviewed. | Avoids PR fatigue |
| 4 | Should the sync run on PR-merge (webhook) or scheduled (nightly)? | **Both.** `[Workflow 1]` will wire the curator-form webhook. `[Infra 2]` will wire the nightly reconciliation. | Belt-and-braces |
| 5 | What happens to legacy library items with `vetted_by=None`? | They stay unsynced. A backfill commit can be done manually later. | Don't paper over missing attribution |

## 8. Files shipped this ticket

| File | Purpose |
|---|---|
| `config/library_approvers.json` | Approver matrix per category |
| `execution/products/library/github_sync.py` | Sync runner + audit + auth check |
| `tests/execution/products/test_github_sync.py` | 11 tests covering auth / slug / render / audit / sync / reconciliation |
| `docs/specs/infra-01-colaberry-approved-classification.md` | This document |

## 9. How to invoke (operator's manual)

```bash
# One asset, dry-run (no GitHub call)
python -c "from execution.products.library import github_sync as gs; \
                print(gs.sync_asset('skills', 'MCP Filesystem Server', \
                                              'ali@colaberry.com', dry_run=True))"

# Real sync — requires gh CLI authenticated with write access to the repo
python -c "from execution.products.library import github_sync as gs; \
                print(gs.sync_asset('mcp', 'MCP Filesystem Server', \
                                              'kes@colaberry.com'))"

# Full nightly reconciliation
python -c "from execution.products.library import github_sync as gs; \
                print(gs.sync_all_approved())"

# Read the audit
python -c "from execution.products.library import github_sync as gs; \
                [print(e) for e in gs.history(category='mcp')]"
```

## 10. Hand-off to next tickets

| Next ticket | What this gives it |
|---|---|
| `[Auth 1]` data model | Confirms the classification flag belongs on the asset records; `[Auth 1]` will add `owner_company_id`, `approval_state` enum (extending the current bool), and `visibility` |
| `[Workflow 1]` publish workflow | Webhook integration point: form POST → `sync_asset()` |
| `[Infra 2]` library→GH sync v2 | Already exists in skeleton — extend with retry policy, gh-CLI-missing fallback, multi-repo support (per-company workspace repos) |
