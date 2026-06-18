"""Per-operator prompt delivery personas.

The "How I want you to work" block of every copied Claude Code prompt is
swapped for the operator's chosen persona, so the SAME task is delivered in
the format that operator processes best — paced, terse, visual, explanatory,
or as a checklist. Accessibility-first: e.g. a dyslexic operator picks
"Visual-first" and gets diagrams auto-opened in the browser instead of prose.

The choice is stored on `tenancy.User.prompt_persona` (server-side, per
operator) and selected on the My Day workspace page; it then applies to every
prompt that operator copies, on every surface and device, until they change it.

Deterministic + testable; no LLM. Default = `copilot` (today's behavior), so an
operator who never picks sees no change.
"""
from __future__ import annotations

DEFAULT_PERSONA = "copilot"

# Each persona: id, label, emoji, blurb (one line for the selector), and the
# working_block — the full "## How I want you to work" markdown section that
# replaces the template default. Keep the header line identical across personas
# so the prompt structure is stable; only the guidance under it changes.
PERSONAS: list[dict] = [
    {
        "id": "copilot",
        "label": "Co-pilot (paced)",
        "emoji": "🧭",
        "blurb": "Step by step, confirms before each move. When you want control.",
        "working_block": (
            "## How I want you to work\n"
            "1. Confirm you understand the task in your own words (one line).\n"
            "2. Walk me through step 1 above, doing the work as you go (don't just narrate).\n"
            "3. After each step, ask if I want to continue or adjust before moving to the next.\n"
            "4. Use the Resources listed above; check what's already in the repo before writing new code.\n"
            "5. If a Stop condition triggers, stop and tell me what you found."
        ),
    },
    {
        "id": "answer",
        "label": "Just the answer",
        "emoji": "⚡",
        "blurb": "BLUF, terse, decision first. When you're short on time.",
        "working_block": (
            "## How I want you to work\n"
            "1. Lead with the answer or deliverable in the first 3 lines. No preamble, no narrating your process.\n"
            "2. Bullets over paragraphs. Put any supporting detail below a `---` divider I can skip.\n"
            "3. Do the work; don't ask me to confirm each step.\n"
            "4. Batch every question you have for me into ONE list at the end, not scattered through.\n"
            "5. If a Stop condition triggers, say so in one line and stop."
        ),
    },
    {
        "id": "visual",
        "label": "Visual-first",
        "emoji": "📊",
        "blurb": "Interactive decision sheet in your browser — tap options, click, paste back. Dyslexia-friendly.",
        "working_block": (
            "## How I want you to work\n"
            "I am a visual thinker (dyslexia-friendly). Do NOT answer in dense prose, and do NOT make any "
            "Basecamp or code change yet. Instead, build me an interactive decision sheet I can fill in, "
            "then I paste a generated prompt back to you to execute everything at once.\n"
            "1. 🧩 Generate ONE self-contained HTML file (inline CSS+JS, no external dependencies) and OPEN IT "
            "IN MY BROWSER automatically. Lead with a short visual summary of this task — a Mermaid-style "
            "flow or an emoji/icon header with the key facts (project, list, due, urgency) — then the controls. "
            "Make it colorful, high-contrast, big tap targets, emoji-labeled, lots of pictures/icons. 🎨\n"
            "2. 🎛️ Turn every open question into a GADGET, not a paragraph: exactly two answers → a radio "
            "toggle / segmented selection; a few answers → radio buttons or a dropdown; anything open-ended → "
            "a text box. Pre-select the safe default and mark it ⭐. Every decision you'd otherwise ask me in "
            "prose becomes one control.\n"
            "3. ☑️ Add a 'Basecamp actions' panel of CHECKBOXES for the moves this task may need — ✅ complete "
            "the todo, 💬 post a comment, 🙋 @mention/tag people, ➕ add people to the project, 📅 set/change "
            "the due date, 📂 move to another list, 🆕 create follow-up todos — each box paired with the field "
            "it needs (who to tag, comment text, date…). Tick the ones this task implies; leave the rest for me.\n"
            "4. 📋 Put a 'Copy Claude Code prompt' button at BOTH the TOP and the BOTTOM. On click it reads "
            "every control + checkbox and assembles ONE ready-to-paste prompt that restates the task, lists my "
            "chosen answers, and spells out the exact Basecamp actions and edits to make — copies it to my "
            "clipboard and flashes 'Copied!'. Show a live preview of that prompt too. Pasting it back must make "
            "you do everything immediately, no further questions.\n"
            "5. 🛑 If a Stop condition triggers, show it as a red callout at the top of the sheet, then still "
            "build the rest."
        ),
    },
    {
        "id": "explain",
        "label": "Explain it to me",
        "emoji": "🎓",
        "blurb": "Full reasoning, trade-offs, the why. When you want depth.",
        "working_block": (
            "## How I want you to work\n"
            "1. Explain your reasoning, not just the conclusion: the why behind the recommendation.\n"
            "2. Show the trade-offs you weighed and what you ruled out, and why.\n"
            "3. Define any term or acronym the first time you use it; a short analogy is welcome where it helps.\n"
            "4. Thoroughness over brevity. Walk me through it as if teaching.\n"
            "5. If a Stop condition triggers, explain what tripped it and what you'd need to proceed."
        ),
    },
    {
        "id": "checklist",
        "label": "Checklist doer",
        "emoji": "✅",
        "blurb": "Numbered actions plus copy-paste-ready commands. Minimal prose.",
        "working_block": (
            "## How I want you to work\n"
            "1. Give me a numbered checklist of concrete actions, each one I can check off.\n"
            "2. Include exact, copy-paste-ready commands, text, and file paths. No 'figure out X'.\n"
            "3. Minimal explanation: just what to do, in order.\n"
            "4. Tag anything that needs my decision with `[DECISION]` so I can spot it fast.\n"
            "5. If a Stop condition triggers, add a `[STOP]` line at that point in the checklist."
        ),
    },
]

_BY_ID = {p["id"]: p for p in PERSONAS}


def get(persona_id: str | None) -> dict:
    """Return the persona dict for an id, falling back to the default."""
    return _BY_ID.get((persona_id or "").strip(), _BY_ID[DEFAULT_PERSONA])


def working_block(persona_id: str | None) -> str:
    """The '## How I want you to work' section for this persona (or default)."""
    return get(persona_id)["working_block"]


def is_valid(persona_id: str | None) -> bool:
    return (persona_id or "").strip() in _BY_ID
