"""AI System Discovery — a sequential, adaptive, functionality-focused intake.

Used by the My-Day "Create a new project" build flow. It walks the user through
nine design dimensions ONE QUESTION AT A TIME. Each question is generated live
from the user's idea AND their previous answers, so the design keeps improving
as they go. Every question and option is concrete to the user's actual product —
no generic framework talk — and the three options are real capabilities the
product could gain, ordered by increasing functionality (A = simplest useful,
C = most powerful).

The chosen capabilities fold into a "refined idea" that drives the build.

Generation is fallback-safe: ``generate_question`` always returns a well-formed
question (synthesizing from a per-dimension fallback if the LLM is unavailable
or returns junk), so the wizard never breaks.
"""

import concurrent.futures
import json
import logging

logger = logging.getLogger(__name__)

# The nine design dimensions, in order. Each is presented as a CONCRETE
# functionality question for the user's product. ``focus`` tells the model what
# kind of capability to ask about for that dimension; ``fallback`` is a safety
# net used only when the LLM is unavailable.
PHASES = [
    {
        "key": "control", "label": "Control & autonomy",
        "focus": "how much the product decides and acts on its own versus asking the user — options are concrete auto-decision features",
        "fallback": ("How much should the product decide on its own?", [
            ("Recommend only", "It suggests actions; the user approves each one."),
            ("Act with guardrails", "It handles routine actions and asks only on the risky ones."),
            ("Run autonomously", "It decides and acts on its own within limits you set."),
        ]),
    },
    {
        "key": "intelligence", "label": "Smarts & personalization",
        "focus": "how smart and personalized the product's suggestions are — options are concrete intelligent features",
        "fallback": ("How personalized should its suggestions be?", [
            ("Same for everyone", "It applies one consistent set of rules."),
            ("Learns preferences", "It adapts to each user's history and patterns."),
            ("Predicts needs", "It anticipates what each user wants before they ask."),
        ]),
    },
    {
        "key": "data", "label": "Data & integrations",
        "focus": "what data and outside systems the product taps into — options are concrete data/integration features",
        "fallback": ("What information should it work from?", [
            ("Your own data", "Only the records you enter and store."),
            ("Connected tools", "Pulls live data from the tools you already use."),
            ("Outside signals too", "Adds external sources like market, web, or partner data."),
        ]),
    },
    {
        "key": "decision", "label": "Decision power",
        "focus": "how advanced the product's logic and optimization is — options are concrete optimization features",
        "fallback": ("How advanced should its decision-making be?", [
            ("Simple rules", "Clear if-this-then-that logic."),
            ("Optimizes tradeoffs", "Weighs many factors to pick the best option."),
            ("Simulates scenarios", "Models what-if outcomes before deciding."),
        ]),
    },
    {
        "key": "execution", "label": "Automation & actions",
        "focus": "what the product does automatically, end to end — options are concrete automation features",
        "fallback": ("How much should it do for the user automatically?", [
            ("Prepares the work", "It drafts and teees up the next step for a person."),
            ("Triggers workflows", "It kicks off the right follow-up actions automatically."),
            ("Completes end to end", "It finishes the task with no hand-offs."),
        ]),
    },
    {
        "key": "agents", "label": "Assistants & roles",
        "focus": "how many specialized assistants or roles the product runs — options are concrete multi-assistant features",
        "fallback": ("How many specialized assistants should it run?", [
            ("One assistant", "A single helper covering the core job."),
            ("A few specialists", "Several assistants each owning one area."),
            ("A coordinated team", "Multiple assistants that hand work to each other."),
        ]),
    },
    {
        "key": "governance", "label": "Trust, safety & audit",
        "focus": "what trust, safety, and audit features the product has — options are concrete safeguard features",
        "fallback": ("What oversight should be built in?", [
            ("Activity log", "A simple record of what it did."),
            ("Full audit trail", "Every action traceable and reviewable."),
            ("Compliance-ready", "Audit trails plus clear reasons for each decision."),
        ]),
    },
    {
        "key": "strategy", "label": "Insights & planning",
        "focus": "what analytics and planning the product offers beyond day-to-day work — options are concrete insight features",
        "fallback": ("What insights should it surface?", [
            ("Day-to-day only", "Focused on getting today's work done."),
            ("Trends & reports", "Surfaces patterns that inform decisions."),
            ("Forward planning", "Helps plan weeks and months ahead."),
        ]),
    },
    {
        "key": "differentiators", "label": "Standout features",
        "focus": "what unique or advanced features set the product apart — options are concrete moat features",
        "fallback": ("What should make it stand out?", [
            ("Solid basics", "Do the core job really well."),
            ("A signature feature", "One standout capability competitors lack."),
            ("A defensible moat", "Proprietary data or models others can't copy."),
        ]),
    },
]

PHASE_KEYS = [p["key"] for p in PHASES]
_PHASE_BY_KEY = {p["key"]: p for p in PHASES}
_LETTERS = ("A", "B", "C")
TOTAL_QUESTIONS = len(PHASES)
MIN_IDEA_CHARS = 20

# Bound a single question's generation so a slow call can't stall the wizard.
_GEN_TIMEOUT_SECONDS = 7.0

_SYSTEM_PROMPT = """You help a non-technical founder design their software product by asking ONE highly specific multiple-choice question that improves the design by adding functionality along a given dimension.

Hard rules:
- The question and ALL three options must be concrete to THIS product. Never generic, never abstract framework language, never restate the dimension name.
- Each option is a real capability the product could have, phrased as a feature. Order them by INCREASING functionality: A = simplest useful version, B = a meaningful step up, C = the most powerful version.
- Build on the user's previous choices so the design keeps improving and stays coherent — don't repeat capabilities they already chose.
- Be terse and plain-language (no jargon, no emojis). Question under 16 words. Each label 2-5 words. Each description ONE concrete sentence, max ~16 words.

Output strict JSON only:
{"question":"<specific question>","options":[{"label":"<short>","description":"<one concrete sentence>"},{"label":"...","description":"..."},{"label":"...","description":"..."}]}"""


def generate_question(idea: str, phase: dict, prior_answers: list | None = None) -> dict:
    """Generate one adaptive, product-specific question for ``phase``.

    ``prior_answers`` is the list of already-chosen options (see refine/state),
    used to make the next question build on what came before. Always returns a
    well-formed question dict; falls back to the dimension's safety-net question
    when the LLM is unavailable or returns junk.
    """
    raw = None
    idea_s = (idea or "").strip()
    if len(idea_s) >= MIN_IDEA_CHARS:
        raw = _generate_raw(idea_s, phase, prior_answers or [])
    return _clean_question(raw, phase) or _fallback_question(phase)


def _generate_raw(idea: str, phase: dict, prior_answers: list):
    """One bounded strict-JSON LLM call for a single phase; parsed dict or None."""
    try:
        from execution.llm_client import is_available
        if not is_available():
            return None
    except Exception:
        return None

    prior_lines = []
    for a in prior_answers:
        choice = a.get("choice") or {}
        label = choice.get("label")
        if a.get("label") and label:
            prior_lines.append(f"- {a['label']}: {label} — {choice.get('description', '')}".rstrip(" —"))
    prior_block = "\n".join(prior_lines) if prior_lines else "(none yet — this is the first question)"

    user_message = (
        f"PRODUCT IDEA:\n{idea[:700]}\n\n"
        f"DIMENSION TO ASK ABOUT — {phase['label']}:\n{phase['focus']}\n\n"
        f"WHAT THEY'VE ALREADY CHOSEN (build on these, don't repeat):\n{prior_block}\n\n"
        f"Write the one question for the '{phase['label']}' dimension now."
    )

    def _call():
        from execution.llm_client import chat
        response = chat(
            system_prompt=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=500,
            temperature=0.6,
            response_format={"type": "json_object"},
        )
        return json.loads(response.content)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(_call).result(timeout=_GEN_TIMEOUT_SECONDS)
    except Exception as e:  # pragma: no cover - exercised via monkeypatch
        logger.warning(f"[SystemDiscovery] question generation failed for {phase['key']}: {e}")
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _clean_question(raw, phase: dict) -> dict | None:
    """Validate the compact LLM shape into a full question dict, or None."""
    if not isinstance(raw, dict):
        return None
    question = raw.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    raw_options = raw.get("options")
    if not isinstance(raw_options, list) or len(raw_options) < 3:
        return None

    options = []
    for idx, letter in enumerate(_LETTERS):
        opt = raw_options[idx]
        if not isinstance(opt, dict):
            return None
        label = opt.get("label")
        description = opt.get("description")
        if not isinstance(description, str) or not description.strip():
            return None
        if not isinstance(label, str) or not label.strip():
            label = f"Option {letter}"
        options.append({
            "letter": letter,
            "label": label.strip()[:60],
            "description": description.strip()[:220],
        })

    return {
        "phase": phase["key"],
        "label": phase["label"],
        "question": question.strip()[:160],
        "options": options,
    }


def _fallback_question(phase: dict) -> dict:
    question, opts = phase["fallback"]
    return {
        "phase": phase["key"],
        "label": phase["label"],
        "question": question,
        "options": [
            {"letter": letter, "label": opts[i][0], "description": opts[i][1]}
            for i, letter in enumerate(_LETTERS)
        ],
    }


def refine_idea(idea: str, answers: list) -> str:
    """Fold the chosen capabilities into a refined idea string for the build.

    ``answers`` is the ordered list of recorded answers, each
    ``{"label": <dimension>, "choice": {"label", "description"}}``.
    """
    lines = [f"Original idea: {(idea or '').strip()}", "", "Chosen capabilities (the product should include):"]
    for a in answers or []:
        choice = a.get("choice") or {}
        label = choice.get("label")
        if not label:
            continue
        line = f"- {a.get('label', '')}: {label}"
        desc = choice.get("description")
        if desc:
            line += f" — {desc}"
        lines.append(line)
    return "\n".join(lines)
