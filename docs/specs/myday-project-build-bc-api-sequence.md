# my-day-build-01 — "Create a new project from My Day": Basecamp API reconcile sequence

**Status:** design / implementation-ready
**Owner:** My Day
**Scope:** the deterministic execution layer that reconciles a desired-state `project-plan.json` into Basecamp 3, plus the `bc_manifest.json` that makes re-runs cheap and idempotent.

This is a Layer-3 (execution) spec per `CLAUDE.md`: the reconciler is a deterministic, rerunnable script. Claude plans and validates it; it does not run business logic. All endpoints below were verified against the official **bc3-api** (https://github.com/basecamp/bc3-api) on 2026-06-24, and every call MUST reuse the repo's existing BC client helpers (cited per step) rather than hand-rolling HTTP.

---

## 1. Object model: plan → Basecamp

The plan is a desired-state tree. Basecamp 3 has a fixed 3-level to-do hierarchy under a single Todoset per project. We map them as:

| Plan concept | Basecamp object | BC endpoint family |
|---|---|---|
| **initiative** | a **to-do LIST** (`todolist`) | `/buckets/{bucket}/todosets/{todoset}/todolists.json` |
| **list / feature** (child of an initiative) | a **GROUP** inside that todolist | `/buckets/{bucket}/todolists/{list}/groups.json` |
| **todo** (leaf) | a **to-do** under the group (or directly under the list) | `/buckets/{bucket}/todolists/{group_or_list}/todos.json` |

Key BC fact this mapping leans on (already exploited by `execution/products/ops/sync.py::_walk_project_todos`): **a group behaves like a sub-list for the todos endpoint** — you POST/GET todos at `/buckets/{bucket}/todolists/{GROUP_ID}/todos.json`, using the *group's* id in the `{list}` slot. A todo filed under a group is NOT returned by the parent list's `/todos.json`, so the reconciler must always create/read leaf todos against the group id, not the list id.

### Identifiers

- `bucket` = the Basecamp **project id** (a.k.a. bucket id). One per built project.
- `todoset` = the single Todoset id for the bucket; **not** a fixed route — it is discovered from `GET /projects/{bucket}.json` → `dock[name==todoset].id` (and the dock entry's `.url`). See `execution/products/library/mcp_tools.py::_tool_list_project_todolists` and `mcp_tools.py::_tool_create_todolist` for the canonical discovery walk; `ops/sync.py::_walk_project_todos` does the same via the dock.
- The plan **never stores BC ids.** All BC ids live in `output/{slug}/bc_manifest.json`.

---

## 2. The manifest (`output/{slug}/bc_manifest.json`)

The plan is portable and id-free; the manifest is the join table between plan and Basecamp.

```jsonc
{
  "schema": 1,
  "slug": "acme-portal",
  "bucket": 47126345,            // BC project/bucket id (set once, on first bind)
  "todoset": 1069479520,         // discovered from the dock, cached here
  "last_reconciled_at": "2026-06-24T14:00:00Z",
  "items": {
    // keyed by the plan's stable `id` (NOT a BC id)
    "init-onboarding": {
      "kind": "initiative",
      "bc_id": 1069480001,       // BC todolist id
      "contentHash": "a1b2…",    // hash of the synced name+description
      "startDate": "2026-07-01", // per-initiative; drives child due_on math
      "position": 1
    },
    "feat-signup": {
      "kind": "list",            // a feature → BC group
      "parent": "init-onboarding",
      "bc_id": 1069480042,       // BC group id (used as a todos source id)
      "contentHash": "c3d4…",
      "position": 1
    },
    "todo-build-form": {
      "kind": "todo",
      "parent": "feat-signup",
      "bc_id": 1069480099,       // BC todo id
      "contentHash": "e5f6…",
      "position": 1
    }
  }
}
```

- **`contentHash`** = a stable hash (e.g. sha256) of the normalized synced fields for that node (title/content + description + due_on + assignee_ids). On a re-run, if `plan node hash == manifest hash`, the node is skipped entirely — **no BC call**. This is what makes steady-state runs O(manifest) with zero network writes.
- **`startDate`** is stored per-initiative so child `due_on` values are recomputed deterministically when an initiative's start shifts.
- Manifest writes are atomic (write temp + rename) and happen incrementally after each successful BC create/update, so an interrupted run leaves a partial-but-correct manifest and the next run resumes.

---

## 3. Shared BC client — reuse, do not reinvent

There are **two** BC HTTP clients in the repo. Pick the one matching where the reconciler runs:

| Client | Location | Use it for | Built-in resilience |
|---|---|---|---|
| `_bc_request(method, url, payload, user)` | `execution/products/library/mcp_tools.py` | reconciler invoked **per-operator** (uses the operator's BC OAuth grant; writes authored by the human). Full absolute URLs (`https://3.basecampapi.com/{account}/…`). | 429/503 retry w/ `Retry-After`; **single 401 self-heal** (drops cached token, forces refresh, retries once) via `_invalidate_bc_token_caches`. |
| `api.get/post/put/delete` + `api.paginated_get` | `tools/bc_mcp/api.py` | reconciler running as the **CB System** service identity (path-relative; `BASE` prepends account). | 401 → `get_token(force_refresh=True)` retry once. Raises `BasecampError(status, msg)` on ≥400. |

**Account id:** `_bc_account()` (env `BASECAMP_ACCOUNT_ID`, default `3945211`) in mcp_tools; `api.ACCOUNT_ID` ("3945211") in bc_mcp. **Use the helper, never a literal.**

For paginated read-backs (Step 7) reuse `api.paginated_get(path, params, max_pages)` (bc_mcp) or the `_paginate` generator pattern in `ops/sync.py` — both already handle BC's append-to-end pagination, the exact bug class that caused the 2026-06-18 stale-list incident (new lists/groups land on page 2+).

> Rate-limit / 401 handling is therefore **already provided** — the reconciler calls these wrappers and never touches `urllib` directly. BC's budget is ~50 req / 10 s; for a large first-run reconcile, throttle like `ops/sync.py` (`HTTP_THROTTLE_SECONDS`, default 0.22s ≈ 5 req/s).

All examples below show **absolute** URLs (the `_bc_request` form). For the bc_mcp client, drop the `https://3.basecampapi.com/{account}` prefix.

---

## 4. The reconcile sequence

Notation: `{account}` = `_bc_account()`, `{bucket}` = manifest `bucket`, `{todoset}` = manifest `todoset`.

### 4.0 Bind / discover (once per run)

```
GET https://3.basecampapi.com/{account}/projects/{bucket}.json
  → resp.dock[ name == "todoset" ].id   → cache as manifest.todoset
  → resp.dock[ name == "todoset" ].url  → GET it → .todolists_url (for first-run read-back)
```

Reuse: `mcp_tools.py::_tool_list_project_todolists` (dock → todoset → `todolists_url`) and `mcp_tools.py::_tool_create_todolist` (same discovery before POST). `ops/sync.py::_walk_project_todos` uses `dock[name==todoset].id` directly.

> Creating the bucket itself (a brand-new BC project) is out of scope for this spec — the build flow binds an already-created/selected bucket. If auto-create is later needed: `POST /projects.json {name, description}`.

### 4.1 Create an initiative → todolist

```
POST https://3.basecampapi.com/{account}/buckets/{bucket}/todosets/{todoset}/todolists.json
{
  "name": "<initiative.title>",
  "description": "<initiative.description html>"   // optional
}
→ 201, resp.id  → manifest.items[<plan id>].bc_id
```

bc3-api: create todolist takes `name` (required) + `description`. The repo posts to the todoset's `todolists_url` (identical route) in `_tool_create_todolist`.

### 4.2 Create a feature → group inside the initiative's todolist

```
POST https://3.basecampapi.com/{account}/buckets/{bucket}/todolists/{list_bc_id}/groups.json
{
  "name": "<feature.title>",
  "color": "<optional: white|red|orange|yellow|green|blue|aqua|purple|gray|pink|brown>"
}
→ 201, resp.id  → manifest group bc_id
```

bc3-api `todolist_groups`: create group is `POST /buckets/{bucket}/todolists/{todolistId}/groups.json`, required `name`, optional `color`. `{list_bc_id}` is the **parent initiative's** todolist id from the manifest.

### 4.3 Create a todo under the group (or directly under the list)

```
POST https://3.basecampapi.com/{account}/buckets/{bucket}/todolists/{group_or_list_bc_id}/todos.json
{
  "content":      "<todo.title>",            // REQUIRED (the visible title)
  "description":  "<todo.description html>",  // optional
  "assignee_ids": [<bc_person_id>, …],        // optional
  "due_on":       "YYYY-MM-DD",               // optional; computed from initiative.startDate
  "starts_on":    "YYYY-MM-DD"                // optional
}
→ 201, resp.id  → manifest todo bc_id
```

`{group_or_list_bc_id}` is the **group** id when the todo's plan parent is a feature (the common case), or the **todolist** id for an initiative-level todo with no feature. Field names verified against bc3-api `todos` (create): `content` required; `description`, `assignee_ids`, `due_on`, `starts_on`, `completion_subscriber_ids`, `notify` optional. Repo precedent: `mcp_tools.py::_tool_create_ticket` (`{content, description}`) and `tools/bc_mcp/server.py::create_todo` (`{content, description, assignee_ids, due_on}` — the exact payload shape to reuse).

### 4.4 Update an existing todo IN PLACE (preserves comments + completion)

When `plan hash != manifest hash` for a node that already has a `bc_id`, **update in place** — do not delete-and-recreate (that would orphan comments and lose completion state):

```
PUT https://3.basecampapi.com/{account}/buckets/{bucket}/todos/{todo_bc_id}.json
{
  "content":      "<todo.title>",            // REQUIRED on PUT
  "description":  "<todo.description html>",
  "assignee_ids": [<bc_person_id>, …],
  "due_on":       "YYYY-MM-DD",
  "starts_on":    "YYYY-MM-DD"
}
→ 200
```

**Critical bc3-api semantics:** the update endpoint is a *replace* of the passed fields — **`content` is required and you must pass ALL fields you want to keep**, or unspecified ones get cleared. The reconciler therefore always sends the full desired field set (content + description + assignee_ids + due_on), built from the plan node, on every update. Updating via PUT (vs delete+recreate) preserves the todo's comment thread and completion status. Repo precedent for `PUT /buckets/{bucket}/todos/{id}.json`: `mcp_tools.py::_tool_recategorize_session` already issues this exact PUT.

> Initiatives (todolists) and features (groups) can likewise be renamed/re-described in place: `PUT …/todolists/{id}.json {name, description}` and `PUT …/todolists/groups/{groupId}.json {name, color}` (note the **`todolists/groups/{groupId}`** shape for a group — see Step 4.6). Same hash-gate applies.

### 4.5 Completion is BC-owned — never overwrite it

The reconciler is a structure/content sync; **completion is operator state in BC**, surfaced read-only into My Day via `ops/sync.py`. The reconciler MUST NOT auto-complete or auto-reopen todos as part of reconciliation. (If a plan node is *removed*, retire it — Step 4.7 — rather than completing it.) For reference, completion is `POST …/todos/{id}/completion.json` (complete) / `DELETE …/todos/{id}/completion.json` (reopen) — `server.py::complete_todo`/`uncomplete_todo`, `mcp_tools.py::_tool_close_ticket`.

### 4.6 Reorder (position) — only when order drifts

Each manifest item stores its desired `position` (1-based). Reposition only the items whose desired position differs from BC's observed order, to minimize calls. **Three distinct reposition routes** (all verified against bc3-api; all `position` is integer ≥ 1):

```
# Reorder a todo within its list/group (optionally move it across parents):
PUT https://3.basecampapi.com/{account}/buckets/{bucket}/todos/{todo_bc_id}/position.json
{ "position": <n>, "parent_id": <group_or_list_bc_id> }   // parent_id optional; include to MOVE a todo into another group/list

# Reorder a group within its todolist (note the todolists/groups/{groupId} shape):
PUT https://3.basecampapi.com/{account}/buckets/{bucket}/todolists/groups/{group_bc_id}/position.json
{ "position": <n> }

# Reorder a todolist within the todoset:
PUT https://3.basecampapi.com/{account}/buckets/{bucket}/todosets/{todoset}/todolists/{list_bc_id}/position.json
{ "position": <n> }
```

`parent_id` on the **todo** reposition is the supported way to *move a todo between groups/lists* without delete+recreate (preserves comments + completion) — prefer it over recreate when a plan node's parent changes.

### 4.7 Retire a removed item — archive (or trash)

When a node exists in the manifest but no longer in the plan, retire its BC object instead of deleting (keeps the audit trail; trashed items auto-purge after 30 days in BC). Todos, groups, and todolists are all "recordings", so one route family covers all three:

```
# Archive (recommended default — recoverable, hidden from active views):
PUT https://3.basecampapi.com/{account}/buckets/{bucket}/recordings/{bc_id}/status/archived.json   → 204

# Trash (stronger — sends to Trash, auto-deletes ~30 days):
PUT https://3.basecampapi.com/{account}/buckets/{bucket}/recordings/{bc_id}/status/trashed.json    → 204

# Un-archive / restore if it reappears in the plan:
PUT https://3.basecampapi.com/{account}/buckets/{bucket}/recordings/{bc_id}/status/active.json      → 204
```

bc3-api `recordings`: status routes take **no body**, return **204**. Both the project-scoped form above and the newer flat form (`…/recordings/{id}/status/archived.json` without `/buckets/{bucket}`) are supported; use the bucket-scoped form for consistency with the rest of the reconciler. After retiring, mark the manifest item `retired: true` (keep the row so a re-add can `active.json`-restore the same `bc_id`).

---

## 5. First-run dedupe-by-breadcrumb (no manifest yet)

A first reconcile against an existing/partially-built bucket, or a first run that was interrupted before the manifest was fully written, must **not double-create**. Before creating any node whose plan id is absent from (or has no `bc_id` in) the manifest, do a **breadcrumb match by title within parent**:

1. **List todolists** in the bucket (paginated — MUST page, lists append to the end):
   ```
   GET https://3.basecampapi.com/{account}/buckets/{bucket}/todosets/{todoset}/todolists.json?page=N
   ```
   Match `initiative.title` (normalized) against existing `todolist.name`. Hit → adopt `todolist.id` into the manifest; miss → create (Step 4.1).
2. **List groups** under the matched/created list (paginated):
   ```
   GET https://3.basecampapi.com/{account}/buckets/{bucket}/todolists/{list_bc_id}/groups.json?page=N
   ```
   Match `feature.title` → adopt group id or create (Step 4.2).
3. **List todos** under the matched/created group (paginated; include completed so a completed-in-BC item isn't recreated):
   ```
   GET …/buckets/{bucket}/todolists/{group_bc_id}/todos.json?page=N
   GET …/buckets/{bucket}/todolists/{group_bc_id}/todos.json?completed=true&page=N
   ```
   Match `todo.title` against `content`/`title` → adopt todo id or create (Step 4.3).

Reuse for the walk: `ops/sync.py::_walk_project_todos` already does **exactly this traversal** (dock → todoset → paginated todolists → paginated groups → active + `completed=true` todos via `_collect`), and `tools/bc_mcp/server.py::list_todolists`/`list_todos` expose the same reads (`list_todos` even handles `include_completed`/`include_archived`). The dedupe scan should call these patterns rather than re-implement pagination. `server.py::_slim_todo` is the shape to match titles on (`title or content`).

**Matching rule:** normalize (trim + casefold + collapse whitespace) and match title within the *correct parent only* (a title is unique within its list/group, not globally). On an ambiguous double-match, prefer the lowest-position item and log the collision — do not create.

Once breadcrumb adoption completes, the manifest is fully populated. **Every subsequent run is O(manifest):** iterate plan nodes, compare `contentHash`, and only touch BC for changed/new/removed nodes — **no breadcrumb scan, no full read-back.** The first run pays the scan cost; steady state does not.

---

## 6. Reconcile algorithm (pseudocode)

```
load plan = output/{slug}/project-plan.json
load manifest = output/{slug}/bc_manifest.json  (or {} on first run)

ensure manifest.bucket, manifest.todoset      # Step 4.0 (discover via dock)

if manifest has no items:                      # first run / interrupted
    breadcrumb_adopt(plan, manifest)           # Step 5 — paginated read-back, title match

for node in plan.depth_first():                # initiatives → features → todos
    desired = normalize(node)                  # title, description, due_on(from initiative.startDate), assignee_ids
    h = hash(desired)
    m = manifest.items.get(node.id)
    if m is None or m.bc_id is None:
        bc_id = create(node)                    # 4.1 / 4.2 / 4.3
        manifest.items[node.id] = {bc_id, h, position, startDate?}
    elif m.contentHash != h:
        update_in_place(m.bc_id, desired)       # 4.4 (PUT, full field set)
        m.contentHash = h
    # else: unchanged → skip, NO BC call

reorder_drifted(plan, manifest)                 # 4.6, only changed positions

for node_id, m in manifest.items:               # removals
    if node_id not in plan and not m.retired:
        archive(m.bc_id)                         # 4.7
        m.retired = True

manifest.last_reconciled_at = now()
atomic_write(manifest)                           # incremental writes after each create are safer
```

Write the manifest **incrementally** (after each successful create) so a crash mid-run never loses a just-created `bc_id` and the next run resumes via the manifest (not a re-scan).

---

## 7. Endpoint summary (all verified against bc3-api, 2026-06-24)

| # | Action | Method + path (`…` = `https://3.basecampapi.com/{account}`) | Body | Resp | Repo reuse |
|---|---|---|---|---|---|
| 0 | Discover todoset | `GET …/projects/{bucket}.json` → `dock[todoset].id`/`.url` | – | 200 | `_tool_list_project_todolists`, `_walk_project_todos` |
| 1 | Create initiative (list) | `POST …/buckets/{bucket}/todosets/{todoset}/todolists.json` | `{name, description}` | 201 | `_tool_create_todolist` |
| 2 | Create feature (group) | `POST …/buckets/{bucket}/todolists/{list}/groups.json` | `{name, color?}` | 201 | (new) — groups read by `_walk_project_todos` |
| 3 | Create todo | `POST …/buckets/{bucket}/todolists/{group_or_list}/todos.json` | `{content, description, assignee_ids, due_on, starts_on}` | 201 | `server.py::create_todo`, `_tool_create_ticket` |
| 4 | Update todo in place | `PUT …/buckets/{bucket}/todos/{id}.json` | `{content, description, assignee_ids, due_on}` (full set) | 200 | `_tool_recategorize_session` (PUT /todos/{id}) |
| 5a | Reposition todo (+move) | `PUT …/buckets/{bucket}/todos/{id}/position.json` | `{position, parent_id?}` | 200 | (new) |
| 5b | Reposition group | `PUT …/buckets/{bucket}/todolists/groups/{group}/position.json` | `{position}` | 200 | (new) |
| 5c | Reposition list | `PUT …/buckets/{bucket}/todosets/{todoset}/todolists/{list}/position.json` | `{position}` | 200 | (new) |
| 6a | Archive (retire) | `PUT …/buckets/{bucket}/recordings/{id}/status/archived.json` | – | 204 | (new) |
| 6b | Trash | `PUT …/buckets/{bucket}/recordings/{id}/status/trashed.json` | – | 204 | (new) |
| 6c | Restore | `PUT …/buckets/{bucket}/recordings/{id}/status/active.json` | – | 204 | (new) |
| 7 | Read-back (dedupe) | `GET …/buckets/{bucket}/todosets/{todoset}/todolists.json?page=N`; `GET …/buckets/{bucket}/todolists/{id}/groups.json?page=N`; `GET …/buckets/{bucket}/todolists/{id}/todos.json[?completed=true]&page=N` | – | 200 | `_walk_project_todos`, `server.py::list_todolists`/`list_todos`, `api.paginated_get` |
| – | Rate-limit / 401 | handled transparently by `_bc_request` / `tools/bc_mcp/api.py::_request` | – | – | reuse the wrappers; never call `urllib` directly |

**Gotchas captured for implementers:**
- A **group is addressed as a todolist** for the todos endpoint (`/todolists/{group_id}/todos.json`) — but as `todolists/groups/{group_id}` for its own update/reposition. Don't mix the two shapes.
- BC **paginates lists by appending new items to the end** — every read-back (todolists, groups, todos) MUST page (the 2026-06-18 stale-list root cause; see `_walk_project_todos` comments).
- A todo under a group is **not** returned by the parent list's `/todos.json` — always read/write leaves at the group id.
- `PUT /todos/{id}.json` **replaces** passed fields and **requires `content`** — always send the complete desired field set or you'll blank descriptions/assignees.
- Prefer `position.json` with `parent_id` to **move** a todo across groups; never delete+recreate (loses comments + completion).

---

## 8. Verification (Layer-4)

Per `CLAUDE.md`, ship tests with the reconciler (Claude designs; tools run):
- **Unit (mocked BC):** hash-gate skips unchanged nodes (zero calls); create→manifest population; PUT sends full field set; removal → `archived.json`; reorder only fires on position drift.
- **Idempotency:** run reconcile twice on the same plan → second run makes **zero write calls** (asserts O(manifest), no breadcrumb scan).
- **Interrupted first run:** populate BC partially, wipe the manifest, re-run → breadcrumb match adopts existing items (asserts **no doubles**), including a `completed=true` match.
- **Pagination:** a >1-page list/group set is fully walked (regression guard mirroring `test_ops_sync.py::test_paginates_todolists_so_new_lists_on_page_two_are_walked`).
- Integration tests run only against a **dev bucket** behind an explicit opt-in flag; never production; workers never send real comms during tests.
