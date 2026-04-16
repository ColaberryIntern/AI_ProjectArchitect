"""10-industry smoke test for the taxonomy-driven recommendation engine.

Runs synthetic sessions through `recommend_design` and writes a markdown
report plus raw JSON to `output/advisory/_taxonomy_smoke/`.

Mix: 5 seeded industries (no LLM call) + 5 novel industries (sync LLM
generation, then cached for future clients in that industry).

Usage:
    python scripts/taxonomy_smoke_10.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from config.settings import ADVISORY_OUTPUT_DIR
from execution.advisory.agent_generator import generate_agents
from execution.advisory.capability_catalog import CAPABILITY_CATALOG
from execution.advisory.capability_mapper import AI_SYSTEMS, BUSINESS_OUTCOMES, map_capabilities, should_include_cory
from execution.advisory.problem_analyzer import analyze_problems
from execution.advisory.recommendation_engine import recommend_design
from execution.advisory.taxonomy_registry import lookup_taxonomy


CAP_BY_ID = {c["id"]: c for c in CAPABILITY_CATALOG}


SCENARIOS = [
    {
        "name": "Regional Electric Cooperative",
        "business_idea": "Regional member-owned electric cooperative serving 65,000 rural households, growing 8% annually.",
        "answers": [
            "We are an electric utility cooperative. We generate and distribute power across 12 rural counties.",
            "About 240 employees; 180 are field crews, linemen, and dispatchers.",
            "Field operations, customer service, and compliance with NERC standards.",
            "Manual crew dispatch for outages, vegetation management on fixed 4-year cycles, slow storm response.",
            "Members call us, we log the outage, dispatchers radio a crew. Billing is quarterly.",
            "Outage Management System, SCADA, NISC billing, ArcGIS, old Excel rate-case spreadsheets.",
            "Most data is in siloed systems — SCADA, OMS, GIS do not talk to each other.",
            "Writing up post-storm damage reports, tracking vegetation crews, preparing NERC filings by hand.",
            "Roughly $3M per year on technology. Ideally see results in 12 months.",
            "Faster restoration after storms, lower vegetation spend, predictive maintenance on substations.",
        ],
    },
    {
        "name": "Mid-Market Freight Brokerage",
        "business_idea": "Asset-light freight brokerage booking full-truckload shipments across the lower 48.",
        "answers": [
            "Freight brokerage and 3PL. We match shippers with motor carriers and handle the financial settlement.",
            "85 employees, mostly sales reps and back-office ops.",
            "Sales, carrier settlement, and accounts receivable.",
            "Manual carrier invoice matching, slow settlement, and disputes from shippers on detention charges.",
            "Shipper books load, we source a carrier, we invoice shipper, we pay carrier after PoD.",
            "McLeod TMS, QuickBooks, Outlook, DAT load board, a lot of spreadsheets.",
            "Data is scattered across TMS, email, and spreadsheets. No single source of truth.",
            "Matching invoices to rate confirmations, chasing carriers for missing paperwork, detention disputes.",
            "About $900K/year budget, 6-12 month horizon.",
            "Faster cash conversion, fewer billing disputes, reps closing more loads per day.",
        ],
    },
    {
        "name": "B2B SaaS HR Tech",
        "business_idea": "Mid-market B2B SaaS platform for mid-size company HR teams — onboarding, benefits, and compliance.",
        "answers": [
            "B2B SaaS. HR compliance and onboarding platform sold to HR directors at 500-5000 person companies.",
            "About 120 employees — 35 in product, 25 in sales, 18 in customer success.",
            "Sales, customer success, and product engineering.",
            "Long sales cycles with procurement, churn in year 2, overwhelmed CSMs handling too many accounts.",
            "MQL from marketing → SDR qualifies → AE demos → procurement → implementation → CSM handoff.",
            "HubSpot, Salesforce, Gong, Intercom, Jira, Segment, Snowflake.",
            "Product usage data sits in Snowflake but sales and CS rarely use it to prioritize.",
            "Manual QBR deck prep, onboarding runbooks assembled by hand, scoring account health by gut feel.",
            "~$1.8M annual tech and tooling budget. Most impact desired within 9 months.",
            "Double net revenue retention, cut time-to-first-value in onboarding by half.",
        ],
    },
    {
        "name": "Multi-Location Medical Clinic",
        "business_idea": "Primary-care and urgent-care clinic group with 14 locations across two states.",
        "answers": [
            "Multi-location outpatient medical clinic and urgent care. Primary care plus walk-in acute visits.",
            "About 380 employees — 90 clinicians, 120 nursing and MA staff, 80 front desk and billing.",
            "Clinical operations, revenue cycle, and patient experience.",
            "Credentialing delays on new hires, prior auth denials, long patient wait times at peak hours.",
            "Patient books online or walks in → triage → clinician visit → orders → billing → follow-up.",
            "Epic EHR, Athena billing, a home-grown patient portal, spreadsheets for staffing.",
            "EHR has the data but clinical ops, RCM, and scheduling rarely stitch it together.",
            "Prior auth phone calls, credentialing packet assembly, staffing forecast by hand every Friday.",
            "~$2.4M budget, 12-month horizon.",
            "Lower prior-auth denial rate, faster credentialing, better patient scheduling.",
        ],
    },
    {
        "name": "Boutique Staffing Agency",
        "business_idea": "Niche staffing and recruiting firm placing light-industrial workers across the Midwest.",
        "answers": [
            "Light-industrial staffing agency. We place warehouse, forklift, and packaging workers at manufacturers.",
            "About 55 internal staff, plus ~900 W-2 associates placed at client sites each week.",
            "Recruiting, client account management, and payroll.",
            "High associate turnover, long time-to-fill, and thin margins squeezed by bill/pay spread.",
            "Client opens a req → recruiter sources → associate placed → time sheets → payroll → bill client.",
            "Bullhorn ATS, ADP Workforce Now, Indeed and ZipRecruiter for sourcing, a lot of texting.",
            "ATS has candidate data but no one uses it for matching beyond keyword search.",
            "Screening resumes, calling associates to confirm shifts, reconciling time sheets.",
            "~$600K/year, 6-month horizon for first wins.",
            "Cut time-to-fill in half, reduce no-show rate, keep associates working 4+ weeks on average.",
        ],
    },
    # ── Novel industries (no seeded profile match) ─────────────────────
    {
        "name": "Natural Wine Importer",
        "business_idea": "Niche wine import brand bringing small-production biodynamic estates into US restaurants and indie retailers.",
        "answers": [
            "Import and distribute low-intervention wines to sommeliers, indie wine shops, and restaurants.",
            "14 employees including 3 sales reps, 2 compliance, 2 warehouse.",
            "Sales, compliance, and allocation planning.",
            "Scarce allocations getting mis-assigned to accounts, TTB paperwork, restaurant slow-pay.",
            "Producer allocation lands → we allocate to accounts → samples out → orders → freight → invoice.",
            "A barely-used NetSuite instance, QuickBooks, Commerce7, and Google Sheets everywhere.",
            "Our allocation history is in sales reps' heads and a shared spreadsheet.",
            "Allocating bottles to the right accounts, chasing state-by-state compliance, slow invoicing.",
            "Maybe $150K we could reallocate to tooling.",
            "Stop under-serving our best accounts, shorten DSO, keep compliance clean.",
        ],
    },
    {
        "name": "Independent Board Game Publisher",
        "business_idea": "Independent tabletop board game publisher funding titles on Kickstarter and selling through hobby distributors.",
        "answers": [
            "Independent board game publisher. Kickstarter campaigns then hobby retail and direct.",
            "11 full-time plus a rotating bench of freelance designers, illustrators, and playtest leads.",
            "Crowdfunding operations, fulfillment, and community/customer support.",
            "Kickstarter fulfillment delays from China, backer support ticket floods, forecasting print runs.",
            "Pitch → Kickstarter → pledge manager → manufacturing → freight → pledge fulfillment → retail.",
            "BackerKit, Gamefound, ShipStation, Discord, Zendesk, QuickBooks.",
            "Backer data lives in BackerKit, support in Zendesk, shipping in ShipStation — no join.",
            "Answering shipping-status questions, reconciling pledge add-ons, chasing factory photos.",
            "Under $100K/year to spend on ops tooling.",
            "Ship on time, cut support ticket volume in half, forecast reprints before we stock out.",
        ],
    },
    {
        "name": "Artisan Leather Atelier",
        "business_idea": "Small artisan leather-goods atelier handcrafting bags sold via e-commerce and a Brooklyn flagship.",
        "answers": [
            "High-end handcrafted leather goods — wallets, totes, bags. Online plus one retail location.",
            "About 16 employees — 10 makers on the bench, 3 e-commerce, 3 retail.",
            "Production scheduling, e-commerce fulfillment, and clienteling.",
            "Custom order backlogs, unpredictable hide yields, VIP clients not getting attention.",
            "Order placed (custom or stock) → queued on bench → QC → shipped or in-store pickup.",
            "Shopify, Klaviyo, Lightspeed POS, a whiteboard for the bench schedule.",
            "Order data in Shopify, bench schedule on the wall — they never reconcile.",
            "Tracking hide usage, resequencing the bench, following up with VIP repair customers.",
            "Probably $40-60K in year one.",
            "Keep bench full without overflow, protect VIP relationships, hit promised ship dates.",
        ],
    },
    {
        "name": "Regional Grain Cooperative",
        "business_idea": "Farmer-owned grain cooperative running six country elevators and a rail-served terminal in the upper Midwest.",
        "answers": [
            "Grain elevator and ag services cooperative — corn, soybeans, wheat. Farmer-members deliver grain we store, blend, and ship.",
            "About 95 employees across six locations plus the terminal.",
            "Grain operations, merchandising, and agronomy services.",
            "Shrink and moisture disputes, slow rail loading, blending mistakes, crop-input sales cycles.",
            "Member delivers → weigh → grade → storage bin → blend → rail/truck out → settle to member.",
            "AgVantage scale tickets, DTN for markets, QuickBooks, a lot of paper scale tickets still.",
            "Scale ticket data sits in AgVantage; agronomy data in a separate spreadsheet.",
            "Reconciling scale tickets, scheduling rail loading crews, agronomy visit reports.",
            "~$350K/year for technology.",
            "Lower shrink, faster rail turns, proactive agronomy outreach to members.",
        ],
    },
    {
        "name": "Pet Grooming Franchise",
        "business_idea": "Franchised pet-grooming chain with 42 corporate and franchisee-owned locations across the Sun Belt.",
        "answers": [
            "Pet grooming franchise — mostly dogs, some cats. Retail-adjacent salons.",
            "About 380 groomers, bathers, and salon managers across 42 locations.",
            "Salon operations, franchisee support, and customer booking.",
            "No-show appointments, high groomer turnover, franchisees calling HQ for the same questions.",
            "Customer books online or walks in → intake → groom → photo → checkout → rebook.",
            "Gingr booking, Square POS, a franchise intranet on SharePoint, Slack.",
            "Booking data per salon, no rollup — HQ cannot see chain-level patterns.",
            "Confirming appointments, answering franchisee how-do-I questions, scheduling groomers around demand.",
            "~$500K/year.",
            "Cut no-shows, keep groomers longer, automate franchisee support triage.",
        ],
    },
]


def _qa_payload(scenario: dict) -> dict:
    """Turn a scenario into an advisory session dict."""
    answers = [
        {
            "question_id": f"q{i+1}",
            "question_text": f"Q{i+1}",
            "answer_text": txt,
            "answered_at": "2026-04-15T00:00:00Z",
        }
        for i, txt in enumerate(scenario["answers"])
    ]
    return {
        "business_idea": scenario["business_idea"],
        "answers": answers,
    }


def run() -> list[dict]:
    """Run all scenarios: Step 4 (recommend_design) + Step 5 (map_capabilities)."""
    results = []
    for scenario in SCENARIOS:
        session = _qa_payload(scenario)
        try:
            recs = recommend_design(session)
            error = None
        except Exception as exc:
            recs = {}
            error = repr(exc)

        caps = {}
        agents: list = []
        if not error:
            session_for_caps = dict(session)
            session_for_caps["selected_outcomes"] = list(recs.get("recommended_outcomes", {}).keys())
            session_for_caps["selected_ai_systems"] = list(recs.get("recommended_systems", {}).keys())
            try:
                caps = map_capabilities(session_for_caps)
            except Exception as exc:
                error = f"map_capabilities: {exc!r}"

            # Step 6: generate agents using taxonomy
            if not error:
                selected_caps = caps.get("recommended", [])
                session_for_caps["selected_capabilities"] = selected_caps
                try:
                    industry_text = scenario["business_idea"]
                    if session["answers"]:
                        industry_text = f"{scenario['business_idea']}\n\n{session['answers'][0]['answer_text']}"
                    combined = scenario["business_idea"] + " " + " ".join(a["answer_text"] for a in session["answers"])
                    taxonomy = lookup_taxonomy(industry_text.strip(), combined)
                except Exception:
                    taxonomy = None
                try:
                    problem_analysis = analyze_problems(session_for_caps)
                    agents = generate_agents(
                        selected_caps,
                        include_cory=should_include_cory(session_for_caps),
                        problem_analysis=problem_analysis,
                        industry_profile=taxonomy,
                    )
                except Exception as exc:
                    error = f"generate_agents: {exc!r}"

        results.append({
            "name": scenario["name"],
            "business_idea": scenario["business_idea"],
            "recs": recs,
            "caps": caps,
            "agents": agents,
            "error": error,
        })
    return results


def _label_map(items: list[dict]) -> dict[str, str]:
    return {it["id"]: it.get("label", it["id"]) for it in items}


def _caps_by_dept() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for cap in CAPABILITY_CATALOG:
        grouped.setdefault(cap.get("department", "Other"), []).append(cap)
    return grouped


CAPS_BY_DEPT = _caps_by_dept()


def build_report(results: list[dict]) -> str:
    outcome_labels = _label_map(BUSINESS_OUTCOMES)
    system_labels = _label_map(AI_SYSTEMS)
    all_outcome_ids = [o["id"] for o in BUSINESS_OUTCOMES]
    all_system_ids = [s["id"] for s in AI_SYSTEMS]

    lines = []
    lines.append("# Taxonomy-Driven Recommendation Smoke Test — 10 Industries")
    lines.append("")
    lines.append(f"Run at: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("For each scenario: what was **on the table** (the full outcome + system catalogs the user sees in Step 4) vs what the engine **pre-selected**. Industry source shows whether the taxonomy came from the 13 seeded profiles, a prior-client cache hit, or a fresh sync LLM generation for a novel industry.")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| # | Scenario | Industry detected | Source | Picked outcomes | Picked systems |")
    lines.append("|---|----------|-------------------|--------|-----------------|----------------|")
    for i, r in enumerate(results, 1):
        recs = r["recs"]
        industry = recs.get("industry_label", "—")
        source = recs.get("industry_source", "—")
        picked_o = ", ".join(outcome_labels.get(k, k) for k in recs.get("recommended_outcomes", {}).keys()) or "—"
        picked_s = ", ".join(system_labels.get(k, k) for k in recs.get("recommended_systems", {}).keys()) or "—"
        lines.append(f"| {i} | {r['name']} | {industry} | {source} | {picked_o} | {picked_s} |")
    lines.append("")

    # Per-scenario detail
    for i, r in enumerate(results, 1):
        lines.append(f"## {i}. {r['name']}")
        lines.append("")
        lines.append(f"**Business idea:** {r['business_idea']}")
        lines.append("")
        if r["error"]:
            lines.append(f"**ERROR:** `{r['error']}`")
            lines.append("")
            continue
        recs = r["recs"]
        lines.append(f"**Industry detected:** `{recs.get('industry_label', '—')}` (key `{recs.get('industry_key', '—')}`, source `{recs.get('industry_source', '—')}`)")
        lines.append("")

        lines.append("### Solution page (Step 4) — outcomes + AI systems")
        lines.append("")
        lines.append("**Business Outcomes — options on the table (pre-selection in bold):**")
        lines.append("")
        picked_outcomes = recs.get("recommended_outcomes", {})
        for oid in all_outcome_ids:
            label = outcome_labels[oid]
            if oid in picked_outcomes:
                lines.append(f"- **[✓] {label}** — {picked_outcomes[oid]}")
            else:
                lines.append(f"- [ ] {label}")
        lines.append("")

        lines.append("**AI Systems — options on the table (pre-selection in bold):**")
        lines.append("")
        picked_systems = recs.get("recommended_systems", {})
        for sid in all_system_ids:
            label = system_labels[sid]
            if sid in picked_systems:
                lines.append(f"- **[✓] {label}** — {picked_systems[sid]}")
            else:
                lines.append(f"- [ ] {label}")
        lines.append("")

        # ── Step 5: Departments page — every capability, status + reason ──
        caps = r.get("caps", {})
        if caps:
            recommended_set = set(caps.get("recommended", []))
            reasoning = caps.get("reasoning", {})
            exclusion = caps.get("exclusion_reasons", {})
            confidence = caps.get("confidence_scores", {})

            lines.append("### Departments page (Step 5) — what was suggested vs. what else was offered")
            lines.append("")
            lines.append(
                f"Industry-aware dept allowance from taxonomy. "
                f"`✓` = pre-selected · `☐` = available, not pre-selected · `✗` = hard-blocked"
            )
            lines.append("")

            # Step 6 agents (pulled from taxonomy agent_roles when available)
            agents = r.get("agents") or []
            if agents:
                lines.append("### Results page (Step 6) — agents generated")
                lines.append("")
                industry_agents = [a for a in agents if str(a.get("id", "")).startswith("agent_industry_")]
                other_agents = [a for a in agents if a not in industry_agents]
                if industry_agents:
                    lines.append("**Industry-specific agents (pulled from taxonomy.agent_roles):**")
                    lines.append("")
                    for a in industry_agents:
                        lines.append(f"- **{a.get('name', '?')}** ({a.get('department', '?')}) — {a.get('role', a.get('description', ''))}")
                    lines.append("")
                if other_agents:
                    lines.append("**Other agents (derived from capabilities):**")
                    lines.append("")
                    for a in other_agents[:6]:
                        lines.append(f"- {a.get('name', '?')} ({a.get('department', '?')})")
                    if len(other_agents) > 6:
                        lines.append(f"- _...and {len(other_agents) - 6} more_")
                    lines.append("")

            for dept in sorted(CAPS_BY_DEPT.keys()):
                dept_caps = CAPS_BY_DEPT[dept]
                # Sort: selected first, then by confidence desc
                dept_caps_sorted = sorted(
                    dept_caps,
                    key=lambda c: (
                        0 if c["id"] in recommended_set else 1,
                        -confidence.get(c["id"], 0),
                    ),
                )
                lines.append(f"- **{dept}**")
                for cap in dept_caps_sorted:
                    cid = cap["id"]
                    name = cap.get("name", cid)
                    if cid in recommended_set:
                        reason = " · ".join(reasoning.get(cid, [])) or "selected"
                        lines.append(f"  - **✓ {name}** — {reason}")
                    elif cid in exclusion:
                        why = exclusion[cid]
                        lines.append(f"  - ✗ {name} — _{why}_")
                    else:
                        conf = confidence.get(cid, 0)
                        lines.append(f"  - ☐ {name} — offered (confidence {conf}%)")
            lines.append("")

    return "\n".join(lines)


def main():
    print(f"Running {len(SCENARIOS)} scenarios...")
    results = run()

    out_dir = ADVISORY_OUTPUT_DIR / "_taxonomy_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    raw_path = out_dir / f"smoke_{ts}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    report = build_report(results)
    report_path = out_dir / f"report_{ts}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\nRaw results:  {raw_path}")
    print(f"Report:       {report_path}\n")
    print(report)


if __name__ == "__main__":
    main()
