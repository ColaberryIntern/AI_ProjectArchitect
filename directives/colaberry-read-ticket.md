# Directive: `colaberry_read_ticket` MCP tool

## Purpose

Give a Claude Code session a **read** path into Basecamp. Until this tool
existed, the colaberry MCP server exposed only write/post tools
(`colaberry_create_ticket`, `colaberry_post_progress`, `colaberry_close_ticket`,
`colaberry_save_doc_to_bc`, …) plus `colaberry://` doctrine/identity/memory
resources — there was **no way to fetch a ticket's body or its comment
thread**. Operators hit this constantly: Claude would say "I can post to
Basecamp but can't read ticket content, please paste it here." This tool
removes that gap.

It returns a ticket's title, full description (raw HTML **and** a
stripped-text version), status, assignees, due date, the parent todolist,
and every comment with author + timestamp + content.

Operator-scoped: the tool reads on behalf of whichever Colaberry operator
owns the calling bearer token (`cmcp_*`), using the same per-user BC token
resolution (`_bc_token`) as every other BC tool.

## Inputs

Tool call `colaberry_read_ticket(args)`:

- `bc_project_id` (**required**, integer): Basecamp project / **bucket** id —
  the number after `/buckets/` in the ticket URL. **Not** the account id.
- `ticket_id` (**required**, integer): the BC todo id — the trailing number
  after `/todos/` in the URL. Alias `todo_id` accepted.
- `include_comments` (optional, boolean, default `true`): set `false` to
  return body-only and skip the comment thread.
- `max_comments` (optional, integer, default `100`, clamped 1–500): cap on
  comments returned. When the thread is longer, the extras are omitted and
  `comments_truncated` is set `true`.

> Pitfall: a Basecamp todo URL is
> `https://3.basecamp.com/<account>/buckets/<bc_project_id>/todos/<ticket_id>`.
> The account id (`3945211`) and the bucket id are **different numbers**.
> Passing the account id as `bc_project_id` used to yield an opaque BC 404.
> The BC tools (`read_ticket`, `post_progress`, `create_ticket`,
> `close_ticket`) now guard against this and return
> `error: "bc_project_id_is_account_id"` with remediation **before** any
> network call — see `_account_id_as_bucket_error` in `mcp_tools.py` and
> `tests/execution/products/test_mcp_bucket_guard.py`. Resolve the real
> bucket from the URL or via `colaberry_find_project`.

## Outputs

```json
{
  "ok": true,
  "bc_project_id": 47502609,
  "ticket": {
    "ticket_id": 9946498513,
    "title": "Draft weekly TWC registration status email",
    "description_html": "<div>…</div>",
    "description_text": "…",
    "completed": false,
    "status": "active",
    "assignees": ["Swati R", "Ali M"],
    "due_on": "2026-06-20",
    "creator": "Ali M",
    "created_at": "2026-06-15T10:00:00Z",
    "updated_at": "2026-06-17T09:00:00Z",
    "todolist": "TWC",
    "todolist_id": 555,
    "comments_count": 2,
    "url": "https://3.basecamp.com/3945211/buckets/47502609/todos/9946498513"
  },
  "comments": [
    {"comment_id": 1, "author": "Ali M", "created_at": "…",
     "content_html": "<div>…</div>", "content_text": "…", "url": "…"}
  ],
  "comments_returned": 2,
  "comments_truncated": false
}
```

### Error cases (always `ok: false`)

- `bc_project_id + ticket_id required` / `… must be integers` — arg validation.
- `ticket_not_found` (BC 404) — wrong bucket or ticket id. Carries a
  `remediation` string explaining the URL anatomy.
- `ticket_forbidden` (BC 401/403) — operator's BC grant doesn't cover this
  project; re-auth via `/profile/connect-basecamp` or ask an admin.
- `ticket_unreachable` — other transient/HTTP failure (`detail` has the raw
  message).
- Comment-thread fetch failures are **non-fatal**: the ticket body is still
  returned with `ok: true` and a `ticket.comments_error` note.

## How success is verified

Unit tests (mocked BC I/O, no network):
`tests/execution/products/test_mcp_read_ticket.py`

- happy path returns body + paginated comments, tags stripped from
  `*_text` fields, raw HTML preserved in `*_html`
- pagination walks until an empty page; `max_comments` truncates and flags
  `comments_truncated`
- `include_comments=false` and `comments_count=0` skip the thread fetch
- 404 → `ticket_not_found`, 403 → `ticket_forbidden`, 500 →
  `ticket_unreachable`
- comment-fetch error is non-fatal (body still returned)
- arg validation (missing/zero/non-integer ids; `todo_id` alias)
- `_html_to_text` block/entity/list handling
- tool is registered in `TOOL_BY_NAME` and reachable via `call_tool`

Run: `python -m pytest tests/execution/products/test_mcp_read_ticket.py -q`

Doctrine (`mcp_doctrine.py`, Session Protocol §10) instructs Claude to call
this tool whenever the user references an existing ticket, instead of asking
for copy-paste.

## Implementation

- Handler: `execution/products/library/mcp_tools.py` →
  `_tool_read_ticket` (+ `_html_to_text`), registered as
  `colaberry_read_ticket`.
- Reuses `_bc_request` (per-user token resolution + 429/503 retry) and
  `_bc_account()`.
- Read-only: issues only `GET` against `/buckets/{bid}/todos/{id}.json` and
  the todo's `comments_url` (paginated `?page=N`, capped at 20 pages).
