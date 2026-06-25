"""Turn a project idea into a plain-language, business-process build plan.

For a non-technical audience: the plan is organized into **7-10 business
processes** — the real-world things the product does for people (e.g. "Getting a
Quote to a Customer"), NOT technical components or document chapters. Under each
process are many concrete, everyday-language tasks (the depth a real build
needs), each carrying a phase (BUILD / BREAK / HARDEN), an AI/human ``kind``, an
"acceptance" written in plain terms, and 2-4 sub-steps.

Two stages, both LLM with deterministic fallbacks:
  1. ``generate_business_processes`` — the 7-10 plain-language process groups.
  2. ``generate_process_tasks`` — the detailed tasks within one process.
``generate_process_plan`` runs both and returns the full plan.

Failure-First is structural: every process is guaranteed ≥1 BUILD and ≥1 BREAK
task, so ``project_plan.validate_plan`` rule 7 holds and the build proceeds.
"""
from __future__ import annotations

import json
import logging

from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

_VALID_PHASES = {"BUILD", "BREAK", "HARDEN"}
_MAX_PROCESSES = 10
_MIN_PROCESSES = 7
_MAX_PTASKS = 24

# ── prompts ─────────────────────────────────────────────────────────

_PROC_SYSTEM = (
    "You organize a software project into 7 to 10 BUSINESS PROCESSES that a person with NO "
    "technical background understands instantly — the real-world things the product does for "
    "people, NOT technical components, layers, or document sections. Use plain, everyday "
    "language; no jargon, no acronyms. Good examples: 'Getting a Quote to a Customer', "
    "'Finding & Booking a Carrier', 'Keeping Customers Updated', 'Handling Problems & Delays', "
    "'Getting Paid', 'Reports & Insights', 'Keeping Information Safe', 'Getting Everyone Set Up'. "
    "Each process has a plain 'name' and one plain sentence 'description' of what it does for the "
    "user. Return STRICT JSON only."
)
_PROC_USER = (
    "Project idea:\n{idea}\n\nContext areas (optional, for inspiration only):\n{areas}\n\n"
    'Return JSON exactly: {{"processes": [{{"name": "<plain name>", "description": "<one plain sentence>"}}]}}'
)

_PTASK_SYSTEM = (
    "You list the concrete tasks to deliver ONE business process of a software product, written "
    "for a SMALL-BUSINESS OWNER with ZERO technical background. Plain, everyday language only — "
    "say WHAT it does for people and WHY it matters, never how it's coded. "
    "NEVER use words like: API, endpoint, backend, frontend, database, schema, query, "
    "authentication, encryption, token, deploy, integration, webhook, log, cache, server, SDK. "
    "If something technical is unavoidable, say it in everyday words (e.g. 'a secure sign-in' not "
    "'authentication'; 'keep the data private' not 'encryption'). Each 'title' should read like a "
    "real step the owner would recognize, starting with an everyday action word. "
    "Produce 12 to 20 tasks covering building each part, handling things that go wrong, and "
    "making it safe & reliable. At least 3 must be BREAK tasks, and each BREAK task handles a "
    "SPECIFIC problem — something missing, invalid, unavailable, late, duplicated, or a human "
    "mistake — and says what the system does so it doesn't break. A BREAK task is NEVER a review, "
    "feedback, or a test. "
    "Each task: 'title'; 'phase' = BUILD (make it work) | BREAK (handle a specific problem) | "
    "HARDEN (make it safe & reliable); 'kind' = 'ai' (the AI assistant does it on its own) or "
    "'human' (you or your team must decide something or provide access/content); 'acceptance' = a "
    "specific, OBSERVABLE result a non-technical person can see and check — include a concrete "
    "signal like a screen, a message, or a number (e.g. 'The customer sees a price within a few "
    "seconds, with the carrier's name' — NEVER 'it works' or 'it's done'); 'steps' = 2 to 4 plain "
    "sub-steps. Do NOT pad with generic 'train staff', 'ensure privacy', or 'test regularly' "
    "tasks unless genuinely specific here. Only include work UNIQUE to this process. STRICT JSON."
)
_PTASK_USER = (
    "Project idea:\n{idea}\n\nBusiness process: {name}\nWhat it does for the user: {desc}\n\n"
    "Other processes (they cover their OWN work — do not duplicate theirs):\n{others}\n\n"
    'Return JSON exactly: {{"tasks": [{{"title": "<plain action>", "phase": "BUILD|BREAK|HARDEN", '
    '"kind": "ai|human", "acceptance": "<plain, observable, how you know it is done>", "steps": ["<plain step>"]}}]}}'
)


# ── normalization + fallbacks ───────────────────────────────────────

def _norm_task(t: dict) -> dict | None:
    title = (t.get("title") or "").strip()
    if not title:
        return None
    phase = (t.get("phase") or "BUILD").upper()
    if phase not in _VALID_PHASES:
        phase = "BUILD"
    kind = "human" if (t.get("kind") or "").lower() == "human" else "ai"
    acceptance = (t.get("acceptance") or "").strip() or f"{title} is done and working."
    steps = [str(s).strip() for s in (t.get("steps") or []) if str(s).strip()][:5]
    return {"title": title, "phase": phase, "kind": kind, "acceptance": acceptance, "steps": steps}


def _ensure_build_break(tasks: list[dict], process_name: str) -> list[dict]:
    phases = {t["phase"] for t in tasks}
    if "BUILD" not in phases:
        tasks.insert(0, {"title": f"Build the core of {process_name}", "phase": "BUILD", "kind": "ai",
                         "acceptance": f"The main part of {process_name} works end to end.",
                         "steps": ["Plan it", "Build it", "Try it out"]})
    if "BREAK" not in phases:
        tasks.append({"title": f"Make sure {process_name} handles mistakes", "phase": "BREAK", "kind": "ai",
                      "acceptance": f"When something is wrong or missing, {process_name} shows a clear, helpful message instead of breaking.",
                      "steps": ["List what could go wrong", "Handle each gracefully", "Test the problem cases"]})
    return tasks


_FALLBACK_PROCESSES = [
    ("Getting Everyone Set Up", "Create accounts, roles, and the basics so people can start using it."),
    ("The Main Thing It Does", "The core job the product performs for its users, day to day."),
    ("Keeping People Informed", "Notifications, updates, and messages so users always know what's happening."),
    ("Handling Problems & Mistakes", "What happens when something goes wrong, so nothing falls through the cracks."),
    ("Reports & Insights", "Clear views and summaries so people can see how things are going."),
    ("Connecting to Other Tools", "Linking the product to the other systems and services people already use."),
    ("Keeping Information Safe", "Protecting data and access so the product is secure and trustworthy."),
    ("Launching & Handing Off", "Final checks, training, and going live with confidence."),
]


def _fallback_process_tasks(name: str) -> list[dict]:
    return _ensure_build_break([
        {"title": f"Build the core of {name}", "phase": "BUILD", "kind": "ai",
         "acceptance": f"{name} works end to end for a normal user.",
         "steps": ["Plan it", "Build it", "Try it out"]},
        {"title": f"Make sure {name} handles mistakes and problems", "phase": "BREAK", "kind": "ai",
         "acceptance": f"Bad or missing input in {name} shows a clear, helpful message instead of breaking.",
         "steps": ["List what could go wrong", "Handle each gracefully", "Test the problem cases"]},
        {"title": f"Make {name} safe and reliable", "phase": "HARDEN", "kind": "ai",
         "acceptance": f"{name} is secure and keeps working under heavy use.",
         "steps": ["Add safeguards", "Test under load", "Review for security"]},
    ], name)


# ── generators ──────────────────────────────────────────────────────

def generate_business_processes(idea: str, areas: list[str] | None = None) -> list[dict]:
    """Return 7-10 plain-language business processes [{name, description}]."""
    procs: list[dict] = []
    if is_available() and idea:
        try:
            resp = chat(
                system_prompt=_PROC_SYSTEM,
                messages=[{"role": "user", "content": _PROC_USER.format(
                    idea=(idea or "")[:3000],
                    areas="\n".join(f"- {a}" for a in (areas or [])[:25]) or "(none)")}],
                temperature=0.4, max_tokens=2000,
                response_format={"type": "json_object"},
            )
            for p in (json.loads(resp.content).get("processes") or [])[:_MAX_PROCESSES]:
                name = (p.get("name") or "").strip()
                if name:
                    procs.append({"name": name, "description": (p.get("description") or "").strip()})
        except (LLMUnavailableError, LLMClientError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.warning("business-process generation failed: %s", e)
        except Exception:  # noqa: BLE001
            logger.warning("business-process generation unexpected error", exc_info=True)
    if len(procs) < _MIN_PROCESSES:
        # top up from the fallback set (deduped by name) to reach a usable count
        have = {p["name"].lower() for p in procs}
        for name, desc in _FALLBACK_PROCESSES:
            if len(procs) >= _MIN_PROCESSES:
                break
            if name.lower() not in have:
                procs.append({"name": name, "description": desc})
    return procs[:_MAX_PROCESSES]


def generate_process_tasks(name: str, description: str, idea: str,
                           others: list[str] | None = None) -> list[dict]:
    """Return the detailed plain-language tasks for one business process."""
    tasks: list[dict] = []
    if is_available():
        try:
            resp = chat(
                system_prompt=_PTASK_SYSTEM,
                messages=[{"role": "user", "content": _PTASK_USER.format(
                    idea=(idea or "")[:2000], name=name, desc=description or name,
                    others="\n".join(f"- {o}" for o in (others or [])) or "(none)")}],
                temperature=0.4, max_tokens=4000,
                response_format={"type": "json_object"},
            )
            tasks = [nt for nt in (_norm_task(t) for t in (json.loads(resp.content).get("tasks") or [])) if nt][:_MAX_PTASKS]
        except (LLMUnavailableError, LLMClientError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.warning("task generation failed for %r: %s", name, e)
        except Exception:  # noqa: BLE001
            logger.warning("task generation unexpected error for %r", name, exc_info=True)
    if not tasks:
        return _fallback_process_tasks(name)
    return _ensure_build_break(tasks, name)


def generate_process_plan(idea: str, areas: list[str] | None = None) -> list[dict]:
    """Full plain-language plan: [{title, description, tasks:[...]}] over 7-10
    business processes, each with its detailed tasks. Always non-empty; every
    process guaranteed a BUILD and a BREAK task."""
    processes = generate_business_processes(idea, areas)
    names = [p["name"] for p in processes]
    plan: list[dict] = []
    for p in processes:
        others = [n for n in names if n != p["name"]]
        tasks = generate_process_tasks(p["name"], p.get("description", ""), idea, others=others)
        plan.append({"title": p["name"], "description": p.get("description", ""), "tasks": tasks})
    return plan
