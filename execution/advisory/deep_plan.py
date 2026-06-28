"""Story-driven deep build-plan generator — the sequential maker/checker chain.

For ANY project this produces a real, deep, minimal-code, multi-agent, Trust-
Before-Intelligence build plan as **traceable user stories**, not a flat ticket
list. The pipeline is SEQUENTIAL so every downstream artifact cites the one
before it (this is what closes the "tickets don't coordinate the requirements
document" gap). See ``docs/specs/myday-project-build-story-decomposition.md``.

Stages (each maker/checker except the deterministic gate):
  1. Requirements   → REQUIREMENTS.md + a structured REQ catalog (stable REQ-ids)
  2. Agent map      → bounded contexts → agents, each owning a set of REQ-ids
  3. Story slicing  → per capability cluster: stories that cite fulfills:[REQ-id],
                      carry Gherkin acceptance (incl a trust scenario), name an agent
  4. Story map      → arrange stories into dynamic releases (r0 = walking skeleton)
  5. Build guide    → per-release educational walkthrough
  6. Traceability   → deterministic gate (deep_plan_trace) — fail-closed on orphan must

A *maker* drafts, a separate *checker* grades against a strict rubric, a
*refiner* fixes gaps — looping until pass or a cap (the maker never grades
itself). Domain-neutral: domain, users, entities, stack are derived from the
idea + discovery choices. Runs on a strong OpenAI model via the app ``chat()``.
"""
from __future__ import annotations

import json
import logging

from config.settings import OUTPUT_DIR
from execution.advisory import deep_plan_trace

logger = logging.getLogger(__name__)

MODEL = "gpt-4o"
PASS_SCORE = 85
MAX_REFINE = 2
_DOC_TOKENS = 14000
_JSON_TOKENS = 6000
_VERDICT_TOKENS = 800

_MAKER_SYS = ("You are a senior product architect and technical writer. You produce real, deep, "
              "minimal-code, multi-agent, trust-first build plans for non-technical founders. "
              "Write COMPREHENSIVELY and at LENGTH — these are professional deliverables, not summaries. "
              "Never wrap up early, never truncate, never say 'and so on'. Fully develop EVERY section with "
              "specifics. Output exactly the artifact requested — no preamble, no meta-commentary.")
_CHECK_SYS = "You are a strict senior reviewer. Output strict JSON only."

_BAD_IDEAS = {"", "undefined", "null", "none", "n/a"}


# ── context / guard (shared) ────────────────────────────────────────

def _guard(project: str) -> str:
    return (
        "ABSOLUTE RULES:\n"
        f"- This is a BRAND-NEW product that DOES NOT EXIST YET: \"{project}\". There is NO codebase or pre-existing system.\n"
        "- Do NOT reference any internal module, file path, function, or pre-existing system. Write from the idea below, from a blank page.\n"
        "- Stay strictly in THIS product's domain (derive the domain, users, and entities from the IDEA). Do NOT drift to another industry.\n"
        "- Name EXTERNAL third-party SaaS/tools that fit THIS domain (e.g. Stripe payments, Twilio SMS, Cal.com/Calendly scheduling, "
        "Mapbox/Google Maps, Supabase/Firebase data+auth, Bubble/Retool/Softr UI, Make.com/Zapier glue, OpenAI/Claude AI). "
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


# ── the single LLM seam (mockable in tests) ─────────────────────────

def _chat(system: str, user: str, max_tokens: int, as_json: bool):
    from execution.llm_client import chat
    if as_json and "json" not in user.lower():
        user = user + "\n\nRespond with strict JSON only."
    kwargs = dict(system_prompt=system, messages=[{"role": "user", "content": user}],
                  model=MODEL, max_tokens=max_tokens, temperature=0.4)
    if as_json:
        kwargs["response_format"] = {"type": "json_object"}
    return chat(**kwargs).content


def _extract_json(raw):
    """Best-effort parse of a model JSON reply (tolerates code fences / prose)."""
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw or "").strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        s = s[4:] if s[:4].lower() == "json" else s
    try:
        return json.loads(s)
    except Exception:
        a, b = s.find("{"), s.rfind("}")
        if a != -1 and b != -1 and b > a:
            try:
                return json.loads(s[a:b + 1])
            except Exception:
                pass
    return None


# ── rubrics ─────────────────────────────────────────────────────────

def _rubric(project: str, idea: str) -> str:
    """DOC rubric (requirements / build guide) — depth + specificity bar."""
    return (
        f"You are a strict senior reviewer of a build artifact for the NEW product \"{project}\". Score 0-100; PASS requires >= {PASS_SCORE}.\n"
        f"Judge HARD on: real depth (no shallow one-liners/filler); specificity to THIS product's actual domain, users, and entities per the IDEA \"{idea}\"; "
        "a genuine MINIMAL-CODE approach naming real EXTERNAL SaaS/tools — NOT bespoke or internal/repo code; teaches a non-technical founder; "
        "a real multi-agent design; Trust-Before-Intelligence woven in. "
        "FAIL anything referencing a codebase/repo/files, or drifting to a different industry than the IDEA. "
        "DEPTH IS MANDATORY — FAIL (score < 70) any artifact that is short or shallow: a requirements or build-guide document under ~1,500 words, "
        "any stub/placeholder section, OR functional requirements that are not numbered REQ-### each with 2+ explicit acceptance criteria. "
        "List each missing/thin part as a gap. Return JSON {\"score\": <int>, \"pass\": <bool>, \"gaps\": [<concrete fix>, ...]}. Be demanding."
    )


def _story_rubric(project: str) -> str:
    """STORY rubric — structural hard-fails for the traceable story set."""
    return (
        f"You are a strict senior reviewer of a USER-STORY SET for the NEW product \"{project}\". Score 0-100; PASS requires >= {PASS_SCORE}.\n"
        "HARD-FAIL (score < 70) the WHOLE set if ANY story: has an empty or invalid 'fulfills' (it MUST cite >=1 real REQ-### id); "
        "lacks Gherkin acceptance with CONCRETE values (given/when/then); has NO trust scenario (at least one acceptance scenario must assert a "
        "trust control — an approval gate, an audit-log entry, an escalation, or access control); has an 'owner_agent' that is not one of the named agents; "
        "or is a thin one-liner that isn't a real vertical slice (INVEST). "
        "Also FAIL if any 'must' requirement is fulfilled by zero stories. Bias toward MORE depth — never penalize a larger story set. "
        "List each problem as a concrete gap. Return JSON {\"score\": <int>, \"pass\": <bool>, \"gaps\": [<concrete fix>, ...]}."
    )


def _make_check(label: str, make_prompt: str, rubric: str, as_json: bool):
    """Draft → (verify → refine)* until pass or cap. Returns text or parsed JSON."""
    out = _chat(_MAKER_SYS, make_prompt, _JSON_TOKENS if as_json else _DOC_TOKENS, as_json)
    draft = _extract_json(out) if as_json else out
    for _ in range(MAX_REFINE):
        body = json.dumps(draft, ensure_ascii=False) if as_json else draft
        try:
            verdict = _extract_json(_chat(_CHECK_SYS, f"{rubric}\n\nARTIFACT — {label}:\n{body}", _VERDICT_TOKENS, True)) or {}
        except Exception as e:
            logger.warning(f"[deep_plan] verify {label} failed: {e}")
            break
        logger.info(f"[deep_plan] {label}: score={verdict.get('score')} pass={verdict.get('pass')}")
        if verdict.get("pass") or not verdict.get("gaps"):
            break
        gaps = "\n- ".join(str(g) for g in verdict.get("gaps", []))
        refine = (f"{_guard('this product')}\n\nImprove this {label} to FIX these gaps. Keep what works; go deeper, "
                  f"stay minimal-code, stay strictly in this product's domain. Return the FULL improved {label} in the SAME format.\n\n"
                  f"GAPS:\n- {gaps}\n\nCURRENT {label}:\n{body}")
        out = _chat(_MAKER_SYS, refine, _JSON_TOKENS if as_json else _DOC_TOKENS, as_json)
        nxt = _extract_json(out) if as_json else out
        if nxt is not None:
            draft = nxt
        else:
            break
    return draft


# ── stage helpers ───────────────────────────────────────────────────

def _normalize_reqs(reqs) -> list:
    """Coerce the REQ catalog: stable REQ-### ids, lowercased priority, list acceptance."""
    out = []
    for i, r in enumerate(reqs or [], 1):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or f"REQ-{i:03d}").strip().upper()
        if not rid.startswith("REQ-"):
            rid = f"REQ-{i:03d}"
        pri = str(r.get("priority") or "should").strip().lower()
        if pri not in ("must", "should", "could"):
            pri = "should"
        acc = r.get("acceptance") or r.get("acceptance_criteria") or []
        if isinstance(acc, str):
            acc = [acc]
        out.append({"id": rid, "priority": pri, "statement": (r.get("statement") or r.get("text") or "").strip(),
                    "acceptance": [str(a) for a in acc], "cluster": (r.get("cluster") or r.get("area") or "General").strip()})
    return out


def _clusters(reqs) -> "list[tuple]":
    """Group reqs by cluster, preserving first-seen order. Returns [(cluster, [reqs])]."""
    order, groups = [], {}
    for r in reqs:
        c = r.get("cluster") or "General"
        if c not in groups:
            groups[c] = []
            order.append(c)
        groups[c].append(r)
    return [(c, groups[c]) for c in order]


def _assign_release_weeks(n: int) -> list:
    """Program weeks 3-11 across n releases. r0 = week 3 (the skeleton); the rest
    split weeks 4-11 into contiguous spans. Returns [(ws, we), ...]."""
    if n <= 0:
        return []
    if n == 1:
        return [(3, 3)]
    spans = [(3, 3)]
    rest = list(range(4, 12))            # weeks 4..11
    k = n - 1
    size = max(1, len(rest) // k)
    idx = 0
    for j in range(k):
        start = rest[min(idx, len(rest) - 1)]
        idx = idx + size if j < k - 1 else len(rest)
        end = rest[min(idx - 1, len(rest) - 1)]
        spans.append((start, max(start, end)))
    return spans


def _coerce_acceptance(acc) -> list:
    """Normalize a story's acceptance into [{scenario, trust, given, when, then}]."""
    out = []
    for a in acc or []:
        if not isinstance(a, dict):
            out.append({"scenario": str(a), "trust": False, "given": "", "when": "", "then": str(a)})
            continue
        out.append({
            "scenario": str(a.get("scenario") or a.get("name") or "Scenario"),
            "trust": bool(a.get("trust")),
            "given": str(a.get("given") or ""),
            "when": str(a.get("when") or ""),
            "then": str(a.get("then") or a.get("expected") or ""),
        })
    return out


def _normalize_stories(stories, agent_names) -> list:
    """Coerce stories; assign stable STORY-### ids; keep only well-formed ones."""
    out = []
    for i, s in enumerate(stories or [], 1):
        if not isinstance(s, dict):
            continue
        fulfills = [str(f).strip().upper() for f in (s.get("fulfills") or []) if str(f).strip()]
        owner = (s.get("owner_agent") or s.get("agent") or "").strip()
        out.append({
            "id": f"STORY-{i:03d}",
            "title": (s.get("title") or "Untitled story").strip(),
            "fulfills": fulfills,
            "owner_agent": owner,
            "slice": (s.get("slice") or "").strip(),
            "narrative": (s.get("narrative") or s.get("story") or "").strip(),
            "acceptance": _coerce_acceptance(s.get("acceptance")),
            "build": (s.get("build") or s.get("design") or "").strip(),
            "vibe": (s.get("vibe") or "").strip(),
            "trust": (s.get("trust") or s.get("tbi") or "").strip(),
            "release": "",
        })
    return out


# ── the chain ───────────────────────────────────────────────────────

def generate_deep_plan(idea: str, choices: str, project: str) -> dict:
    """Run the sequential maker/checker chain. Returns the full story-driven plan.

    Raises ValueError on a missing/garbage idea; RuntimeError if the LLM is down.
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
    doc_rubric = _rubric(project, idea)

    # ── Stage 1: requirements doc (maker/checker) ──
    req_prompt = (f"{ctx}\n\nWrite a DEEP, professional REQUIREMENTS DOCUMENT (markdown) for \"{project}\": "
                  "vision & problem; target users/personas (derive from the idea); in-scope vs out-of-scope; the chosen capabilities as "
                  "detailed functional requirements — NUMBER EACH ONE 'REQ-001', 'REQ-002', … and give EACH a priority (must/should/could) and "
                  "2+ concrete acceptance criteria; multi-agent requirements; Trust-Before-Intelligence requirements; success metrics; constraints; assumptions.")
    requirements = _make_check("requirements", req_prompt, doc_rubric, False)

    # ── Stage 1b: extract the structured REQ catalog ──
    cat = _make_check("REQ catalog", (
        f"From the REQUIREMENTS document below, extract the machine-readable REQ catalog as JSON: "
        '{"reqs":[{"id":"REQ-001","priority":"must|should|could","statement":"...","acceptance":["...","..."],'
        '"cluster":"the capability area this requirement belongs to"}]}. Include EVERY numbered requirement; '
        "group related requirements under the same short 'cluster' label.\n\nREQUIREMENTS:\n" + str(requirements)),
        doc_rubric, True)
    reqs = _normalize_reqs((cat or {}).get("reqs"))
    if not reqs:
        raise RuntimeError("deep_plan: requirements stage produced no REQ catalog")

    # ── Stage 2: agent map (maker/checker) ──
    agent_prompt = (f"{ctx}\n\nFrom the REQUIREMENTS below, derive the MULTI-AGENT ORGANIZATION using bounded contexts. "
                    "Return JSON {\"agents\":[{\"name\":\"...\",\"context\":\"...\",\"owns\":[\"REQ-001\",...],"
                    "\"commands\":\"commands it issues\",\"reacts\":\"events it reacts to\",\"autonomy\":\"...\",\"gate\":\"approval gate or empty\"}]} "
                    "plus ONE coordinator agent. EVERY REQ-id must be owned by exactly one agent. Include a Trust/Governance agent that observes every command.\n\n"
                    f"REQ CATALOG:\n{json.dumps(reqs, ensure_ascii=False)}")
    agents_raw = _make_check("agent map", agent_prompt, doc_rubric, True)
    agents = [a for a in ((agents_raw or {}).get("agents") or []) if isinstance(a, dict) and a.get("name")]
    agent_names = [a["name"] for a in agents] or ["Coordinator"]

    # ── Stage 3: story slicing, per capability cluster (maker/checker) ──
    story_rubric = _story_rubric(project)
    all_stories: list = []
    for cluster, creqs in _clusters(reqs):
        cl_prompt = (f"{ctx}\n\nSlice the requirements in THIS capability cluster into vertical-slice USER STORIES. "
                     "For EACH story return: {\"title\",\"fulfills\":[\"REQ-###\",...],\"narrative\":\"As a ... I want ... so that ...\","
                     "\"slice\":\"Command → Event → Read-model\",\"owner_agent\":\"a name from the AGENT MAP\","
                     "\"acceptance\":[{\"scenario\",\"trust\":true|false,\"given\",\"when\",\"then\"}],\"build\":\"concrete steps naming real external tools\","
                     "\"vibe\":\"a paste-ready build prompt\",\"trust\":\"the trust controls for this slice\"}. "
                     "HARD RULES: fulfills must be non-empty and cite only ids from THIS cluster (a story may also cite a cross-cutting id); "
                     "every story has Gherkin acceptance with concrete values AND at least one acceptance scenario with trust=true; "
                     "owner_agent must be one of the agent names; cover every requirement in the cluster; bias toward depth. "
                     "Return JSON {\"stories\":[...]}.\n\n"
                     f"AGENT NAMES: {agent_names}\n\nCLUSTER '{cluster}' REQUIREMENTS:\n{json.dumps(creqs, ensure_ascii=False)}")
        out = _make_check(f"stories[{cluster}]", cl_prompt, story_rubric, True)
        all_stories.extend((out or {}).get("stories") or [])

    stories = _normalize_stories(all_stories, agent_names)
    if not stories:
        raise RuntimeError("deep_plan: story slicing produced no stories")

    # ── Stage 4: story map → dynamic releases (maker/checker) ──
    story_index = {s["id"]: s for s in stories}
    map_prompt = (f"{ctx}\n\nArrange these STORIES into a story map of dynamic RELEASES (sprints). "
                  "Return JSON {\"releases\":[{\"key\":\"r0\",\"name\":\"short release name\",\"goal\":\"...\","
                  "\"stories\":[\"STORY-001\",...],\"demo\":\"what the founder demos at sprint end\"}]}. "
                  "RULES: release r0 is the WALKING SKELETON — the thinnest end-to-end path that runs WITH the trust spine on "
                  "(an audit-log entry + one approval gate). No release may contain a single story. Order later releases by demo value. "
                  "Every story must appear in exactly one release.\n\n"
                  f"STORIES (id: title):\n" + "\n".join(f"{s['id']}: {s['title']}" for s in stories))
    rel_raw = _make_check("story map", map_prompt, doc_rubric, True)
    releases = _build_releases((rel_raw or {}).get("releases"), stories, story_index)

    # ── Stage 5: build guide (maker) ──
    guide = _chat(_MAKER_SYS,
                  f"{ctx}\n\nUsing the REQUIREMENTS, AGENT MAP, and RELEASES below, write the BUILD GUIDE (markdown) for \"{project}\": "
                  "a release-by-release educational walkthrough for the founder to build this WITH AI and MINIMAL CODE while understanding each step. "
                  "Per release: goal, the stories in it, the vibe-code prompts, minimal-code shortcuts (real external tools), and what the founder learns. "
                  "End with the trust dashboard + launch.\n\n"
                  f"REQUIREMENTS:\n{requirements}\n\nAGENTS:\n{json.dumps(agents, ensure_ascii=False)}\n\n"
                  f"RELEASES:\n{json.dumps([{k: r[k] for k in ('key', 'name', 'goal', 'stories')} for r in releases], ensure_ascii=False)}",
                  _DOC_TOKENS, False)

    # ── Stage 6: deterministic traceability gate ──
    trace = deep_plan_trace.validate(reqs, stories)
    logger.info("[deep_plan] %s", deep_plan_trace.summarize(trace))
    rtm = deep_plan_trace.render_rtm_md(reqs, stories, trace)

    return {
        "project": project,
        "requirements": requirements,
        "reqs": reqs,
        "architecture": agents_to_md(project, agents),
        "agents": agents,
        "stories": stories,
        "releases": releases,
        "build_guide": guide,
        "rtm": rtm,
        "trace": trace,
        "story_count": len(stories),
        "ticket_count": len(stories),       # back-compat for status messages
    }


def _build_releases(raw_releases, stories, story_index) -> list:
    """Validate/repair the release map: every story placed once, weeks assigned, no thin release."""
    seen, releases = set(), []
    for r in (raw_releases or []):
        if not isinstance(r, dict):
            continue
        ids = [str(x).strip().upper() for x in (r.get("stories") or []) if str(x).strip().upper() in story_index]
        ids = [i for i in ids if i not in seen]
        if not ids:
            continue
        seen.update(ids)
        releases.append({"key": "", "name": (r.get("name") or "Release").strip(),
                         "goal": (r.get("goal") or "").strip(), "stories": ids,
                         "demo": (r.get("demo") or "").strip(), "weeks": (3, 3)})
    # Any stories the model dropped → append to the last release (or a new one).
    leftover = [s["id"] for s in stories if s["id"] not in seen]
    if leftover:
        if releases:
            releases[-1]["stories"].extend(leftover)
        else:
            releases.append({"key": "", "name": "Build", "goal": "", "stories": leftover, "demo": "", "weeks": (3, 3)})
    # Merge any single-story release into the previous (no thin releases).
    merged: list = []
    for r in releases:
        if merged and len(r["stories"]) < deep_plan_trace.MIN_PER_RELEASE:
            merged[-1]["stories"].extend(r["stories"])
        else:
            merged.append(r)
    if len(merged) >= 2 and len(merged[-1]["stories"]) < deep_plan_trace.MIN_PER_RELEASE:
        last = merged.pop()
        merged[-1]["stories"].extend(last["stories"])
    # Assign keys + week spans, and stamp each story's release.
    spans = _assign_release_weeks(len(merged))
    for i, r in enumerate(merged):
        r["key"] = f"r{i}"
        r["weeks"] = spans[i] if i < len(spans) else (11, 11)
        for sid in r["stories"]:
            if sid in story_index:
                story_index[sid]["release"] = r["key"]
    return merged


def agents_to_md(project: str, agents: list) -> str:
    """Render the agent map as the ARCHITECTURE.md document."""
    lines = [f"# {project} — Agent Map & Architecture", "",
             "The multi-agent system, derived from the requirements via bounded contexts. "
             "Each requirement is owned by exactly one agent; the Trust/Governance agent observes every command.", "",
             "| Agent | Owns | Commands | Reacts to | Approval gate |", "|---|---|---|---|---|"]
    for a in agents:
        owns = ", ".join(a.get("owns") or [])
        lines.append(f"| {a.get('name', '')} | {owns} | {a.get('commands', '')} | {a.get('reacts', '')} | {a.get('gate') or '—'} |")
    return "\n".join(lines)


# ── store-first ─────────────────────────────────────────────────────

def store_deep_plan(slug: str, plan: dict) -> dict:
    """Write the docs + plan to output/{slug}/ (store-first). Returns the paths."""
    base = OUTPUT_DIR / slug
    docs = base / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    paths = {
        "requirements": docs / "REQUIREMENTS.md",
        "architecture": docs / "ARCHITECTURE.md",
        "build_guide": docs / "BUILD_GUIDE.md",
        "rtm": docs / "TRACEABILITY.md",
        "plan": base / "deep_plan.json",
    }
    paths["requirements"].write_text(plan.get("requirements", ""), encoding="utf-8")
    paths["architecture"].write_text(plan.get("architecture", ""), encoding="utf-8")
    paths["build_guide"].write_text(plan.get("build_guide", ""), encoding="utf-8")
    paths["rtm"].write_text(plan.get("rtm", ""), encoding="utf-8")
    paths["plan"].write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
