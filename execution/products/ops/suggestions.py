"""Per-todo action recipe + Claude Code-ready prompt.

The action recipe is keyed on simple title/description regex — deterministic,
no LLM call. Phase 2+ can replace this with an LLM-derived recipe; the
deterministic version stays as a fallback.

Returned shape:
    {
      "action_kind":  "reply" | "decision" | "meeting" | "research" |
                     "build" | "review" | "schedule" | "default",
      "one_line":     short imperative summary,
      "deliverable":  one sentence naming the artifact AND where it goes,
      "steps":        ["...", "...", ...]            # numbered approach
      "resources":    [{"kind", "name", "why"}, ...]  # tools/skills/agents to lean on
      "stop_conditions": ["...", "..."],              # when to pause and escalate
      "urgency_summary": "Due in 2 days — score 76 (human_required)"
    }

`generate_prompt` wraps the suggestion in a ready-to-fire Claude Code prompt
including the todo's full context.
"""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

from . import personas
from .store import OpsTodo

# ── Predicted deliverable outputs (structured, colored, per-file confidence) ──
#
# The LLM (llm_suggest) returns predicted_outputs; we normalize them here so the
# briefing renders read-only colored bullets and the workspace renders editable
# rows. Keys below MUST match the `.out-<key>` CSS in _my_day_styles.html and the
# MD_OUTPUT_TYPES list in that same template's JS.
OUTPUT_TYPES = [
    {"key": "code",     "label": "Code"},
    {"key": "doc",      "label": "Doc"},
    {"key": "pdf",      "label": "PDF"},
    {"key": "slides",   "label": "Slides"},
    {"key": "sheet",    "label": "Sheet"},
    {"key": "html",     "label": "HTML"},
    {"key": "image",    "label": "Image"},
    {"key": "diagram",  "label": "Diagram"},
    {"key": "notebook", "label": "Notebook"},
    {"key": "video",    "label": "Video"},
    {"key": "audio",    "label": "Audio"},
    {"key": "data",     "label": "Data"},
    {"key": "dataset",  "label": "Dataset"},
    {"key": "config",   "label": "Config"},
    {"key": "archive",  "label": "Archive"},
    {"key": "folder",   "label": "Folder"},
    {"key": "email",    "label": "Email"},
    {"key": "text",     "label": "Text"},
    {"key": "other",    "label": "Other"},
]
_OUTPUT_TYPE_KEYS = {t["key"] for t in OUTPUT_TYPES}

# Visual outputs: when a task produces MORE THAN ONE of these, they go in a folder.
VISUAL_TYPES = {"image", "html", "diagram", "slides"}

# The Colaberry HTML house standard (fetched from Basecamp). Any .html output
# follows this; the pasted Claude Code session can read the full master prompt
# at the URL via the Basecamp MCP.
# Decision / approval tickets always hand back a distributable decision record.
DECISION_KINDS = {"decision"}
_RECORD_TYPES = {"doc", "pdf", "html"}
DECISION_RECORD_RULE = (
    "The decision record follows the Colaberry decision rubric: (1) DECISION — the verdict in one line; "
    "(2) CONTEXT — who needs it, what is being decided, and why now; (3) OPTIONS — each option considered "
    "with its trade-offs (cost / risk / effort); (4) RATIONALE — why this option won; (5) CONSEQUENCES & "
    "NEXT ACTIONS — what happens now, the owner, and the date; (6) SIGN-OFF — who approves. One page, "
    "decision-first, ready to distribute."
)

HTML_FORMAT_URL = "https://app.basecamp.com/3945211/buckets/7463955/todos/10039770075"
HTML_FORMAT_RULE = (
    "Any .html output follows the Colaberry HTML dashboard standard: ONE self-contained .html "
    "(inline CSS, vanilla JS, Mermaid + Chart.js from CDN); model the data into ONE embedded JS "
    "object that both the tables and the charts read; pick visuals from the data SHAPE (KPI cards, "
    "bar/doughnut, grouped bar, Mermaid gantt/flowchart/sequence, conditional-format heatmap, full "
    "detail table); executive palette (slate bg #eef2f6, ink #0f172a, accent teal #0f766e, "
    "green/amber/red status); sticky nav; responsive; lead with the 'so what'. "
    f"Full master prompt: {HTML_FORMAT_URL}"
)

# Extension → type, used to recover a type when the model omits/garbles it.
_EXT_TYPE = {
    "py": "code", "js": "code", "ts": "code", "tsx": "code", "jsx": "code",
    "java": "code", "go": "code", "rb": "code", "rs": "code", "php": "code",
    "c": "code", "cpp": "code", "cs": "code", "sh": "code", "sql": "code", "css": "code",
    "html": "html", "htm": "html",
    "json": "data", "xml": "data",
    "yaml": "config", "yml": "config", "toml": "config", "ini": "config", "env": "config",
    "csv": "sheet", "xlsx": "sheet", "xls": "sheet", "tsv": "sheet",
    "doc": "doc", "docx": "doc", "md": "doc", "rtf": "doc",
    "txt": "text", "pdf": "pdf", "ppt": "slides", "pptx": "slides", "key": "slides",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "svg": "image", "webp": "image",
    "mmd": "diagram", "drawio": "diagram", "vsdx": "diagram",
    "ipynb": "notebook",
    "mp4": "video", "mov": "video", "webm": "video",
    "mp3": "audio", "wav": "audio", "m4a": "audio",
    "zip": "archive", "tar": "archive", "gz": "archive", "7z": "archive",
    "parquet": "dataset", "db": "dataset", "sqlite": "dataset",
    "eml": "email", "msg": "email",
}


def _ext_to_type(name: str) -> str:
    if name.endswith("/"):
        return "folder"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXT_TYPE.get(ext, "other")


def normalize_outputs(raw) -> list:
    """Coerce the model's predicted_outputs into [{name, type, confidence}].

    Type falls back to the extension (then 'other') when missing/unknown;
    confidence is clamped to an int 0-100; blank names are dropped. Tolerant of a
    bare string, a single dict, or a list of either."""
    if isinstance(raw, (str, dict)):
        raw = [raw]
    out: list = []
    for item in raw or []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("file") or item.get("filename") or "").strip()
        if not name:
            continue
        typ = str(item.get("type") or "").strip().lower()
        if typ not in _OUTPUT_TYPE_KEYS:
            typ = _ext_to_type(name)
        try:
            conf = int(round(float(item.get("confidence", 0))))
        except (TypeError, ValueError):
            conf = 0
        out.append({"name": name, "type": typ, "confidence": max(0, min(100, conf))})
    return out


def _ensure_decision_record(action_kind: str, outputs: list) -> list:
    """A decision/approval always hands back a distributable decision record. If
    the model didn't already predict a document output (doc/pdf/html), prepend a
    'decision-record.md' so the Downloads list is never empty for a decision."""
    if action_kind not in DECISION_KINDS:
        return outputs
    if any(o.get("type") in _RECORD_TYPES for o in outputs):
        return outputs
    return [{"name": "decision-record.md", "type": "doc", "confidence": 90}] + list(outputs)


def normalize_qa(raw) -> dict:
    """Coerce the model's qa_process into {target, checks}. Checks is a clean list
    of non-empty strings; target is a trimmed string. Tolerant of a bare list/str."""
    if isinstance(raw, list):
        raw = {"checks": raw}
    if isinstance(raw, str):
        raw = {"checks": [raw]}
    if not isinstance(raw, dict):
        return {"target": "", "checks": []}
    checks = raw.get("checks") or raw.get("steps") or []
    if isinstance(checks, str):
        checks = [checks]
    checks = [str(c).strip() for c in checks if str(c).strip()]
    return {"target": str(raw.get("target") or raw.get("artifact") or "").strip(), "checks": checks}


def _downloads_block(outputs, action_kind: str = "") -> str:
    """The '## Downloads' body: the files to create early, plus the folder rule,
    the decision-record rubric, and the HTML standard when relevant. Empty-ish
    note when there are no files."""
    if not outputs:
        return "No file downloads expected for this task.\n"
    n = len(outputs)
    lines = [
        f"Create these {n} file{'' if n == 1 else 's'} FIRST (stub them), then complete them per the "
        "Details below. Save them in your Downloads folder."
    ]
    lines += [f"- {o['name']} ({o['type']}, ~{o['confidence']}% sure)" for o in outputs]
    visuals = [o for o in outputs if o["type"] in VISUAL_TYPES]
    if len(visuals) > 1:
        lines.append(f"You have {len(visuals)} visuals: put them together in ONE named folder, not loose files.")
    elif n >= 4:
        lines.append(f"You have {n} files: group related ones under a named folder so the deliverable stays organized.")
    if action_kind in DECISION_KINDS:
        lines.append(DECISION_RECORD_RULE)
    if any(o["type"] == "html" for o in outputs):
        lines.append(HTML_FORMAT_RULE)
    return "\n".join(lines) + "\n"


def _qa_block(qa) -> str:
    """The '### Verify (QA)' Details section (empty string when no checks)."""
    qa = qa or {}
    checks = qa.get("checks") or []
    if not checks:
        return ""
    target = qa.get("target") or "the artifact"
    lines = [f"### Verify (QA) — {target}",
             "Get the artifact, then run this EXACT check before you approve or ship:"]
    lines += [f"- {c}" for c in checks]
    return "\n".join(lines) + "\n\n"


def normalize_next_actions(raw) -> list:
    """Up to 3 follow-up actions to offer, order preserved (most-confident first).
    Tolerant of a bare string; blanks dropped; capped at 3."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()][:3]


def _next_actions_block(actions) -> str:
    """The '## Ask me next' section: 3 follow-ups to offer, most-confident first."""
    if not actions:
        return ""
    lines = ["## Ask me next",
             "When the deliverable is done, ask me whether to do any of these (most confident first):"]
    lines += [f"{i}. {a}" for i, a in enumerate(actions, 1)]
    return "\n".join(lines) + "\n\n"


# ── Action recipes (regex-keyed, ordered most-specific-first) ──────────

_RECIPES = [
    {
        "kind": "decision",
        "match": re.compile(r"\b(approve|decide|sign[- ]?off|confirm|reject|accept|deny|go/no[- ]go)\b", re.I),
        "one_line": "Make the call: approve, request changes, or reject.",
        "deliverable": "A short recommendation — **verdict · reason · next action** — saved with `colaberry_remember` and posted to the BC ticket as a decision record once confirmed.",
        "steps": [
            "Read the BC thread + any linked docs to surface the decision being asked.",
            "Identify the 1-2 facts that flip the decision (cost, risk, dependency).",
            "Write a 3-line decision: verdict, reason, next action.",
            "If unsure: list the specific info needed and who would have it.",
        ],
        # When the todo is a HUMAN TASK (category=human_required or a "HUMAN TASK"
        # marker in the description), the AI cannot satisfy a "Ali confirms"-style
        # Definition of Done — only the named owner can. build_suggestion() swaps
        # in these step overrides so the AI produces a recommendation for the owner
        # to confirm, not an authoritative verdict it isn't entitled to post.
        # `{owner}` is substituted with the extracted owner name (or "the owner").
        "human_required_step_overrides": {
            2: "Draft a recommendation (verdict + reason + next action) for {owner} to confirm. Do NOT post it as the final decision; the call is theirs.",
        },
        "resources": [
            {"kind": "tool", "name": "colaberry_remember", "why": "Persist the decision + rationale to operator memory so it's auditable later"},
            {"kind": "tool", "name": "colaberry_save_doc_to_bc", "why": "Post the decision record to the BC ticket as a durable artifact once confirmed"},
        ],
        "stop_conditions": [
            "If the decision impacts >$10k or external commitments, pause and surface to Ram before posting.",
            "If a stakeholder you'd normally consult is missing from the thread, loop them in instead of deciding alone.",
        ],
    },
    {
        "kind": "reply",
        "match": re.compile(r"\b(reply|respond|follow[- ]up|answer|email|message)\b", re.I),
        "one_line": "Draft and send a reply that closes the loop on this thread.",
        "deliverable": "A sent reply (Gmail or BC comment, matching where the thread lives) that closes the loop — answer first, then context.",
        "steps": [
            "Read the most recent 3 messages on the thread to understand the ask.",
            "Identify what they actually need (answer / decision / action / acknowledgement).",
            "Draft a reply: lead with the answer, then context, then any ask of them.",
            "Send via Gmail or paste back as a BC comment — match where the conversation lives.",
        ],
        "resources": [
            {"kind": "tool", "name": "gmail (send)", "why": "Send the reply from your Colaberry address"},
        ],
        "stop_conditions": [
            "If the reply commits Colaberry to >4h of work or new deliverables, surface to Ram first.",
            "If you're unsure of any technical claim, fact-check before sending.",
        ],
    },
    {
        "kind": "meeting",
        "match": re.compile(r"\b(meeting|schedul|calendar|sync|1:1|standup|huddle|kickoff)\b", re.I),
        "one_line": "Decide whether this meeting is needed; schedule or replace with async.",
        "deliverable": "Either a calendar invite with an explicit agenda + intended outcome, or a 1-page async doc that replaces the meeting.",
        "steps": [
            "Ask: what decision or alignment does this meeting produce? If unclear, the meeting probably shouldn't happen.",
            "Propose async first: a 1-page doc + 24h comment window often replaces the meeting.",
            "If meeting IS needed: send a calendar invite with explicit agenda + outcome.",
            "Block 30 min prep + 15 min decision-capture immediately after.",
        ],
        "resources": [
            {"kind": "tool", "name": "google-calendar", "why": "Schedule + send the invite"},
        ],
        "stop_conditions": [
            "Don't schedule a recurring meeting without a 30-day kill date.",
            "If the attendee list is >5, default to async unless explicitly justified.",
        ],
    },
    {
        "kind": "research",
        "match": re.compile(r"\b(research|investigat\w*|look[ -]into|figure out|find out|explore|discover|assess|evaluate)\b", re.I),
        "one_line": "Time-box the investigation; produce a written finding.",
        "deliverable": "A 1-page finding: question · answer · source · confidence.",
        "steps": [
            "Set a hard time-box: 30 min, 2 hours, or 1 day. Don't exceed.",
            "List 3-5 specific questions you need answered.",
            "Search the existing repo + Slack + BC + Vault for prior art before doing fresh work.",
            "Write findings as a 1-page doc with: question, answer, source, confidence.",
        ],
        "resources": [
            {"kind": "skill", "name": "deep-research", "why": "Multi-source fact-checked report with citations"},
            {"kind": "tool", "name": "colaberry_get_asset", "why": "Pulls vetted KB assets as LLM-readable input before fresh research"},
        ],
        "stop_conditions": [
            "If you blow the time-box, escalate with what you HAVE found — don't keep digging silently.",
            "If sources conflict, flag it explicitly; don't pick one and move on.",
        ],
    },
    {
        "kind": "build",
        "match": re.compile(r"\b(build|implement|create|develop|code|ship|deploy|wire|integrate)\b", re.I),
        "one_line": "Ship the smallest version that proves the idea, then iterate.",
        "deliverable": "The smallest working version that hits the acceptance criteria, plus a 'shipped' BC comment: commit SHA · what it does · what's deferred.",
        "steps": [
            "Re-read the BC thread for acceptance criteria. If absent, write your own + post for confirmation.",
            "Sketch the data model + 1 happy-path flow before writing code.",
            "Build the minimum that hits the acceptance criteria — defer polish to a follow-up commit.",
            "Test against real data; if local test passes, deploy + smoke on prod.",
            "Post a 'shipped' comment with: commit SHA, what it does, what's deferred.",
        ],
        "resources": [
            {"kind": "agent", "name": "claude-code", "why": "Drive the build; deterministic per the test loop"},
            {"kind": "workflow", "name": "tsc-clean + commit + deploy + verify", "why": "Standard per-phase loop"},
        ],
        "stop_conditions": [
            "If acceptance criteria are unclear after the BC re-read, STOP and ask before writing code.",
            "Don't deploy without local tests passing.",
            "Don't bundle a refactor into a feature commit.",
        ],
    },
    {
        "kind": "review",
        "match": re.compile(r"\b(review|check|verify|audit|qa|test|validat)\b", re.I),
        "one_line": "Inspect, write findings, recommend a verdict.",
        "deliverable": "A written verdict — ship / ship-with-fixes / hold / reject — with each concern tagged blocker / nit / question.",
        "steps": [
            "Read what you're reviewing end-to-end before forming an opinion.",
            "List specific concerns with line/section references.",
            "Categorize each: blocker / nit / question.",
            "Recommend: ship / ship-with-fixes / hold / reject.",
        ],
        "resources": [
            {"kind": "skill", "name": "code-review", "why": "Structured PR review at the requested effort level"},
        ],
        "stop_conditions": [
            "If you can't tell what 'good' looks like for this artifact, ask before reviewing.",
            "Don't approve work you don't actually understand — flag it as 'need pairing' instead.",
        ],
    },
    {
        "kind": "schedule",
        "match": re.compile(r"\b(date|deadline|when|by\s+(?:friday|monday|tuesday|wednesday|thursday|saturday|sunday|tomorrow|next week)|due\s+by)\b", re.I),
        "one_line": "Pin down a specific date or set up the cadence.",
        "deliverable": "A specific date posted back to the thread AND added to the calendar in the same step.",
        "steps": [
            "Identify what's actually being scheduled (a delivery? a recurring touchpoint?).",
            "Propose a specific date with rationale; don't ask 'when works?'",
            "If recurring: set a 30-day kill date and a clear cadence.",
            "Post the date + put it on the calendar in the same step.",
        ],
        "resources": [
            {"kind": "tool", "name": "google-calendar", "why": "Create the entry as you confirm the date"},
        ],
        "stop_conditions": [
            "Don't agree to a date you haven't checked against your own calendar.",
        ],
    },
]

_DEFAULT_RECIPE: dict[str, Any] = {
    "kind": "default",
    "one_line": "Read the task, decide the next concrete step, do it.",
    "deliverable": "One concrete next action done (a reply, a decision, a doc, or code), with a short note back on what you did + what's next.",
    "steps": [
        "Open the BC ticket + skim the most recent activity.",
        "Identify the single next action — a reply, a decision, a doc, a piece of code.",
        "Do that one thing. Don't broaden scope.",
        "Post back what you did + what's next.",
    ],
    "resources": [],
    "stop_conditions": [
        "If you can't identify a single next action after 5 min of reading, escalate.",
    ],
}


def _match_recipe(title: str, description: str) -> dict[str, Any]:
    text = f"{title}\n{description}"
    for r in _RECIPES:
        if r["match"].search(text):
            return r
    return _DEFAULT_RECIPE


# Owner marker in PMO-generated descriptions, e.g.
#   <strong>Owner:</strong> Ali Muwwakkil
# Tolerates optional closing tag and surrounding whitespace; stops at the next
# tag or end of line so we capture just the name.
_OWNER_RE = re.compile(r"Owner:\s*(?:</strong>)?\s*([^<\n]+?)\s*(?:<|$)", re.I)
_HUMAN_TASK_RE = re.compile(r"\bHUMAN[ _-]?TASK\b", re.I)

# Returned by _human_owner when the todo is human-owned but no owner NAME could
# be extracted. Callers branch on this so the prompt doesn't print the clunky
# "(owner: the owner)" — see build_suggestion's owner_note.
_GENERIC_OWNER = "the owner"

# Dependency markers stamped by the task generator on approval/review tasks.
# Contract: directives/approval-task-dependency-linking.md. We surface them in
# the prompt's CONTEXT block so a fresh session can reach the artifact without
# asking the operator for a link.
#
# The value may be a BARE URL as the generator wrote it, OR — once the
# description round-trips through Basecamp, which autolinks bare URLs on save —
# an anchor: `Depends-on: <a ... href="URL">URL</a>`. A plain `[^<]+?` capture
# dies at the anchor's leading "<" and reads empty, so we read the href when the
# value is an anchor and fall back to the bare text otherwise (PENDING stays
# bare). The anchor alternative is tried first so the bare branch never swallows
# the "<". Mirrors the generator's extractMarkers (Accelerator repo,
# lib/dependencyLinks.js).
_MARKER_VALUE_TMPL = (
    r'{label}:\s*(?:</strong>)?\s*'
    r'(?:<a[^>]*href="([^"]+)"[^>]*>[^<]*</a>|([^<\n]+?))\s*(?:<|$)'
)


def _marker_value(label: str, desc: str) -> str | None:
    m = re.search(_MARKER_VALUE_TMPL.format(label=label), desc, re.I)
    if not m:
        return None
    return (m.group(1) or m.group(2) or "").strip() or None


def _dependency_block(todo: OpsTodo) -> str:
    """Render the Depends-on / Artifact links (if the generator stamped them)
    as an explicit prompt section. Empty string when neither marker is present
    so non-approval tasks get no orphan heading."""
    desc = todo.description or ""
    dep = _marker_value("Depends-on", desc)
    art = _marker_value("Artifact", desc)
    if not dep and not art:
        return ""
    lines = ["", "## Dependency (review this before acting)"]
    if dep:
        lines.append(f"**Drafting task:** {dep}")
    if art:
        if art.upper() == "PENDING":
            lines.append(
                "**Artifact:** not attached yet (PENDING). The thing to approve "
                "does not exist. Do NOT treat this as an approver delay: the next "
                "step belongs to the drafting task's owner, not this gate."
            )
        else:
            lines.append(f"**Artifact:** {art}")
    return "\n".join(lines) + "\n"


def _human_owner(todo: OpsTodo) -> str | None:
    """Return the named owner if this todo is a human-required decision the AI
    can only *recommend* on (not decide), else None.

    A todo is human-owned when the scorer tagged it `human_required` OR the
    PMO stamped a "HUMAN TASK" marker in the description. When so, we try to
    pull the owner's name from the `Owner:` marker; if absent, fall back to
    the generic "the owner" so callers always get a usable label.
    """
    is_human = todo.category == "human_required" or bool(
        _HUMAN_TASK_RE.search(todo.description or "")
    )
    if not is_human:
        return None
    m = _OWNER_RE.search(todo.description or "")
    if m:
        name = m.group(1).strip()
        if name:
            return name
    return _GENERIC_OWNER


def _urgency_summary(todo: OpsTodo) -> str:
    parts: list[str] = []
    if todo.due_on:
        parts.append(f"due {todo.due_on}")
    if todo.urgency_score:
        parts.append(f"score {todo.urgency_score}")
    if todo.category and todo.category != "unscored":
        parts.append(f"({todo.category})")
    return " · ".join(parts) if parts else "no urgency signal"


def build_suggestion(todo: OpsTodo) -> dict[str, Any]:
    """Produce the structured per-todo recipe shown above the workspace.

    For human-owned tasks, any `human_required_step_overrides` on the matched
    recipe are applied so the AI is steered toward a recommendation the owner
    confirms — never an authoritative verdict it isn't entitled to post. The
    `owner_note` (if any) is surfaced in the generated prompt's expectations.
    """
    recipe = _match_recipe(todo.title, todo.description)
    steps = list(recipe["steps"])

    owner = _human_owner(todo)
    owner_note = ""
    if owner:
        overrides = recipe.get("human_required_step_overrides") or {}
        for idx, new_step in overrides.items():
            if 0 <= idx < len(steps):
                steps[idx] = new_step.format(owner=owner)
        if overrides:
            if owner == _GENERIC_OWNER:
                # No name resolved — don't print "(owner: the owner)".
                owner_note = (
                    "This task is marked HUMAN TASK. Final confirmation rests "
                    "with the owner, not you. Your job is to produce a "
                    "recommendation for them to confirm, not to post the "
                    "verdict yourself."
                )
            else:
                owner_note = (
                    f"This task is marked HUMAN TASK (owner: {owner}). Final "
                    f"confirmation rests with {owner}. Your job is to produce a "
                    f"recommendation for them to confirm, not to post the verdict "
                    f"yourself."
                )

    return {
        "action_kind": recipe["kind"],
        "one_line": recipe["one_line"],
        "deliverable": recipe["deliverable"],
        "steps": steps,
        "resources": list(recipe["resources"]),
        "stop_conditions": list(recipe["stop_conditions"]),
        "urgency_summary": _urgency_summary(todo),
        "owner_note": owner_note,
        # A decision/approval always hands back a decision record, even on the
        # deterministic (no-LLM) path; other kinds predict nothing until the LLM fills it.
        "predicted_outputs": _ensure_decision_record(recipe["kind"], []),
        "qa_process": {"target": "", "checks": []},  # LLM fills the verification process
        "next_actions": [],  # LLM fills up to 3 follow-ups to offer
    }


def merge_llm_suggestion(todo: OpsTodo, enhanced: dict[str, Any]) -> dict[str, Any]:
    """Fold an LLM `enhance()` result into the deterministic suggestion so the
    focus card renders through the SAME BLUF `generate_prompt` as every other
    surface — one template, no second prompt-assembler to drift (the lesson from
    the duplicated runbook).

    Start from `build_suggestion` (deterministic: ownership, resources, urgency,
    HTML-clean description) and override only the ticket-specific content the LLM
    is better at: the deliverable (`goal_line`), steps, and stop conditions. When
    the LLM's action_kind differs, re-key `one_line`/`deliverable` defaults off
    the matching recipe so the task line can't read "This is a reply task. Make
    the call…". `summary_paragraph` is carried for the focus-card UI (the copied
    prompt ignores it).

    Robust to a partial/garbage result: each field overrides only when present
    and well-typed; otherwise the deterministic value stands.
    """
    s = build_suggestion(todo)

    kind = (enhanced.get("action_kind") or "").strip().lower()
    if kind:
        s["action_kind"] = kind
        lookup = "reply" if kind == "email" else kind
        recipe = next((r for r in _RECIPES if r["kind"] == lookup), _DEFAULT_RECIPE)
        s["one_line"] = recipe["one_line"]
        s["deliverable"] = recipe["deliverable"]

    goal = (enhanced.get("goal_line") or "").strip()
    if goal:
        s["deliverable"] = goal

    steps = enhanced.get("specific_steps")
    if isinstance(steps, list) and steps:
        s["steps"] = [str(x) for x in steps]

    stops = enhanced.get("stop_conditions")
    if isinstance(stops, list) and stops:
        s["stop_conditions"] = [str(x) for x in stops]

    s["summary_paragraph"] = (enhanced.get("summary_paragraph") or "").strip()
    s["predicted_outputs"] = _ensure_decision_record(
        s["action_kind"], normalize_outputs(enhanced.get("predicted_outputs")))
    s["qa_process"] = normalize_qa(enhanced.get("qa_process"))
    s["next_actions"] = normalize_next_actions(enhanced.get("next_actions"))
    return s


# Structure (a test asserts it): DETAILS first (context, description, steps, QA,
# resources, stop, working protocol), then SUMMARY (the story + what you hand back)
# and DOWNLOADS (the files) at the END, right before the deliver contract — per
# operator preference to lead with the work and recap the summary + files last.
_PROMPT_TEMPLATE = """\
# {title}

## Details
This is a **{action_kind}** task. {one_line}

{ownership_block}**Project:** {project_name} — {project_url}
**List:** {todolist_name} — {list_url}
**Due:** {due_on} · **Urgency:** {urgency_summary}
**BC URL (this task):** {bc_app_url}

{description_block}

{comments_block}### Suggested steps
{steps_block}

{qa_block}### Lean on
{resources_block}

### Stop & escalate if
{stop_block}
{dependency_block}
{working_block}

## Summary
{summary_block}**You hand back:** {deliverable}

## Downloads
{downloads_block}
{next_actions_block}## Deliver, then confirm
- Aim to complete the deliverable in this session; if you have what you need, produce it in one full attempt before coming back with questions.
- Save every file you produce or download into the Downloads folder, and attach it to the Basecamp ticket FROM the Downloads folder. Use that same path every time.
- Every document or file is a REAL EXTERNAL FILE (e.g. .docx, .pdf, .md, .html) created on your machine and saved to the Downloads folder. Do NOT create Basecamp documents: all deliverables are external files attached to the ticket, NEVER authored as Basecamp Docs.
- When the work is done, state your confidence as a percentage (0-100) that the deliverable is complete and correct.
- Then ASK before you post anything to Basecamp, send any outbound message, or mark this todo complete. Do not auto-post, auto-send, or auto-close.

Begin.
"""


class _HTMLToText(HTMLParser):
    """Convert a Basecamp description's HTML to markdown-ish plain text.

    The BC description is stored as HTML (`<div>`, `<p>`, `<strong>`, `<a>`,
    `<li>` …). The workspace WEB page renders it as HTML (`|safe`), but the
    COPIED PROMPT is plain text pasted into Claude Code, where literal
    `<div><strong>…` tags are just noise. This keeps the emphasis/links/lists
    as markdown and drops the tag soup. Only the prompt path uses this; the web
    page is unchanged.
    """

    _BLOCK = {"p", "div", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("strong", "b"):
            self._parts.append("**")
        elif tag in ("em", "i"):
            self._parts.append("*")
        elif tag == "code":
            self._parts.append("`")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag == "a":
            self._href = dict(attrs).get("href")
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("strong", "b"):
            self._parts.append("**")
        elif tag in ("em", "i"):
            self._parts.append("*")
        elif tag == "code":
            self._parts.append("`")
        elif tag == "a":
            if self._href:
                self._parts.append(f" ({self._href})")
            self._href = None
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)


def _html_to_text(html: str) -> str:
    """BC-description HTML → trimmed markdown-ish plain text (entities decoded,
    runs of blank lines collapsed)."""
    parser = _HTMLToText()
    parser.feed(html)
    text = unescape("".join(parser._parts))
    out = "\n".join(line.rstrip() for line in text.splitlines())
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip()


def generate_prompt(
    todo: OpsTodo,
    suggestion: dict[str, Any] | None = None,
    comments: str = "",
    persona: str | None = None,
    outputs_in_prompt: bool = True,
) -> str:
    """A ready-to-paste Claude Code prompt for this specific todo.

    `comments` (optional) is recent BC thread text; when present it renders a
    `## Recent comments` block below the description. The focus card passes it
    so the LLM-enhanced prompt keeps the thread context inline; other surfaces
    omit it and the block is absent.

    `persona` (optional) is the operator's delivery preference id; it swaps the
    `## How I want you to work` block for that persona's guidance (default =
    `copilot`, today's behavior). See `personas.py`.
    """
    s = suggestion or build_suggestion(todo)

    desc_text = _html_to_text(todo.description) if todo.description else ""
    description_block = f"## Description\n{desc_text}" if desc_text else ""
    comments_block = (
        f"## Recent comments\n{comments.strip()}\n\n" if comments and comments.strip() else ""
    )
    steps_block = "\n".join(f"{i+1}. {step}" for i, step in enumerate(s["steps"]))
    resources_block = (
        "\n".join(f"- **{r['kind']}** `{r['name']}` — {r['why']}" for r in s["resources"])
        if s["resources"] else "(none specific — use your default tools)"
    )
    stop_block = "\n".join(f"- {c}" for c in s["stop_conditions"]) if s["stop_conditions"] else "(none)"
    owner_note = s.get("owner_note") or ""
    # Ownership rides in Details, right before the metadata (who owns the call
    # modifies the deliverable). Trailing blank line separates it from the meta.
    ownership_block = f"**Ownership:** {owner_note}\n\n" if owner_note else ""
    # The LLM-enhanced who-needs-what story leads the ## Summary section. Absent on
    # the deterministic fallback (no LLM) — then Summary shows only "You hand back".
    summary = (s.get("summary_paragraph") or "").strip()
    summary_block = f"{summary}\n\n" if summary else ""
    # ## Downloads body: on the workspace (outputs_in_prompt=False) we emit a
    # [[DOWNLOADS]] marker the page fills client-side from the operator's EDITED
    # list; everywhere else the AI-predicted list (+ folder/HTML rules) is baked in.
    downloads_block = (_downloads_block(s.get("predicted_outputs"), s.get("action_kind", ""))
                       if outputs_in_prompt else "[[DOWNLOADS]]\n")
    qa_block = _qa_block(s.get("qa_process"))
    next_actions_block = _next_actions_block(s.get("next_actions"))

    return _PROMPT_TEMPLATE.format(
        title=todo.title,
        summary_block=summary_block,
        downloads_block=downloads_block,
        next_actions_block=next_actions_block,
        qa_block=qa_block,
        project_name=todo.bc_project_name,
        project_url=todo.project_url or "(no URL)",
        todolist_name=todo.bc_todolist_name,
        list_url=todo.list_url or "(no URL)",
        due_on=todo.due_on or "no due date",
        urgency_summary=s["urgency_summary"],
        bc_app_url=todo.bc_app_url or "(no URL)",
        dependency_block=_dependency_block(todo),
        description_block=description_block,
        comments_block=comments_block,
        action_kind=s["action_kind"],
        one_line=s["one_line"],
        deliverable=s["deliverable"],
        steps_block=steps_block,
        resources_block=resources_block,
        stop_block=stop_block,
        ownership_block=ownership_block,
        working_block=personas.working_block(persona),
    )
