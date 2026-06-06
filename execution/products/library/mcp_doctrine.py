"""MCP resources exposed by the Colaberry server.

Each resource has a URI, mimeType, and a builder function that returns
text content. The MCP route serializes the content into the standard
{contents: [{uri, mimeType, text}]} envelope.

Resources fall into three categories:
  - colaberry://doctrine/*   - org + tenant policy + the session protocol
  - colaberry://memory/*     - the user's OPERATOR_MEMORY (cross-session)
  - colaberry://identity/*   - the user's BC project / todolist / etc.

All resources are read-only. Writes go through tools (e.g. colaberry_remember).

Per-user identity comes from the User record resolved from the MCP auth
token; each resource builder takes `user` as its only argument.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from . import mcp_tools
from . import operator_scaffold


@dataclass
class Resource:
    uri: str
    name: str
    description: str
    mime_type: str
    builder: Callable

    def to_listing(self) -> dict:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


# ── Builders ────────────────────────────────────────────────────────


_SESSION_PROTOCOL = """\
# Colaberry Session Protocol (mandatory)

You're running inside a Colaberry operator's Claude Code session. The full
operating doctrine is loaded via the MCP resources below; read them at the
start of every session. The summary you must internalize:

## 1. Mandatory ticket per substantive prompt (Op 2)

On every user prompt:

a. Call `colaberry_classify_prompt(text=<the prompt>)`. If kind == "readonly",
   answer the prompt and stop. No ticket needed.

b. If kind == "substantive":
   - Read the user's personal anchor via `colaberry_get_personal_anchor()`.
   - **Check the `valid` flag in the response.** If `valid: false`, the
     anchor is broken (stale ids, missing config, unreachable BC). Read
     `validation_error` + `validation_message` and STOP. Tell the user
     exactly what's wrong and what to do (the validation_message has the
     remediation copy). DO NOT call create_ticket against a known-broken
     anchor and DO NOT silently fall back to some other project -- that
     destroys the audit trail.
   - Also check the working directory for a `.colaberry.json` file. If it
     exists, the project's bc_project_id + default_list_id are in it; this
     is the project-context anchor.
   - If neither the personal anchor nor a project anchor matches an existing
     ticket the user referenced (URL or `BC#<id>`), derive a title via
     `colaberry_derive_ticket_title(text)`, ask the user to confirm or edit,
     then call `colaberry_create_ticket` to create the session anchor in
     personal. ALSO create-or-anchor against the project ticket if the work
     is project-scoped.

If `colaberry_create_ticket` returns `ok: false` with an error like
`anchor_not_set`, `anchor_stale_no_recoverable_list`, or
`create_ticket_failed_even_after_recovery`, STOP. Read the
`remediation` field and tell the user. Do not proceed with substantive
work until the anchor is established -- Op 2 is non-negotiable. If
the call succeeds with `self_healed: true`, mention the warning to the
user so they know their tenancy needs an admin update.

## 2. Faithful progress updates (Op 3)

As you work, post structured HTML progress cards via `colaberry_post_progress`
on EVERY anchor you established (personal + project, if applicable). Each
card starts with an idempotency marker comment `<!-- step:KIND:HASH -->`
so re-posting on retry is a no-op. KIND is one of: build_started, design_drafted,
review_requested, child_closed, blocker, pivot.

## 3. Auto-close on completion (Op 4)

When work is done:
  - Personal anchor: call `colaberry_close_ticket` with the confidence
    you'd give the work. >= 0.85 auto-closes. < 0.85 asks the user first.
  - Project anchor: NEVER auto-close. Always ask the user explicitly
    ("Should I mark BC#<id> complete?") and only close on explicit yes.

## 4. Operator memory (Op 5)

  - At session start: read `colaberry_get_memory()` and respect what's there.
  - When the user corrects you, call `colaberry_remember(scope=..., fact=...)`
    so future sessions inherit the lesson.
  - Style/preference corrections -> scope="style".
  - Tooling/process corrections -> scope="tooling".
  - Domain knowledge -> scope="domain".

## 5. The .colaberry.json convention

If the working directory contains a `.colaberry.json` file like:

    {
      "bc_project_id": 67890,
      "bc_project_name": "Enterprise Accelerator",
      "default_list_id": 11111,
      "team_review_required": true
    }

then treat that as the project-context anchor for THIS session. Dual-update:
post all progress to BOTH your personal anchor AND the project ticket(s).
The user doesn't need to repeat the project context every prompt.

If the file is missing, the session is "general" and only the personal
anchor is used until the user references a project explicitly.

## 6. Override flags

  - `--no-ticket <prompt>` at prompt start: skip ticket creation for this
    one prompt; log a bypass record.
  - `--ticket <prompt>` at prompt start: force ticket creation even if the
    prompt looks read-only.

## 7. Grow the Colaberry library opportunistically

### 7a. User-initiated adds (explicit)

When the user explicitly says ANY of:
  - "add this to our library"
  - "save this as a Colaberry skill / agent / prompt / MCP / workflow / ..."
  - "remember this for the team"
  - "put this in the library"

...fire `colaberry_propose_asset` IMMEDIATELY with whatever you have in
context. Don't ask 20 questions first -- pick a category (your best
guess), a name (Title Case from the artifact), and a one-line
description. The server tags the proposal to the user as `submitted_by`,
auto-approves it during the rollout window, and surfaces it at
`/library/<category>/<id>` so the user sees their addition the next
time they visit the library.

### 7b. Opportunistic adds (your judgment)

ALSO call `colaberry_propose_asset` when, during normal project work,
you notice the user:
  - authors a new reusable thing (skill, agent, prompt, MCP server,
    template, workflow, policy, etc.) that isn't yet in Colaberry's
    library
  - invokes an existing 3rd-party asset (an npm MCP server, a public
    skill from elsewhere, etc.) that probably ISN'T in their Colaberry
    catalog yet

Be light-touch: name + category + one-line description + one-line
`why_useful` is enough. The user can flesh details out from the asset
detail page later. The goal is fast capture so the company catalog grows
naturally from real project work.

Before proposing, check whether the asset already exists in the library:
call `colaberry_list_assets(category=..., query=<name fragment>)`. If
there's an obvious match, don't re-propose; if there isn't, fire
`colaberry_propose_asset`. The server auto-tags proposals to the
operator's own company.

## 8. Categorize personal-project session anchors

When you create a session anchor in the operator's personal BC project,
the server auto-categorizes it: pick one of the operator's existing
todolists by matching keywords from the title + snippet against the list
names (with synonyms) AND prior categorizations the operator has made.

You don't have to call `colaberry_categorize_session` manually --
`colaberry_create_ticket` does it for you whenever:
  - `bc_project_id` equals the operator's personal_bc_project_id, AND
  - no `todolist_id` was supplied

What you DO have to handle:

  a. If `create_ticket` returns `error: "categorization_low_confidence"`,
     STOP. The response carries:
       - the top-guess list (with confidence %),
       - up to 4 alternative lists,
       - a `suggest_new_list_name` (the server's guess at a new category
         name based on the topic).

     Ask the user, plainly:
       "I'd file this under <top guess> (<confidence>%). Other options:
        <alt1>, <alt2>, <alt3>. Or I can create a new list called
        <suggest_new_list_name>. Which?"

     Then call `create_ticket` again with an explicit `todolist_id`, OR
     call `colaberry_create_todolist(name=...)` first and use the new
     list's id.

  b. When the user says "move this ticket to <list>" or "this should be
     filed under <X>", call `colaberry_recategorize_session(ticket_id,
     new_todolist_id, bc_project_id, reason=...)`. This both moves the
     ticket AND logs the override so future similar topics bias toward
     the user's choice. Manual moves in the BC UI are NOT seen by us;
     get the user to ask Claude so the learning loop closes.

  c. NEVER override the auto-categorization silently by passing your
     own `todolist_id` when the user didn't ask you to. The whole point
     is making categorization auditable -- every decision should be
     either auto + receipt-on-ticket OR explicit + user-driven.

The receipt: every auto-categorized ticket has a visible "Filed under:
<list> (confidence N%)" line at the top of the description plus a
hidden HTML comment with the full rationale (matched keywords,
alternatives considered, history hits). When team members ask 'why is
this in <list>?', read the receipt back from the ticket -- you don't
need to re-run the categorizer.
"""


def _build_session_protocol(user) -> str:
    return _SESSION_PROTOCOL


def _build_org_claude_md(user) -> str:
    layer = operator_scaffold.fetch_org_claude_md()
    return layer.body


def _build_identity(user) -> str:
    """Return the user's identity + per-user BC ids as a JSON-like markdown."""
    pid = getattr(user, "personal_bc_project_id", None)
    tid = getattr(user, "personal_bc_todolist_id", None)
    account_id = os.environ.get("BASECAMP_ACCOUNT_ID", "3945211")
    parts = [
        f"# Operator identity\n",
        f"- email: `{user.email}`",
        f"- display_name: `{user.display_name}`",
        f"- user_id: `{user.user_id}`",
        f"- company_id: `{user.company_id}`",
        "",
        "## Basecamp",
        f"- account_id: `{account_id}`",
        f"- personal_bc_project_id: `{pid or '(not provisioned)'}`",
        f"- personal_bc_todolist_id: `{tid or '(not provisioned)'}`",
    ]
    if pid:
        parts.append(f"- personal_bc_project_url: https://3.basecamp.com/{account_id}/projects/{pid}")
    if getattr(user, "workspace_repo", None):
        parts.append("")
        parts.append("## GitHub")
        parts.append(f"- workspace_repo: {user.workspace_repo}")
    return "\n".join(parts) + "\n"


def _build_memory(user) -> str:
    return mcp_tools._read_memory(user.email) or (
        f"# OPERATOR_MEMORY for {user.email}\n\n"
        "(Empty so far. Will populate as you correct the operator's behavior.)\n"
    )


def _build_shared_kb(user) -> str:
    """Concatenated colaberry.com / .ai / .enterprise.colaberry.com scrape."""
    sources = operator_scaffold.scrape_colaberry_knowledge()
    parts = []
    for s in sources:
        parts.append(f"# {s.name}\n")
        parts.append(f"_Source: {s.source}_\n")
        parts.append(s.body[:5000])
        parts.append("\n\n---\n")
    return "\n".join(parts) if parts else "# Shared KB unavailable\n"


RESOURCES: list[Resource] = [
    Resource(
        uri="colaberry://doctrine/session-protocol",
        name="Session Protocol",
        description=(
            "The mandatory protocol for every Claude Code session running against "
            "this MCP server. Read FIRST before responding to any user prompt. "
            "Covers ticket creation, progress updates, auto-close, memory, and "
            "the .colaberry.json convention."
        ),
        mime_type="text/markdown",
        builder=_build_session_protocol,
    ),
    Resource(
        uri="colaberry://doctrine/org",
        name="Colaberry org doctrine",
        description=(
            "Org-wide CLAUDE.md fetched fresh from the central AI_ProjectArchitect "
            "repo. Governs how all Colaberry operators should behave."
        ),
        mime_type="text/markdown",
        builder=_build_org_claude_md,
    ),
    Resource(
        uri="colaberry://identity/self",
        name="Operator identity",
        description=(
            "Your operator identity: email, display name, company, personal BC "
            "project id, default todolist id, workspace repo URL. Use these "
            "values when calling colaberry_create_ticket / colaberry_post_progress."
        ),
        mime_type="text/markdown",
        builder=_build_identity,
    ),
    Resource(
        uri="colaberry://memory/self",
        name="Operator memory",
        description=(
            "Your accumulated cross-session memory: style preferences, corrections, "
            "domain knowledge. Read at session start; append to via colaberry_remember."
        ),
        mime_type="text/markdown",
        builder=_build_memory,
    ),
    Resource(
        uri="colaberry://kb/shared",
        name="Colaberry shared knowledge base",
        description=(
            "Scraped content from www.colaberry.com, www.colaberry.ai, and "
            "www.enterprise.colaberry.com. Use for org-context questions."
        ),
        mime_type="text/markdown",
        builder=_build_shared_kb,
    ),
]


RESOURCE_BY_URI: dict[str, Resource] = {r.uri: r for r in RESOURCES}


def read_resource(uri: str, user) -> str:
    """Fetch the content of a resource by URI."""
    res = RESOURCE_BY_URI.get(uri)
    if not res:
        raise ValueError(f"unknown resource {uri!r}; available: {list(RESOURCE_BY_URI.keys())}")
    return res.builder(user)
