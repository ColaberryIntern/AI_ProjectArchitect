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
   - Also check the working directory for a `.colaberry.json` file. If it
     exists, the project's bc_project_id + default_list_id are in it; this
     is the project-context anchor.
   - If neither the personal anchor nor a project anchor matches an existing
     ticket the user referenced (URL or `BC#<id>`), derive a title via
     `colaberry_derive_ticket_title(text)`, ask the user to confirm or edit,
     then call `colaberry_create_ticket` to create the session anchor in
     personal. ALSO create-or-anchor against the project ticket if the work
     is project-scoped.

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
