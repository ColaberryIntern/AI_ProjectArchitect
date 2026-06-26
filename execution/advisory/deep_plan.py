"""Deep project-plan generator — the maker/checker loop (Loop Architect doctrine).

For ANY project, produces a real, deep, minimal-code, multi-agent, Trust-Before-
Intelligence build plan: three documents (Requirements, Architecture & Design,
Build Guide) plus a lean set of vertical-slice tickets. A *maker* drafts each
artifact, a separate *checker* grades it against a strict rubric, and a *refiner*
fixes the gaps — looping until it passes or a cap is hit (the maker never grades
itself). Everything is domain-neutral: the domain, users, entities, and tech
stack are derived from the idea + the discovery choices.

This is the in-app port of the validated Workflow loop; it runs on a strong
OpenAI model via the app's ``chat()`` client. Generation is stored to files
first (``output/{slug}/``) so a heavy run never strands the build mid-write.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os

from config.settings import OUTPUT_DIR

logger = logging.getLogger(__name__)

MODEL = "gpt-4o"          # strong, runs in-app (vs the gpt-4o-mini default)
PASS_SCORE = 85
MAX_REFINE = 2
_DOC_TOKENS = 14000      # docs are long-form
_TICKET_TOKENS = 8000
_VERDICT_TOKENS = 700

_MAKER_SYS = ("You are a senior product architect and technical writer. You produce real, deep, "
              "minimal-code, multi-agent, trust-first build plans for non-technical founders. "
              "Write COMPREHENSIVELY and at LENGTH — these are professional deliverables, not summaries. "
              "Never wrap up early, never truncate, never say 'and so on'. Fully develop EVERY section with "
              "specifics. A requirements or build-guide document should run well over 1,500 words. "
              "Output exactly the artifact requested — no preamble, no meta-commentary.")
_CHECK_SYS = "You are a strict senior reviewer. Output strict JSON only."

_BAD_IDEAS = {"", "undefined", "null", "none", "n/a"}


def _guard(project: str) -> str:
    return (
        "ABSOLUTE RULES:\n"
        f"- This is a BRAND-NEW product that DOES NOT EXIST YET: \"{project}\". There is NO codebase or pre-existing system.\n"
        "- Do NOT reference any internal module, file path, function, or pre-existing system. Write from the idea below, from a blank page.\n"
        "- Stay strictly in THIS product's domain (derive the domain, users, and entities from the IDEA). Do NOT drift to another industry.\n"
        "- Name EXTERNAL third-party SaaS/tools that fit THIS domain (e.g. across domains: Stripe payments, Twilio SMS, Cal.com/Calendly scheduling, "
        "Mapbox/Google Maps location & routing, Supabase/Firebase data+auth, Bubble/Retool/Softr UI, Make.com/Zapier glue, OpenAI/Claude AI). "
        "Favor configuring these over bespoke code. NEVER reference internal code."
    )


def _context(project: str, idea: str, choices: str) -> str:
    return (
        f"{_guard(project)}\n\nPRODUCT: {project}\nIDEA: {idea}\n\n"
        f"WHAT THE FOUNDER CHOSE IN DISCOVERY:\n{choices}\n\n"
        f"AUDIENCE: a non-technical founder in a build program who must BUILD \"{project}\" with AI help and UNDERSTAND what they built. "
        "Derive the real users/personas and core entities from the IDEA.\n"
        "NON-NEGOTIABLES: (1) MINIMAL CODE — favor external no-code/low-code + AI tools over bespoke code. "
        "(2) It MUST be a multi-agent system (specialist AI assistants + a coordinator). "
        "(3) Trust-Before-Intelligence woven throughout — audit log, approval gates, escalation, a trust dashboard, "
        "and a governance score (INPACT + GOALS, 7 layers of trust)."
    )


def _rubric(project: str, idea: str) -> str:
    return (
        f"You are a strict senior reviewer of a build plan for the NEW product \"{project}\". Score 0-100; PASS requires >= {PASS_SCORE}.\n"
        f"Judge HARD on: real depth (no shallow one-liners/filler); specificity to THIS product's actual domain, users, and entities as described in the IDEA \"{idea}\"; "
        "a genuine MINIMAL-CODE approach naming real EXTERNAL SaaS/tools fit for the domain — NOT bespoke code, NOT internal/repo code; teaches a non-technical founder; "
        "a real multi-agent design; and Trust-Before-Intelligence woven in. "
        "FAIL anything referencing a codebase/repo/files, or drifting to a different product/industry than the IDEA. "
        "DEPTH IS MANDATORY — FAIL (score < 70) any artifact that is short or shallow: a requirements or build-guide "
        "document under ~1,500 words, any stub/placeholder section, an architecture that doesn't fully specify EVERY "
        "agent's triggers/inputs/outputs/approval-gates plus the full data model, functional requirements without 2+ "
        "explicit acceptance criteria each, or a ticket plan with fewer than 12 tickets. List each missing/thin part as a gap. "
        "Return JSON {\"score\": <int>, \"pass\": <bool>, \"gaps\": [<concrete fix>, ...]}. Be demanding."
    )


_TICKETS_SHAPE = (
    'Return strict JSON: {"sprints":[{"key":"s0","title":"...","goal":"...","tickets":['
    '{"title":"...","story":"As a ... I want ... so that ...","design":"key decisions",'
    '"build":"concrete steps naming real external tools","test":"acceptance — how the founder verifies it works",'
    '"vibe":"a paste-ready AI prompt to build it","tbi":"the trust controls for this slice","agent":"owning assistant name or empty"}]}]}'
)


def _chat(system: str, user: str, max_tokens: int, as_json: bool):
    from execution.llm_client import chat
    # OpenAI json_object mode requires the literal word "json" in the messages.
    if as_json and "json" not in user.lower():
        user = user + "\n\nRespond with strict JSON only."
    kwargs = dict(system_prompt=system, messages=[{"role": "user", "content": user}],
                  model=MODEL, max_tokens=max_tokens, temperature=0.5)
    if as_json:
        kwargs["response_format"] = {"type": "json_object"}
    return chat(**kwargs).content


def _make_check(label: str, make_prompt: str, rubric: str, as_json: bool):
    """Draft → (verify → refine)* until pass or cap. Returns text or parsed JSON."""
    out = _chat(_MAKER_SYS, make_prompt, _TICKET_TOKENS if as_json else _DOC_TOKENS, as_json)
    draft = json.loads(out) if as_json else out
    for i in range(MAX_REFINE):
        body = json.dumps(draft, ensure_ascii=False) if as_json else draft
        try:
            verdict = json.loads(_chat(_CHECK_SYS, f"{rubric}\n\nARTIFACT — {label}:\n{body}", _VERDICT_TOKENS, True))
        except Exception as e:
            logger.warning(f"[deep_plan] verify {label} failed: {e}")
            break
        logger.info(f"[deep_plan] {label}: score={verdict.get('score')} pass={verdict.get('pass')}")
        if verdict.get("pass") or not verdict.get("gaps"):
            break
        gaps = "\n- ".join(str(g) for g in verdict.get("gaps", []))
        refine = (f"{_guard('this product')}\n\nImprove this {label} to FIX these gaps. Keep what works; go deeper, "
                  f"stay minimal-code, stay strictly in this product's domain. Return the FULL improved {label}.\n\n"
                  f"GAPS:\n- {gaps}\n\nCURRENT {label}:\n{body}")
        out = _chat(_MAKER_SYS, refine, _TICKET_TOKENS if as_json else _DOC_TOKENS, as_json)
        try:
            draft = json.loads(out) if as_json else out
        except Exception:
            break  # keep the last good draft
    return draft


def generate_deep_plan(idea: str, choices: str, project: str) -> dict:
    """Run the maker/checker loop and return the full plan.

    Raises ValueError if the idea is missing/garbage (the "undefined" guard).
    """
    idea = (idea or "").strip()
    if idea.lower() in _BAD_IDEAS:
        raise ValueError(f"deep_plan: refusing to build from a missing/garbage idea: {idea!r}")
    project = (project or idea[:48]).strip()
    choices = (choices or "(none specified)").strip()

    from execution.llm_client import is_available
    if not is_available():
        raise RuntimeError("deep_plan: LLM unavailable")

    ctx = _context(project, idea, choices)
    rubric = _rubric(project, idea)

    req_prompt = (f"{ctx}\n\nWrite a DEEP, professional REQUIREMENTS DOCUMENT (markdown) for \"{project}\": "
                  "vision & problem; target users/personas (derive from the idea); in-scope vs out-of-scope; the chosen capabilities as "
                  "detailed functional requirements EACH with acceptance criteria; multi-agent requirements; Trust-Before-Intelligence "
                  "requirements; success metrics (domain-appropriate); constraints; assumptions. A real, thorough deliverable.")
    arch_prompt = (f"{ctx}\n\nWrite a DEEP ARCHITECTURE & DESIGN DOCUMENT (markdown) for \"{project}\": system overview with an ASCII diagram; "
                   "the MULTI-AGENT ORGANIZATION (each agent: name, role, triggers, inputs/outputs, autonomy level, approval gates, escalation); "
                   "the Coordinator/Control Tower; the data model (core entities of THIS product); integration points (name the real external tools "
                   "this domain needs); the TRUST-BEFORE-INTELLIGENCE design (audit log, trust dashboard, governance scoring, 7 layers, INPACT+GOALS); "
                   "and the concrete MINIMAL-CODE stack with explicit trade-offs.")
    ticket_prompt = (f"{ctx}\n\nProduce a LEAN, DEEP build plan as JSON for \"{project}\". EXACTLY 6 sprints (keys s0..s5): "
                     "s0 Foundation & Trust, s1 Core MVP, s2 Feature Sprint A, s3 Feature Sprint B, s4 Make the Agents Work Together, "
                     "s5 Trust Dashboard & Launch. 12-18 tickets TOTAL, each a COMPLETE VERTICAL SLICE (own design/build/test); no shallow one-liners. "
                     f"{_TICKETS_SHAPE}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        f_req = pool.submit(_make_check, "requirements", req_prompt, rubric, False)
        f_arch = pool.submit(_make_check, "architecture", arch_prompt, rubric, False)
        f_tick = pool.submit(_make_check, "tickets", ticket_prompt, rubric, True)
        reqs, arch, tickets = f_req.result(), f_arch.result(), f_tick.result()

    guide = _chat(_MAKER_SYS,
                  f"{ctx}\n\nUsing the REQUIREMENTS and ARCHITECTURE below, write the BUILD GUIDE (markdown) for \"{project}\": a sprint-by-sprint "
                  "educational walkthrough for the founder to build this WITH AI and MINIMAL CODE while understanding each step. Per sprint: goal, "
                  "tickets, vibe-code prompts, minimal-code shortcuts (real external tools), and what the founder learns. End with the trust dashboard + launch.\n\n"
                  f"REQUIREMENTS:\n{reqs}\n\nARCHITECTURE:\n{arch}\n\nTICKET PLAN:\n{json.dumps(tickets, ensure_ascii=False)}",
                  _DOC_TOKENS, False)

    n = sum(len(s.get("tickets", [])) for s in (tickets or {}).get("sprints", []))
    logger.info(f"[deep_plan] generated {n} tickets + 3 docs for {project!r}")
    return {"project": project, "requirements": reqs, "architecture": arch,
            "build_guide": guide, "tickets": tickets or {"sprints": []}, "ticket_count": n}


def store_deep_plan(slug: str, plan: dict) -> dict:
    """Write the docs + plan to output/{slug}/ (store-first). Returns the paths."""
    base = OUTPUT_DIR / slug
    docs = base / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    paths = {
        "requirements": docs / "REQUIREMENTS.md",
        "architecture": docs / "ARCHITECTURE.md",
        "build_guide": docs / "BUILD_GUIDE.md",
        "plan": base / "deep_plan.json",
    }
    paths["requirements"].write_text(plan.get("requirements", ""), encoding="utf-8")
    paths["architecture"].write_text(plan.get("architecture", ""), encoding="utf-8")
    paths["build_guide"].write_text(plan.get("build_guide", ""), encoding="utf-8")
    paths["plan"].write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
