"""Comment-level AI attribution — the basis for the AI Share metric.

AI Share answers: of the comments a person *authors*, how many are AI-paired work (written
through Claude Code) vs typed by hand? This is the honest measure of who is actually using
the AI-paired system. Validated against real BC threads (2026-06-26).

A comment falls into one of THREE classes:

  - AI  — the person doing work through AI: the `via <name>'s Claude Code` attribution line,
          an AI-actor account ("CB System"), or an operating-doctrine work card
          (`per operating doctrine` / `Auto-attached by sendWithBcAttach`, Op-3 `<!-- step:`).
  - AMBIENT — work-agnostic system housekeeping posted via the operator's token (daily
          dashboards, backlog snapshots, overdue reminders/escalations, auto-pickup status
          cards). It is neither the person choosing AI nor the person typing, so it is
          EXCLUDED from the ratio — counting it would over-credit whoever the bot posts as
          (validated: it pushed pure manual workers to a false ~38%).
  - HUMAN — genuinely hand-typed prose.

AI Share = AI / (AI + HUMAN). Validated on live data: builders who work through Claude Code
score high (Swati 89%, Kes 77%), pure manual workers score ~0% (Ram 3%, Narendra 0%), and
ambient automation no longer distorts the picture.

Pure + deterministic; no I/O. The runner gathers raw comments (BC API) and feeds them here.
"""
from __future__ import annotations

import re

AI = "ai"
HUMAN = "human"
AMBIENT = "ambient"

# The person actively working THROUGH the AI system.
_VIA_CLAUDE = re.compile(r"via\s+.{0,40}?claude\s*code", re.IGNORECASE)
_AI_WORK_MARKERS = (
    "per operating doctrine",
    "auto-attached by sendwithbcattach",
    "<!-- step:",
)

# Work-agnostic system housekeeping posted via an operator's token. Excluded from the ratio.
# These are stable templates emitted by the CB System automation (My Day scheduler, overdue
# escalation, auto-pickup status, daily dashboards) — not a person's authored comment.
_AMBIENT_MARKERS = (
    "backlog snapshot at",
    "launch readiness dashboard",
    "daily dashboard snapshot",
    "automated read-only snapshot",
    "please redirect or unblock",
    "please post a status update here",
    "cb will escalate",
    "executive email until",
    "quick status check: where are we",
    "reminder - this was due",
    "cb system first-pass deliverable",
    "cb system is starting this task now",
    "first-pass deliverable for",
    "[draft - ali reviews",
)


def classify_comment(author: str, body: str, ai_actors: set | None = None) -> str:
    """Return "ai", "human", or "ambient" for one comment.

    `author` is the BC comment creator name; `body` is its text or HTML. `ai_actors` are bot
    account names whose own comments are AI by definition (default {"CB System"})."""
    actors = ai_actors if ai_actors is not None else {"CB System"}
    text = (body or "").lower()
    # AI-work signals win first (a doctrine/Claude card may also mention dashboard words).
    if (author or "").strip() in actors:
        return AI
    if _VIA_CLAUDE.search(body or ""):
        return AI
    if any(m in text for m in _AI_WORK_MARKERS):
        return AI
    if any(m in text for m in _AMBIENT_MARKERS):
        return AMBIENT
    return HUMAN


def tally_comments(comments: list, ai_actors: set | None = None) -> dict:
    """Aggregate comments into per-person {ai, human, ambient, total, ai_share}.

    `total` and `ai_share` exclude ambient automation: ai_share = ai / (ai + human).
    Each comment exposes `author`/`creator` and `content`/`content_text`/`content_html`/`body`.
    """
    out: dict = {}
    for c in comments:
        author = _get(c, "author", "creator")
        body = _get(c, "content_text", "content", "content_html", "body", "text")
        if not author:
            continue
        bucket = classify_comment(author, body, ai_actors)
        row = out.setdefault(author, {"ai": 0, "human": 0, "ambient": 0})
        row[bucket] += 1
    for row in out.values():
        authored = row["ai"] + row["human"]
        row["total"] = authored
        row["ai_share"] = round(row["ai"] / authored, 3) if authored else None
    return out


def _get(obj, *keys):
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else getattr(obj, k, None)
        if v:
            return v
    return ""
