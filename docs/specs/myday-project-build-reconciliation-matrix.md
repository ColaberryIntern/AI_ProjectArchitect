# [My Day] Project-Build Reconciliation Matrix

**Status:** Spec / design — implementation-ready.
**Scope:** The deterministic state-reconciliation rules for the "Create a new project from My Day" feature. This is the authoritative decision table the reconciler MUST implement. Every (input state) → (action) cell is one deterministic action; there is no narrative judgement at runtime.

---

## 1. Inputs and vocabulary

### 1.1 Desired state — `project-plan.json` (schema `cb-project-plan/v1`)

A tree of plan nodes. Hierarchy:

```
initiatives[]            (status, id, startDate-source ...)
  └─ lists[]             (status, id ...)
       └─ todos[]        (status, id, assignee, dueOffsetDays ...)
  designs[]              (per node, hashed)
  peopleMap{}            (alias → BC person id)
```

Basecamp mapping (fixed):

| Plan node    | Basecamp object        | Assignable? | Due-dateable? | Orderable? | Completable in BC? |
|--------------|------------------------|-------------|---------------|------------|--------------------|
| `initiative` | BC **todolist**        | no          | no            | yes (list of lists) | no |
| `list`       | BC **todolist group**  | no          | no            | yes (group order within the list) | no |
| `todo`       | BC **todo**            | yes (required) | yes (required) | yes (todo order within group) | **yes** |

Groups and todolists carry no assignee and no due date. Only todos do, and for todos **both are mandatory** (see §4).

### 1.2 Plan node `status` (authored in the plan)

| status     | Meaning                                              | Reconciler stance |
|------------|-----------------------------------------------------|-------------------|
| `active`   | Intended to exist in BC.                             | Create / keep in sync. |
| `proposed` | Discovered mid-build; awaits human promotion to `active`. | **SKIP entirely** — never created, never touched, never archived. |
| `retired`  | Soft-delete. Author wants it gone from active BC.   | Archive/trash the BC item; **KEEP the manifest entry** for audit. |

### 1.3 Identity and change detection

- **`id`** — stable identity. Pure function of document position in the plan tree. This is the join key into the manifest. It does **not** change when content changes; it only changes if the node physically moves to a new position (which the reconciler treats as a new node + a now-orphaned old node — see §6.5).
- **`contentHash`** — `sha256` over the canonical serialization of `{title, charter|acceptance, phase, dueOffsetDays, assignee, order, status, designs, deps}`. Answers exactly one question: *"did this node's authored content change since last sync?"* Ordering (`order`) is included, so a pure re-sort flips the hash; the reconciler still distinguishes "only order changed" from "fields changed" by field-level diff (see §5.2), because a reorder is a cheaper BC operation than a full update.

### 1.4 The manifest — `output/{slug}/bc_manifest.json`

The reconciler's memory of the last successful sync. Per plan `id` it stores:

```jsonc
{
  "slug": "acme-onboarding-revamp",
  "project": { "bc_project_id": 41234567, "todoset_id": 9988776 },
  "initiativeStartDates": { "init-1": "2026-07-01", "init-2": "2026-07-15" },
  "nodes": {
    "init-1":            { "level": "initiative", "bc": { "todolist_id": 55501 }, "contentHash": "ab12…", "status": "active", "lastSynced": "2026-06-24T13:02:11Z" },
    "init-1/list-1":     { "level": "list",       "bc": { "group_id": 55510 },    "contentHash": "cd34…", "status": "active", "lastSynced": "…" },
    "init-1/list-1/td-1":{ "level": "todo",       "bc": { "todo_id": 55520 },     "contentHash": "ef56…", "status": "active", "dueOn": "2026-07-08", "assignee": 17454835, "bcCompleted": false, "lastSynced": "…" }
  }
}
```

Notes:
- `initiativeStartDates[initiativeId]` is written **once**, when the initiative is first created, and is the anchor for resolving every descendant todo's `dueOffsetDays` → absolute `due_on` (§4.2). It is never silently recomputed.
- `bcCompleted` is a cached read of the last-observed BC completion state for todos. It is the guard that prevents resurrection (§6.3).
- A manifest entry is the **only** durable record that ties a plan `id` to a BC object id.

### 1.5 First-run breadcrumb match (no manifest, or `id` missing from manifest)

When there is no manifest entry for an `id`, the reconciler does **not** immediately create. It first tries a **breadcrumb match** to recover from a lost/partial manifest:

- Each BC object the reconciler creates is stamped with a breadcrumb: a hidden marker `<!-- cb:id=<planId> -->` appended to the BC object's description/notes (todolists, groups) or todo notes (todos). Todos additionally carry the breadcrumb in the todo's notes field.
- Breadcrumb match = "is there exactly one live BC object at the correct parent whose breadcrumb equals this plan `id`?"
- **Exactly one** → re-link (adopt the BC id into the manifest), then proceed as if the manifest had existed.
- **Zero** → genuine create.
- **Two or more** → **CONFLICT → flag** (ambiguous adoption; never guess). Human resolves.

Breadcrumb match is also the recovery path for an **interrupted run** where the BC object was created but the manifest write never landed (§6.2).

---

## 2. How to read the matrix

For each plan node the reconciler computes three facts, in order:

1. **`planStatus`** — `active` | `proposed` | `retired`.
2. **`manifest`** — `none` (no entry for this `id`) | `present`.
3. **`bc`** — does the BC object resolve?
   - If `manifest=present`: look up by stored BC id → `exists` | `missing` (404/trashed).
   - If `manifest=none`: run breadcrumb match → `adopt-1` (exactly one) | `adopt-0` (none) | `adopt-many` (≥2).
4. When `manifest=present` and `bc=exists`, also compute **`hash`** — `same` | `changed` (and, if changed, the field-level diff to choose update vs reorder, §5.2).

Each row below is fully determined by these facts. Cells are actions, defined in §3.

---

## 3. Action vocabulary (one deterministic operation each)

| Action | Definition |
|--------|------------|
| **create** | POST a new BC object at the correct parent, stamp breadcrumb, write a fresh manifest entry (incl. `contentHash`, and for initiatives `initiativeStartDates`, for todos `dueOn`/`assignee`/`bcCompleted:false`). Parents-before-children ordering (§7). |
| **update-in-place** | PATCH the existing BC object's changed fields, then overwrite manifest `contentHash` + `lastSynced`. Completion is never touched (§6.3). |
| **reorder-only** | Issue only the BC position/move call (no field PATCH), then overwrite manifest `contentHash`. Used when the field-diff shows `order` is the *only* changed field. |
| **skip** | Do nothing in BC and do nothing to the manifest. (proposed nodes; and unchanged nodes.) |
| **archive-or-trash** | Move the BC object to archived (todolists/groups) or trashed (todos) per BC capability; set manifest `status:retired`, keep the entry. Children-before-parents ordering (§7). |
| **re-link** | Adopt a breadcrumb-matched BC id into the manifest (write/repair the entry), then re-evaluate the row as `manifest=present, bc=exists` in the same pass. |
| **conflict → flag** | Emit a `ReconcileConflict` record (id, level, reason, candidate BC ids), make **no** BC mutation and **no** manifest mutation for this node, and continue with siblings. Surfaced to the human in the run report. |

---

## 4. Todo-specific invariants (enforced on every create/update of a todo)

### 4.1 Assignee is mandatory
Every todo MUST end with an assignee. Resolution order:
1. `peopleMap[node.assignee]` → BC person id.
2. If the node has no `assignee` (human todo), assignee = **the project creator** (the My Day user who launched the build).
3. If `peopleMap` has no entry for a named alias → **CONFLICT → flag** (do not silently fall back to creator for a *named* assignee; an unresolvable named person is an authoring error).

### 4.2 Due date is mandatory
Every todo MUST end with a `due_on`. Resolved at write time:
- `due_on = initiativeStartDates[ancestorInitiativeId] + node.dueOffsetDays` (calendar days).
- `initiativeStartDates` is the value stored in the manifest when the initiative was created — it is **not** recomputed each run, so a todo's absolute due date is stable across runs even if "today" moves.
- If the ancestor initiative has no stored `startDate` yet (e.g. initiative is being created in the same pass), the start date is resolved/created first (parents-before-children, §7) and persisted before any descendant todo's due date is computed.
- `dueOffsetDays` is part of `contentHash`; a change to it on an existing todo is an **update-in-place** (re-resolve `due_on`, PATCH, store new `dueOn`).

---

## 5. The reconciliation matrix

Legend: `—` = not applicable / cannot occur. Where a level differs, it is called out in the cell.

### 5.1 Master matrix — by status × manifest × BC resolution

| # | planStatus | manifest | bc resolution | hash | **Initiative action** | **List (group) action** | **Todo action** |
|---|-----------|----------|---------------|------|------------------------|--------------------------|-----------------|
| 1 | proposed  | any | any | any | **skip** | **skip** | **skip** |
| 2 | active | none | adopt-0 (no breadcrumb) | — | **create** | **create** | **create** (resolve assignee §4.1 + due_on §4.2) |
| 3 | active | none | adopt-1 (one breadcrumb) | — | **re-link** → then row 6/7/8 | **re-link** → then row 6/7/8 | **re-link** → then row 6/7/8 |
| 4 | active | none | adopt-many (≥2 breadcrumbs) | — | **conflict → flag** | **conflict → flag** | **conflict → flag** |
| 5 | active | present | missing (BC id 404/trashed) | — | **conflict → flag** (see §6.4) | **conflict → flag** | **conflict → flag** |
| 6 | active | present | exists | same | **skip** | **skip** | **skip** |
| 7 | active | present | exists | changed (fields ≠ order-only) | **update-in-place** | **update-in-place** (title/notes only — no assignee/due) | **update-in-place** (re-resolve due_on if dueOffsetDays changed; never un-complete §6.3) |
| 8 | active | present | exists | changed (order is the *only* diff) | **reorder-only** | **reorder-only** | **reorder-only** |
| 9 | retired | none | adopt-0 | — | **skip** then **create-orphan-manifest-entry** as `retired` (nothing in BC to archive; record the audit row) | same | same |
| 10 | retired | none | adopt-1 | — | **re-link** → then row 12 | **re-link** → then row 12 | **re-link** → then row 12 |
| 11 | retired | none | adopt-many | — | **conflict → flag** | **conflict → flag** | **conflict → flag** |
| 12 | retired | present | exists | any | **archive-or-trash** (keep entry, set `status:retired`) | **archive-or-trash** | **archive-or-trash**; if `bcCompleted=true` see row 14 |
| 13 | retired | present | missing (already gone from BC) | — | **skip** (set/confirm manifest `status:retired`; nothing to delete) | **skip** (same) | **skip** (same) |
| 14 | retired | present | exists, human already **completed** it in BC | — | — (initiatives/lists can't be completed) | — | **leave the completion; just mark archived** — trash/archive the todo, set manifest `status:retired`, preserve `bcCompleted:true`. Do NOT reopen, do NOT delete the completion record. |

Row 9 nuance: a node authored directly as `retired` with no manifest and no BC object never existed in BC. There is nothing to archive. The reconciler still writes a `retired` manifest entry (with `bc: null`) so the audit trail records that the author retired a never-built node. Implement as a single "record-retired-orphan" write; no BC call.

### 5.2 Field-diff sub-rule for row 7 vs row 8 (the only place hash `changed` forks)

When `hash=changed` on an existing node, compute the per-field diff of the hashed field set:

```
changedFields = fieldsThatDiffer({title, charter|acceptance, phase, dueOffsetDays, assignee, order, status, designs, deps})
```

- `changedFields == {order}` → **reorder-only** (row 8).
- `changedFields ⊇ anything other than order` → **update-in-place** (row 7). If `order` is also in the set, the reorder is folded into the same update pass (one PATCH for fields + one move call), still recorded as a single row-7 action.
- For **list (group)** and **initiative (todolist)** rows: `assignee` and `dueOffsetDays` are not part of those levels' authored content, so they can never appear in `changedFields`; an update touches title/notes/breadcrumb only.

---

## 6. Named edge cases (each maps to exactly one matrix row)

### 6.1 First run, no manifest at all
The manifest file does not exist. For every node: `manifest=none`. Each node runs breadcrumb match. On a true first run there are no breadcrumbs, so every `active` node hits **row 2 (create)** and every `retired` node hits **row 9**. The manifest is created incrementally as nodes are created. Parents before children (§7) guarantees a created initiative's `todolist_id` and `startDate` exist before its lists/todos are created.

### 6.2 Interrupted run — manifest partially written
A previous run created some BC objects but crashed before persisting all manifest entries (or before the atomic manifest swap). On the next run:
- Nodes whose entry **was** persisted → `manifest=present`, resolve normally (rows 5–8 / 12–14).
- Nodes whose BC object was created but entry **was not** persisted → `manifest=none`, breadcrumb match finds the orphaned-but-stamped BC object → **row 3 / row 10 (re-link)**, adopting the existing BC id instead of creating a duplicate.
- Guarantee: because every create stamps a breadcrumb *before* (or atomically with) returning, re-link recovers idempotently. **No duplicate BC objects are ever created** by a re-run. This is the core idempotency property.

### 6.3 Node completed in BC, then its upstream content changes
A human marked the BC todo complete. Upstream, the author edits the todo's title/charter/due → `hash=changed` → **row 7 (update-in-place)**. The reconciler PATCHes the changed fields **but never sends a `completed` transition**. Completion state is owned by BC/humans, not the plan. Concretely: the update payload excludes any completion field; the BC todo stays complete; manifest `contentHash` is updated, `bcCompleted` stays `true`. *Never resurrect a completion by un-completing.*

### 6.4 `active` node whose manifest BC id is missing (row 5)
The manifest says a BC object should exist but the id 404s (a human deleted/trashed it out of band). The reconciler does **not** silently recreate (that would fight a deliberate human deletion) and does **not** silently drop it. It emits **conflict → flag** with reason `manifest-bc-missing` and the human chooses: recreate (clear the manifest entry → next run hits row 2) or retire (author flips status to `retired` → next run hits row 13). This is the one place "BC missing but plan-active" is intentionally human-gated.

### 6.5 Node moved in the plan tree (id changed by position)
Because `id` is a pure function of position, moving a node yields a *new* id at the new position and leaves the *old* id with no plan node. Handling:
- New id → `manifest=none` → breadcrumb match. The moved BC object still carries the old breadcrumb, so unless re-stamped it won't match the new id → **row 2 (create)** at the new location.
- Old id → present in manifest but no longer in the plan → treated as an **orphan** (see §6.6).

Net effect of a move = create-at-new-position + orphan-old. This is intentional and safe (no data loss; old item is handled by the orphan rule, not auto-deleted). If structural moves must preserve identity, that is a separate (out-of-scope) "stable explicit id" enhancement, not a runtime guess.

### 6.6 Orphan — manifest entry whose plan `id` no longer exists
A manifest entry has no corresponding node in the current plan (deleted from the plan, or moved per §6.5). The reconciler **never auto-trashes an orphan** (deletion is destructive and must be deliberate). It emits **conflict → flag** with reason `orphaned-manifest-entry`. Human resolves by either re-adding the node to the plan or explicitly retiring it. (Rationale: matches CLAUDE.md intern-safety — no destructive action without explicit intent.)

---

## 7. Ordering guarantees (topological)

Within a single reconcile pass:

- **Creates and updates: parents before children.** An initiative (todolist) is created before its lists (groups); a list is created before its todos. Guarantees parent BC ids and `initiativeStartDates` exist before children reference them (§4.2). Sibling order is applied after the sibling set exists (reorder calls last within a level).
- **Archives/trashes (retire): children before parents.** Todos are trashed before their group; groups archived before their todolist. Avoids archiving a parent that still holds active children and keeps BC's tree consistent at every intermediate step.
- **Conflicts never block siblings.** A `conflict → flag` on one node halts only that node (and, for a create, its would-be descendants, which then also flag with reason `parent-unresolved`); all unrelated branches continue. The run completes and returns a report; it does not abort.

Pass shape:

```
1. Resolve project + todoset (create project if first run).
2. DESCEND (parents→children): for each active node, evaluate matrix rows 1–11; create/re-link/update/reorder/skip/flag.
3. ASCEND  (children→parents): for each retired node, evaluate rows 12–14; archive-or-trash.
4. Orphan sweep: any manifest id absent from the plan → flag (§6.6). No auto-delete.
5. Atomic manifest write (temp file + rename) — single durable swap so a crash leaves either the old or the new manifest, never a torn one.
6. Emit run report: counts per action + every ReconcileConflict.
```

---

## 8. Idempotency contract (acceptance criteria)

1. Running the reconciler twice on an unchanged plan + intact manifest produces **zero** BC mutations (all rows hit 1/6/13).
2. Running after any interruption (§6.2) produces **zero** duplicate BC objects (breadcrumb re-link).
3. No reconcile run ever un-completes a BC todo (§6.3) or auto-deletes an orphan (§6.6) or recreates a human-deleted active item without a human gate (§6.4).
4. Every created/updated todo has both an assignee and a `due_on` (§4) or the run flags it; a todo is never written to BC missing either.
5. `proposed` nodes leave **no** trace in BC across any number of runs (row 1).
6. Retired nodes are archived but their manifest entry survives for audit (rows 12–14); a human-completed retired todo keeps its completion (row 14).

---

## 9. Conflict report schema

Each `conflict → flag` emits:

```jsonc
{
  "planId": "init-1/list-1/td-7",
  "level": "todo",
  "reason": "manifest-bc-missing | adopt-many | orphaned-manifest-entry | unresolvable-assignee | parent-unresolved",
  "candidateBcIds": [55520, 55988],     // for adopt-many
  "detail": "human-readable one-liner",
  "noMutation": true                     // always true: a flag never mutates BC or manifest
}
```

The run is **not** failed by conflicts; it returns `status: completed-with-conflicts` and the report. Conflicts are the human-gated queue, consistent with the approval-gated, intern-safe doctrine in `CLAUDE.md`.

---

## 10. Open decisions (flagged for review, not runtime)

- **Breadcrumb storage on todos:** notes-field marker vs a BC custom field — notes is universally available; chosen here. Confirm it survives BC's notes sanitization.
- **Stable explicit ids (§6.5):** whether to add an optional author-supplied `stableId` so structural moves preserve BC identity instead of create+orphan. Out of scope for v1; documented so the position-derived `id` behavior is not mistaken for a bug.
- **Orphan auto-retire policy:** currently always flag (§6.6). A future opt-in "auto-retire orphans" toggle could move row to archive-or-trash — must remain opt-in per intern-safety rules.
