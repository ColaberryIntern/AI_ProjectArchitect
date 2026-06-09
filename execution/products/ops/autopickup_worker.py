"""[Auto-Pickup Worker] Phase 1: read-only draft worker for Ali Personal.

Sibling to cb_mention_worker.py. Instead of waiting for an @CB System
mention, this worker scans top AI-tier todos per assignee in allowlisted
BC buckets and posts a "Proposed next step" comment on each.

Phase 1 scope (per spec ticket #9977150294):
  - Only run on buckets in OPS_AUTOPICKUP_BUCKETS (default: Ali Personal)
  - Only draft -- never execute side effects
  - Only top-N AI-tier todos per scan (default 3)
  - Idempotent: seen-set keyed on (todo_id, bc_updated_at)
  - Audit row per scan + per processed todo

Disabled by default. Set OPS_AUTOPICKUP_ENABLED=true to turn on. Lives
behind an env flag because Phase 1 still posts to live BC -- needs
explicit operator consent before the cron starts touching tickets.

Confidence-gated behavior (matches Op 4 doctrine):
  - >=0.85: Phase 1 still drafts -- promotes to execute in Phase 2
  - 0.5 to 0.85: drafts proposed action
  - <0.5: drafts a focused clarifying question

Lives under execution/products/ops/ alongside cb_mention_worker so the
same scheduler can host both jobs and the same admin endpoints can
expose both heartbeats.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT

from . import bc_comments, store, tokens

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────

ENABLED = os.environ.get("OPS_AUTOPICKUP_ENABLED", "false").strip().lower() == "true"
INTERVAL_MINUTES = int(os.environ.get("OPS_AUTOPICKUP_INTERVAL_MINUTES", "15"))
TOP_N = int(os.environ.get("OPS_AUTOPICKUP_TOP_N", "3"))
DEFAULT_BUCKETS = "7463955"  # Ali Personal
ALLOWLIST = [
    int(b.strip())
    for b in (os.environ.get("OPS_AUTOPICKUP_BUCKETS") or DEFAULT_BUCKETS).split(",")
    if b.strip()
]
# Phase 1 user pool: just Ali. Phase 2+ would walk every user with a token.
PHASE1_USERS = [
    e.strip()
    for e in (os.environ.get("OPS_AUTOPICKUP_USERS") or "ali@colaberry.com").split(",")
    if e.strip()
]

SEEN_PATH = PROJECT_ROOT / "output" / "ops" / "_autopickup" / "seen.json"
HEARTBEAT_PATH = PROJECT_ROOT / "output" / "ops" / "_autopickup" / "heartbeat.json"
AUDIT_DIR = PROJECT_ROOT / "output" / "ops" / "_autopickup"


# ── Audit + state ──────────────────────────────────────────────────


@dataclass
class AutopickupResult:
    autopickup_id: str
    user_email: str
    bucket: int
    todo_id: int
    todo_title: str
    bc_updated_at: str
    confidence: float = 0.0
    action_proposed: str = ""
    comment_id: int | None = None
    comment_url: str = ""
    status: str = ""  # "drafted" | "skipped_seen" | "skipped_no_context" | "failed"
    error: str = ""
    started_at: str = ""
    finished_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seen_path() -> Path:
    return SEEN_PATH


def _seen() -> set[str]:
    if not _seen_path().exists():
        return set()
    try:
        return set(json.loads(_seen_path().read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_seen(s: set[str]) -> None:
    p = _seen_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Cap at most 5000 entries so the file never grows unboundedly. We
    # keep the most recent N by sort order (lexically newer todo_ids
    # tend to be larger numbers; ordering is approximate not exact).
    if len(s) > 5000:
        s = set(sorted(s)[-5000:])
    p.write_text(json.dumps(sorted(s)), encoding="utf-8")


def _seen_key(todo_id: int, bc_updated_at: str) -> str:
    return f"todo:{todo_id}:{bc_updated_at or 'unknown'}"


def _audit_file() -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"


def _append_audit(r: AutopickupResult) -> None:
    try:
        with _audit_file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(r)) + "\n")
    except Exception:
        logger.warning("autopickup: audit write failed", exc_info=True)


def _write_heartbeat(summary: dict) -> None:
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("autopickup: heartbeat write failed", exc_info=True)


# ── LLM call (single OpenAI gpt-4o for Phase 1; split to Haiku+Sonnet later) ──
#
# Spec calls for Haiku 4.5 draft + Sonnet 4.6 self-grade per Ali's 6-decision
# approval. The existing ops LLM infra uses OpenAI gpt-4o (see plan_inference,
# llm_suggest). For Phase 1 we reuse that to ship; the Anthropic split is a
# Phase 1.5 cost optimization tracked on the spec ticket.


_SYSTEM_PROMPT = """You are an autopickup agent for Ali Muwwakkil. You see one Basecamp ticket at a time and propose the ONE next action that would move it forward.

You must respond with strict JSON matching this exact schema:

{
  "action": "ONE concrete next step. Verb + specific named target. Max 25 words.",
  "why": "ONE sentence: the evidence from the ticket that makes this the next step.",
  "side_effects": ["If approved, what happens. List 1-4 items. Include external sends (email, BC posts in other projects, GitHub PR creation) explicitly. If purely a draft, say 'none -- this is a draft only'."],
  "confidence_pct": <integer 0-100 reflecting how sure you are this is the right next step>,
  "needs_input": ["Specific things Ali would need to tell you before you could execute. Empty list if none."]
}

Confidence calibration:
  >=85 = clear next step, evidence in the ticket is unambiguous
  50-84 = best-guess next step, evidence is partial
  <50 = you need clarification before recommending anything; put your question in needs_input

NO em-dashes anywhere in the JSON values. Forbidden chars: em-dash, en-dash. Use colons or hyphens.
NO chatbot-speak ("I hope this helps", "feel free to"). Executive tone, terse, specific."""


def _llm_propose(todo_title: str, todo_description: str,
                                  recent_comments: list[dict]) -> dict | None:
    """Single LLM call: draft + self-grade in one shot.

    Returns the parsed JSON or None if the call fails.
    """
    from .llm_suggest import _get_client
    client = _get_client()
    if client is None:
        logger.info("autopickup: no LLM client available; skipping")
        return None

    # Build the user message with the ticket context
    comments_block = ""
    if recent_comments:
        comments_block = "\n\nRecent comments (oldest first):\n"
        for c in recent_comments[-5:]:
            who = (c.get("creator") or {}).get("name", "?")
            when = (c.get("created_at") or "")[:19]
            body = bc_comments._strip_html(c.get("content") or "")[:600]
            comments_block += f"\n--- {who} at {when} ---\n{body}\n"

    user_msg = (
        f"BC ticket title: {todo_title}\n\n"
        f"Description:\n{bc_comments._strip_html(todo_description)[:2000]}"
        f"{comments_block}"
    )

    model = os.environ.get("OPS_AUTOPICKUP_MODEL",
                                       os.environ.get("OPS_LLM_MODEL", "gpt-4o"))
    try:
        resp = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            timeout=20.0,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=1200,
        )
        text = resp.choices[0].message.content or ""
        return json.loads(text)
    except Exception:
        logger.warning("autopickup: LLM call failed", exc_info=True)
        return None


# ── BC IO ──────────────────────────────────────────────────────────


def _bc_get(path: str, token: str) -> Any:
    """Wrapper around BC GET. Reused from sync._bc_get."""
    from .sync import _bc_get as _real_get
    return _real_get(path, token, {})


def _bc_post_comment(bucket: int, todo_id: int, html: str, token: str) -> tuple[bool, str, dict]:
    import urllib.error
    import urllib.request
    url = (
        f"https://3.basecampapi.com/3945211/buckets/{bucket}/"
        f"recordings/{todo_id}/comments.json"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps({"content": html}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Colaberry-autopickup-worker/1",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read())
            return True, "ok", body
    except urllib.error.HTTPError as e:
        return False, f"http_{e.code}", {}
    except Exception as e:
        return False, f"error_{type(e).__name__}", {}


def _fetch_bc_todo(bucket: int, todo_id: int, token: str) -> dict | None:
    try:
        return _bc_get(f"/buckets/{bucket}/todos/{todo_id}.json", token)
    except Exception:
        logger.warning("autopickup: fetch todo %s failed", todo_id, exc_info=True)
        return None


def _fetch_recent_comments(bucket: int, todo_id: int, token: str) -> list[dict]:
    try:
        body = _bc_get(
            f"/buckets/{bucket}/recordings/{todo_id}/comments.json", token,
        )
        return body if isinstance(body, list) else []
    except Exception:
        return []


# ── Comment shape ──────────────────────────────────────────────────


def _render_comment(plan: dict, autopickup_id: str) -> str:
    """Format the autopickup comment per the W3b-style structured shape."""
    confidence = int(plan.get("confidence_pct", 0))
    action = (plan.get("action") or "").strip()
    why = (plan.get("why") or "").strip()
    side_effects = plan.get("side_effects") or []
    needs = plan.get("needs_input") or []
    needs_block = ""
    if needs:
        needs_block = (
            "<p><strong>Needs your input:</strong></p><ul>"
            + "".join(f"<li>{n}</li>" for n in needs)
            + "</ul>"
        )
    side_effects_block = (
        "<ul>" + "".join(f"<li>{s}</li>" for s in side_effects) + "</ul>"
        if side_effects else "<p>none specified</p>"
    )
    return (
        f"<h4>Auto-pickup: proposed next step (confidence: {confidence}%)</h4>"
        f"<p><strong>Action:</strong> {action}</p>"
        f"<p><strong>Why:</strong> {why}</p>"
        f"<p><strong>Side effects if approved:</strong></p>{side_effects_block}"
        f"{needs_block}"
        f"<p>Phase 1 is draft-only. To act on this, reply with approve "
        f"(or thumbs-up) and I will execute. Reply with corrections to redo.</p>"
        f"<p><em>autopickup_id: {autopickup_id}. Logged at "
        f"output/ops/_autopickup/{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl.</em></p>"
    )


# ── Public entrypoint ──────────────────────────────────────────────


def _user_top_ai_todos(user_email: str, bucket: int) -> list[Any]:
    """Return up to TOP_N AI-tier active todos from the user's local
    ops store, filtered to the given bucket, sorted by urgency_score desc."""
    todos = store.load_todos(user_email)
    candidates = []
    for t in todos:
        if t.bc_project_id != bucket:
            continue
        if t.status != "active":
            continue
        if t.is_dismissed:
            continue
        # AI-tier == NOT human_required (per rollup.tier semantics)
        if (t.category or "") == "human_required":
            continue
        candidates.append(t)
    candidates.sort(key=lambda x: (-(x.urgency_score or 0),
                                                       x.due_on or "9999-12-31"))
    return candidates[:TOP_N]


def scan_for_user(user_email: str) -> dict:
    """Phase 1 entrypoint per user. Returns a summary dict for the heartbeat.

    Walks each allowlisted bucket, finds top-N AI-tier todos, drafts a
    proposed-next-step comment on each unseen one.
    """
    summary = {
        "started_at": _now_iso(),
        "user_email": user_email,
        "buckets_checked": 0,
        "candidates": 0,
        "drafted": 0,
        "skipped_seen": 0,
        "skipped_no_llm": 0,
        "skipped_no_context": 0,
        "failed": 0,
        "errors": [],
    }

    token, src = tokens.get_user_token(user_email)
    if not token:
        summary["error"] = "no_token"
        summary["token_source"] = src
        return summary

    seen = _seen()

    for bucket in ALLOWLIST:
        summary["buckets_checked"] += 1
        todos = _user_top_ai_todos(user_email, bucket)
        summary["candidates"] += len(todos)
        for t in todos:
            apid = f"ap-{uuid.uuid4().hex[:10]}"
            started = _now_iso()
            # BC ticket fetch for updated_at + comments
            bc_data = _fetch_bc_todo(bucket, int(t.bc_id), token)
            bc_updated = (bc_data or {}).get("updated_at", "")
            key = _seen_key(int(t.bc_id), bc_updated)
            if key in seen:
                summary["skipped_seen"] += 1
                continue

            comments = _fetch_recent_comments(bucket, int(t.bc_id), token)

            # If the most recent comment already came from autopickup,
            # skip -- the human has not had a chance to react yet.
            already_drafted = False
            for c in comments[-3:]:
                body = bc_comments._strip_html(c.get("content") or "")
                if "autopickup_id:" in body:
                    already_drafted = True
                    break
            if already_drafted:
                seen.add(key)
                summary["skipped_seen"] += 1
                continue

            plan = _llm_propose(
                todo_title=t.title or "",
                todo_description=(bc_data or {}).get("description", ""),
                recent_comments=comments,
            )
            if not plan:
                summary["skipped_no_llm"] += 1
                seen.add(key)  # don't retry until ticket updates
                continue

            html = _render_comment(plan, apid)
            ok, detail, body = _bc_post_comment(bucket, int(t.bc_id), html, token)

            result = AutopickupResult(
                autopickup_id=apid,
                user_email=user_email,
                bucket=bucket,
                todo_id=int(t.bc_id),
                todo_title=(t.title or "")[:120],
                bc_updated_at=bc_updated,
                confidence=float(plan.get("confidence_pct", 0)) / 100.0,
                action_proposed=(plan.get("action") or "")[:200],
                comment_id=body.get("id") if isinstance(body, dict) else None,
                comment_url=body.get("app_url") if isinstance(body, dict) else "",
                status="drafted" if ok else "failed",
                error=detail if not ok else "",
                started_at=started,
                finished_at=_now_iso(),
            )
            _append_audit(result)

            if ok:
                seen.add(key)
                summary["drafted"] += 1
            else:
                summary["failed"] += 1
                summary["errors"].append({"todo_id": int(t.bc_id), "detail": detail})

    _save_seen(seen)
    summary["finished_at"] = _now_iso()
    _write_heartbeat(summary)
    return summary


def scan_all_users() -> dict:
    """Cron entrypoint. Walks PHASE1_USERS (default just Ali) and runs
    scan_for_user for each. No-op when OPS_AUTOPICKUP_ENABLED is false."""
    if not ENABLED:
        return {"status": "disabled",
                       "hint": "set OPS_AUTOPICKUP_ENABLED=true to enable"}
    all_summary = {"started_at": _now_iso(), "users": {}}
    for email in PHASE1_USERS:
        try:
            all_summary["users"][email] = scan_for_user(email)
        except Exception as e:
            logger.warning("autopickup: scan failed for %s", email, exc_info=True)
            all_summary["users"][email] = {"error": f"{type(e).__name__}: {e}"}
    all_summary["finished_at"] = _now_iso()
    return all_summary
