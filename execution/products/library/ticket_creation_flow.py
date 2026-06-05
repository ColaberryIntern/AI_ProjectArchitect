"""Ticket creation + classification flow for Op 2 (mandatory ticket doctrine).

Implements the contract from docs/specs/operator-02-mandatory-ticket-doctrine.md
(BC todo 9967247783).

When Claude Code starts a session, it reads the user's first prompt and routes
through this module:

    classification = classify_prompt(prompt)
       -> 'substantive'       : ticket required; create one or reuse existing
       -> 'readonly'          : no ticket needed; proceed
       -> 'override_no_ticket': user passed `--no-ticket` flag; bypass + log
       -> 'override_force'    : user passed `--ticket` flag; force-create

Then per-classification:

    'substantive':
       - If prompt contains a BC URL or `BC#<id>`, reuse that ticket
       - Else: derive a proposed title, ask user to confirm/edit, then create

    'readonly': proceed without ticket; no session-state write

    'override_no_ticket': write SessionState with ticket_bypass.active=True
                         + reason="user_explicit_no_ticket_flag"

Stdlib only. Uses urllib for the BC API calls.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

USER_AGENT = "Colaberry Operator Ticket Flow (ali@colaberry.com)"

# ----- Classification -------------------------------------------------------

# Verbs that strongly imply substantive work. Not exhaustive; the LLM (Claude)
# is the final arbiter when this regex is ambiguous. The list is intentionally
# conservative — false positives create extra tickets (mild noise), false
# negatives skip the ticket-creation gate (violates the doctrine).
SUBSTANTIVE_VERBS = re.compile(
    r"\b("
    r"build|implement|create|write|add|update|edit|modify|change|refactor|"
    r"fix|patch|repair|debug|resolve|address|"
    r"deploy|ship|release|push|merge|"
    r"send|email|post|notify|message|alert|"
    r"delete|remove|drop|truncate|"
    r"install|configure|setup|provision|"
    r"migrate|backfill|seed|"
    r"rename|move|"
    r"commit"
    r")\b",
    re.IGNORECASE,
)

# Question words / read-only verbs. Match heuristic only; Claude judges edge cases.
READONLY_VERBS = re.compile(
    r"^\s*("
    r"what|how|why|when|where|who|which|"
    r"show|tell|explain|describe|"
    r"list|find|search|look|check|inspect|"
    r"summarize|recap"
    r")\b",
    re.IGNORECASE,
)

# Override flags (must be at the start of the prompt; CLI-style)
NO_TICKET_FLAG = re.compile(r"^\s*--no-ticket\b", re.IGNORECASE)
FORCE_TICKET_FLAG = re.compile(r"^\s*--ticket\b", re.IGNORECASE)

# BC URL / shorthand patterns
BC_URL_PATTERN = re.compile(
    r"https?://(?:3\.)?app\.basecamp\.com/(\d+)/buckets/(\d+)/todos/(\d+)",
    re.IGNORECASE,
)
BC_SHORTHAND_PATTERN = re.compile(r"\bBC#(\d+)\b", re.IGNORECASE)


@dataclass
class PromptClassification:
    kind: str                       # 'substantive' | 'readonly' | 'override_no_ticket' | 'override_force'
    existing_ticket_ref: Optional[dict] = None  # {bucket_id, todo_id, url, account_id} if found
    matched_signal: Optional[str] = None        # which regex matched (for debugging)


def classify_prompt(prompt: str) -> PromptClassification:
    """Classify the user's prompt to decide ticket flow.

    Order of precedence (highest first):
      1. --no-ticket / --ticket flags at prompt start
      2. BC URL or BC#<id> mention -> substantive with existing_ticket_ref
      3. Substantive verb match -> substantive (will derive title + create)
      4. Read-only verb match -> readonly
      5. Default -> substantive (when in doubt, create a ticket; doctrine bias)
    """
    if NO_TICKET_FLAG.match(prompt):
        return PromptClassification(kind="override_no_ticket", matched_signal="--no-ticket flag")
    if FORCE_TICKET_FLAG.match(prompt):
        return PromptClassification(kind="override_force", matched_signal="--ticket flag")

    bc_url_match = BC_URL_PATTERN.search(prompt)
    if bc_url_match:
        return PromptClassification(
            kind="substantive",
            existing_ticket_ref={
                "account_id": bc_url_match.group(1),
                "bucket_id": bc_url_match.group(2),
                "todo_id": bc_url_match.group(3),
                "url": bc_url_match.group(0),
            },
            matched_signal="BC URL in prompt",
        )

    bc_shorthand_match = BC_SHORTHAND_PATTERN.search(prompt)
    if bc_shorthand_match:
        return PromptClassification(
            kind="substantive",
            existing_ticket_ref={"todo_id": bc_shorthand_match.group(1), "url": None},
            matched_signal="BC#<id> shorthand in prompt",
        )

    if SUBSTANTIVE_VERBS.search(prompt):
        return PromptClassification(kind="substantive", matched_signal="substantive verb match")

    if READONLY_VERBS.match(prompt):
        return PromptClassification(kind="readonly", matched_signal="read-only verb at start")

    # Default: lean toward substantive. Doctrine says "no ticket = no work" so
    # the safer error is creating an unnecessary ticket vs skipping a required one.
    return PromptClassification(kind="substantive", matched_signal="default (no clear read-only signal)")


# ----- Title derivation -----------------------------------------------------

def derive_proposed_title(prompt: str, max_chars: int = 90) -> str:
    """Derive a short ticket title from the user's first prompt.

    Simple heuristic: take the first sentence (split on .?!), strip newlines,
    collapse whitespace, truncate. Claude can override this when calling
    create_ticket_for_session() with an explicit title.
    """
    # Strip any leading flag
    p = NO_TICKET_FLAG.sub("", prompt)
    p = FORCE_TICKET_FLAG.sub("", p)
    # First sentence
    first = re.split(r"[.?!\n]", p, maxsplit=1)[0]
    first = re.sub(r"\s+", " ", first).strip()
    if len(first) > max_chars:
        first = first[: max_chars - 1].rstrip() + "..."
    return first or "Untitled Claude Code session"


# ----- Confirmation message rendering --------------------------------------

def render_confirmation_message(proposed_title: str) -> str:
    """The Markdown text Claude shows to the user before creating the ticket.

    Per Op 2 spec workflow A. This is what the operator sees in their Claude
    Code session. The visual artifact in the v01 review email shows this
    rendered as if Claude had just printed it.
    """
    return (
        "Before I start, I'll create a Basecamp ticket in your personal project "
        "to track this work.\n\n"
        f"**Proposed title:** {proposed_title}\n\n"
        "Edit the title if you want, or reply `confirm` to proceed."
    )


# ----- Ticket creation -----------------------------------------------------

def create_ticket_for_session(
    title: str,
    description: str,
    account_id: str,
    bucket_id: str,
    todolist_id: str,
    bc_token: str,
    timeout: float = 30.0,
) -> dict:
    """POST a new todo into the user's personal BC project. Returns the created todo dict.

    Raises RuntimeError on API failure (caller handles).
    """
    url = (
        f"https://3.basecampapi.com/{account_id}"
        f"/buckets/{bucket_id}/todolists/{todolist_id}/todos.json"
    )
    data = json.dumps({"content": title, "description": description}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {bc_token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return json.loads(text)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"BC ticket create failed: HTTP {e.code} {e.reason} {body[:200]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"BC ticket create failed: URLError {e.reason}") from e


# ----- Reusing existing tickets --------------------------------------------

def fetch_existing_ticket(
    account_id: str,
    bucket_id: str,
    todo_id: str,
    bc_token: str,
    timeout: float = 15.0,
) -> dict:
    """GET an existing todo for context loading. Returns the todo dict."""
    url = (
        f"https://3.basecampapi.com/{account_id}"
        f"/buckets/{bucket_id}/todos/{todo_id}.json"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {bc_token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"BC todo fetch failed: HTTP {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"BC todo fetch failed: URLError {e.reason}") from e
