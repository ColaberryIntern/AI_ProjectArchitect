"""Comment-level AI attribution — the basis for the AI Share metric.

AI Share answers: of the comments posted under a person's Basecamp account, how many were
AI-posted vs genuinely typed by hand?

Key finding (live probe, 2026-06-26): Basecamp has NO dedicated AI account for comments and
exposes no integration/bot flag on a comment. Every comment is attributed to a human User.
The AI/automation posts THROUGH human accounts (via their OAuth tokens) — e.g. auto-pickup
cards reading "CB System: automated response" appear under whichever operator is assigned.
So the only way to tell an AI-posted comment from a hand-typed one is the content: the
self-labelling `via <name>'s Claude Code` line, the stable CB-System automation templates
(dashboards, backlog snapshots, overdue nudges, auto-pickup status), and bare file/attachment
dumps. Everything the system posts is AI; the residual is genuinely typed prose.

  AI Share = AI-posted / (AI-posted + hand-typed).

The hand-typed residual is an UPPER BOUND on human: an AI comment posted by a path that does
not self-label (a one-off script, a raw API post) can still look typed. The exact fix is a
posted-comment ledger (record every comment id the system creates); auto-pickup and @CB
already log theirs. Validated live: Ali 86%, Swati 82%, Kes 76%, pure manual workers ~0%.

Pure + deterministic; no I/O. The runner gathers raw comments (BC API) and feeds them here.
"""
from __future__ import annotations

import re

AI = "ai"
HUMAN = "human"

_VIA_CLAUDE = re.compile(r"via\s+.{0,40}?claude\s*code", re.IGNORECASE)
_WORDS = re.compile(r"[a-zA-Z]{3,}")
_FILE = re.compile(r"\.(mp4|m4a|pdf|html|js|png|jpg|jpeg|csv|md|docx|zip|mov|wav|pptx|xlsx)\b",
                   re.IGNORECASE)

# Stable templates the CB System automation posts through operators' accounts. These are
# system-generated (not typed), so they are AI. Each was observed verbatim in live threads.
_AI_TEMPLATES = (
    "per operating doctrine",
    "auto-attached by sendwithbcattach",
    "<!-- step:",
    "cb system: automated response",
    "anticipated goal:",
    "cb system first-pass deliverable",
    "cb system is starting this task now",
    "first-pass deliverable for",
    "[draft - ali reviews",
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
)


def classify_comment(author: str, body: str, ai_actors: set | None = None) -> str:
    """Return "ai" or "human" for one comment. AI = posted by the system (a dedicated AI
    account, a `via … Claude Code` self-label, a CB automation template, or a bare
    file/attachment dump). Human = genuinely typed prose."""
    actors = ai_actors if ai_actors is not None else {"CB System"}
    if (author or "").strip() in actors:
        return AI
    if _VIA_CLAUDE.search(body or ""):
        return AI
    text = (body or "").lower()
    if any(m in text for m in _AI_TEMPLATES):
        return AI
    # A bare file/attachment dump (filenames, little surrounding prose) is a kit/system post,
    # not typing. Strip the filename tokens first so an underscored filename is not miscounted
    # as prose, then require real prose around it.
    if _FILE.search(body or ""):
        prose = re.sub(r"\S+\.(?:mp4|m4a|pdf|html|js|png|jpg|jpeg|csv|md|docx|zip|mov|wav|pptx|xlsx)\b",
                       " ", body or "", flags=re.IGNORECASE)
        if len(_WORDS.findall(prose)) < 6:
            return AI
    return HUMAN


def tally_comments(comments: list, ai_actors: set | None = None) -> dict:
    """Aggregate comments into per-person {ai, human, total, ai_share}.
    ai_share = ai / (ai + human). Each comment exposes `author`/`creator` and
    `content`/`content_text`/`content_html`/`body`."""
    out: dict = {}
    for c in comments:
        author = _get(c, "author", "creator")
        body = _get(c, "content_text", "content", "content_html", "body", "text")
        if not author:
            continue
        bucket = classify_comment(author, body, ai_actors)
        row = out.setdefault(author, {"ai": 0, "human": 0})
        row[bucket] += 1
    for row in out.values():
        total = row["ai"] + row["human"]
        row["total"] = total
        row["ai_share"] = round(row["ai"] / total, 3) if total else None
    return out


def _get(obj, *keys):
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else getattr(obj, k, None)
        if v:
            return v
    return ""
