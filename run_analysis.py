"""Run full consultant analysis on freight brokerage PRD."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from execution.advisory.industry_profiles import detect_industry, get_profile, get_dept_ftes, estimate_revenue

business_idea = (
    "We are building a financial operating system for freight brokerages and 3PLs "
    "that orchestrates the full post-delivery money flow: billing, invoicing (AR), "
    "disputes, settlement (AP). The product converts delivered shipments into correct "
    "customer invoices that get paid faster and correct carrier settlements paid safely. "
    "Key problems: document-driven gating (BOL, POD), accessorial charge complexity, "
    "two-sided invoicing with asymmetrical risk, disputes as normal process, fraud and "
    "identity risk, and regulatory compliance (FMCSA surety bonds)."
)

industry_id, confidence = detect_industry(business_idea)
profile = get_profile(industry_id)

print("=" * 70)
print("PHASE 1: INDUSTRY DETECTION")
print("=" * 70)
print(f"Detected: {profile['label']} (ID: {industry_id})")
print(f"Confidence: {confidence}")
print()

# Phase 2
employee_count = 150
dept_ftes = get_dept_ftes(industry_id, employee_count)
est_revenue = estimate_revenue(industry_id, employee_count)
print("=" * 70)
print("PHASE 2: COMPANY SIZING")
print("=" * 70)
print(f"Employee Count: {employee_count}")
print(f"Estimated Revenue: ${est_revenue:,.0f}")
print("Department FTEs:")
for dept, count in sorted(dept_ftes.items(), key=lambda x: -x[1]):
    cost = profile['dept_structure'][dept]['avg_fte_cost']
    print(f"  {dept:20s}: {count:3d} FTEs @ ${cost:,}/yr = ${count * cost:,.0f}")
print()

# Phase 3
print("=" * 70)
print("PHASE 3: IDENTIFIED PROBLEMS (industry pain catalog)")
print("=" * 70)
for pain in profile.get('pain_catalog', []):
    print(f"  [{pain['id']}] {pain['label']}")
    print(f"    Root Cause: {pain['root_cause']}")
    impact_pct = pain['typical_impact_pct']
    est_impact = est_revenue * impact_pct
    print(f"    Typical Impact: {impact_pct*100:.0f}% of revenue = ~${est_impact:,.0f}/yr")
    print()

# Phase 4
print("=" * 70)
print("PHASE 4: RECOMMENDED SYSTEMS (consultant-grade names)")
print("=" * 70)
for dept, name in profile.get('system_names', {}).items():
    print(f"  {name:40s} ({dept})")
print()

# Phase 5
print("=" * 70)
print("PHASE 5: AGENT ARCHITECTURE")
print("=" * 70)
total_agents = 0
for dept, roles in profile.get('agent_roles', {}).items():
    print(f"  [{dept.upper()}]")
    for role in roles:
        print(f"    - {role['name']}")
        print(f"      Role: {role['role']}")
        total_agents += 1
    print()
print(f"  Total specialized agents: {total_agents}")
print(f"  + AI Control Tower (executive orchestrator)")
print()

# Phase 6
print("=" * 70)
print("PHASE 6: FINANCIAL IMPACT ESTIMATE")
print("=" * 70)

# Only departments directly automated by the product get savings
# Management, HR, technology are not directly automated by a billing system
automatable_depts = {"billing", "collections_ar", "carrier_pay_ap", "operations", "compliance", "finance"}
# Department-specific automation rates (not flat 35%)
dept_automation_rates = {
    "billing": 0.45,          # Highest — document processing, rate validation, charge assembly
    "collections_ar": 0.35,   # Invoice composition, aging prioritization, short-pay matching
    "carrier_pay_ap": 0.40,   # Three-way matching, payee verification, quick pay processing
    "operations": 0.20,       # Dispute evidence binders, duplicate detection (still needs humans)
    "compliance": 0.30,       # Monitoring and reporting automated, judgment calls stay human
    "finance": 0.15,          # Margin reporting automated, strategic decisions stay human
}

total_savings = 0
print("  LABOR COST SAVINGS (directly automated departments only):")
for dept in automatable_depts:
    if dept not in profile['dept_structure']:
        continue
    info = profile['dept_structure'][dept]
    ftes = dept_ftes.get(dept, 0)
    cost = info['avg_fte_cost']
    auto_rate = dept_automation_rates.get(dept, 0.20)
    savings = ftes * cost * auto_rate
    if savings > 0:
        total_savings += savings
        print(f"    {dept:20s}: {ftes} FTEs x ${cost:,} x {auto_rate*100:.0f}% automation = ${savings:,.0f}/yr")

print(f"  Total Labor Savings: ${total_savings:,.0f}/yr")
print()

# Revenue impact reframed as loss prevention / cash flow improvement
total_prevention = 0
print("  LOSS PREVENTION & CASH FLOW IMPROVEMENT:")
prevention_items = {
    "billing_accuracy":   ("Billing error reduction", 0.015),       # 1.5% of revenue recovered
    "collections_ar":     ("DSO reduction / faster collection", 0.008),  # 0.8% freed working capital cost
    "carrier_pay_ap":     ("Avoided overpayments & fraud", 0.010),  # 1.0% of carrier payments saved
    "dispute_reduction":  ("Dispute resolution cost reduction", 0.005),  # 0.5% from fewer/faster disputes
}
for key, (label, pct) in prevention_items.items():
    amount = est_revenue * pct
    total_prevention += amount
    print(f"    {label:45s}: {pct*100:.1f}% of ${est_revenue:,.0f} = ${amount:,.0f}/yr")
print(f"  Total Loss Prevention: ${total_prevention:,.0f}/yr")
print()

annual_benefit = total_savings + total_prevention

# Implementation cost scales with company size and system complexity
base_impl_cost = 150_000  # Base platform cost
per_agent_cost = 35_000   # Per agent customization/integration
integration_cost = min(est_revenue * 0.005, 200_000)  # ~0.5% of revenue, capped
total_agents_count = sum(len(roles) for roles in profile.get('agent_roles', {}).values())
implementation_cost = round(base_impl_cost + (total_agents_count * per_agent_cost) + integration_cost)

# Year 1 includes implementation, Years 2-3 are maintenance (20% of impl)
annual_maintenance = round(implementation_cost * 0.20)
three_year_cost = implementation_cost + (annual_maintenance * 2)
three_year_benefit = annual_benefit * 3
three_year_roi = ((three_year_benefit - three_year_cost) / three_year_cost) * 100
payback_months = (implementation_cost / annual_benefit) * 12

print("  SUMMARY:")
print(f"    Annual Labor Savings:     ${total_savings:,.0f}")
print(f"    Annual Loss Prevention:   ${total_prevention:,.0f}")
print(f"    Total Annual Benefit:     ${annual_benefit:,.0f}")
print(f"    Implementation Cost:      ${implementation_cost:,.0f} (Year 1)")
print(f"    Annual Maintenance:       ${annual_maintenance:,.0f} (Years 2+)")
print(f"    3-Year Total Cost:        ${three_year_cost:,.0f}")
print(f"    3-Year Total Benefit:     ${three_year_benefit:,.0f}")
print(f"    Payback Period:           {payback_months:.0f} months")
print(f"    3-Year ROI:               {three_year_roi:.0f}%")
print()

# Phase 7
print("=" * 70)
print("PHASE 7: ROOT CAUSE ANALYSIS (PRD-specific)")
print("=" * 70)
prd_problems = [
    {
        "stated": "Document-driven gating and paperwork debt",
        "root_cause": "BOL/POD/lumper receipts must be received and verified before billing starts. Missing or unclear paperwork delays the entire payment cycle.",
        "financial_impact": f"Invoice cycle delayed 3-5 days per shipment x {employee_count*20} shipments/month x $15/day = ~${employee_count*20*4*15*12:,.0f}/yr",
        "system": "Document Intelligence Engine",
    },
    {
        "stated": "Accessorial charge complexity and disputes",
        "root_cause": "Detention, layover, TONU, lumper fees trigger manual review. Evidence requirements inconsistent. Payers hold entire invoices over single disputed lines.",
        "financial_impact": f"15% of invoices disputed, avg $85/dispute resolution = ~${int(employee_count*20*12*0.15*85):,.0f}/yr in labor + delayed collection",
        "system": "Billing Accuracy & Accessorial Rules Engine",
    },
    {
        "stated": "Two-sided invoicing with asymmetrical risk",
        "root_cause": "Brokers pay carriers before collecting from customers, carrying cash-flow and counterparty risk. No automated AR/AP orchestration.",
        "financial_impact": f"DSO reduction 45 to 30 days on ${est_revenue:,.0f} = ~${int(est_revenue/365*15*0.06):,.0f}/yr freed working capital",
        "system": "AR/AP Orchestration Platform",
    },
    {
        "stated": "Fraud and identity risk (double brokering, carrier identity theft)",
        "root_cause": "No automated carrier identity verification. Payee bank changes not flagged. Double-brokering creates payment disputes and chain-of-custody confusion.",
        "financial_impact": f"Industry fraud 2-5% of settlements. On ${int(est_revenue*0.7):,.0f} carrier payments = ${int(est_revenue*0.7*0.03):,.0f}/yr exposure",
        "system": "Carrier Trust & Fraud Detection System",
    },
    {
        "stated": "Dispute resolution is normal process, not edge case",
        "root_cause": "Disputes require evidence from 5+ systems, manual correspondence, negotiated outcomes. No taxonomy, no playbooks, no automated evidence binders.",
        "financial_impact": f"8 hours/dispute x 200 disputes/month x $45/hr = ~${8*200*45*12:,.0f}/yr",
        "system": "Dispute Resolution Intelligence",
    },
]

for i, p in enumerate(prd_problems, 1):
    print(f"  Problem {i}: {p['stated']}")
    print(f"    Root Cause: {p['root_cause']}")
    print(f"    Financial Impact: {p['financial_impact']}")
    print(f"    Recommended System: {p['system']}")
    print()

# Final Report
print("=" * 70)
print("CONSULTANT RECOMMENDATION SUMMARY")
print("=" * 70)
print(f"""
COMPANY: Mid-size Freight Brokerage / 3PL
INDUSTRY: {profile['label']}
EMPLOYEES: {employee_count}
EST. REVENUE: ${est_revenue:,.0f}
AI MATURITY: Low (no existing AI systems described)

RECOMMENDED AI SYSTEMS:
  1. Document Intelligence Engine
  2. Billing Accuracy & Accessorial Rules Engine
  3. AR/AP Orchestration Platform
  4. Carrier Trust & Fraud Detection System
  5. Dispute Resolution Intelligence
  6. AI Freight Financial Control Tower

EXPECTED IMPACT:
  Annual Labor Savings:     ${total_savings:,.0f}
  Annual Loss Prevention:   ${total_prevention:,.0f}
  Total Annual Benefit:     ${annual_benefit:,.0f}
  Implementation Cost:      ${implementation_cost:,.0f}
  Payback Period:           {payback_months:.0f} months
  3-Year ROI:               {three_year_roi:.0f}%

CRITICAL DESIGN PRINCIPLES:
  - AI handles: document classification, extraction, anomaly detection, evidence summarization
  - Deterministic handles: contract lookup, rate math, charge eligibility, state transitions, audit
  - Human-in-the-loop: liability admission, credits over threshold, payee changes, first-time carriers
  - Event-driven workflow with explicit state machines (billing, invoice, settlement)
  - Idempotency required (duplicate document/event handling)
  - SOC 2 aligned audit trails
""")
