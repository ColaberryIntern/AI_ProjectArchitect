"""Industry Profiles for Consultant-Grade Recommendations.

Each profile contains real-world benchmarks calibrated to make the
AI Workforce Designer output feel like it came from a senior operations
consultant who deeply understands the user's vertical.
"""

INDUSTRY_PROFILES = {

    # ─── Logistics & Supply Chain ───────────────────────────────────────
    "logistics": {
        "label": "Logistics & Supply Chain",
        "aliases": ["shipping", "freight", "trucking", "warehousing", "distribution", "3pl", "last mile", "courier", "fulfillment"],
        "dept_structure": {
            "operations":       {"pct_of_headcount": 0.45, "avg_fte_cost": 52_000},
            "technology":       {"pct_of_headcount": 0.08, "avg_fte_cost": 95_000},
            "customer_support": {"pct_of_headcount": 0.12, "avg_fte_cost": 42_000},
            "sales":            {"pct_of_headcount": 0.10, "avg_fte_cost": 72_000},
            "finance":          {"pct_of_headcount": 0.05, "avg_fte_cost": 78_000},
            "management":       {"pct_of_headcount": 0.08, "avg_fte_cost": 115_000},
            "hr":               {"pct_of_headcount": 0.04, "avg_fte_cost": 62_000},
            "field_services":   {"pct_of_headcount": 0.08, "avg_fte_cost": 48_000},
        },
        "revenue_per_employee": 180_000,
        "avg_margin": 0.06,
        "revenue_lift_by_dept": {
            "operations": 0.12, "sales": 0.05, "customer_support": 0.03, "technology": 0.02,
        },
        "ai_adoption_rate": 0.18,
        "pain_catalog": [
            {"id": "route_inefficiency", "label": "Route Inefficiency", "root_cause": "No real-time visibility into traffic, driver location, or delivery priority", "financial_formula": "wasted_miles_per_day * cost_per_mile * 260", "typical_impact_pct": 0.08},
            {"id": "manual_dispatch", "label": "Manual Dispatch", "root_cause": "Dispatchers make phone calls instead of using automated assignment based on proximity and skills", "financial_formula": "dispatch_hours_per_day * hourly_rate * 260", "typical_impact_pct": 0.05},
            {"id": "shipment_visibility", "label": "No Shipment Visibility", "root_cause": "Data spread across emails, spreadsheets, and disconnected TMS with no single source of truth", "financial_formula": "lost_shipments + customer_penalties + manual_tracking_labor", "typical_impact_pct": 0.04},
            {"id": "capacity_waste", "label": "Underutilized Capacity", "root_cause": "Trucks running partial loads because planning is manual and not optimized for consolidation", "financial_formula": "empty_miles_pct * total_fleet_miles * cost_per_mile", "typical_impact_pct": 0.10},
            {"id": "driver_turnover", "label": "High Driver Turnover", "root_cause": "Poor scheduling, excessive wait times, and lack of driver satisfaction visibility", "financial_formula": "turnover_rate * drivers * recruitment_cost", "typical_impact_pct": 0.06},
        ],
        "system_names": {
            "operations": "Fleet & Route Intelligence System",
            "customer_support": "Shipment Communication Engine",
            "sales": "Freight Pipeline Optimizer",
            "finance": "Freight Cost Intelligence",
            "technology": "AI Logistics Control Tower",
        },
        "agent_roles": {
            "operations": [
                {"name": "AI Route Optimizer", "role": "Calculates optimal delivery routes accounting for traffic, time windows, vehicle capacity, and driver hours"},
                {"name": "AI Dispatch Coordinator", "role": "Auto-assigns drivers to loads based on proximity, endorsements, hours-of-service, and priority"},
                {"name": "AI Load Planner", "role": "Consolidates partial shipments to maximize trailer utilization and reduce deadhead miles"},
                {"name": "AI Delay Predictor", "role": "Monitors weather, traffic, and port congestion to predict delays before they cascade"},
            ],
            "customer_support": [
                {"name": "AI Shipment Tracker", "role": "Provides real-time ETAs to customers automatically via text, email, and portal updates"},
                {"name": "AI Claims Handler", "role": "Processes damage and delay claims with automated documentation and resolution routing"},
            ],
        },
    },

    # ─── Freight Financial Operations (Brokerage / 3PL billing & settlement) ──
    "freight_finops": {
        "label": "Freight Brokerage & 3PL Financial Operations",
        "aliases": ["freight broker", "3pl", "freight brokerage", "load board", "freight bill", "freight audit", "freight payment", "carrier settlement", "freight invoice", "bol", "pod", "proof of delivery", "bill of lading", "accessorial", "detention", "lumper", "tonu", "quick pay", "factoring", "double broker", "rate confirmation", "fmcsa"],
        "dept_structure": {
            "billing":          {"pct_of_headcount": 0.20, "avg_fte_cost": 52_000},
            "collections_ar":   {"pct_of_headcount": 0.12, "avg_fte_cost": 48_000},
            "carrier_pay_ap":   {"pct_of_headcount": 0.12, "avg_fte_cost": 50_000},
            "operations":       {"pct_of_headcount": 0.25, "avg_fte_cost": 55_000},
            "compliance":       {"pct_of_headcount": 0.04, "avg_fte_cost": 72_000},
            "technology":       {"pct_of_headcount": 0.08, "avg_fte_cost": 95_000},
            "finance":          {"pct_of_headcount": 0.06, "avg_fte_cost": 82_000},
            "management":       {"pct_of_headcount": 0.08, "avg_fte_cost": 120_000},
            "hr":               {"pct_of_headcount": 0.05, "avg_fte_cost": 58_000},
        },
        "revenue_per_employee": 350_000,
        "avg_margin": 0.12,
        "revenue_lift_by_dept": {
            # Financial ops systems don't generate new revenue — they prevent leakage and accelerate collection
            "billing": 0.02,           # 2% revenue recovered from billing accuracy improvements
            "collections_ar": 0.03,    # 3% from faster collection and reduced write-offs
            "carrier_pay_ap": 0.01,    # 1% from avoided overpayments
            "operations": 0.01,        # 1% from reduced operational rework
        },
        "ai_adoption_rate": 0.12,
        "pain_catalog": [
            {"id": "document_gating", "label": "Document-Driven Payment Delays", "root_cause": "BOL, POD, and lumper receipts must be received and verified before billing can start. Missing or unclear paperwork delays the entire invoice cycle by 3-5 days per shipment.", "financial_formula": "delayed_shipments_per_month * days_delayed * daily_interest_cost", "typical_impact_pct": 0.03},
            {"id": "accessorial_disputes", "label": "Accessorial Charge Disputes", "root_cause": "Detention, layover, TONU, and lumper charges trigger manual review because evidence requirements (timestamps, receipts, pre-approvals) are inconsistent. Payers hold entire invoices over single disputed accessorial lines.", "financial_formula": "disputed_invoices_pct * total_invoiced * resolution_cost_per_dispute", "typical_impact_pct": 0.04},
            {"id": "two_sided_risk", "label": "Two-Sided Invoicing Cash Flow Risk", "root_cause": "Brokers invoice shippers (AR) while carriers invoice brokers (AP). When brokers pay carriers before collecting from customers, they carry cash-flow and counterparty risk. No automated AR/AP orchestration.", "financial_formula": "avg_dso_days * daily_revenue * cost_of_capital", "typical_impact_pct": 0.02},
            {"id": "dispute_as_normal", "label": "Disputes Are Normal Process, Not Edge Cases", "root_cause": "Freight disputes involve evidence assembly from 5+ systems, manual correspondence, and negotiated outcomes. No structured taxonomy, no playbooks, no automated evidence binders. Each dispute averages 8 hours of labor.", "financial_formula": "disputes_per_month * hours_per_dispute * hourly_rate", "typical_impact_pct": 0.03},
            {"id": "fraud_identity", "label": "Carrier Fraud and Identity Theft", "root_cause": "No automated carrier identity verification against FMCSA records. Payee bank detail changes not flagged. Double-brokering schemes create payment disputes, chain-of-custody confusion, and direct financial loss.", "financial_formula": "carrier_payments * fraud_rate", "typical_impact_pct": 0.02},
            {"id": "billing_accuracy", "label": "Billing Errors and Margin Leakage", "root_cause": "Rate confirmation terms applied incorrectly, fuel surcharge calculations wrong, customer SOP rules misapplied across accounts. Systematic errors not caught until customer disputes.", "financial_formula": "error_rate * total_billed * avg_undercharge", "typical_impact_pct": 0.03},
            {"id": "compliance_audit", "label": "FMCSA Compliance and Audit Burden", "root_cause": "Manual tracking of broker surety bond status, carrier authority verification, and regulatory filing requirements. Audit prep takes weeks.", "financial_formula": "compliance_fte_cost + regulatory_fine_risk", "typical_impact_pct": 0.01},
        ],
        "system_names": {
            "billing": "Freight Billing Accuracy Engine",
            "collections_ar": "AR Collection & Invoice Intelligence",
            "carrier_pay_ap": "Carrier Settlement & Pay Platform",
            "operations": "Load-to-Cash Orchestrator",
            "compliance": "FMCSA Compliance & Fraud Shield",
            "finance": "Freight Margin Intelligence",
            "technology": "AI Freight Financial Control Tower",
        },
        "agent_roles": {
            "billing": [
                {"name": "AI Document Classifier", "role": "Classifies incoming documents (BOL, POD, lumper receipts, rate confirmations) and extracts key fields with confidence scoring. Routes incomplete packages for human review."},
                {"name": "AI Rate Validator", "role": "Compares computed charges against contracted rates, fuel surcharge tables, and customer SOP rules. Flags mismatches before invoices are issued."},
                {"name": "AI Accessorial Auditor", "role": "Validates detention, layover, TONU, and lumper charges against evidence requirements (timestamps, receipts, pre-approvals). Blocks unbacked charges."},
                {"name": "AI Bill Lock Agent", "role": "Determines when all evidence requirements are met and locks the charge package for invoicing. Tracks document completeness per customer policy."},
            ],
            "collections_ar": [
                {"name": "AI Invoice Composer", "role": "Generates customer-facing invoices with correct charge detail, required documentation bundle (BOL/POD), and payer-specific formatting rules."},
                {"name": "AI Collection Prioritizer", "role": "Scores open invoices by collection probability, aging risk, and customer payment patterns. Recommends escalation actions."},
                {"name": "AI Short-Pay Reconciler", "role": "Matches partial payments to invoice line items, identifies disputed amounts, and opens residual cases automatically."},
            ],
            "carrier_pay_ap": [
                {"name": "AI Three-Way Matcher", "role": "Matches carrier invoice to rate confirmation and delivery evidence (POD). Flags discrepancies for AP review."},
                {"name": "AI Payee Verifier", "role": "Validates carrier identity against FMCSA records, flags bank detail changes, detects factoring redirections, and prevents payment to compromised identities."},
                {"name": "AI Quick Pay Processor", "role": "Computes quick-pay fees, validates eligibility, and routes accelerated payments through approval flow."},
            ],
            "operations": [
                {"name": "AI Dispute Manager", "role": "Auto-compiles evidence binders (rate confirmation, BOL, POD, timestamps, communications), classifies dispute type, and generates draft responses grounded in attached evidence."},
                {"name": "AI Duplicate Detector", "role": "Prevents duplicate invoices and duplicate payments when the same event, document, or file is received multiple times."},
            ],
            "compliance": [
                {"name": "AI Double-Broker Detector", "role": "Identifies high-risk double-brokering patterns by analyzing carrier assignment chains, pricing anomalies, and authority verification gaps."},
                {"name": "AI Compliance Monitor", "role": "Tracks broker surety bond status, carrier authority changes, and FMCSA enforcement actions. Alerts on compliance risks."},
            ],
        },
        "hitl_patterns": {
            "approval_gates": [
                {"domain": "billing", "condition": "Accessorial charge exceeds $2,000", "approver": "Billing Supervisor"},
                {"domain": "billing", "condition": "New customer first invoice", "approver": "Finance Controller"},
                {"domain": "settlement", "condition": "First-time carrier payment", "approver": "AP Manager"},
                {"domain": "settlement", "condition": "Payee bank details changed", "approver": "Finance Controller"},
                {"domain": "settlement", "condition": "Quick pay amount exceeds $10,000", "approver": "AP Manager"},
                {"domain": "disputes", "condition": "Admitting liability or issuing credit", "approver": "Finance Controller"},
                {"domain": "disputes", "condition": "Write-off exceeds $500", "approver": "Finance Controller"},
                {"domain": "compliance", "condition": "Regulatory submission", "approver": "Compliance Officer"},
            ],
            "human_roles": [
                {"title": "AI Billing Supervisor", "layer": "frontline", "manages": ["billing", "collections_ar"], "ratio": "1 supervisor per 500 invoices/week"},
                {"title": "AP Manager", "layer": "frontline", "manages": ["carrier_pay_ap"], "ratio": "1 manager per 300 settlements/week"},
                {"title": "Finance Controller", "layer": "critical", "manages": ["billing", "settlement", "disputes"], "ratio": "1 per company"},
                {"title": "Compliance Officer", "layer": "critical", "manages": ["compliance"], "ratio": "1 per company"},
                {"title": "Operations Director", "layer": "strategic", "manages": ["all systems"], "ratio": "1 per company"},
            ],
            "autonomy_roadmap": {
                "month_1": {"mode": "shadow", "description": "AI runs in parallel. Humans do normal work. Compare outputs.", "human_touch_pct": 80},
                "month_3": {"mode": "assist", "description": "AI executes routine tasks. Humans approve exceptions and edge cases.", "human_touch_pct": 30},
                "month_6": {"mode": "autonomous", "description": "AI executes by default. Humans handle 5-10% exceptions only.", "human_touch_pct": 5},
            },
        },
    },

    # ─── Healthcare / Healthcare Staffing ───────────────────────────────
    "healthcare": {
        "label": "Healthcare & Life Sciences",
        "aliases": ["hospital", "clinic", "medical", "pharma", "biotech", "health system", "patient", "clinical", "nursing", "staffing", "credentialing", "telehealth"],
        "dept_structure": {
            "operations":       {"pct_of_headcount": 0.15, "avg_fte_cost": 58_000},
            "clinical":         {"pct_of_headcount": 0.40, "avg_fte_cost": 85_000},
            "technology":       {"pct_of_headcount": 0.08, "avg_fte_cost": 98_000},
            "customer_support": {"pct_of_headcount": 0.10, "avg_fte_cost": 45_000},
            "finance":          {"pct_of_headcount": 0.07, "avg_fte_cost": 82_000},
            "compliance":       {"pct_of_headcount": 0.05, "avg_fte_cost": 88_000},
            "hr":               {"pct_of_headcount": 0.06, "avg_fte_cost": 65_000},
            "management":       {"pct_of_headcount": 0.09, "avg_fte_cost": 130_000},
        },
        "revenue_per_employee": 220_000,
        "avg_margin": 0.04,
        "revenue_lift_by_dept": {
            "operations": 0.06, "clinical": 0.08, "customer_support": 0.04, "finance": 0.05,
        },
        "ai_adoption_rate": 0.14,
        "pain_catalog": [
            {"id": "credential_delays", "label": "Credentialing Bottleneck", "root_cause": "Manual verification of licenses, certifications, and background checks delays onboarding by weeks", "financial_formula": "avg_days_to_credential * daily_revenue_per_nurse * open_positions", "typical_impact_pct": 0.07},
            {"id": "shift_gaps", "label": "Unfilled Shifts", "root_cause": "Manual scheduling with phone calls and spreadsheets, no predictive demand matching", "financial_formula": "unfilled_shifts_per_month * agency_premium_per_shift", "typical_impact_pct": 0.09},
            {"id": "documentation_burden", "label": "Clinical Documentation Burden", "root_cause": "Clinicians spend 2+ hours per day on documentation instead of patient care", "financial_formula": "clinicians * 2hrs * hourly_rate * 260", "typical_impact_pct": 0.12},
            {"id": "claim_denials", "label": "Insurance Claim Denials", "root_cause": "Coding errors, missing documentation, and late submissions", "financial_formula": "denied_claims_pct * annual_billing * rework_cost", "typical_impact_pct": 0.05},
            {"id": "patient_no_shows", "label": "Patient No-Shows", "root_cause": "No automated reminders, no predictive risk scoring, no waitlist backfill", "financial_formula": "no_show_rate * daily_appointments * avg_revenue_per_visit * 260", "typical_impact_pct": 0.06},
        ],
        "system_names": {
            "operations": "Clinical Workforce Optimizer",
            "clinical": "Patient Care Intelligence",
            "customer_support": "Patient Experience Engine",
            "finance": "Revenue Cycle Intelligence",
            "compliance": "Compliance & Credentialing Automation",
            "technology": "AI Clinical Control Tower",
        },
        "agent_roles": {
            "operations": [
                {"name": "AI Shift Matcher", "role": "Predicts staffing demand and auto-matches available nurses to open shifts based on credentials, location, and preference"},
                {"name": "AI Credential Verifier", "role": "Automates license verification, background checks, and certification tracking with real-time expiration alerts"},
            ],
            "clinical": [
                {"name": "AI Documentation Assistant", "role": "Generates clinical notes from voice recordings and structured data, reducing documentation time by 60%"},
                {"name": "AI Triage Coordinator", "role": "Scores patient acuity from intake data and routes to appropriate care level"},
            ],
            "finance": [
                {"name": "AI Claim Optimizer", "role": "Validates coding accuracy before submission, flags likely denials, and auto-generates appeals for rejected claims"},
            ],
        },
    },

    # ─── B2B SaaS ──────────────────────────────────────────────────────
    "saas": {
        "label": "B2B SaaS & Software",
        "aliases": ["software", "saas", "platform", "app", "subscription", "cloud", "api", "devtools", "martech", "fintech"],
        "dept_structure": {
            "engineering":      {"pct_of_headcount": 0.35, "avg_fte_cost": 135_000},
            "sales":            {"pct_of_headcount": 0.20, "avg_fte_cost": 95_000},
            "marketing":        {"pct_of_headcount": 0.10, "avg_fte_cost": 88_000},
            "customer_support": {"pct_of_headcount": 0.12, "avg_fte_cost": 65_000},
            "product":          {"pct_of_headcount": 0.08, "avg_fte_cost": 125_000},
            "finance":          {"pct_of_headcount": 0.05, "avg_fte_cost": 95_000},
            "hr":               {"pct_of_headcount": 0.04, "avg_fte_cost": 78_000},
            "management":       {"pct_of_headcount": 0.06, "avg_fte_cost": 155_000},
        },
        "revenue_per_employee": 250_000,
        "avg_margin": 0.15,
        "revenue_lift_by_dept": {
            "sales": 0.12, "marketing": 0.08, "customer_support": 0.06, "engineering": 0.03,
        },
        "ai_adoption_rate": 0.35,
        "pain_catalog": [
            {"id": "lead_quality", "label": "Low Lead Quality", "root_cause": "Marketing generates volume but no scoring or intent signals, so sales wastes time on unqualified leads", "financial_formula": "unqualified_leads_pct * sales_hours_wasted * avg_rep_hourly", "typical_impact_pct": 0.08},
            {"id": "churn", "label": "Customer Churn", "root_cause": "No early warning system for at-risk accounts, reactive support instead of proactive engagement", "financial_formula": "churn_rate * arr * months_to_recover", "typical_impact_pct": 0.15},
            {"id": "slow_sales_cycle", "label": "Long Sales Cycles", "root_cause": "Manual proposal creation, no competitive intelligence at deal level, slow legal/procurement process", "financial_formula": "excess_days_in_cycle * pipeline_value * time_value_of_money", "typical_impact_pct": 0.06},
            {"id": "support_scaling", "label": "Support Can't Scale", "root_cause": "Every ticket handled by humans, no self-service, no automated resolution for common issues", "financial_formula": "tickets_per_month * cost_per_ticket * automation_eligible_pct", "typical_impact_pct": 0.07},
            {"id": "onboarding_friction", "label": "Slow Customer Onboarding", "root_cause": "Manual setup, custom configuration, no guided activation flow", "financial_formula": "avg_onboarding_days * daily_opportunity_cost * new_customers_per_month", "typical_impact_pct": 0.05},
        ],
        "system_names": {
            "sales": "Pipeline Velocity System",
            "marketing": "Demand Generation Engine",
            "customer_support": "Customer Retention Intelligence",
            "engineering": "AI Development Accelerator",
            "product": "Product Intelligence System",
            "technology": "AI Revenue Operations Tower",
        },
        "agent_roles": {
            "sales": [
                {"name": "AI Deal Desk Analyst", "role": "Scores deals by likelihood to close, identifies stalled opportunities, and recommends next-best-action for each rep"},
                {"name": "AI Pipeline Forecaster", "role": "Generates weekly revenue forecasts from CRM data, email activity, and meeting patterns with 90%+ accuracy"},
                {"name": "AI Competitive Intel Agent", "role": "Monitors competitor pricing, features, and market moves to arm reps with real-time battlecards"},
            ],
            "customer_support": [
                {"name": "AI Churn Predictor", "role": "Identifies at-risk accounts 30 days before churn based on usage patterns, support tickets, and NPS trends"},
                {"name": "AI Ticket Resolver", "role": "Auto-resolves common support requests using knowledge base and past resolutions, escalating only complex issues"},
            ],
            "marketing": [
                {"name": "AI Lead Scorer", "role": "Scores inbound leads by purchase intent using website behavior, content engagement, and firmographic data"},
                {"name": "AI Content Strategist", "role": "Identifies content gaps from search data and competitor analysis, recommends topics with highest conversion potential"},
            ],
        },
    },

    # ─── E-Commerce & Retail ───────────────────────────────────────────
    "ecommerce": {
        "label": "E-Commerce & Retail",
        "aliases": ["retail", "ecommerce", "e-commerce", "store", "shop", "marketplace", "dtc", "consumer", "fashion", "cpg", "grocery"],
        "dept_structure": {
            "operations":       {"pct_of_headcount": 0.30, "avg_fte_cost": 45_000},
            "marketing":        {"pct_of_headcount": 0.15, "avg_fte_cost": 75_000},
            "sales":            {"pct_of_headcount": 0.12, "avg_fte_cost": 55_000},
            "customer_support": {"pct_of_headcount": 0.15, "avg_fte_cost": 38_000},
            "technology":       {"pct_of_headcount": 0.10, "avg_fte_cost": 95_000},
            "finance":          {"pct_of_headcount": 0.05, "avg_fte_cost": 72_000},
            "management":       {"pct_of_headcount": 0.08, "avg_fte_cost": 110_000},
            "hr":               {"pct_of_headcount": 0.05, "avg_fte_cost": 55_000},
        },
        "revenue_per_employee": 320_000,
        "avg_margin": 0.08,
        "revenue_lift_by_dept": {
            "marketing": 0.10, "operations": 0.06, "customer_support": 0.04, "sales": 0.08,
        },
        "ai_adoption_rate": 0.28,
        "pain_catalog": [
            {"id": "cart_abandonment", "label": "Cart Abandonment", "root_cause": "No real-time intervention, generic follow-up emails, no personalization based on browse behavior", "financial_formula": "abandoned_carts_per_month * avg_cart_value * recovery_rate_improvement", "typical_impact_pct": 0.12},
            {"id": "inventory_mismatch", "label": "Inventory Forecasting Errors", "root_cause": "Demand planning based on historical averages without accounting for trends, seasonality, or external signals", "financial_formula": "overstock_cost + stockout_lost_revenue", "typical_impact_pct": 0.08},
            {"id": "return_costs", "label": "High Return Rates", "root_cause": "Poor product descriptions, no size/fit prediction, no quality feedback loop from returns data", "financial_formula": "return_rate * annual_revenue * processing_cost_pct", "typical_impact_pct": 0.06},
            {"id": "customer_acquisition", "label": "Rising CAC", "root_cause": "Broad targeting, no lookalike modeling, ad spend not optimized by channel ROI", "financial_formula": "total_ad_spend * (1 - efficiency_improvement)", "typical_impact_pct": 0.10},
            {"id": "fulfillment_speed", "label": "Slow Fulfillment", "root_cause": "Manual pick-pack-ship, no warehouse optimization, no carrier rate shopping", "financial_formula": "orders_per_day * time_saved_per_order * labor_rate", "typical_impact_pct": 0.05},
        ],
        "system_names": {
            "operations": "Fulfillment & Inventory Intelligence",
            "marketing": "Customer Acquisition Engine",
            "customer_support": "Shopper Experience System",
            "sales": "Revenue Optimization Platform",
            "technology": "AI Commerce Control Tower",
        },
        "agent_roles": {
            "marketing": [
                {"name": "AI Cart Recovery Agent", "role": "Triggers personalized recovery campaigns within 15 minutes of cart abandonment based on browse history and purchase patterns"},
                {"name": "AI Ad Optimizer", "role": "Allocates ad spend across channels in real-time based on ROAS, adjusting bids and creative by audience segment"},
            ],
            "operations": [
                {"name": "AI Demand Planner", "role": "Forecasts SKU-level demand using sales velocity, seasonal trends, social signals, and weather data"},
                {"name": "AI Fulfillment Optimizer", "role": "Routes orders to optimal warehouse/carrier combination for fastest delivery at lowest cost"},
            ],
        },
    },

    # ─── Professional Services & Consulting ────────────────────────────
    "consulting": {
        "label": "Professional Services & Consulting",
        "aliases": ["consulting", "advisory", "professional services", "engineering firm", "architecture"],
        "dept_structure": {
            "delivery":         {"pct_of_headcount": 0.50, "avg_fte_cost": 110_000},
            "sales":            {"pct_of_headcount": 0.12, "avg_fte_cost": 105_000},
            "marketing":        {"pct_of_headcount": 0.06, "avg_fte_cost": 82_000},
            "operations":       {"pct_of_headcount": 0.08, "avg_fte_cost": 68_000},
            "finance":          {"pct_of_headcount": 0.06, "avg_fte_cost": 88_000},
            "hr":               {"pct_of_headcount": 0.06, "avg_fte_cost": 72_000},
            "technology":       {"pct_of_headcount": 0.06, "avg_fte_cost": 105_000},
            "management":       {"pct_of_headcount": 0.06, "avg_fte_cost": 165_000},
        },
        "revenue_per_employee": 200_000,
        "avg_margin": 0.20,
        "revenue_lift_by_dept": {
            "delivery": 0.10, "sales": 0.12, "marketing": 0.06, "operations": 0.04,
        },
        "ai_adoption_rate": 0.22,
        "pain_catalog": [
            {"id": "utilization", "label": "Low Utilization Rates", "root_cause": "Manual resource scheduling, no visibility into skills availability, bench time not optimized", "financial_formula": "bench_hours_per_month * avg_billable_rate", "typical_impact_pct": 0.12},
            {"id": "proposal_time", "label": "Slow Proposal Turnaround", "root_cause": "Each proposal written from scratch, no reuse of past wins, no automated pricing", "financial_formula": "avg_proposal_hours * hourly_rate * proposals_per_month * win_rate_improvement", "typical_impact_pct": 0.06},
            {"id": "scope_creep", "label": "Scope Creep & Overruns", "root_cause": "No real-time budget tracking against SOW, milestone drift detected too late", "financial_formula": "overrun_projects_pct * avg_project_value * overrun_pct", "typical_impact_pct": 0.08},
            {"id": "knowledge_loss", "label": "Knowledge Walks Out the Door", "root_cause": "Institutional knowledge stored in people's heads, no capture or retrieval system", "financial_formula": "turnover_rate * headcount * ramp_up_cost_per_hire", "typical_impact_pct": 0.05},
            {"id": "pipeline_visibility", "label": "No Pipeline Visibility", "root_cause": "Deals tracked in spreadsheets, no forecasting, partners sandbagging numbers", "financial_formula": "forecast_error_pct * quarterly_revenue * cost_of_miss", "typical_impact_pct": 0.07},
        ],
        "system_names": {
            "delivery": "Engagement Delivery Intelligence",
            "sales": "Pipeline & Proposal Engine",
            "operations": "Resource Utilization Optimizer",
            "finance": "Project Profitability System",
            "technology": "AI Practice Control Tower",
        },
        "agent_roles": {
            "delivery": [
                {"name": "AI Resource Scheduler", "role": "Matches available consultants to project needs based on skills, certifications, client preference, and utilization targets"},
                {"name": "AI Project Health Monitor", "role": "Tracks budget burn, milestone progress, and client satisfaction signals to flag at-risk engagements before overruns"},
            ],
            "sales": [
                {"name": "AI Proposal Generator", "role": "Assembles proposals from past winning content, pricing templates, and case studies tailored to the prospect's industry"},
                {"name": "AI Win/Loss Analyzer", "role": "Identifies patterns in won vs lost deals to improve positioning, pricing, and pursuit decisions"},
            ],
        },
    },

    # ─── Staffing & Recruiting ────────────────────────────────────────
    "staffing": {
        "label": "Staffing & Recruiting",
        "aliases": ["staffing", "staffing agency", "recruiting", "recruitment", "temp agency", "temporary staffing", "placement", "workforce solutions", "talent acquisition"],
        "dept_structure": {
            "sales":            {"pct_of_headcount": 0.25, "avg_fte_cost": 72_000},
            "recruiting":       {"pct_of_headcount": 0.35, "avg_fte_cost": 55_000},
            "operations":       {"pct_of_headcount": 0.15, "avg_fte_cost": 48_000},
            "finance":          {"pct_of_headcount": 0.08, "avg_fte_cost": 68_000},
            "hr":               {"pct_of_headcount": 0.07, "avg_fte_cost": 58_000},
            "management":       {"pct_of_headcount": 0.10, "avg_fte_cost": 95_000},
        },
        "revenue_per_employee": 140_000,
        "avg_margin": 0.04,
        "revenue_lift_by_dept": {
            "sales": 0.08, "recruiting": 0.12, "operations": 0.04,
        },
        "ai_adoption_rate": 0.12,
        "pain_catalog": [
            {"id": "time_to_fill", "label": "Slow Time-to-Fill", "root_cause": "Manual resume screening, phone-based candidate outreach, no automated matching against open reqs", "financial_formula": "avg_days_to_fill * daily_gross_margin_loss * open_reqs", "typical_impact_pct": 0.10},
            {"id": "no_show_rate", "label": "High No-Show Rate", "root_cause": "Associates confirm shifts by phone, no automated reminders, no penalty tracking, last-minute replacements scrambled manually", "financial_formula": "no_show_rate * shifts_per_week * replacement_cost", "typical_impact_pct": 0.08},
            {"id": "associate_turnover", "label": "Associate Turnover", "root_cause": "Poor shift matching to preferences, no engagement tracking, associates leave for competitors with better scheduling", "financial_formula": "turnover_rate * associates * recruitment_cost_per_hire", "typical_impact_pct": 0.12},
            {"id": "timesheet_reconciliation", "label": "Timesheet Reconciliation Chaos", "root_cause": "Paper or text-based time reporting, manual entry into payroll, disputes over hours worked", "financial_formula": "reconciliation_hours_per_week * hourly_rate * 52", "typical_impact_pct": 0.05},
            {"id": "thin_margins", "label": "Margin Compression", "root_cause": "Bill/pay rate spread eroding, no real-time visibility into per-placement profitability", "financial_formula": "placements * avg_bill_rate * margin_erosion_pct", "typical_impact_pct": 0.06},
        ],
        "system_names": {
            "recruiting": "Talent Matching Intelligence",
            "sales": "Client Pipeline Engine",
            "operations": "Workforce Scheduling Optimizer",
            "finance": "Placement Profitability System",
            "technology": "AI Staffing Control Tower",
        },
        "agent_roles": {
            "recruiting": [
                {"name": "AI Resume Matcher", "role": "Scores and ranks candidates against open reqs by skills, location, availability, and past placement success"},
                {"name": "AI Shift Confirmer", "role": "Sends automated shift reminders via text, detects likely no-shows from response patterns, triggers backup sourcing"},
                {"name": "AI Candidate Engager", "role": "Maintains contact with bench associates through automated check-ins, availability polls, and re-engagement campaigns"},
            ],
            "operations": [
                {"name": "AI Timesheet Processor", "role": "Validates submitted hours against scheduled shifts, flags discrepancies, auto-generates payroll-ready reports"},
            ],
        },
    },

    # ─── Energy & Utilities / Electric Cooperatives ────────────────────
    "utility": {
        "label": "Energy & Utilities",
        "aliases": ["utility", "electric", "cooperative", "co-op", "coop", "power", "energy", "grid", "solar", "wind", "gas", "water", "nreca", "municipal"],
        "dept_structure": {
            "operations":       {"pct_of_headcount": 0.25, "avg_fte_cost": 62_000},
            "field_services":   {"pct_of_headcount": 0.30, "avg_fte_cost": 55_000},
            "customer_support": {"pct_of_headcount": 0.12, "avg_fte_cost": 42_000},
            "technology":       {"pct_of_headcount": 0.08, "avg_fte_cost": 88_000},
            "finance":          {"pct_of_headcount": 0.06, "avg_fte_cost": 75_000},
            "compliance":       {"pct_of_headcount": 0.05, "avg_fte_cost": 82_000},
            "management":       {"pct_of_headcount": 0.08, "avg_fte_cost": 105_000},
            "hr":               {"pct_of_headcount": 0.06, "avg_fte_cost": 58_000},
        },
        "revenue_per_employee": 350_000,
        "avg_margin": 0.05,
        "revenue_lift_by_dept": {
            "field_services": 0.10, "operations": 0.08, "customer_support": 0.04, "finance": 0.03,
        },
        "ai_adoption_rate": 0.08,
        "pain_catalog": [
            {"id": "truck_rolls", "label": "Wasted Truck Rolls", "root_cause": "Crews dispatched with incomplete info, multiple visits to same area, poor routing", "financial_formula": "unnecessary_rolls_per_week * cost_per_roll * 52", "typical_impact_pct": 0.08},
            {"id": "vegetation", "label": "Inefficient Vegetation Management", "root_cause": "Fixed-cycle trimming regardless of actual risk, trimming low-risk areas while high-risk corridors grow into lines", "financial_formula": "veg_budget * waste_pct + veg_outage_cost", "typical_impact_pct": 0.10},
            {"id": "storm_response", "label": "Slow Storm Response", "root_cause": "Manual crew dispatch, overwhelmed call center, no proactive member communication", "financial_formula": "storm_cost_premium + customer_satisfaction_impact", "typical_impact_pct": 0.06},
            {"id": "outage_prediction", "label": "Reactive Equipment Maintenance", "root_cause": "No predictive monitoring of transformers and feeders, equipment fails without warning", "financial_formula": "emergency_repair_cost - planned_maintenance_cost * prevented_failures", "typical_impact_pct": 0.07},
            {"id": "compliance_burden", "label": "Manual Compliance Reporting", "root_cause": "One FTE assembles NERC/FERC/PUC reports manually from 8 different systems", "financial_formula": "compliance_fte_cost + audit_risk_cost", "typical_impact_pct": 0.03},
        ],
        "system_names": {
            "field_services": "Crew Productivity Engine",
            "operations": "Grid Intelligence System",
            "customer_support": "Member Services Automation",
            "finance": "Rate Case Automation Platform",
            "compliance": "Regulatory Compliance Engine",
            "technology": "AI Grid Control Tower",
        },
        "agent_roles": {
            "field_services": [
                {"name": "AI Crew Dispatcher", "role": "Auto-assigns crews to work orders based on location, skills, certifications, and priority. Generates daily prioritized work plans."},
                {"name": "AI Route Optimizer", "role": "Calculates optimal crew routes to eliminate wasted truck rolls and maximize line-miles covered per crew per day"},
                {"name": "AI Vegetation Scheduler", "role": "Prioritizes trimming by actual risk score instead of fixed cycles, telling crews where to trim and where to skip"},
            ],
            "operations": [
                {"name": "AI Outage Predictor", "role": "Monitors transformer thermal data, load patterns, and weather to predict equipment failures 48 hours before they happen"},
                {"name": "AI Grid Monitor", "role": "Continuously monitors voltage, frequency, and power quality across all feeders and substations"},
            ],
            "customer_support": [
                {"name": "AI Storm Communicator", "role": "Auto-sends outage alerts to affected members before they call, handles inbound inquiries with real-time ETAs"},
                {"name": "AI Member Service Bot", "role": "Handles billing questions, payment arrangements, outage reports, and new connection applications 24/7"},
            ],
        },
    },

    # ─── Manufacturing ─────────────────────────────────────────────────
    "manufacturing": {
        "label": "Manufacturing",
        "aliases": ["manufacturing", "factory", "production", "assembly", "machining", "fabrication", "plant", "industrial", "automotive parts", "precision"],
        "dept_structure": {
            "production":       {"pct_of_headcount": 0.45, "avg_fte_cost": 48_000},
            "quality":          {"pct_of_headcount": 0.08, "avg_fte_cost": 62_000},
            "operations":       {"pct_of_headcount": 0.10, "avg_fte_cost": 58_000},
            "sales":            {"pct_of_headcount": 0.08, "avg_fte_cost": 78_000},
            "technology":       {"pct_of_headcount": 0.06, "avg_fte_cost": 92_000},
            "finance":          {"pct_of_headcount": 0.05, "avg_fte_cost": 75_000},
            "maintenance":      {"pct_of_headcount": 0.10, "avg_fte_cost": 55_000},
            "management":       {"pct_of_headcount": 0.08, "avg_fte_cost": 115_000},
        },
        "revenue_per_employee": 280_000,
        "avg_margin": 0.08,
        "revenue_lift_by_dept": {
            "production": 0.10, "quality": 0.06, "operations": 0.05, "sales": 0.04, "maintenance": 0.08,
        },
        "ai_adoption_rate": 0.20,
        "pain_catalog": [
            {"id": "downtime", "label": "Unplanned Downtime", "root_cause": "Reactive maintenance, no sensor monitoring, no failure prediction for critical equipment", "financial_formula": "downtime_hours_per_month * cost_per_hour_downtime", "typical_impact_pct": 0.12},
            {"id": "quality_defects", "label": "Quality Defects", "root_cause": "Manual inspection catches defects too late in process, no real-time quality monitoring", "financial_formula": "defect_rate * production_volume * rework_cost_per_unit", "typical_impact_pct": 0.07},
            {"id": "production_scheduling", "label": "Suboptimal Production Scheduling", "root_cause": "Manual scheduling in spreadsheets, no optimization for changeover times, material availability, or demand priority", "financial_formula": "changeover_waste + expedite_premiums + missed_delivery_penalties", "typical_impact_pct": 0.06},
            {"id": "supply_chain", "label": "Supply Chain Disruptions", "root_cause": "No early warning for supplier delays, single-source dependencies, no alternative sourcing automation", "financial_formula": "line_stop_hours * hourly_production_value", "typical_impact_pct": 0.08},
            {"id": "energy_waste", "label": "Energy Waste", "root_cause": "Equipment running during non-production hours, no optimization of energy-intensive processes", "financial_formula": "annual_energy_cost * waste_pct", "typical_impact_pct": 0.04},
        ],
        "system_names": {
            "production": "Production Intelligence System",
            "quality": "Quality Assurance Engine",
            "operations": "Supply Chain Optimizer",
            "maintenance": "Predictive Maintenance Platform",
            "sales": "Order-to-Delivery Accelerator",
            "technology": "AI Manufacturing Control Tower",
        },
        "agent_roles": {
            "production": [
                {"name": "AI Production Scheduler", "role": "Optimizes production sequences to minimize changeover time, maximize throughput, and meet delivery deadlines"},
                {"name": "AI Yield Optimizer", "role": "Adjusts process parameters in real-time to maximize output quality and minimize material waste"},
            ],
            "maintenance": [
                {"name": "AI Equipment Monitor", "role": "Analyzes vibration, temperature, and power consumption data to predict failures 72 hours before they happen"},
                {"name": "AI Maintenance Planner", "role": "Schedules preventive maintenance during planned downtime windows to avoid production disruption"},
            ],
            "quality": [
                {"name": "AI Visual Inspector", "role": "Uses computer vision to detect defects in real-time at production speed, catching issues before they reach packaging"},
            ],
        },
    },

    # ─── Financial Services ────────────────────────────────────────────
    "finserv": {
        "label": "Financial Services",
        "aliases": ["bank", "banking", "credit union", "insurance", "fintech", "wealth management", "investment", "lending", "mortgage", "payments", "financial"],
        "dept_structure": {
            "operations":       {"pct_of_headcount": 0.20, "avg_fte_cost": 68_000},
            "sales":            {"pct_of_headcount": 0.15, "avg_fte_cost": 95_000},
            "compliance":       {"pct_of_headcount": 0.10, "avg_fte_cost": 95_000},
            "technology":       {"pct_of_headcount": 0.15, "avg_fte_cost": 115_000},
            "customer_support": {"pct_of_headcount": 0.12, "avg_fte_cost": 52_000},
            "finance":          {"pct_of_headcount": 0.08, "avg_fte_cost": 98_000},
            "risk":             {"pct_of_headcount": 0.08, "avg_fte_cost": 105_000},
            "management":       {"pct_of_headcount": 0.12, "avg_fte_cost": 155_000},
        },
        "revenue_per_employee": 400_000,
        "avg_margin": 0.25,
        "revenue_lift_by_dept": {
            "sales": 0.10, "operations": 0.06, "customer_support": 0.05, "risk": 0.08,
        },
        "ai_adoption_rate": 0.30,
        "pain_catalog": [
            {"id": "loan_processing", "label": "Slow Loan Processing", "root_cause": "Manual document review, no automated underwriting for standard cases, compliance checks done sequentially", "financial_formula": "avg_processing_days * applications_per_month * daily_interest_opportunity", "typical_impact_pct": 0.08},
            {"id": "fraud_detection", "label": "Fraud Losses", "root_cause": "Rule-based detection misses novel fraud patterns, too many false positives waste investigator time", "financial_formula": "fraud_losses + false_positive_investigation_cost", "typical_impact_pct": 0.06},
            {"id": "compliance_cost", "label": "Compliance Cost", "root_cause": "Manual KYC/AML checks, regulatory reporting assembled by hand, audit prep takes weeks", "financial_formula": "compliance_team_cost + regulatory_fine_risk", "typical_impact_pct": 0.05},
            {"id": "customer_onboarding", "label": "Slow Account Opening", "root_cause": "Multi-day onboarding with manual identity verification, document collection, and approval chains", "financial_formula": "abandonment_rate * applications * avg_lifetime_value", "typical_impact_pct": 0.07},
            {"id": "cross_sell", "label": "Missed Cross-Sell", "root_cause": "No next-best-product recommendations, relationship managers don't have full view of customer needs", "financial_formula": "eligible_customers * cross_sell_rate_improvement * avg_product_value", "typical_impact_pct": 0.10},
        ],
        "system_names": {
            "operations": "Lending & Processing Automation",
            "sales": "Relationship Intelligence Platform",
            "compliance": "Regulatory Compliance Engine",
            "risk": "Risk & Fraud Intelligence",
            "customer_support": "Client Experience System",
            "technology": "AI Financial Control Tower",
        },
        "agent_roles": {
            "operations": [
                {"name": "AI Underwriter", "role": "Auto-approves standard loan applications using credit data, income verification, and risk scoring, escalating only exceptions"},
                {"name": "AI Document Processor", "role": "Extracts data from financial documents (pay stubs, tax returns, bank statements) with 99% accuracy"},
            ],
            "risk": [
                {"name": "AI Fraud Detector", "role": "Identifies suspicious transactions in real-time using behavioral patterns, not just rules, reducing false positives by 60%"},
                {"name": "AI Risk Scorer", "role": "Continuously scores portfolio risk using market data, borrower behavior, and macroeconomic indicators"},
            ],
        },
    },

    # ─── Education & Training ──────────────────────────────────────────
    "education": {
        "label": "Education & Training",
        "aliases": ["education", "university", "college", "school", "training", "edtech", "lms", "e-learning", "academic", "higher ed", "k-12"],
        "dept_structure": {
            "instruction":      {"pct_of_headcount": 0.40, "avg_fte_cost": 72_000},
            "student_services": {"pct_of_headcount": 0.15, "avg_fte_cost": 48_000},
            "technology":       {"pct_of_headcount": 0.10, "avg_fte_cost": 82_000},
            "operations":       {"pct_of_headcount": 0.10, "avg_fte_cost": 52_000},
            "marketing":        {"pct_of_headcount": 0.08, "avg_fte_cost": 65_000},
            "finance":          {"pct_of_headcount": 0.05, "avg_fte_cost": 68_000},
            "management":       {"pct_of_headcount": 0.07, "avg_fte_cost": 105_000},
            "hr":               {"pct_of_headcount": 0.05, "avg_fte_cost": 58_000},
        },
        "revenue_per_employee": 120_000,
        "avg_margin": 0.10,
        "revenue_lift_by_dept": {
            "marketing": 0.08, "student_services": 0.06, "instruction": 0.04, "operations": 0.03,
        },
        "ai_adoption_rate": 0.15,
        "pain_catalog": [
            {"id": "enrollment_decline", "label": "Enrollment Decline", "root_cause": "No predictive modeling for at-risk applicants, generic outreach, slow admissions process", "financial_formula": "lost_students * avg_tuition * retention_years", "typical_impact_pct": 0.12},
            {"id": "student_retention", "label": "Student Dropout", "root_cause": "No early warning for struggling students, interventions too late, no personalized support", "financial_formula": "dropout_rate * enrolled * avg_tuition * remaining_years", "typical_impact_pct": 0.10},
            {"id": "admin_burden", "label": "Administrative Burden", "root_cause": "Manual registration, advising, financial aid processing, and scheduling", "financial_formula": "admin_staff_cost * automation_eligible_pct", "typical_impact_pct": 0.06},
            {"id": "curriculum_relevance", "label": "Outdated Curriculum", "root_cause": "No labor market data integration, slow feedback loop from employers and graduates", "financial_formula": "enrollment_impact_of_relevance * tuition", "typical_impact_pct": 0.05},
        ],
        "system_names": {
            "student_services": "Student Success Intelligence",
            "marketing": "Enrollment Growth Engine",
            "instruction": "Adaptive Learning Platform",
            "operations": "Campus Operations Optimizer",
            "technology": "AI Academic Control Tower",
        },
        "agent_roles": {
            "student_services": [
                {"name": "AI Retention Predictor", "role": "Identifies at-risk students 4 weeks before dropout using attendance, grades, LMS engagement, and financial aid status"},
                {"name": "AI Academic Advisor", "role": "Recommends course sequences, degree pathways, and intervention resources personalized to each student's goals and performance"},
            ],
            "marketing": [
                {"name": "AI Enrollment Optimizer", "role": "Scores prospective students by likelihood to enroll and persist, targeting outreach to highest-potential applicants"},
            ],
        },
    },

    # ─── Real Estate ───────────────────────────────────────────────────
    "real_estate": {
        "label": "Real Estate & Property Management",
        "aliases": ["real estate company", "real estate firm", "real estate agency", "property management company", "realty", "commercial real estate", "reit", "real estate developer", "property developer"],
        "dept_structure": {
            "sales":            {"pct_of_headcount": 0.35, "avg_fte_cost": 85_000},
            "operations":       {"pct_of_headcount": 0.15, "avg_fte_cost": 55_000},
            "marketing":        {"pct_of_headcount": 0.12, "avg_fte_cost": 72_000},
            "finance":          {"pct_of_headcount": 0.08, "avg_fte_cost": 88_000},
            "technology":       {"pct_of_headcount": 0.08, "avg_fte_cost": 95_000},
            "management":       {"pct_of_headcount": 0.10, "avg_fte_cost": 125_000},
            "property_mgmt":    {"pct_of_headcount": 0.12, "avg_fte_cost": 48_000},
        },
        "revenue_per_employee": 350_000,
        "avg_margin": 0.15,
        "revenue_lift_by_dept": {
            "sales": 0.12, "marketing": 0.08, "operations": 0.04, "property_mgmt": 0.06,
        },
        "ai_adoption_rate": 0.12,
        "pain_catalog": [
            {"id": "lead_follow_up", "label": "Slow Lead Follow-Up", "root_cause": "Agents overwhelmed with leads, no prioritization, best leads go cold while agents chase low-quality inquiries", "financial_formula": "cold_leads_per_month * avg_commission * conversion_rate_improvement", "typical_impact_pct": 0.10},
            {"id": "market_analysis", "label": "Manual Market Analysis", "root_cause": "Comp analysis and pricing done manually, missing market shifts until after competitors react", "financial_formula": "mispriced_listings * avg_days_on_market_premium * opportunity_cost", "typical_impact_pct": 0.06},
            {"id": "tenant_management", "label": "Reactive Tenant Management", "root_cause": "Maintenance requests handled manually, no predictive maintenance, lease renewals managed in spreadsheets", "financial_formula": "vacancy_rate_reduction * portfolio_value * annual_rent_pct", "typical_impact_pct": 0.08},
        ],
        "system_names": {
            "sales": "Deal Flow Intelligence",
            "marketing": "Property Marketing Engine",
            "operations": "Transaction Automation Platform",
            "property_mgmt": "Property Operations Intelligence",
            "technology": "AI Real Estate Control Tower",
        },
        "agent_roles": {
            "sales": [
                {"name": "AI Lead Prioritizer", "role": "Scores buyer/seller leads by purchase readiness using search behavior, financial signals, and life event data"},
                {"name": "AI Comp Analyzer", "role": "Generates instant comparative market analysis with pricing recommendations based on real-time transaction data"},
            ],
        },
    },

    # ─── Insurance ─────────────────────────────────────────────────────
    "insurance": {
        "label": "Insurance",
        "aliases": ["insurance", "underwriting", "claims", "actuarial", "reinsurance", "p&c", "life insurance", "health insurance", "policyholder", "premium"],
        "dept_structure": {
            "underwriting":     {"pct_of_headcount": 0.15, "avg_fte_cost": 88_000},
            "claims":           {"pct_of_headcount": 0.20, "avg_fte_cost": 62_000},
            "sales":            {"pct_of_headcount": 0.15, "avg_fte_cost": 78_000},
            "customer_support": {"pct_of_headcount": 0.12, "avg_fte_cost": 48_000},
            "compliance":       {"pct_of_headcount": 0.08, "avg_fte_cost": 92_000},
            "technology":       {"pct_of_headcount": 0.10, "avg_fte_cost": 105_000},
            "finance":          {"pct_of_headcount": 0.06, "avg_fte_cost": 95_000},
            "management":       {"pct_of_headcount": 0.08, "avg_fte_cost": 140_000},
            "hr":               {"pct_of_headcount": 0.06, "avg_fte_cost": 65_000},
        },
        "revenue_per_employee": 300_000,
        "avg_margin": 0.12,
        "revenue_lift_by_dept": {
            "underwriting": 0.08, "claims": 0.06, "sales": 0.10, "customer_support": 0.04,
        },
        "ai_adoption_rate": 0.22,
        "pain_catalog": [
            {"id": "claims_processing", "label": "Slow Claims Processing", "root_cause": "Manual document review, sequential approvals, no automated triage for simple claims", "financial_formula": "avg_claim_days * claims_per_month * customer_satisfaction_cost", "typical_impact_pct": 0.08},
            {"id": "underwriting_speed", "label": "Slow Underwriting", "root_cause": "Manual risk assessment, no automated data enrichment, same process for simple and complex risks", "financial_formula": "quotes_lost_to_speed * avg_premium * policy_term", "typical_impact_pct": 0.10},
            {"id": "fraud_leakage", "label": "Claims Fraud", "root_cause": "Rule-based detection misses sophisticated fraud, SIU overwhelmed with false positives", "financial_formula": "estimated_fraud_pct * total_claims_paid", "typical_impact_pct": 0.06},
            {"id": "renewal_retention", "label": "Low Renewal Rates", "root_cause": "No proactive retention outreach, pricing not competitive at renewal, no loyalty scoring", "financial_formula": "lapsed_policies * avg_premium * acquisition_cost_saved", "typical_impact_pct": 0.09},
        ],
        "system_names": {
            "underwriting": "Intelligent Underwriting Platform",
            "claims": "Claims Processing Automation",
            "sales": "Distribution & Growth Engine",
            "customer_support": "Policyholder Experience System",
            "compliance": "Regulatory Intelligence",
            "technology": "AI Insurance Control Tower",
        },
        "agent_roles": {
            "claims": [
                {"name": "AI Claims Triage", "role": "Auto-classifies claims by complexity, fast-tracks simple claims for instant payment, routes complex claims to specialists"},
                {"name": "AI Fraud Detector", "role": "Identifies suspicious claims using pattern analysis across claimant history, provider networks, and timing anomalies"},
            ],
            "underwriting": [
                {"name": "AI Risk Scorer", "role": "Enriches applications with third-party data and generates risk scores in seconds instead of days"},
            ],
        },
    },

    # ─── Nonprofit ─────────────────────────────────────────────────────
    "nonprofit": {
        "label": "Nonprofit & NGO",
        "aliases": ["nonprofit", "non-profit", "ngo", "charity", "foundation", "association", "mission", "philanthropy", "social impact", "501c"],
        "dept_structure": {
            "programs":         {"pct_of_headcount": 0.40, "avg_fte_cost": 52_000},
            "fundraising":      {"pct_of_headcount": 0.15, "avg_fte_cost": 62_000},
            "marketing":        {"pct_of_headcount": 0.08, "avg_fte_cost": 55_000},
            "operations":       {"pct_of_headcount": 0.10, "avg_fte_cost": 48_000},
            "finance":          {"pct_of_headcount": 0.08, "avg_fte_cost": 65_000},
            "technology":       {"pct_of_headcount": 0.05, "avg_fte_cost": 75_000},
            "management":       {"pct_of_headcount": 0.08, "avg_fte_cost": 95_000},
            "hr":               {"pct_of_headcount": 0.06, "avg_fte_cost": 52_000},
        },
        "revenue_per_employee": 100_000,
        "avg_margin": 0.05,
        "revenue_lift_by_dept": {
            "fundraising": 0.15, "marketing": 0.08, "programs": 0.04, "operations": 0.03,
        },
        "ai_adoption_rate": 0.08,
        "pain_catalog": [
            {"id": "donor_retention", "label": "Donor Attrition", "root_cause": "No donor journey tracking, generic appeals, no predicted giving capacity or lapsed donor recovery", "financial_formula": "lapsed_donors * avg_gift * retention_rate_improvement", "typical_impact_pct": 0.12},
            {"id": "grant_management", "label": "Grant Compliance Burden", "root_cause": "Manual reporting to funders, tracking restricted funds in spreadsheets, missed deadlines", "financial_formula": "compliance_staff_hours * hourly_rate + at_risk_funding", "typical_impact_pct": 0.05},
            {"id": "impact_measurement", "label": "Can't Prove Impact", "root_cause": "Outcome data collected inconsistently, no automated impact reporting, stories don't connect to data", "financial_formula": "funding_lost_to_weak_impact_case * grant_applications", "typical_impact_pct": 0.08},
        ],
        "system_names": {
            "fundraising": "Donor Intelligence Engine",
            "programs": "Impact Measurement Platform",
            "marketing": "Outreach & Engagement System",
            "operations": "Mission Operations Optimizer",
            "technology": "AI Mission Control Tower",
        },
        "agent_roles": {
            "fundraising": [
                {"name": "AI Donor Predictor", "role": "Identifies likely major gift prospects from giving history, wealth signals, and engagement patterns"},
                {"name": "AI Stewardship Agent", "role": "Automates personalized thank-you communications and impact updates based on each donor's giving history and interests"},
            ],
        },
    },

    # ─── Government ────────────────────────────────────────────────────
    "government": {
        "label": "Government & Public Sector",
        "aliases": ["government", "federal", "state", "municipal", "city", "county", "public sector", "agency", "department", "defense"],
        "dept_structure": {
            "operations":       {"pct_of_headcount": 0.30, "avg_fte_cost": 65_000},
            "customer_support": {"pct_of_headcount": 0.15, "avg_fte_cost": 52_000},
            "technology":       {"pct_of_headcount": 0.12, "avg_fte_cost": 95_000},
            "compliance":       {"pct_of_headcount": 0.08, "avg_fte_cost": 78_000},
            "finance":          {"pct_of_headcount": 0.08, "avg_fte_cost": 72_000},
            "hr":               {"pct_of_headcount": 0.07, "avg_fte_cost": 62_000},
            "management":       {"pct_of_headcount": 0.10, "avg_fte_cost": 105_000},
            "programs":         {"pct_of_headcount": 0.10, "avg_fte_cost": 58_000},
        },
        "revenue_per_employee": 150_000,
        "avg_margin": 0.0,
        "revenue_lift_by_dept": {
            "operations": 0.08, "customer_support": 0.06, "technology": 0.04, "programs": 0.03,
        },
        "ai_adoption_rate": 0.10,
        "pain_catalog": [
            {"id": "citizen_wait", "label": "Long Citizen Wait Times", "root_cause": "All inquiries handled by humans, no self-service, no appointment scheduling, no triage", "financial_formula": "inquiries_per_month * avg_handle_time * hourly_cost", "typical_impact_pct": 0.08},
            {"id": "permit_processing", "label": "Slow Permit/Application Processing", "root_cause": "Paper-based workflows, sequential approvals, no status visibility for applicants", "financial_formula": "avg_processing_days * applications * economic_impact_of_delay", "typical_impact_pct": 0.10},
            {"id": "data_silos", "label": "Data Silos Between Departments", "root_cause": "Each department has its own systems, no interoperability, duplicate data entry", "financial_formula": "duplicate_labor_hours * hourly_rate + error_correction_cost", "typical_impact_pct": 0.06},
        ],
        "system_names": {
            "operations": "Citizen Services Automation",
            "customer_support": "Constituent Experience Platform",
            "technology": "Government Data Intelligence",
            "programs": "Program Delivery Optimizer",
            "compliance": "Policy Compliance Engine",
        },
        "agent_roles": {
            "customer_support": [
                {"name": "AI Citizen Service Bot", "role": "Handles permit inquiries, appointment scheduling, and status checks 24/7 across phone, web, and text"},
            ],
            "operations": [
                {"name": "AI Permit Processor", "role": "Auto-reviews standard permit applications against zoning codes and regulations, flagging only exceptions for human review"},
            ],
        },
    },
}


def get_profile(industry_id: str) -> dict | None:
    """Get an industry profile by ID."""
    return INDUSTRY_PROFILES.get(industry_id)


def detect_industry(text: str) -> tuple[str, float]:
    """Detect industry vertical from free text (business idea + Q1 answer).

    Returns (industry_id, confidence) where confidence is 0.0-1.0.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}

    for industry_id, profile in INDUSTRY_PROFILES.items():
        score = 0
        # Check aliases
        for alias in profile.get("aliases", []):
            if alias in text_lower:
                score += 3
        # Check label
        if profile["label"].lower() in text_lower:
            score += 5
        # Check pain catalog keywords
        for pain in profile.get("pain_catalog", []):
            label_words = pain["label"].lower().split()
            for word in label_words:
                if len(word) > 3 and word in text_lower:
                    score += 1
        if score > 0:
            scores[industry_id] = score

    if not scores:
        return ("consulting", 0.2)  # Default fallback

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = min(scores[best] / max(total, 1) + 0.2, 1.0)

    return (best, round(confidence, 2))


def get_dept_ftes(industry_id: str, total_employees: int) -> dict[str, int]:
    """Calculate department FTE counts based on industry profile and company size."""
    profile = INDUSTRY_PROFILES.get(industry_id)
    if not profile:
        return {}

    result = {}
    for dept, info in profile["dept_structure"].items():
        result[dept] = max(1, round(total_employees * info["pct_of_headcount"]))
    return result


def estimate_revenue(industry_id: str, total_employees: int) -> int:
    """Estimate annual revenue from employee count and industry benchmark."""
    profile = INDUSTRY_PROFILES.get(industry_id)
    if not profile:
        return total_employees * 200_000
    return total_employees * profile["revenue_per_employee"]
