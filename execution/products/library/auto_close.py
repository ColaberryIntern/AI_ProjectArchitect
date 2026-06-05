"""Auto-close tickets with confidence gate for Op 4.

Implements the contract from docs/specs/operator-04-auto-close-tickets.md
(BC todo 9967247829).

When Claude finishes substantive work, this module decides whether to:
  - AUTO-CLOSE: post the green close-summary card + mark BC ticket complete
  - ASK-TO-CONFIRM: post the amber close-request card; ticket stays open

Decision:
  1. is_ticket_done(session_state, ...) -> bool (4 gates)
  2. compute_confidence(...) -> 0..1 across 5 dimensions
  3. decide_close_action(...) -> 'auto_close' | 'ask_confirm' | 'not_ready'
  4. execute (post comment + optionally complete the BC todo)

Stdlib only. Uses urllib for the BC API.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

USER_AGENT = "Colaberry Operator Auto-Close (ali@colaberry.com)"

# ----- Constants -----------------------------------------------------------

AUTO_CLOSE_THRESHOLD = 0.85   # confidence >= this -> auto-close (Ali-confirmed 2026-06-05)
HIGH_BLAST_THRESHOLD = 0.70   # confidence >= this but < AUTO_CLOSE -> ask-to-confirm
# Below HIGH_BLAST -> not_ready (don't even ask; work probably isn't done)


# ----- Inputs --------------------------------------------------------------

@dataclass
class WorkArtifacts:
    """What the session produced. Caller fills these in from the session state."""
    work_shipped: bool                          # commit happened, files written, etc.
    verification_passed: bool                   # tsc clean OR tests pass OR user-confirmed
    progress_md_updated: bool                   # PROGRESS.md has an entry for this ticket
    recent_blockers_count: int = 0              # last 5 step comments; 0 means no recent blocker
    files_touched: list[str] = field(default_factory=list)
    summary: str = ""                           # one-line summary of what shipped
    test_evidence: Optional[str] = None         # test name or "tsc clean" or "user-confirmed"


@dataclass
class ConfidenceScore:
    """5-dimension confidence breakdown (matches CLAUDE.md root)."""
    directive_clarity: float = 0.0          # how clear was the user's ask
    test_coverage: float = 0.0              # how strong is the verification
    reversibility: float = 0.0              # 1.0 = trivially revert; 0 = can't undo
    blast_radius: float = 0.0               # 1.0 = local; 0 = touches prod / many systems
    compliance_safety: float = 0.0          # 1.0 = no compliance/security impact

    def aggregate(self) -> float:
        """Simple mean. Could be weighted; keeping flat for v1 transparency."""
        return (
            self.directive_clarity
            + self.test_coverage
            + self.reversibility
            + self.blast_radius
            + self.compliance_safety
        ) / 5.0


@dataclass
class CloseDecision:
    """Output of decide_close_action()."""
    action: str                              # 'auto_close' | 'ask_confirm' | 'not_ready'
    confidence: ConfidenceScore
    aggregate_confidence: float
    reasoning: str                           # one-line explanation
    blocking_reasons: list[str] = field(default_factory=list)  # if not_ready


# ----- Done-gate logic -----------------------------------------------------

def is_ticket_done(artifacts: WorkArtifacts) -> tuple[bool, list[str]]:
    """Returns (done?, blocking_reasons).

    4 gates per spec:
      1. work_shipped
      2. verification_passed
      3. progress_md_updated
      4. no recent blocker (count == 0 in last 5 step comments)
    """
    blocking = []
    if not artifacts.work_shipped:
        blocking.append("work not yet shipped")
    if not artifacts.verification_passed:
        blocking.append("verification has not passed (no test pass / tsc clean / user-confirmed signal)")
    if not artifacts.progress_md_updated:
        blocking.append("PROGRESS.md not yet updated with this ticket's entry")
    if artifacts.recent_blockers_count > 0:
        blocking.append(f"recent blocker in last 5 step comments ({artifacts.recent_blockers_count})")
    return (len(blocking) == 0), blocking


# ----- Confidence scoring --------------------------------------------------

def compute_confidence(
    directive_clarity: float,
    test_coverage: float,
    reversibility: float,
    blast_radius: float,
    compliance_safety: float,
) -> ConfidenceScore:
    """Caller passes 0..1 scores per dimension; we just wrap and clamp."""
    def clamp(x: float) -> float:
        return max(0.0, min(1.0, x))
    return ConfidenceScore(
        directive_clarity=clamp(directive_clarity),
        test_coverage=clamp(test_coverage),
        reversibility=clamp(reversibility),
        blast_radius=clamp(blast_radius),
        compliance_safety=clamp(compliance_safety),
    )


# ----- Top-level decision --------------------------------------------------

def decide_close_action(
    artifacts: WorkArtifacts,
    confidence: ConfidenceScore,
) -> CloseDecision:
    """Decide what to do at session-end. Pure function -- no side effects."""
    done, blocking = is_ticket_done(artifacts)
    agg = confidence.aggregate()

    if not done:
        return CloseDecision(
            action="not_ready",
            confidence=confidence,
            aggregate_confidence=agg,
            reasoning=f"Done-gate failed: {len(blocking)} blocker(s).",
            blocking_reasons=blocking,
        )

    if agg >= AUTO_CLOSE_THRESHOLD:
        return CloseDecision(
            action="auto_close",
            confidence=confidence,
            aggregate_confidence=agg,
            reasoning=f"Confidence {agg:.2f} >= auto-close threshold {AUTO_CLOSE_THRESHOLD}.",
        )

    if agg >= HIGH_BLAST_THRESHOLD:
        return CloseDecision(
            action="ask_confirm",
            confidence=confidence,
            aggregate_confidence=agg,
            reasoning=(
                f"Confidence {agg:.2f} below auto-close ({AUTO_CLOSE_THRESHOLD}) but above ask threshold "
                f"({HIGH_BLAST_THRESHOLD}). Posting ask-to-confirm comment; ticket stays open."
            ),
        )

    # Below HIGH_BLAST_THRESHOLD: work is done by the gate but confidence is so low
    # that even asking is premature. Treat as not_ready with a quality concern.
    return CloseDecision(
        action="not_ready",
        confidence=confidence,
        aggregate_confidence=agg,
        reasoning=(
            f"Done-gate passed but confidence {agg:.2f} is below the ask threshold "
            f"({HIGH_BLAST_THRESHOLD}). Quality concern -- not safe to close or ask yet."
        ),
        blocking_reasons=["confidence too low to surface for close (likely needs more verification)"],
    )


# ----- Comment renderers ---------------------------------------------------

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_auto_close_card(
    artifacts: WorkArtifacts,
    decision: CloseDecision,
    session_id: str,
    commit_sha: Optional[str] = None,
    progress_md_url: Optional[str] = None,
) -> str:
    """The green close-summary card posted by the auto-close path."""
    files_html = ""
    if artifacts.files_touched:
        items = "".join(f"<li><code>{_esc(p)}</code></li>" for p in artifacts.files_touched[:10])
        if len(artifacts.files_touched) > 10:
            items += f"<li>...and {len(artifacts.files_touched) - 10} more</li>"
        files_html = f"<div><strong>Files touched:</strong></div><ul style='margin: 4px 0 0 22px;'>{items}</ul>"

    commit_html = ""
    if commit_sha:
        commit_html = f"<div><strong>Commit:</strong> <code>{_esc(commit_sha)}</code></div>"

    progress_html = ""
    if progress_md_url:
        progress_html = f'<div><strong>PROGRESS.md entry:</strong> <a href="{_esc(progress_md_url)}">link</a></div>'

    verification = artifacts.test_evidence or "verified"

    return (
        f"<!-- close:auto:{session_id} -->\n"
        f'<div style="border: 2px solid #15803d; padding: 14px; background: #e6f4ea; border-radius: 6px;">'
        f'  <div style="font-weight: 700; font-size: 14px; color: #137333;">&#9989; Closed &mdash; work complete</div>'
        f'  <div style="margin-top: 10px;"><strong>What shipped:</strong> {_esc(artifacts.summary or "(no summary provided)")}</div>'
        f'  <div style="margin-top: 6px;"><strong>Verification:</strong> {_esc(verification)}</div>'
        f'  {files_html}'
        f'  {commit_html}'
        f'  {progress_html}'
        f'  <div style="margin-top: 8px; font-size: 12px; color: #2d3748;">'
        f'    Confidence: <strong>{decision.aggregate_confidence:.2f}</strong> '
        f'    (directive {decision.confidence.directive_clarity:.2f} &middot; '
        f'    tests {decision.confidence.test_coverage:.2f} &middot; '
        f'    reversibility {decision.confidence.reversibility:.2f} &middot; '
        f'    blast {decision.confidence.blast_radius:.2f} &middot; '
        f'    compliance {decision.confidence.compliance_safety:.2f}) &middot; '
        f'    Auto-close threshold: {AUTO_CLOSE_THRESHOLD:.2f} &middot; Session {_esc(session_id)}'
        f'  </div>'
        f'</div>'
    )


def render_ask_confirm_card(
    artifacts: WorkArtifacts,
    decision: CloseDecision,
    session_id: str,
) -> str:
    """The amber ask-to-confirm card posted when confidence is below auto-close."""
    return (
        f"<!-- close:request:{session_id} -->\n"
        f'<div style="border: 2px solid #d4a017; padding: 14px; background: #fef7e0; border-radius: 6px;">'
        f'  <div style="font-weight: 700; font-size: 14px; color: #b06000;">&#9888;&#65039; Ready to close? Confidence is below auto-close threshold.</div>'
        f'  <div style="margin-top: 10px;"><strong>What shipped:</strong> {_esc(artifacts.summary or "(no summary provided)")}</div>'
        f'  <div style="margin-top: 6px;"><strong>Why I am not auto-closing:</strong> {_esc(decision.reasoning)}</div>'
        f'  <div style="margin-top: 6px;"><strong>Verification status:</strong> {_esc(artifacts.test_evidence or "verified by gate")}</div>'
        f'  <div style="margin-top: 10px;">Reply <code>close</code> on this ticket to mark complete, or <code>keep open</code> to defer.</div>'
        f'  <div style="margin-top: 8px; font-size: 12px; color: #2d3748;">'
        f'    Confidence: <strong>{decision.aggregate_confidence:.2f}</strong> &middot; '
        f'    Auto-close threshold: {AUTO_CLOSE_THRESHOLD:.2f} &middot; '
        f'    Ask threshold: {HIGH_BLAST_THRESHOLD:.2f} &middot; Session {_esc(session_id)}'
        f'  </div>'
        f'</div>'
    )


# ----- BC API: complete the todo -------------------------------------------

def complete_bc_todo(
    account_id: str,
    bucket_id: str,
    todo_id: str,
    bc_token: str,
    timeout: float = 15.0,
) -> bool:
    """POST /buckets/{p}/todos/{id}/completion.json. Idempotent on BC's side.

    Returns True on success (including 204 No Content). False on failure.
    """
    url = f"https://3.basecampapi.com/{account_id}/buckets/{bucket_id}/todos/{todo_id}/completion.json"
    req = urllib.request.Request(
        url, data=b"{}", method="POST",
        headers={
            "Authorization": f"Bearer {bc_token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as _resp:
            return True
    except urllib.error.HTTPError as e:
        if e.code in (204, 200):
            return True
        logger.warning("complete_bc_todo: HTTP %s %s", e.code, e.reason)
        return False
    except urllib.error.URLError as e:
        logger.warning("complete_bc_todo: URLError %s", e.reason)
        return False


def post_close_comment(
    account_id: str,
    bucket_id: str,
    todo_id: str,
    content: str,
    bc_token: str,
    timeout: float = 15.0,
) -> Optional[dict]:
    """POST a close-summary or ask-to-confirm comment. Returns the comment dict or None."""
    url = f"https://3.basecampapi.com/{account_id}/buckets/{bucket_id}/recordings/{todo_id}/comments.json"
    data = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
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
            return json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        logger.warning("post_close_comment: HTTP %s %s", e.code, e.reason)
        return None
    except urllib.error.URLError as e:
        logger.warning("post_close_comment: URLError %s", e.reason)
        return None


# ----- Top-level execute ---------------------------------------------------

@dataclass
class ExecuteResult:
    action_taken: str           # 'auto_closed' | 'ask_posted' | 'not_ready_skipped'
    comment_id: Optional[int] = None
    bc_completed: bool = False
    decision: Optional[CloseDecision] = None


def execute_close_decision(
    decision: CloseDecision,
    artifacts: WorkArtifacts,
    account_id: str,
    bucket_id: str,
    todo_id: str,
    bc_token: str,
    session_id: str,
    commit_sha: Optional[str] = None,
    progress_md_url: Optional[str] = None,
) -> ExecuteResult:
    """Side-effecting executor. Posts comment + optionally completes the BC todo."""
    if decision.action == "auto_close":
        content = render_auto_close_card(artifacts, decision, session_id, commit_sha, progress_md_url)
        comment = post_close_comment(account_id, bucket_id, todo_id, content, bc_token)
        completed = complete_bc_todo(account_id, bucket_id, todo_id, bc_token)
        return ExecuteResult(
            action_taken="auto_closed",
            comment_id=comment.get("id") if comment else None,
            bc_completed=completed,
            decision=decision,
        )
    if decision.action == "ask_confirm":
        content = render_ask_confirm_card(artifacts, decision, session_id)
        comment = post_close_comment(account_id, bucket_id, todo_id, content, bc_token)
        return ExecuteResult(
            action_taken="ask_posted",
            comment_id=comment.get("id") if comment else None,
            bc_completed=False,
            decision=decision,
        )
    # not_ready: do nothing on BC
    return ExecuteResult(action_taken="not_ready_skipped", decision=decision)
