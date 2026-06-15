"""Per-todo action recipe + Claude Code-ready prompt.

The action recipe is keyed on simple title/description regex — deterministic,
no LLM call. Phase 2+ can replace this with an LLM-derived recipe; the
deterministic version stays as a fallback.

Returned shape:
    {
      "action_kind":  "reply" | "decision" | "meeting" | "research" |
                     "build" | "review" | "schedule" | "default",
      "one_line":     short imperative summary,
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
from typing import Any

from .store import OpsTodo

# ── Action recipes (regex-keyed, ordered most-specific-first) ──────────

_RECIPES = [
    {
        "kind": "decision",
        "match": re.compile(r"\b(approve|decide|sign[- ]?off|confirm|reject|accept|deny|go/no[- ]go)\b", re.I),
        "one_line": "Make the call: approve, request changes, or reject.",
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
    return "the owner"


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
            owner_note = (
                f"This task is marked HUMAN TASK (owner: {owner}). Final "
                f"confirmation rests with {owner}. Your job is to produce a "
                f"recommendation for them to confirm, not to post the verdict "
                f"yourself."
            )

    return {
        "action_kind": recipe["kind"],
        "one_line": recipe["one_line"],
        "steps": steps,
        "resources": list(recipe["resources"]),
        "stop_conditions": list(recipe["stop_conditions"]),
        "urgency_summary": _urgency_summary(todo),
        "owner_note": owner_note,
    }


_PROMPT_TEMPLATE = """\
You're helping me work through this Basecamp task. Context first, then the recipe I want you to follow.

## Task
**Title:** {title}
**Project:** {project_name} — {project_url}
**List:** {todolist_name} — {list_url}
**Due:** {due_on}
**Urgency:** {urgency_summary}
**BC URL (this task):** {bc_app_url}
{dependency_block}
{description_block}

## Action kind
**{action_kind}** — {one_line}

## Steps
{steps_block}

## Resources to lean on
{resources_block}

## When to stop and escalate
{stop_block}

## What I want from you
1. Confirm you understand the task in your own words (one line).
2. Walk me through step 1 of the recipe above, doing the work as you go (don't just narrate).
3. After each step, ask if I want to continue or adjust before moving to the next.
4. Use the Resources listed above; check what's already in the repo before writing new code.
5. If a Stop condition triggers, stop and tell me what you found.
{owner_note_block}
Begin.
"""


def generate_prompt(todo: OpsTodo, suggestion: dict[str, Any] | None = None) -> str:
    """A ready-to-paste Claude Code prompt for this specific todo."""
    s = suggestion or build_suggestion(todo)

    description_block = (
        f"## Description\n{todo.description.strip()}" if todo.description else ""
    )
    steps_block = "\n".join(f"{i+1}. {step}" for i, step in enumerate(s["steps"]))
    resources_block = (
        "\n".join(f"- **{r['kind']}** `{r['name']}` — {r['why']}" for r in s["resources"])
        if s["resources"] else "(none specific — use your default tools)"
    )
    stop_block = "\n".join(f"- {c}" for c in s["stop_conditions"]) if s["stop_conditions"] else "(none)"
    owner_note = s.get("owner_note") or ""
    owner_note_block = f"\n## Ownership\n{owner_note}\n" if owner_note else ""

    return _PROMPT_TEMPLATE.format(
        title=todo.title,
        project_name=todo.bc_project_name,
        project_url=todo.project_url or "(no URL)",
        todolist_name=todo.bc_todolist_name,
        list_url=todo.list_url or "(no URL)",
        due_on=todo.due_on or "no due date",
        urgency_summary=s["urgency_summary"],
        bc_app_url=todo.bc_app_url or "(no URL)",
        dependency_block=_dependency_block(todo),
        description_block=description_block,
        action_kind=s["action_kind"],
        one_line=s["one_line"],
        steps_block=steps_block,
        resources_block=resources_block,
        stop_block=stop_block,
        owner_note_block=owner_note_block,
    )
