"""Faithful ticket progress updates for Op 3.

Implements the contract from docs/specs/operator-03-faithful-ticket-updates.md
(BC todo 9967247804).

As Claude Code works through a session, this module posts structured HTML-card
comments on the active BC ticket. The ticket becomes the live progress log a
manager can read at a glance.

Design points:
  - 10 closed step kinds (no organic expansion)
  - Idempotent: same step twice produces one comment (signature check)
  - Rate-limited 1 comment / 60s with blocker / diagnostic_mode / step_complete bypass
  - Comments are append-only; we never edit prior comments
  - Failures degrade gracefully (BC API down -> log + continue)

Stdlib only. Uses urllib for the BC API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

USER_AGENT = "Colaberry Operator Ticket Updater (ali@colaberry.com)"

# ----- Step kinds (closed v1 set) ------------------------------------------

STEP_KINDS = frozenset({
    "file_edit",          # substantive edit to an existing file
    "file_create",        # new file created
    "file_delete",        # file deleted
    "deploy_started",     # deploy command issued
    "deploy_completed",   # deploy finished + verification done
    "test_run",           # tests executed (pass/fail count surfaced)
    "external_send",      # email / Slack / BC comment outside active ticket
    "blocker",            # failure with no auto-recovery; halts work
    "diagnostic_mode",    # Claude entered diagnostic loop per CLAUDE.md
    "step_complete",      # logical pause in work; end-of-step recap
})

# Steps that BYPASS the rate limiter (high-signal events the manager must see fast)
BYPASS_RATE_LIMIT = frozenset({"blocker", "diagnostic_mode", "step_complete"})

# Visual tone per kind (left-border color of the card)
KIND_TONE = {
    "file_edit":         "#2b6cb0",  # blue
    "file_create":       "#15803d",  # green (something new exists)
    "file_delete":       "#d4a017",  # amber (caution)
    "deploy_started":    "#5a32a3",  # purple
    "deploy_completed":  "#15803d",  # green
    "test_run":          "#2b6cb0",  # blue
    "external_send":     "#5a32a3",  # purple
    "blocker":           "#b91c1c",  # red
    "diagnostic_mode":   "#b91c1c",  # red
    "step_complete":     "#1a365d",  # navy (end-of-step)
}

DEFAULT_RATE_LIMIT_SECONDS = 60.0
DEFAULT_HTTP_TIMEOUT_S = 15.0


# ----- Data types ----------------------------------------------------------

@dataclass
class StepEvidence:
    """The structured payload for one step.

    All fields optional; the renderer picks the ones that apply for each kind.
    """
    kind: str
    summary: str = ""                       # one-line plain text
    file_path: Optional[str] = None         # for file_edit / create / delete
    extra_paths: list[str] = field(default_factory=list)
    test_name: Optional[str] = None         # for test_run
    test_pass: Optional[int] = None
    test_fail: Optional[int] = None
    deploy_env: Optional[str] = None        # for deploy_*
    deploy_commit: Optional[str] = None
    deploy_verification_url: Optional[str] = None
    send_recipient: Optional[str] = None    # for external_send
    send_subject: Optional[str] = None
    blocker_description: Optional[str] = None
    blocker_attempted_action: Optional[str] = None
    blocker_error: Optional[str] = None
    diagnostic_reason: Optional[str] = None
    next_step_hint: Optional[str] = None    # for step_complete

    def signature_hash(self) -> str:
        """Stable SHA-256 hash of the evidence content. Used for idempotency."""
        payload = {
            k: v for k, v in self.__dict__.items()
            if v not in (None, "", [])
        }
        s = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


@dataclass
class PostResult:
    """Outcome of a single post_step() call."""
    status: str        # 'posted' | 'queued' | 'deduped' | 'skipped' | 'failed'
    kind: str
    bc_comment_id: Optional[int] = None
    bc_comment_url: Optional[str] = None
    reason: Optional[str] = None
    signature: Optional[str] = None


# ----- BC API helpers ------------------------------------------------------

def _bc_get(url: str, bc_token: str, timeout: float = DEFAULT_HTTP_TIMEOUT_S) -> tuple[bool, object]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {bc_token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(body) if body else []
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"


def _bc_post(url: str, body: dict, bc_token: str, timeout: float = DEFAULT_HTTP_TIMEOUT_S) -> tuple[bool, object]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {bc_token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        err = ""
        try:
            err = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return False, f"HTTP {e.code}: {e.reason} {err[:200]}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"


# ----- HTML card renderer --------------------------------------------------

def _esc(s: str) -> str:
    """Minimal HTML escape (stdlib html.escape would do too)."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_card(evidence: StepEvidence, session_id: str, timestamp_iso: str) -> str:
    """Render the structured HTML card for one step. Includes the signature comment marker."""
    tone = KIND_TONE.get(evidence.kind, "#4a5568")
    signature = evidence.signature_hash()

    body_lines = []
    if evidence.summary:
        body_lines.append(_esc(evidence.summary))
    if evidence.file_path:
        body_lines.append(f"<code>{_esc(evidence.file_path)}</code>")
    if evidence.extra_paths:
        for p in evidence.extra_paths[:5]:
            body_lines.append(f"<code>{_esc(p)}</code>")
        if len(evidence.extra_paths) > 5:
            body_lines.append(f"...and {len(evidence.extra_paths) - 5} more")
    if evidence.test_name:
        result = ""
        if evidence.test_pass is not None or evidence.test_fail is not None:
            result = f" &mdash; {evidence.test_pass or 0} pass / {evidence.test_fail or 0} fail"
        body_lines.append(f"Test: <code>{_esc(evidence.test_name)}</code>{result}")
    if evidence.deploy_env:
        line = f"Deploy &rarr; <code>{_esc(evidence.deploy_env)}</code>"
        if evidence.deploy_commit:
            line += f" (commit <code>{_esc(evidence.deploy_commit)}</code>)"
        body_lines.append(line)
    if evidence.deploy_verification_url:
        body_lines.append(f'Verification: <a href="{_esc(evidence.deploy_verification_url)}">link</a>')
    if evidence.send_recipient:
        line = f"Sent to <code>{_esc(evidence.send_recipient)}</code>"
        if evidence.send_subject:
            line += f': "{_esc(evidence.send_subject)}"'
        body_lines.append(line)
    if evidence.blocker_description:
        body_lines.append(f"<strong>Blocker:</strong> {_esc(evidence.blocker_description)}")
    if evidence.blocker_attempted_action:
        body_lines.append(f"<em>Attempted:</em> <code>{_esc(evidence.blocker_attempted_action)}</code>")
    if evidence.blocker_error:
        body_lines.append(f"<em>Error:</em> <code>{_esc(evidence.blocker_error)}</code>")
    if evidence.diagnostic_reason:
        body_lines.append(f"<strong>Diagnostic mode:</strong> {_esc(evidence.diagnostic_reason)}")
    if evidence.next_step_hint:
        body_lines.append(f"<em>Next:</em> {_esc(evidence.next_step_hint)}")

    body_html = "<br />".join(body_lines) if body_lines else "(no details)"

    return (
        f"<!-- step:{evidence.kind}:{signature} -->\n"
        f'<div style="border-left: 3px solid {tone}; padding: 10px 14px; background: #f7fafc; border-radius: 4px;">'
        f'  <div style="font-size: 11px; color: #4a5568;">'
        f'    <strong>{_esc(timestamp_iso)}</strong> &middot; <code>{_esc(evidence.kind)}</code> &middot; session {_esc(session_id)}'
        f'  </div>'
        f'  <div style="margin-top: 6px; font-size: 14px; color: #1a202c;">{body_html}</div>'
        f'</div>'
    )


# ----- Rate limiter --------------------------------------------------------

class CommentRateLimiter:
    """In-process token-bucket. 1 comment / 60s default.

    Steps in BYPASS_RATE_LIMIT post immediately. Other kinds queue and drain
    when the next slot opens. flush() drains everything (used at session-end).

    Thread-safe.
    """

    def __init__(self, seconds_per_comment: float = DEFAULT_RATE_LIMIT_SECONDS):
        self.seconds_per_comment = seconds_per_comment
        self._lock = threading.Lock()
        self._last_post_at = 0.0
        self._queue: deque = deque()  # (evidence, callback) pairs

    def enqueue(self, evidence: StepEvidence, post_fn) -> str:
        """Returns 'posted' if posted immediately, 'queued' if it had to wait."""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_post_at
            if evidence.kind in BYPASS_RATE_LIMIT or elapsed >= self.seconds_per_comment:
                self._last_post_at = now
                post_fn(evidence)
                self._drain_locked(post_fn, now)
                return "posted"
            self._queue.append((evidence, post_fn))
            return "queued"

    def _drain_locked(self, post_fn, now: float) -> None:
        """Drain anything that's eligible right now. Caller holds the lock."""
        while self._queue:
            elapsed = now - self._last_post_at
            if elapsed < self.seconds_per_comment:
                break
            ev, fn = self._queue.popleft()
            self._last_post_at = now
            fn(ev)
            now = time.time()

    def flush(self, post_fn) -> int:
        """Drain everything regardless of rate limit. Returns count drained.

        Use at session-end so step_complete + queued items all land.
        """
        drained = 0
        with self._lock:
            while self._queue:
                ev, fn = self._queue.popleft()
                fn(ev)
                self._last_post_at = time.time()
                drained += 1
        return drained


# ----- Top-level updater ---------------------------------------------------

@dataclass
class TicketUpdater:
    """The thing Claude Code calls to post step updates on the active ticket."""
    account_id: str
    bucket_id: str
    todo_id: str
    session_id: str
    bc_token: str
    rate_limiter: CommentRateLimiter = field(default_factory=CommentRateLimiter)
    _recent_signatures: deque = field(default_factory=lambda: deque(maxlen=40))

    def _check_idempotent(self, signature: str) -> bool:
        """True if this signature has already been posted (cached in-memory).

        Also walks the last 20 BC comments via API as a safety net so a fresh
        process doesn't re-post (the in-memory cache is per-process).
        """
        if signature in self._recent_signatures:
            return True
        # Safety net: query BC for recent comments
        ok, comments = _bc_get(
            f"https://3.basecampapi.com/{self.account_id}/buckets/{self.bucket_id}/recordings/{self.todo_id}/comments.json",
            self.bc_token,
        )
        if not ok or not isinstance(comments, list):
            return False  # fail-open: if we can't check, allow the post
        for c in comments[-20:]:
            content = c.get("content", "") or ""
            if signature in content:
                self._recent_signatures.append(signature)
                return True
        return False

    def _do_post(self, evidence: StepEvidence) -> PostResult:
        """Actually POST the comment. Called by the rate limiter or directly."""
        if evidence.kind not in STEP_KINDS:
            return PostResult(status="failed", kind=evidence.kind, reason=f"unknown step kind: {evidence.kind}")

        signature = evidence.signature_hash()
        if self._check_idempotent(signature):
            return PostResult(status="deduped", kind=evidence.kind, signature=signature, reason="already posted")

        timestamp_iso = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        content = render_card(evidence, self.session_id, timestamp_iso)

        ok, body = _bc_post(
            f"https://3.basecampapi.com/{self.account_id}/buckets/{self.bucket_id}/recordings/{self.todo_id}/comments.json",
            {"content": content},
            self.bc_token,
        )
        if not ok:
            logger.warning("ticket_updater BC post failed: %s", body)
            return PostResult(status="failed", kind=evidence.kind, reason=str(body), signature=signature)
        self._recent_signatures.append(signature)
        return PostResult(
            status="posted",
            kind=evidence.kind,
            bc_comment_id=body.get("id"),
            bc_comment_url=body.get("app_url"),
            signature=signature,
        )

    def post_step(self, evidence: StepEvidence) -> PostResult:
        """Public entry point. Routes through rate limiter (or bypass).

        Returns a PostResult with status='posted', 'queued', 'deduped', or 'failed'.
        """
        # The rate limiter calls _do_post; we capture its result by wrapping in a
        # mutable holder since the limiter callback returns None.
        result_holder: dict = {}

        def _runner(ev: StepEvidence) -> None:
            result_holder["result"] = self._do_post(ev)

        outcome = self.rate_limiter.enqueue(evidence, _runner)
        if outcome == "queued":
            return PostResult(status="queued", kind=evidence.kind, signature=evidence.signature_hash())
        # outcome == "posted": _runner ran inside the limiter; result is in holder
        return result_holder.get(
            "result",
            PostResult(status="failed", kind=evidence.kind, reason="rate limiter did not produce a result"),
        )

    def flush(self) -> int:
        """Drain queued steps at session-end. Returns count drained."""
        result_holder: dict = {"count": 0}

        def _runner(ev: StepEvidence) -> None:
            self._do_post(ev)
            result_holder["count"] += 1

        # Replace the limiter's flush to use our runner
        with self.rate_limiter._lock:
            while self.rate_limiter._queue:
                ev, _ = self.rate_limiter._queue.popleft()
                _runner(ev)
                self.rate_limiter._last_post_at = time.time()
        return result_holder["count"]
