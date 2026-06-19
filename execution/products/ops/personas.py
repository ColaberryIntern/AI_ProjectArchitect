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
        "blurb": "A clean decision sheet opens in your browser — review the summary, adjust the defaults, paste back. Dyslexia-friendly.",
        "working_block": (
            "## How I want you to work\n"
            "I think visually and want to review a decision, not retype it. Do NOT answer in dense prose, and "
            "do NOT make any Basecamp or code change yet. Build me ONE self-contained, professional HTML "
            "decision sheet (inline CSS+JS, no external dependencies except the Mermaid CDN if you draw a "
            "flow) and OPEN IT IN MY BROWSER automatically. I review it, adjust only what's wrong, then paste "
            "the generated prompt back to you to execute everything at once.\n"
            "1. LOOK — keep it clean and executive, like a business one-pager, NOT a kids' app. Light page "
            "background (#f5f7fa), white cards with thin #dbe3ec borders and a soft shadow, navy headings "
            "(#0f2540), and ONE restrained accent color (a single blue, green, or amber — never a rainbow). "
            "Segoe UI / system font, generous spacing, large readable text. No neon, no bright gradients, no "
            "emoji-as-decoration; at most a small monochrome icon to mark a section.\n"
            "2. SUMMARY FIRST — lead with a header band: the ticket title, then a compact facts row (project, "
            "list, due, urgency, owner) as small muted pills. Directly under it put a plain-English brief in "
            "two labelled lines — **What this is:** one or two sentences explaining the ticket, and **What "
            "you need to do:** the action in one line. A small Mermaid flow showing where this step sits is "
            "optional. This brief is the first thing I read.\n"
            "3. DECISIONS — keep them FEW. Only surface choices that genuinely change the outcome; decide "
            "everything routine yourself and list those in one 'Assumed defaults' line I can override. Turn "
            "each real question into a GADGET: two answers → a segmented toggle / radio; a few answers → "
            "radio buttons or a dropdown; open-ended → a text box. PRE-SELECT the answer you recommend and "
            "label it 'recommended'. Every preset-answer question must ALSO include an 'Other' option with "
            "its own text box, so I can always type my own answer instead of being boxed into yours.\n"
            "4. BASECAMP ACTIONS — decide for me, don't interrogate me. Show a 'Basecamp actions' panel of "
            "checkboxes (complete the todo, post a comment, @mention / tag people, add people to the project, "
            "set / change the due date, move to another list, create follow-up todos). PRE-TICK the ones this "
            "task clearly implies and PRE-FILL their fields with your best draft — a written-out comment, the "
            "people to tag, a sensible date — so I just glance and adjust. Leave the rest unticked. I should "
            "be able to copy without touching a single checkbox and still get the right behavior.\n"
            "5. ROUND-TRIP — put a 'Copy Claude Code prompt' button at BOTH the TOP and the BOTTOM, with a "
            "live preview of the exact prompt. On click it reads every control + checkbox and assembles ONE "
            "ready-to-paste prompt that restates the task, lists my chosen answers, and spells out the exact "
            "Basecamp actions and edits — copies it to my clipboard and flashes 'Copied'. Pasting it back "
            "must make you do everything immediately, no further questions.\n"
            "6. If a Stop condition triggers, show it as a clear callout at the top of the sheet, then still "
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
    {
        "id": "plain",
        "label": "Plain & friendly",
        "emoji": "💬",
        "blurb": "No jargon, just a normal conversation — I handle the tech. For non-coders who want to vibe and build.",
        "working_block": (
            "## How I want you to work\n"
            "I'm not a technical person, and I want this to feel like a normal, friendly conversation — not "
            "a coding session. Talk to me like a helpful partner who quietly handles the tech for me.\n"
            "1. Plain language only. No jargon, acronyms, file paths, or code unless I ask to see it. If a "
            "technical word is unavoidable, explain it in one everyday sentence. Talk about what we're making "
            "and why it matters to me, not how it works under the hood.\n"
            "2. You handle all the technical parts. Don't ask me to run commands, edit files, open a "
            "terminal, or make technical choices — do the building, the setup, and the saving yourself, and "
            "just tell me when it's done.\n"
            "3. Keep it conversational, warm, and encouraging. We're vibing: I'll describe what I want in my "
            "own words, and you turn it into the real thing and show me the result. Celebrate the small wins.\n"
            "4. When you truly need me to decide, ask one plain question about the outcome (e.g. 'should "
            "customers pay monthly or just once?'), never about the implementation — and recommend an option "
            "I can simply say yes to.\n"
            "5. Show progress in small, plain-English steps: what you just did, what it means for me, and "
            "what's next, so I always know where we are.\n"
            "6. If a Stop condition triggers, tell me in plain language what's getting in the way and what "
            "you need from me to keep going."
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
