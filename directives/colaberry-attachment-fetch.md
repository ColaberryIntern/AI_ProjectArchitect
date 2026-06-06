# Directive: `colaberry_attachment_fetch` MCP tool

## Purpose

Provide a single MCP tool that downloads a file attachment from Gmail, Basecamp,
or Drive, stages it in the operator's Google Drive under a known path, and
returns a Drive reference (file id + URL + metadata) — never raw bytes. This
lets a Claude Code session use the already-available `claude.ai Google Drive`
connector to read the file content without paying MCP transport cost for the
attachment bytes, and gives Colaberry an audit trail of every inbound file the
operator's AI handled.

Operator-scoped: the tool acts on behalf of whichever Colaberry operator owns
the calling bearer token (`cmcp_*`). The operator's own Gmail / Drive
credentials are used — never a shared service account.

## Inputs

Tool call `colaberry_attachment_fetch(args)`:

- `source` (required): `"gmail"` | `"basecamp"` | `"drive"`
- Gmail args (required when `source="gmail"`):
  - `message_id`: Gmail thread/message id (e.g. `19e99910dab2b17e`)
  - `attachment_id`: Gmail attachment id from the message's `payload.parts`
- Basecamp args (required when `source="basecamp"`):
  - `project_id`: BC bucket id (e.g. `7463955`)
  - `recording_id`: BC recording id (the comment / todo with the attachment)
  - `attachment_sgid`: BC blob sgid
- Drive args (required when `source="drive"`):
  - `drive_file_id`: Drive file id — passthrough mode; the tool re-resolves
    metadata + returns the same ref without re-uploading (must be a file
    the advisor's Drive client created; arbitrary user-owned files are
    rejected by the `drive.file` scope)
- Common (optional):
  - `destination_subpath`: override the default `<YYYY-MM>` folder

Bearer-token routing (handled by the MCP server route, not by the tool itself):

- `Authorization: Bearer cmcp_...` → resolves to `tenancy.User`
- `X-MCP-Hostname` → recorded per-device (already wired)

## Outputs

JSON object with these keys:

```json
{
  "ok": true,
  "drive_file_id": "1AbC...",
  "drive_url": "https://drive.google.com/file/d/1AbC.../view",
  "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "size_bytes": 124680,
  "source": "gmail",
  "source_message_id": "19e99910dab2b17e",
  "sender": "jackie@chalkstrategies.com",
  "filename": "February Commission 2026.xlsx",
  "saved_at": "2026-06-06T12:34:56Z",
  "reused_existing": false
}
```

Error case:

```json
{
  "ok": false,
  "error": "<short machine-readable code>: <human description>",
  "source": "...",
  "source_id_echo": {...}
}
```

Drive path used: `Drive:/Colaberry Inbound/<source>/<sender_or_project_slug>/<YYYY-MM>/<original_filename>`

## Steps

1. Validate `args` against the source's required fields. Reject with
   `error: "missing_required: <field>"` if any are absent. Echo back the args
   so the caller's tool result is self-describing.
2. Look up the operator's Google OAuth refresh token via
   `google_oauth_token.get_refresh_token_for_operator(user)`. The store is the
   advisor's vault, key `(user.user_id, "google_oauth_refresh")`. If missing,
   return `error: "no_google_oauth_grant: operator needs to run bootstrap_google_oauth.py"`.
3. Exchange the refresh token for a short-lived access token via Google's
   OAuth `/token` endpoint (cached in-process for ~50 minutes; never written to
   disk; never logged).
4. Compute the idempotency key:
   - `gmail` → `f"gmail:{message_id}:{attachment_id}"`
   - `basecamp` → `f"basecamp:{project_id}:{recording_id}:{attachment_sgid}"`
   - `drive` → `f"drive:{drive_file_id}"`
   Look it up in `output/library/_attachment_index/<operator_email>.json`. On
   hit AND the Drive file id still exists (verified via Drive `files.get`),
   return the existing ref with `reused_existing: true`. **Do not re-download.**
5. Run the source adapter:
   - `gmail_attachment.fetch(message_id, attachment_id, access_token)` →
     `(filename, mime_type, bytes, sender)`. Sender comes from the parent
     message's `From:` header.
   - `basecamp_attachment.fetch(project_id, recording_id, sgid, bc_token)` →
     `(filename, mime_type, bytes, sender_project_name)`. BC token comes from
     the existing `_bc_token(user)` chain.
   - `drive_attachment.fetch(drive_file_id, access_token)` → `(filename,
     mime_type, metadata)` — no bytes, this is a metadata-only passthrough.
6. For non-Drive sources: upload bytes to Drive via `drive_staging.upload(
   bytes, filename, mime_type, sub_path, access_token)`. `sub_path` is the
   folder hierarchy computed in step 4. Folders are created idempotently.
7. Write the idempotency entry to `output/library/_attachment_index/<email>.json`
   linking the key → returned reference.
8. Return the result JSON.

## Edge Cases

- **Refresh token rotated/revoked by the operator (Google returns
  `invalid_grant`)** → return `error: "google_grant_invalid: operator needs to
  re-run bootstrap_google_oauth.py"`. Do NOT auto-retry — the only fix is
  human-mediated re-consent.
- **Access token expires mid-call** → the OAuth helper attempts one re-exchange
  on 401 from the API call. Subsequent 401 → propagate as
  `error: "google_api_unauthorized"`.
- **Gmail 404** (message gone, attachment id stale) → return
  `error: "gmail_attachment_not_found"`. Don't fall through to BC/Drive.
- **Gmail 429 (rate limit)** → backoff exponential 1s, 2s, 4s. Max 3 attempts.
  Then return `error: "gmail_rate_limited"`.
- **Basecamp blob URL changed format** (BC sometimes restructures URLs) →
  attempt the documented URL; on 404, log the attempt and return
  `error: "basecamp_blob_unreachable"`.
- **Drive quota exceeded** during upload → return
  `error: "drive_quota_exceeded"`. The operator owns the quota; surface the
  failure rather than retrying.
- **`source="drive"` but the file id is not owned by our app (i.e. not in our
  `drive.file` scope)** → return
  `error: "drive_file_not_accessible: drive.file scope limits us to files the
  advisor uploaded"`. Operator can re-stage manually if they want it in the
  index.
- **Two concurrent calls with the same idempotency key** (race) → second
  request sees the in-progress index entry (sentinel `{"status": "uploading"}`
  with a 60s timestamp) and either waits or returns `error:
  "fetch_in_progress"`. Decision: return the error; caller can retry. Avoid
  duplicate Drive uploads.
- **File over 25MB** → Gmail supports up to 25MB attachments inline. BC has its
  own limit. Drive supports much larger. Document the per-source cap in the
  response when applicable; don't silently truncate.
- **PII in filenames** (e.g. SSN in the filename a tax-doc sender used) → the
  filename is preserved verbatim. The audit log records the filename. Drive
  ACL is the operator's own — same surface the original sender already had.

## Safety Constraints

- **Per-operator credentials only.** Never use a shared/service-account Google
  token. Per-user vault lookup keyed by `user.user_id` is the only path.
- **No raw token logs.** `refresh_token`, `access_token`, `client_secret`
  must be redacted in any log line (substitute `[REDACTED]`). Same rule applies
  to the audit JSONL.
- **No raw bytes returned through MCP.** The Drive staging step is non-optional
  except for `source="drive"` passthrough.
- **Least-privilege Drive scope.** App uses `drive.file` only — restricts
  us to files our app created. We cannot read or modify arbitrary user
  Drive content. This is an explicit security choice.
- **Broader Gmail scope.** App uses `gmail.modify` (read + compose + send +
  label, but NOT permanent delete). Chosen over `gmail.readonly` so future
  email-sending MCP tools (compose draft, send reply, archive, etc.) don't
  require operators to re-bootstrap. The attachment-fetch tool itself only
  uses the read portion. The "no permanent delete" boundary is preserved.
- **Idempotent on retry.** Calling twice with the same key MUST return the
  same Drive ref without re-uploading.
- **Failure-first.** Explicit 15s HTTP timeout per outbound call. Capped 3
  retries with exponential backoff on transient 5xx + 429 only. No silent
  swallows — every error path returns a machine-readable `error` code.

## Verification

- Unit tests (mocked HTTP) for each source adapter — happy, 401, 404, 429,
  malformed payload. Live at `tests/execution/products/library/test_attachment_fetch.py`.
- Idempotency test: two calls with the same args → one Drive upload, two
  identical refs returned.
- Auth boundary test: call with a `cmcp_*` token whose operator has no vault
  entry → returns `no_google_oauth_grant` cleanly, no crash, no leakage.
- Integration test (gated by `RUN_GOOGLE_INTEGRATION=1`): pull a known small
  attachment from `ali@colaberry.com`'s mailbox; verify it lands at the
  expected Drive path; second call returns `reused_existing: true`.
- Redaction test: assert no log line in the test run contains substrings of
  the test refresh token or access token.

## Bootstrap

`scripts/bootstrap_google_oauth.py` — one-time interactive flow. Run as the
operator on their own machine:

1. Prompts for the operator's Google account (defaults to bearer-token owner)
2. Spins a temporary localhost callback server
3. Opens browser to Google OAuth consent for `gmail.modify` + `drive.file`
4. Exchanges code for refresh token
5. Writes refresh token to advisor's vault via `vault.store_secret(
   user.user_id, "google_oauth_refresh", refresh_token, ttl_days=180)`
6. Prints "Bootstrap complete" + the next-rotation timestamp

The script does NOT print or log the refresh token. To re-bootstrap, run again
(the new entry overwrites the old).
