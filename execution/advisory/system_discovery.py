"""AI System Discovery Framework — the 9-phase, multiple-choice intake.

This replaces the generic 10-question company profiler (``question_engine``) for
the My-Day "Create a new project" build flow. Instead of profiling the *company*
(size, departments, tools), it profiles the *AI system being proposed* across
nine axes — control, intelligence, data, decision, execution, agents,
governance, strategy, and differentiators.

For a given idea an LLM writes ONE short question per phase, each with three
domain-tailored options labeled A (baseline) / B (intermediate) / C (advanced),
where every option teaches the user what that level looks like for THEIR
project. The user answers at least five; the chosen levels fold into a "refined
idea" that drives the background build.

Generation is fallback-safe: ``generate_discovery_questions`` always returns
exactly nine well-formed questions, synthesizing any the LLM omits or mangles
from the deterministic fallback below, so the flow never breaks when the LLM is
slow, off, or returns junk.
"""

import concurrent.futures
import json
import logging
import threading

logger = logging.getLogger(__name__)

# The 9 phases, in their canonical order. ``levels`` are the A/B/C anchors the
# framework forces a choice between; the LLM tailors them to the user's domain.
PHASES = [
    {"key": "control",         "category": "Control Model",       "axis": "Who makes decisions",
     "levels": {"A": "AI recommends", "B": "AI assists", "C": "AI executes"}},
    {"key": "intelligence",    "category": "Intelligence Depth",  "axis": "How smart",
     "levels": {"A": "Rules-based", "B": "Adaptive", "C": "Self-learning"}},
    {"key": "data",            "category": "Data Scope",          "axis": "What it sees",
     "levels": {"A": "Internal data only", "B": "External signals", "C": "Full ecosystem"}},
    {"key": "decision",        "category": "Decision Complexity", "axis": "Thinking depth",
     "levels": {"A": "Basic", "B": "Multi-variable optimization", "C": "Scenario simulation"}},
    {"key": "execution",       "category": "Execution Level",     "axis": "Action capability",
     "levels": {"A": "Suggest", "B": "Trigger workflows", "C": "Fully automate"}},
    {"key": "agents",          "category": "Agent Structure",     "axis": "How many AI roles",
     "levels": {"A": "Single AI", "B": "Multiple agents", "C": "A full AI org"}},
    {"key": "governance",      "category": "Governance & Trust",  "axis": "Enterprise readiness",
     "levels": {"A": "Basic logs", "B": "Full auditability", "C": "Compliance + explainability"}},
    {"key": "strategy",        "category": "Strategy Layer",      "axis": "Reach",
     "levels": {"A": "Operational only", "B": "Strategic insights", "C": "Long-term planning"}},
    {"key": "differentiators", "category": "Differentiators",     "axis": "Moat",
     "levels": {"A": "None yet", "B": "Simulation / digital twin", "C": "Proprietary models"}},
]

PHASE_KEYS = [p["key"] for p in PHASES]
_PHASE_BY_KEY = {p["key"]: p for p in PHASES}
_LETTERS = ("A", "B", "C")
MIN_ANSWERS = 5
MIN_IDEA_CHARS = 20

# System instruction for the framework. The output is deliberately COMPACT — a
# question plus three short A/B/C descriptions per phase — because the model is
# verbose and a fuller shape (nested options + labels) blows past the token cap
# and truncates. We supply the option labels ourselves (the canonical level
# anchors); the LLM only contributes the domain-specific descriptions.
_SYSTEM_PROMPT = """You write multiple-choice discovery questions for a software-system idea using the AI System Discovery Framework. For each phase provided, write ONE short question tailored to the user's idea, plus three options A (baseline/simplest), B (sensible middle), and C (most advanced). For each option write a SHORT description that teaches what that level looks like for THIS specific project. Output strict JSON only.

RULES:
- Every description MUST reference the user's actual domain — never generic phrasing, never abstract definitions.
- A = simplest/safest, C = most sophisticated, B = a sensible middle.
- Be terse: each question under 14 words; each description ONE phrase, 12 words or fewer. No emojis.
- One entry per phase provided, using the exact phase keys given.

Return strict JSON exactly like this (descriptions only — no labels):
{"questions":[{"phase":"<key>","question":"<short question>","A":"<≤12 words>","B":"<≤12 words>","C":"<≤12 words>"}]}"""

# Deterministic fallback content — generic but on-framework, used per-phase
# whenever the LLM is unavailable or omits/mangles a phase.
_FALLBACK = {
    "control": ("Who should make the decisions in your system?", {
        "A": ("AI recommends", "The AI suggests options and a person makes the final call."),
        "B": ("AI assists", "The AI handles routine decisions and escalates the tricky ones."),
        "C": ("AI executes", "The AI makes and acts on decisions on its own within set limits."),
    }),
    "intelligence": ("How smart should the system be?", {
        "A": ("Rules-based", "It follows fixed rules you define up front."),
        "B": ("Adaptive", "It adjusts to patterns in your data over time."),
        "C": ("Self-learning", "It keeps improving itself from outcomes automatically."),
    }),
    "data": ("What information should it work from?", {
        "A": ("Internal only", "Just your own records and systems."),
        "B": ("External signals", "Your data plus outside sources like market or web signals."),
        "C": ("Full ecosystem", "A connected view across your data, partners, and the wider market."),
    }),
    "decision": ("How deep should its thinking go?", {
        "A": ("Basic", "Straightforward, single-factor decisions."),
        "B": ("Optimization", "Weighs many factors at once to find the best option."),
        "C": ("Simulation", "Models what-if scenarios before deciding."),
    }),
    "execution": ("How much should it actually do?", {
        "A": ("Suggest", "It proposes the next step for a person to carry out."),
        "B": ("Trigger workflows", "It kicks off the right workflows automatically."),
        "C": ("Fully automate", "It completes the work end to end without hand-offs."),
    }),
    "agents": ("How many AI roles should it have?", {
        "A": ("Single AI", "One assistant handling the core job."),
        "B": ("Multiple agents", "A few specialized agents working together."),
        "C": ("Full AI org", "A coordinated team of agents with distinct roles."),
    }),
    "governance": ("How much oversight and trust does it need?", {
        "A": ("Basic logs", "A simple record of what it did."),
        "B": ("Full auditability", "Every action traceable and reviewable."),
        "C": ("Compliance-ready", "Audit trails plus clear reasons for each decision, ready for regulators."),
    }),
    "strategy": ("How far should its thinking reach?", {
        "A": ("Operational", "Focused on day-to-day execution."),
        "B": ("Strategic insights", "Surfaces insights that inform bigger decisions."),
        "C": ("Long-term planning", "Helps plan months and years ahead."),
    }),
    "differentiators": ("What will set your system apart?", {
        "A": ("None yet", "Start simple; build an edge later."),
        "B": ("Digital twin", "A working model of your business to test against."),
        "C": ("Proprietary models", "Custom models trained on your unique data as a moat."),
    }),
}


def generate_discovery_questions(idea: str) -> list[dict]:
    """Return exactly 9 discovery questions for ``idea``.

    LLM-tailored per phase where possible; any phase the model omits or mangles
    is filled from the deterministic fallback, so the result is always 9
    well-formed questions in canonical phase order.
    """
    raw = None
    idea_s = (idea or "").strip()
    if len(idea_s) >= MIN_IDEA_CHARS:
        raw = _generate_raw(idea_s)
    return _coerce_questions(raw, idea_s)


# Phases per LLM call. With the COMPACT output shape each call is small and
# fast, so fanning the 9 phases into a few concurrent calls cuts wall-clock to
# ~6-7s (vs ~11s for one call) with no truncation. _coerce_questions re-imposes
# the canonical order, so batch/return order does not matter.
_BATCH_SIZE = 3

# Safety ceiling so a single stuck call can't hang the page; any phase whose
# batch misses the deadline falls back. Compact batches finish well under this.
_GEN_TIMEOUT_SECONDS = 10.0


def _generate_raw(idea: str):
    """Generate all phases via concurrent compact batched LLM calls.

    Returns ``{"questions": [...]}`` merged from every batch that finished in
    time, or None if the LLM is unavailable / nothing finished (callers then
    fall back).
    """
    try:
        from execution.llm_client import is_available
        if not is_available():
            return None
    except Exception:
        return None

    batches = [PHASES[i:i + _BATCH_SIZE] for i in range(0, len(PHASES), _BATCH_SIZE)]
    questions: list = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(batches))
    try:
        futures = [pool.submit(_generate_batch, idea, batch) for batch in batches]
        done, _pending = concurrent.futures.wait(futures, timeout=_GEN_TIMEOUT_SECONDS)
        for future in done:
            try:
                questions.extend(future.result() or [])
            except Exception:
                pass
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[SystemDiscovery] batched generation failed: {e}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return {"questions": questions} if questions else None


def _generate_batch(idea: str, phases_subset: list[dict]) -> list:
    """One compact strict-JSON LLM call for a subset of phases; [] on any issue.

    Retries once on a transient failure (empty/parse error) — compact calls fail
    fast and the other batches run concurrently, so the retry rarely extends
    wall-clock but markedly cuts the chance a batch's phases fall back.
    """
    from execution.llm_client import chat

    spec = [{"phase": p["key"], "category": p["category"], "axis": p["axis"], "levels": p["levels"]}
            for p in phases_subset]
    user_message = (
        f"USER'S IDEA:\n{idea[:800]}\n\n"
        f"PHASES (write one question each, in this order):\n{json.dumps(spec, indent=2)}"
    )
    for attempt in range(2):
        try:
            response = chat(
                system_prompt=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=900,
                temperature=0.6,
                response_format={"type": "json_object"},
            )
            raw = json.loads(response.content)
            items = raw.get("questions") if isinstance(raw, dict) else None
            if isinstance(items, list) and items:
                return items
        except Exception as e:  # pragma: no cover - exercised via monkeypatch
            logger.warning(f"[SystemDiscovery] batch attempt {attempt + 1} failed: {e}")
    return []


def _coerce_questions(raw, idea: str) -> list[dict]:
    """Merge LLM output with the fallback into exactly 9 ordered questions."""
    by_phase: dict = {}
    if isinstance(raw, dict) and isinstance(raw.get("questions"), list):
        for item in raw["questions"]:
            if isinstance(item, dict) and item.get("phase") in _PHASE_BY_KEY:
                by_phase.setdefault(item["phase"], item)

    out = []
    for phase in PHASES:
        cleaned = _clean_question(by_phase.get(phase["key"]), phase)
        out.append(cleaned or _fallback_question(phase))
    return out


def _clean_question(item, phase: dict) -> dict | None:
    """Build one phase's question from the compact LLM shape.

    Expects ``{"question": str, "A": str, "B": str, "C": str}``. Option labels
    come from the canonical level anchors; descriptions come from the LLM, with
    any missing/blank one filled from the fallback. Returns None only when the
    question is unusable or the LLM contributed nothing — the phase then falls
    back entirely.
    """
    if not isinstance(item, dict):
        return None
    question = item.get("question")
    if not isinstance(question, str) or not question.strip():
        return None

    fb_levels = _FALLBACK[phase["key"]][1]
    options = []
    have_llm = False
    for letter in _LETTERS:
        desc = item.get(letter)
        if isinstance(desc, str) and desc.strip():
            description = desc.strip()[:240]
            have_llm = True
        else:
            description = fb_levels[letter][1]
        options.append({"letter": letter, "label": phase["levels"][letter], "description": description})

    if not have_llm:
        return None

    return {
        "phase": phase["key"],
        "category": phase["category"],
        "axis": phase["axis"],
        "question": question.strip()[:160],
        "options": options,
    }


def _fallback_question(phase: dict) -> dict:
    question, levels = _FALLBACK[phase["key"]]
    return {
        "phase": phase["key"],
        "category": phase["category"],
        "axis": phase["axis"],
        "question": question,
        "options": [
            {"letter": letter, "label": phase["levels"][letter], "description": levels[letter][1]}
            for letter in _LETTERS
        ],
    }


def refine_idea(idea: str, answers: dict, questions: list[dict] | None = None) -> str:
    """Fold the chosen A/B/C levels into a refined idea string for the build.

    ``answers`` maps phase key -> chosen letter. ``questions`` (the generated
    set) supplies the domain-specific label/description for each choice; missing
    entries fall back to the canonical level anchors.
    """
    qby = {q.get("phase"): q for q in (questions or [])}
    lines = [f"Original idea: {(idea or '').strip()}", "", "Desired AI-system profile:"]
    for phase in PHASES:
        letter = (answers or {}).get(phase["key"])
        if letter not in _LETTERS:
            continue
        q = qby.get(phase["key"], {})
        opt = next((o for o in q.get("options", []) if o.get("letter") == letter), None)
        label = (opt or {}).get("label") or phase["levels"][letter]
        description = (opt or {}).get("description") or ""
        line = f"- {phase['category']}: {label}"
        if description:
            line += f" — {description}"
        lines.append(line)
    return "\n".join(lines)


# ─── Background generation (instant start; page reveals when ready) ──────────

def _run_discovery(session_id: str, idea: str) -> None:
    """Generate the questions, then persist them and flip the status to ready.

    Generation (the slow part) runs first; the session is reloaded only
    immediately before writing, so a concurrent save is never clobbered.
    Always marks "ready" (fallback questions are still a complete set).
    """
    questions = []
    try:
        questions = generate_discovery_questions(idea)
    except Exception as e:  # pragma: no cover - generate is already guarded
        logger.warning(f"[SystemDiscovery] background generation failed: {e}")
        questions = _coerce_questions(None, (idea or "").strip())

    try:
        from execution.advisory.advisory_state_manager import load_session, save_session
        session = load_session(session_id)
        session["discovery_questions"] = questions
        session["discovery_status"] = "ready"
        save_session(session)
    except Exception as e:  # pragma: no cover - session may be gone (e.g. test teardown)
        logger.warning(f"[SystemDiscovery] could not persist questions for {session_id}: {e}")


def kick_discovery(session_id: str, idea: str) -> None:
    """Run ``_run_discovery`` in a daemon thread so the request returns instantly."""
    threading.Thread(
        target=_run_discovery,
        args=(session_id, idea),
        name=f"disc-{session_id[:8]}",
        daemon=True,
    ).start()
