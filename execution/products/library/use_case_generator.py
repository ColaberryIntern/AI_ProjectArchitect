"""Use Case generator — composes a realistic business use case from
2–5 vetted Library assets.

Two paths:
    1. LLM-driven (preferred): asks the configured LLM to compose a
       crafty, persona-driven use case. Uses JSON response format.
    2. Hand-crafted fallback: rotates through a curated set of templates
       so even without an LLM key the scheduler keeps adding value.

"Crafty" prompt design:
    - System prompt insists on real business pain, named personas,
      concrete numbers, composable tool usage (≥ 2 tools).
    - Output is a strict JSON schema the validator enforces.
    - Failures fall back to the crafted bank.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import asdict
from typing import Any

from . import inventory, store, use_cases

LAYER = "product"
PRODUCT = "library"


# ── LLM prompt ───────────────────────────────────────────────────────


_SYSTEM_PROMPT = """You are a senior product strategist at Colaberry.

Your job: invent a CONCRETE, REALISTIC business use case that an actual
working professional would recognize as their own problem. Then describe
how they'd solve it using a specific combination of tools from the
Colaberry Library.

Rules:
- Pick a NAMED PERSONA (e.g. "Maya, a Demand Gen Manager at a 50-person
  Series B SaaS") — never generic ("a user", "a team").
- State a PAINFUL specific problem with concrete numbers
  ("scrambling to summarize 30 RFPs/week, losing 6 hours per rep").
- Use 2–4 of the provided tools meaningfully — don't shoehorn.
- The walkthrough is 5 numbered steps, each one sentence, very concrete.
- The outcome_metric is quantifiable (hours saved / revenue gained /
  errors reduced / time-to-X cut).
- Complexity: "quick_win" (≤1 day to set up), "moderate" (1–2 weeks),
  or "advanced" (multi-month, requires integration).

Output STRICT JSON with this exact shape (no markdown, no commentary):
{
  "title": "…",
  "summary": "…",
  "persona": "…",
  "industry": "…",
  "complexity": "quick_win|moderate|advanced",
  "problem": "3–4 sentences",
  "solution": "3–4 sentences",
  "walkthrough": ["step1","step2","step3","step4","step5"],
  "outcome_metric": "…",
  "tags": ["…","…"],
  "tools_used": [
    {"category": "<library category key>",
     "asset_id": "<exact asset name from the tools provided>",
     "role": "one-line why this tool"}
  ]
}
"""


def _pick_tool_sample(seed: int | None = None) -> list[dict[str, Any]]:
    """Pick 3–5 assets across categories, preferring enriched ones."""
    rnd = random.Random(seed) if seed is not None else random.Random()
    cats = ["mcp", "agents", "skills", "prompts", "capabilities"]
    pool: list[dict[str, Any]] = []
    for cat in cats:
        rows = inventory.load_category(cat) or []
        rnd.shuffle(rows)
        # Prefer enriched, take a few
        enriched = []
        plain = []
        for r in rows[:30]:
            asset_id = r.get("name") or r.get("id") or ""
            if not asset_id:
                continue
            meta = store.get_metadata("global", cat, asset_id)
            tagged = {**r, "_category": cat,
                          "_what_its_for": getattr(meta, "what_its_for", ""),
                          "_enriched": meta.enrichment_state == "enriched"}
            if tagged["_enriched"]:
                enriched.append(tagged)
            else:
                plain.append(tagged)
        # Prefer 2 enriched + 1 plain per category if available
        pool.extend(enriched[:2])
        pool.extend(plain[:1])
    rnd.shuffle(pool)
    return pool[:6]


def _format_tools_for_prompt(tools: list[dict]) -> str:
    lines = []
    for t in tools:
        cat = t.get("_category", "")
        name = t.get("name") or t.get("id") or "?"
        desc = (t.get("description") or "")[:200]
        purpose = t.get("_what_its_for") or ""
        lines.append(
            f"- [{cat}] {name}: {desc}" + (f" | purpose: {purpose}" if purpose else "")
        )
    return "\n".join(lines)


# ── Hand-crafted bank (used when LLM not available) ─────────────────


_CRAFTED_BANK: list[dict[str, Any]] = [
    {
        "title": "RFP triage that doesn't eat your week",
        "summary": "Auto-summarize, classify, and route incoming RFPs so sales engineers respond in hours, not days.",
        "persona": "Maya, Senior Sales Engineer at a 200-person SaaS company",
        "industry": "B2B SaaS",
        "complexity": "moderate",
        "problem": "Maya's team gets 25–30 RFPs/week, each 20–60 pages. Today the rep skims, types a Slack summary by hand, and decides whether to escalate to Solutions Engineering. Average response time is 18 hours. They lose deals because competitors respond in 4.",
        "solution": "An RFP triage workflow ingests the PDF via the Filesystem MCP, summarizes via a tuned prompt, classifies must-haves vs nice-to-haves, and posts a structured digest to the right Slack channel with @-mention escalations.",
        "walkthrough": [
            "Sales rep drops the RFP PDF into a shared drive watched by the Filesystem MCP server.",
            "An agent invokes the 'RFP Summary Prompt' to produce a 3-section digest (scope, must-haves, red flags).",
            "A classification skill scores fit against the company's ICP and tags the RFP.",
            "The Slack MCP posts the digest into #rfp-triage with an @-mention if the score exceeds 0.75.",
            "Sales Engineering picks up only the high-fit ones — response time drops from 18 hours to 3.",
        ],
        "outcome_metric": "Average response time cut from 18 → 3 hours; 12 hours/week saved per rep.",
        "tags": ["sales", "rfp", "triage", "automation"],
        "tools_used": [
            {"category": "mcp", "asset_id": "MCP Filesystem Server", "role": "watches the inbound RFP drop folder"},
            {"category": "mcp", "asset_id": "MCP Slack Server", "role": "posts digests + escalations"},
            {"category": "prompts", "asset_id": "RFP Summary Prompt v2", "role": "structures the digest"},
        ],
    },
    {
        "title": "Engineering release notes that write themselves",
        "summary": "Auto-generate release notes from PRs merged this week, grouped by user-visible change vs internal.",
        "persona": "Sam, Engineering Manager at a 30-engineer fintech startup",
        "industry": "Fintech",
        "complexity": "quick_win",
        "problem": "Sam's team ships weekly but the release notes are perpetually 2 weeks behind. Engineers paste PR titles into a Notion doc, marketing rewrites half, and the customer-facing change log is always stale. CS reads commit hashes to answer support tickets.",
        "solution": "A scheduled job lists merged PRs via the GitHub MCP, the Claude Tool-Use skill classifies each as user-visible vs internal, and a release-notes prompt drafts a clean changelog grouped by category.",
        "walkthrough": [
            "Every Friday 4pm, a GitHub MCP call lists PRs merged into main since the last release.",
            "Each PR title + body goes through a classification skill: 'user-visible' / 'internal' / 'security'.",
            "User-visible PRs go through the release-notes prompt to draft customer-friendly bullets.",
            "Output posted to #release-notes in Slack with a 'Approve' button.",
            "One click → publishes to the public changelog page and the in-app banner.",
        ],
        "outcome_metric": "Release notes ship on the same day as the release, not 2 weeks later.",
        "tags": ["engineering", "release", "automation", "changelog"],
        "tools_used": [
            {"category": "mcp", "asset_id": "MCP GitHub Server", "role": "lists merged PRs"},
            {"category": "skills", "asset_id": "Claude Tool Use (Function Calling)", "role": "classifies user-visible vs internal"},
            {"category": "mcp", "asset_id": "MCP Slack Server", "role": "delivers the draft with approve button"},
        ],
    },
    {
        "title": "Support ticket triage during graveyard hours",
        "summary": "An autonomous agent triages support tickets at 3am: severity, owner, response draft — humans confirm in the morning.",
        "persona": "Priya, Head of Support at a US-based SaaS company with EU customers",
        "industry": "SaaS / Customer Support",
        "complexity": "advanced",
        "problem": "EU customers file tickets at 3am US time. Critical bugs sit untouched until 9am ET. Priya's overnight on-call is one engineer who triages reactively — by the time the team starts, the queue is 40+ deep and the response NPS tanks.",
        "solution": "A CrewAI multi-agent crew runs every 15 minutes overnight: one agent reads new tickets via Zendesk integration, classifies severity, drafts a response, and posts to Slack. On-call engineer just approves or escalates.",
        "walkthrough": [
            "Every 15 min, a scheduler fires the 'Overnight Triage' crew.",
            "Triage agent pulls new tickets from Zendesk via a connector.",
            "Classification skill assigns severity (P0–P3) based on language signals.",
            "Response-draft agent writes a templated first reply (with personalization).",
            "Slack MCP posts to #night-triage with severity color-coded and approve/escalate buttons.",
        ],
        "outcome_metric": "Time-to-first-response on overnight tickets: 3h → 12 min.",
        "tags": ["support", "triage", "agents", "after-hours"],
        "tools_used": [
            {"category": "agents", "asset_id": "CrewAI Multi-Agent Framework", "role": "orchestrates the overnight crew"},
            {"category": "mcp", "asset_id": "MCP Slack Server", "role": "delivers triage updates"},
            {"category": "skills", "asset_id": "Claude Tool Use (Function Calling)", "role": "classification + drafting"},
        ],
    },
    {
        "title": "Investor update from raw Stripe + product data",
        "summary": "Pull the metrics, draft a narrative, send to investors — a founder's 4-hour task becomes 20 minutes.",
        "persona": "Daniel, Founder/CEO of a 12-person Seed-stage startup",
        "industry": "Startup ops",
        "complexity": "moderate",
        "problem": "Monthly investor updates take Daniel half a day: pulling Stripe MRR, product analytics, hiring updates, lessons. Half the data is wrong because he copy-pastes from 4 different dashboards.",
        "solution": "Connectors pull Stripe + product analytics + ATS data; an analyst agent reconciles them; the investor-update prompt drafts the email in Daniel's voice. He reviews and tweaks, then sends.",
        "walkthrough": [
            "Stripe MCP fetches MRR, churn, MoM growth.",
            "Product analytics MCP fetches weekly active accounts.",
            "ATS connector fetches hires + open roles.",
            "Analyst agent reconciles inconsistencies (e.g., trial conversions).",
            "Investor-update prompt drafts the email with Daniel's tone of voice.",
        ],
        "outcome_metric": "Investor update cycle: 4h → 20 min; data accuracy goes from ~85% to 99%.",
        "tags": ["founder", "investor-relations", "reporting"],
        "tools_used": [
            {"category": "mcp", "asset_id": "MCP GitHub Server", "role": "engineering velocity stats"},
            {"category": "agents", "asset_id": "CrewAI Multi-Agent Framework", "role": "reconciles cross-source data"},
            {"category": "prompts", "asset_id": "Claude Prompt Caching", "role": "caches investor voice templates"},
        ],
    },
    {
        "title": "Compliance audit prep without the heart attack",
        "summary": "Continuous compliance: agents check policies against actual production state, surface drift weekly, no scramble at audit time.",
        "persona": "Rachel, Head of Security at a Series B HealthTech company",
        "industry": "HealthTech / Compliance",
        "complexity": "advanced",
        "problem": "Rachel does SOC 2 + HIPAA audits annually. Every year she scrambles for 4 weeks pulling evidence, finding gaps. This year she missed a HIPAA control because an engineer disabled MFA on a service account 6 months ago and no one noticed.",
        "solution": "A weekly compliance crew checks each control: agents inspect production config via cloud connectors, compare against policy library, flag drift. Auditors see a dashboard, not a scramble.",
        "walkthrough": [
            "Compliance crew runs every Monday 6am.",
            "Each agent owns one control family (access, encryption, logging, etc.).",
            "Agents inspect production state via the cloud MCP servers.",
            "Drift gets flagged with severity + evidence + suggested fix.",
            "Weekly digest in Slack with a one-click 'open ticket' for each gap.",
        ],
        "outcome_metric": "Audit prep: 4 weeks → 2 days. Zero late-discovered gaps.",
        "tags": ["security", "compliance", "soc2", "hipaa"],
        "tools_used": [
            {"category": "agents", "asset_id": "CrewAI Multi-Agent Framework", "role": "compliance crew"},
            {"category": "mcp", "asset_id": "MCP Filesystem Server", "role": "evidence collection"},
        ],
    },
    {
        "title": "Marketing site → product trial conversion lift",
        "summary": "Personalize the landing-page hero based on the visitor's likely persona, in real time.",
        "persona": "Theo, Growth Lead at a 40-person dev-tools company",
        "industry": "Dev tools / Marketing",
        "complexity": "moderate",
        "problem": "Theo's landing page converts at 2.4%. Personalization based on UTM/referrer would help but his stack can't render different hero copy without a CMS migration.",
        "solution": "A real-time agent reads the visitor's UTM + referrer + IP-inferred company, classifies into one of 6 personas, and the Claude Tool-Use skill swaps the hero copy + CTA via a header script.",
        "walkthrough": [
            "Visitor lands; pixel fires with referrer + UTM + company match.",
            "Persona classifier scores them against 6 archetypes (eng / pm / founder / etc.).",
            "Claude Tool-Use selects the matching hero copy variant.",
            "Hero + CTA + social proof swap in 200ms — before paint.",
            "Conversion tracked per persona segment; A/B against control.",
        ],
        "outcome_metric": "Trial signup conversion: 2.4% → 3.7% (controlled test).",
        "tags": ["marketing", "personalization", "conversion"],
        "tools_used": [
            {"category": "skills", "asset_id": "Claude Tool Use (Function Calling)", "role": "swap hero copy in real time"},
            {"category": "prompts", "asset_id": "Claude Prompt Caching", "role": "cache the 6 persona variants"},
        ],
    },
]


def _crafted_one(seed: int | None = None) -> dict[str, Any]:
    rnd = random.Random(seed) if seed is not None else random.Random()
    return dict(rnd.choice(_CRAFTED_BANK))


# ── LLM call ────────────────────────────────────────────────────────


def _llm_generate_one(tools: list[dict]) -> dict[str, Any] | None:
    """Returns parsed use-case dict, or None on failure."""
    try:
        from execution.llm_client import chat, is_available
    except Exception:
        return None
    if not is_available():
        return None

    user_message = (
        "Compose ONE use case using 2–4 of these tools. Output STRICT JSON only.\n\n"
        f"Available tools:\n{_format_tools_for_prompt(tools)}\n"
    )

    try:
        resp = chat(
            system_prompt=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=1500,
            temperature=0.85,
            response_format={"type": "json_object"},
        )
        content = resp.content.strip()
        # Strip code fences if model returned them
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        return json.loads(content)
    except Exception:
        return None


def _validate(raw: dict[str, Any]) -> bool:
    required = ("title", "persona", "problem", "solution",
                  "walkthrough", "tools_used")
    if not all(k in raw and raw[k] for k in required):
        return False
    if not isinstance(raw["walkthrough"], list) or len(raw["walkthrough"]) < 3:
        return False
    if not isinstance(raw["tools_used"], list) or not raw["tools_used"]:
        return False
    return True


# ── Public API ──────────────────────────────────────────────────────


def generate_one(workspace: str = "global", creator: str = "scheduler:daily",
                       seed: int | None = None) -> use_cases.UseCase:
    """Generate one use case. Tries LLM first; falls back to crafted bank."""
    tools = _pick_tool_sample(seed=seed)
    raw = _llm_generate_one(tools) if tools else None
    source = "llm-generated"
    if not raw or not _validate(raw):
        raw = _crafted_one(seed=seed)
        source = "hand-crafted"

    uc = use_cases.UseCase(
        use_case_id=use_cases.new_id(),
        workspace=workspace,
        title=str(raw.get("title", ""))[:200],
        summary=str(raw.get("summary", "") or raw.get("title", ""))[:280],
        persona=str(raw.get("persona", ""))[:200],
        industry=str(raw.get("industry", ""))[:80],
        complexity=str(raw.get("complexity", "moderate")),
        problem=str(raw.get("problem", "")),
        solution=str(raw.get("solution", "")),
        walkthrough=[str(s) for s in (raw.get("walkthrough") or [])][:8],
        tools_used=[{"category": t.get("category", ""),
                          "asset_id": t.get("asset_id", ""),
                          "role": t.get("role", "")[:200]}
                          for t in (raw.get("tools_used") or [])],
        outcome_metric=str(raw.get("outcome_metric", ""))[:240],
        tags=[str(t) for t in (raw.get("tags") or [])][:10],
        created_at="",  # save() sets it
        created_by=creator,
        source=source,
        generator_meta={"tools_offered": len(tools), "ts": time.time()},
        # NOT auto-vetted. Colaberry-vetted is a human curation signal —
        # generator output must be reviewed before it earns the badge.
        vetted=False,
        vetted_by=None,
        vetted_at=None,
        vetted_notes="",
    )
    use_cases.save(uc)
    return uc


def generate_many(workspace: str, n: int, creator: str = "scheduler:bootstrap",
                       seed: int | None = None) -> list[use_cases.UseCase]:
    out = []
    for i in range(n):
        s = (seed + i) if seed is not None else None
        out.append(generate_one(workspace, creator, seed=s))
    return out
