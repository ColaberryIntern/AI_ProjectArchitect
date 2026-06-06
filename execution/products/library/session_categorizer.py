"""Rule-based categorizer for Claude Code session anchors.

When a user has a few todolists in their personal Basecamp project
(e.g. "Sales / Outreach", "Engineering", "AI Pathway"), the categorizer
picks which list a new session anchor should be filed under. Goal: cut
the "why is this filed here?" questions Ali warned us about by making
every categorization decision auditable.

Algorithm (v1, deliberately mechanical):

  1. Tokenize the session title + snippet.
  2. For each candidate todolist, build a token vocabulary from:
        a. the list's name (expanded via SYNONYMS so 'Sales' covers
           sales/deal/client/prospect/lead/pipeline/quote/proposal/...)
        b. the most-recent ~5 todo titles in that list (best-effort,
           cached if the caller passes recent_titles_by_list_id)
        c. the user's prior categorizations into that list (history
           bias -- if the user has previously sent topics resembling
           this one to list X, list X gets boosted)
  3. Score = unweighted token overlap + 2 * history-bias hits.
  4. Pick top list if its score > 0; confidence = score / max-possible.
  5. Log the decision to a per-user JSONL so future runs can use it +
     so Ali can show a receipt when the categorization is questioned.

When confidence is below CONFIDENCE_ASK_USER_BELOW (~0.35), the caller
SHOULD ask the user which list to use instead of silently filing. The
return dict carries `should_ask_user: True` for exactly that case.

Phase 1 ships rule-based + log capture. Phase 2 (deferred) replaces the
scoring with an LLM call that reads the log as training data.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional


# ── Tunables ──────────────────────────────────────────────────────────

# Below this confidence, the caller should ask the user which list.
CONFIDENCE_ASK_USER_BELOW = 0.35
# History-boost weight: how many extra "score points" does each prior
# matching ticket in the same list add?
HISTORY_BOOST_PER_HIT = 2.0
# Tokens shorter than this are dropped (catches 'a', 'to', 'on', ...).
MIN_TOKEN_LEN = 3
# Tokens this set are dropped (English stopwords that survived MIN_TOKEN_LEN).
_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "onto", "out", "off",
    "over", "under", "very", "just", "than", "then", "this", "that",
    "these", "those", "have", "has", "had", "but", "not", "are", "was",
    "were", "been", "being", "will", "can", "could", "should", "would",
    "they", "them", "their", "you", "your", "yours", "our", "his",
    "her", "him", "its",
}


# ── Synonym map ───────────────────────────────────────────────────────
# Each entry maps a category-name keyword (lowercased) to extra tokens
# that should count as "matching" the same category. Critical for thin
# list names: a list called "Sales / Outreach" matches a session about
# pricing or a quote even without literal sales/outreach in the title.
#
# Edit this map to teach the categorizer about new domains. It's the
# main lever before we move to LLM-based v2.

SYNONYMS: dict[str, set[str]] = {
    "sales": {"sales", "outreach", "deal", "client", "prospect", "lead",
                          "pipeline", "quote", "proposal", "sow", "discovery",
                          "demo", "intro", "follow-up", "followup", "negotiation",
                          "customer", "buyer", "account", "crm"},
    "outreach": {"outreach", "email", "linkedin", "intro", "cold",
                              "warm", "reach", "sequence", "drip"},
    "marketing": {"marketing", "campaign", "ads", "ad", "content", "seo",
                              "landing", "page", "website", "social", "post",
                              "blog", "newsletter", "brand", "messaging"},
    "engineering": {"engineering", "code", "build", "deploy", "ship", "bug",
                                  "fix", "feature", "refactor", "test", "ci", "cd",
                                  "infra", "infrastructure", "backend", "frontend",
                                  "api", "endpoint", "schema", "migration", "pr",
                                  "pull", "commit", "merge", "branch", "review"},
    "ops": {"ops", "operations", "internal", "tooling", "process", "policy",
                "doc", "documentation", "runbook", "playbook", "automation"},
    "finance": {"finance", "invoice", "bill", "billing", "revenue", "expense",
                          "budget", "forecast", "p&l", "pnl", "books", "accounting",
                          "tax", "commission", "payroll", "ar", "ap", "cashflow",
                          "subscription"},
    "hr": {"hr", "hiring", "interview", "candidate", "offer", "onboarding",
              "review", "performance", "benefits", "pto", "payroll", "people"},
    "people": {"people", "team", "1:1", "1on1", "feedback", "meeting"},
    "support": {"support", "customer", "ticket", "incident", "issue", "bug",
                          "complaint", "refund", "request", "help"},
    "research": {"research", "investigate", "explore", "study", "spike",
                              "prototype", "experiment", "data", "analysis"},
    "strategy": {"strategy", "planning", "roadmap", "vision", "okr", "kpi",
                              "goal", "priorities", "okrs"},
    "personal": {"personal", "misc", "notes", "general", "random"},
    "ideas": {"ideas", "someday", "backlog", "idea", "concept", "brainstorm"},
    "ai": {"ai", "llm", "claude", "gpt", "openai", "anthropic", "mcp",
              "agent", "prompt", "model"},
}


@dataclass
class CategorizationResult:
    """The full receipt of one categorization decision -- the thing we
    log + render in the ticket body so 'why is this here?' is answerable.
    """
    chosen_list_id: Optional[int] = None
    chosen_list_name: str = ""
    confidence: float = 0.0
    rationale: str = ""
    matched_tokens: list[str] = field(default_factory=list)
    history_hits: int = 0
    alternatives: list[dict] = field(default_factory=list)  # [{id, name, score}]
    should_ask_user: bool = True   # True when confidence is below threshold
    suggest_new_list_name: str = ""  # Non-empty when no list scored at all


# ── Tokenization ──────────────────────────────────────────────────────


def tokenize(text: str) -> set[str]:
    if not text:
        return set()
    out: set[str] = set()
    cur: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch == "&":
            cur.append(ch)
        else:
            if cur:
                w = "".join(cur)
                if len(w) >= MIN_TOKEN_LEN and w not in _STOPWORDS:
                    out.add(w)
                cur = []
    if cur:
        w = "".join(cur)
        if len(w) >= MIN_TOKEN_LEN and w not in _STOPWORDS:
            out.add(w)
    return out


def _expand_via_synonyms(name_tokens: Iterable[str]) -> set[str]:
    """Given the tokens of a category name, add synonyms.

    "Sales / Outreach" -> {"sales", "outreach"} -> expanded to include
    deal/client/prospect/etc. via SYNONYMS["sales"] + SYNONYMS["outreach"].
    """
    out: set[str] = set()
    for t in name_tokens:
        out.add(t)
        if t in SYNONYMS:
            out |= SYNONYMS[t]
    return out


# ── Categorization log ───────────────────────────────────────────────


def _log_dir() -> Path:
    here = Path(__file__).resolve()
    # repo root = .../execution/products/library/<file> -> 3 parents up
    root = here.parents[3]
    p = root / "output" / "library" / "_categorization_log"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_decision(user_email: str, *, session_title: str,
                          result: CategorizationResult,
                          bc_project_id: int) -> None:
    if not user_email:
        return
    safe = user_email.replace("/", "_").replace("\\", "_")
    p = _log_dir() / f"{safe}.jsonl"
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_title": session_title,
        "bc_project_id": bc_project_id,
        **asdict(result),
    }
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        # Logging is advisory; never break the live flow.
        pass


def log_override(user_email: str, *, ticket_id: int,
                            old_list_id: int, old_list_name: str,
                            new_list_id: int, new_list_name: str,
                            session_title: str, reason: str = "") -> None:
    """Append a user-initiated override to the log. This entry is the
    strongest signal for future categorization: when the user explicitly
    moves a ticket, every similar future topic should bias toward the
    new list."""
    if not user_email:
        return
    safe = user_email.replace("/", "_").replace("\\", "_")
    p = _log_dir() / f"{safe}.jsonl"
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "override",
        "ticket_id": ticket_id,
        "old_list_id": old_list_id,
        "old_list_name": old_list_name,
        "new_list_id": new_list_id,
        "new_list_name": new_list_name,
        "session_title": session_title,
        "user_reason": reason,
    }
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def load_recent_log(user_email: str, *, limit: int = 200) -> list[dict]:
    if not user_email:
        return []
    safe = user_email.replace("/", "_").replace("\\", "_")
    p = _log_dir() / f"{safe}.jsonl"
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ── Main entry point ──────────────────────────────────────────────────


def categorize(*,
                      session_title: str,
                      session_snippet: str,
                      candidate_lists: list[dict],
                      user_email: str = "",
                      recent_titles_by_list_id: Optional[dict] = None,
                      ) -> CategorizationResult:
    """Score each candidate list and return the best match.

    candidate_lists: [{id, name, completed}] from
        colaberry_list_project_todolists or equivalent.
    recent_titles_by_list_id: optional {list_id: [title1, title2, ...]}
        to feed extra signal from in-list content. Best-effort; the
        categorizer works without it.
    """
    if not session_title and not session_snippet:
        return CategorizationResult(
            rationale="empty_input",
            should_ask_user=True,
        )
    target = tokenize(session_title) | tokenize(session_snippet)
    if not target:
        return CategorizationResult(
            rationale="no_meaningful_tokens",
            should_ask_user=True,
        )

    active = [ll for ll in (candidate_lists or [])
                       if not ll.get("completed") and ll.get("id") and ll.get("name")]
    if not active:
        # Suggest a sensible new-list name based on the first noticeable token.
        suggested = _suggest_name_from_tokens(target)
        return CategorizationResult(
            rationale="no_active_lists_in_project",
            should_ask_user=True,
            suggest_new_list_name=suggested,
        )

    history = load_recent_log(user_email, limit=200) if user_email else []
    # Index history by list_id for the boost
    history_by_list: dict[int, list[set]] = {}
    for h in history:
        lid = h.get("chosen_list_id") or h.get("new_list_id")
        if not lid:
            continue
        title = h.get("session_title", "")
        if title:
            history_by_list.setdefault(int(lid), []).append(tokenize(title))

    scored: list[tuple[float, dict, set, int]] = []  # (score, list, matched, hits)
    for ll in active:
        list_tokens = _expand_via_synonyms(tokenize(ll["name"]))
        # Recent titles add weakly-weighted vocabulary
        recent_titles = (recent_titles_by_list_id or {}).get(ll["id"], [])
        for t in recent_titles[:5]:
            list_tokens |= tokenize(t)
        matched = target & list_tokens
        overlap = len(matched)
        # History boost
        hits = 0
        for hist_tokens in history_by_list.get(int(ll["id"]), []):
            shared = target & hist_tokens
            if len(shared) >= 2:
                hits += 1
        score = float(overlap) + HISTORY_BOOST_PER_HIT * hits
        scored.append((score, ll, matched, hits))

    scored.sort(key=lambda x: -x[0])
    top_score, top_list, top_matched, top_hits = scored[0]

    if top_score <= 0:
        suggested = _suggest_name_from_tokens(target)
        return CategorizationResult(
            confidence=0.0,
            rationale="no_overlap_with_any_existing_list",
            alternatives=[{"id": s[1]["id"], "name": s[1]["name"], "score": s[0]}
                                    for s in scored[:5]],
            should_ask_user=True,
            suggest_new_list_name=suggested,
        )

    # Confidence: capped at 1.0. Use a denominator that rewards genuinely
    # good matches but doesn't let huge sessions inflate it artificially.
    denom = max(3, min(len(target), 10))
    confidence = min(1.0, top_score / denom)

    rationale = (
        f"top list = {top_list['name']!r}; matched {len(top_matched)} tokens "
        f"({', '.join(sorted(top_matched))})"
    )
    if top_hits:
        rationale += f"; +{top_hits} prior session(s) in this list"

    return CategorizationResult(
        chosen_list_id=int(top_list["id"]),
        chosen_list_name=top_list["name"],
        confidence=round(confidence, 3),
        rationale=rationale,
        matched_tokens=sorted(top_matched),
        history_hits=top_hits,
        alternatives=[{"id": s[1]["id"], "name": s[1]["name"], "score": s[0]}
                                for s in scored[1:5]],
        should_ask_user=(confidence < CONFIDENCE_ASK_USER_BELOW),
    )


def _suggest_name_from_tokens(target: set[str]) -> str:
    """Pick a plausible category name when no existing list matches.

    Looks for tokens that appear as keys in SYNONYMS (those are the
    canonical category names we know about). If none match, falls
    back to the first reasonable noun-like token Title-Cased."""
    for t in sorted(target):
        if t in SYNONYMS:
            return t.title()
    # Fallback: first non-stopword token Title-Cased
    for t in sorted(target):
        if len(t) >= 4 and t not in _STOPWORDS:
            return t.title()
    return "General"


# ── Render the receipt for a ticket body ──────────────────────────────


def render_transparency_block(result: CategorizationResult) -> str:
    """Visible italic line + hidden HTML comment with the full rationale.

    Designed to be prepended to a ticket description. The HTML comment
    is stable enough that future tools could parse it to answer "why is
    this here?" without re-running the categorizer.
    """
    if not result.chosen_list_name:
        return ""
    alts_blob = ", ".join(f"{a['name']}({a['score']})"
                                          for a in (result.alternatives or [])[:3])
    comment = (
        "<!-- colaberry_categorization: "
        f"list={result.chosen_list_name!r} "
        f"confidence={result.confidence} "
        f"matched={','.join(result.matched_tokens)} "
        f"history_hits={result.history_hits} "
        f"alternatives=[{alts_blob}] "
        f"rationale={result.rationale!r} "
        "-->"
    )
    visible = (
        f'<p><em>Filed under: {result.chosen_list_name} '
        f"(confidence {int(result.confidence * 100)}%)</em></p>"
    )
    return f"{comment}\n{visible}\n"
