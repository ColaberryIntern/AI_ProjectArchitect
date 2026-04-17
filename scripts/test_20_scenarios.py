"""Test 20 industry scenarios through the recommendation + capability pipeline."""
from execution.advisory.recommendation_engine import recommend_design
from execution.advisory.capability_mapper import map_capabilities

SCENARIOS = [
    {"name": "Electric Cooperative", "idea": "Rural electric distribution cooperative serving 45,000 meters across 8 counties.", "answers": [
        "Rural electric cooperative, we distribute power to residential and agricultural members.",
        "51-200 employees", "Operations, Customer Support, Finance, Engineering",
        "Storm response is manual - dispatching crews by radio. Vegetation management on fixed 4-year cycles. FERC reports take 40 hours manually.",
        "Members call our 4-person call center for outages. Wait times during storms exceed 30 minutes.",
        "NISC iVUE, Milsoft, ESRI ArcGIS, SCADA, Excel", "Gut feel and Excel reports from 6 systems",
        "Outage dispatching manual. Vegetation risk is a guy in a truck with clipboard. FERC Form 1 takes 40 hours.",
        "50K-100K, 6-12 months", "Reduce storm restoration 30%, cut vegetation spend 25%, auto-generate FERC reports."]},
    {"name": "Freight Brokerage", "idea": "Asset-light freight brokerage booking full-truckload shipments.", "answers": [
        "Freight brokerage and 3PL matching shippers with carriers.", "51-200 employees",
        "Sales, Operations, Finance", "Manual carrier invoice matching, slow settlement, detention charge disputes.",
        "Shipper books load, we source carrier, invoice shipper, pay carrier after PoD.",
        "McLeod TMS, QuickBooks, DAT load board, spreadsheets", "Spreadsheets and gut feel",
        "Matching invoices to rate confirmations, chasing carriers for paperwork, detention disputes.",
        "900K/year, 6-12 months", "Faster cash conversion, fewer billing disputes, reps closing more loads per day."]},
    {"name": "SaaS HR Platform", "idea": "Mid-market B2B SaaS platform for HR teams.", "answers": [
        "B2B SaaS HR compliance platform sold to HR directors.", "51-200 employees",
        "Sales, Customer Support, Engineering", "Long sales cycles, churn in year 2, overwhelmed CSMs.",
        "MQL, SDR qualifies, AE demos, procurement, implementation, CSM handoff.",
        "HubSpot, Salesforce, Gong, Intercom, Jira", "BI dashboards and Salesforce reports",
        "Manual QBR deck prep, onboarding runbooks by hand, account health by gut feel.",
        "1.8M/year, 9 months", "Double net revenue retention, cut time-to-first-value by half."]},
    {"name": "Medical Clinic Group", "idea": "Primary care and urgent care clinic group with 14 locations.", "answers": [
        "Multi-location outpatient medical clinic and urgent care.", "201-1000 employees",
        "Operations, Customer Support, Finance", "Credentialing delays, prior auth denials, long patient wait times.",
        "Patient books online or walks in, triage, clinician visit, billing, follow-up.",
        "Epic EHR, Athena billing, spreadsheets for staffing", "Mix of EHR data and spreadsheets",
        "Prior auth phone calls, credentialing packet assembly, staffing forecast by hand.",
        "2.4M, 12 months", "Lower prior-auth denial rate, faster credentialing, better scheduling."]},
    {"name": "Staffing Agency", "idea": "Light-industrial staffing firm placing warehouse workers.", "answers": [
        "Light-industrial staffing agency placing warehouse, forklift, packaging workers.", "11-50 employees",
        "Sales, Operations, HR", "High associate turnover, long time-to-fill, thin margins.",
        "Client opens req, recruiter sources, associate placed, time sheets, payroll, bill client.",
        "Bullhorn ATS, ADP Workforce Now, Indeed", "ATS keyword search only",
        "Screening resumes, calling associates to confirm shifts, reconciling time sheets.",
        "600K/year, 6 months", "Cut time-to-fill in half, reduce no-show rate."]},
    {"name": "Wine Importer", "idea": "Niche wine import brand bringing biodynamic estates into US restaurants.", "answers": [
        "Import and distribute low-intervention wines to sommeliers and restaurants.", "11-50 employees",
        "Sales, Operations, Finance", "Scarce allocations mis-assigned, TTB paperwork, restaurant slow-pay.",
        "Producer allocation, allocate to accounts, samples, orders, freight, invoice.",
        "NetSuite, QuickBooks, Commerce7, Google Sheets", "Google Sheets and memory",
        "Allocating bottles to right accounts, state-by-state compliance, slow invoicing.",
        "150K, 6-12 months", "Stop under-serving best accounts, shorten DSO, keep compliance clean."]},
    {"name": "Grain Cooperative", "idea": "Farmer-owned grain cooperative running six country elevators.", "answers": [
        "Grain elevator and ag services cooperative - corn, soybeans, wheat.", "51-200 employees",
        "Operations, Sales, Finance", "Shrink and moisture disputes, slow rail loading, blending mistakes.",
        "Member delivers, weigh, grade, storage bin, blend, rail/truck out, settle to member.",
        "AgVantage scale tickets, DTN for markets, QuickBooks", "Scale ticket data and spreadsheets",
        "Reconciling scale tickets, scheduling rail loading crews, agronomy visit reports.",
        "350K/year, 12 months", "Lower shrink, faster rail turns, proactive agronomy outreach."]},
    {"name": "Pet Grooming Franchise", "idea": "Franchised pet grooming chain with 42 locations.", "answers": [
        "Pet grooming franchise, mostly dogs, retail-adjacent salons.", "201-1000 employees",
        "Operations, Customer Support, HR", "No-show appointments, high groomer turnover, franchisee support.",
        "Customer books online or walks in, intake, groom, photo, checkout, rebook.",
        "Gingr booking, Square POS, SharePoint, Slack", "Per-salon data, no rollup",
        "Confirming appointments, answering franchisee questions, scheduling groomers.",
        "500K/year, 6-12 months", "Cut no-shows, keep groomers longer, automate franchisee support."]},
    {"name": "Insurance Agency", "idea": "Independent insurance agency writing P&C and life policies.", "answers": [
        "Independent insurance agency, property and casualty plus life insurance.", "11-50 employees",
        "Sales, Customer Support, Finance", "Renewal follow-ups missed, quoting takes too long, claims support overwhelmed.",
        "Client calls or finds us online, we quote carriers, bind policy, service claims.",
        "Applied Epic, carrier portals, Outlook, Excel", "Carrier reports and gut feel",
        "Re-keying data across carrier portals, chasing renewal signatures, claims doc assembly.",
        "200K, 6-12 months", "Zero missed renewals, quote in under 5 minutes, claims docs auto-assembled."]},
    {"name": "Construction GC", "idea": "Mid-size commercial general contractor building offices and retail.", "answers": [
        "Commercial general contractor, offices and retail, Southeast US.", "51-200 employees",
        "Operations, Finance, Engineering", "Sub scheduling conflicts, change order tracking chaos, RFI delays.",
        "Owner RFPs, we bid, win, manage subs, inspections, punch list, close out.",
        "Procore, Bluebeam, QuickBooks, Excel", "Project managers track in Procore and spreadsheets",
        "Daily logs by hand, sub pay app processing, lien waiver tracking, RFI routing.",
        "500K, 12 months", "Every RFI answered in 24 hours, zero missed change orders, daily logs automated."]},
    {"name": "Dental Practice", "idea": "Multi-location dental practice with 8 offices.", "answers": [
        "Multi-location dental practice, general and cosmetic dentistry.", "51-200 employees",
        "Operations, Customer Support, Finance", "Patient no-shows, insurance verification delays, low treatment acceptance.",
        "Patients book online or call, check-in, treatment, checkout with payment plan.",
        "Dentrix, insurance portals, phone system", "Dentrix reports and manual tracking",
        "Insurance eligibility verification calls, appointment confirmations, treatment plan follow-ups.",
        "300K, 6-12 months", "No-show under 5%, insurance verified before arrival, 80% treatment acceptance."]},
    {"name": "Accounting Firm", "idea": "Regional CPA firm with 45 professionals doing tax and audit.", "answers": [
        "Regional CPA firm, tax preparation, audit, business advisory.", "11-50 employees",
        "Operations, Sales, Finance", "Tax season bottlenecks, document collection chaos, low advisory upsell.",
        "Referrals, engagement letter, document collection, preparation, review, filing.",
        "CCH Axcess, Thomson Reuters, SharePoint, Excel", "Time tracking reports and partner judgment",
        "Chasing clients for tax documents, data entry from scans, engagement letter generation.",
        "250K, 6-12 months", "Cut document collection 50%, zero missed deadlines, advisory revenue up 20%."]},
    {"name": "Auto Dealership", "idea": "Multi-brand auto dealership group with 4 locations.", "answers": [
        "Multi-brand auto dealership, new and used vehicles, service departments.", "201-1000 employees",
        "Sales, Customer Support, Operations", "Slow lead follow-up, service scheduling chaos, F&I too long.",
        "Customer walks in or submits online lead, test drive, negotiation, F&I, delivery.",
        "DealerSocket CRM, Reynolds DMS, CarFax", "DMS reports and sales manager gut feel",
        "Entering leads from 8 sources, service appointment phone tag, inventory reconciliation.",
        "800K, 6-12 months", "Internet leads contacted in under 5 min, service capacity up 20%."]},
    {"name": "Property Management", "idea": "Residential property management company managing 2,200 units.", "answers": [
        "Residential property management, apartments and townhomes.", "51-200 employees",
        "Operations, Customer Support, Finance", "Maintenance backlogs, lease renewal delays, rent collection friction.",
        "Tenants apply online, sign lease, submit maintenance via portal, renew or move out.",
        "AppFolio, vendor portals, Outlook, Excel", "AppFolio reports and PM judgment",
        "Dispatching maintenance vendors, chasing overdue rent, lease renewal paperwork.",
        "400K, 12 months", "Maintenance resolved in 48 hours, 95% lease renewal rate."]},
    {"name": "3PL Warehouse", "idea": "Third-party logistics warehouse for 80 e-commerce brands.", "answers": [
        "3PL warehouse, pick pack ship fulfillment for e-commerce brands.", "51-200 employees",
        "Operations, Customer Support, Finance", "Picking errors, inventory discrepancies, SLA misses.",
        "Client sends orders via API, we pick, pack, ship, send tracking, handle returns.",
        "ShipStation, NetSuite, barcode scanners, Excel", "WMS reports and floor manager experience",
        "Inventory cycle counts by hand, order priority sorting, returns processing.",
        "600K, 6-12 months", "Pick accuracy 99.9%, same-day ship 98%, real-time client dashboards."]},
    {"name": "Law Firm", "idea": "Mid-size law firm with 30 attorneys doing litigation and corporate.", "answers": [
        "Mid-size law firm, litigation, corporate, real estate.", "51-200 employees",
        "Operations, Sales, Finance", "Conflict checks slow, document review bottleneck, low realization.",
        "Referrals, intake, conflict check, engagement, matter management, billing.",
        "Clio, Westlaw, NetDocuments, Excel for billing", "Partner billing review and time entries",
        "Conflict checks across 15 years, document review for discovery, time entry reconciliation.",
        "500K, 12 months", "Conflict checks in 5 minutes, doc review 70% faster, realization above 90%."]},
    {"name": "Metal Fabrication", "idea": "Custom metal fabrication shop for aerospace and defense.", "answers": [
        "Custom metal fabrication, CNC machining and welding for aerospace and defense.", "51-200 employees",
        "Operations, Engineering, Finance", "Job scheduling conflicts, quality escape rate, quoting takes 3 days.",
        "RFQ from contractor, we quote, win PO, program CNC, fabricate, QC inspect, ship.",
        "JobBOSS ERP, SolidWorks, CMM inspection, Excel", "ERP job cost reports and supervisor knowledge",
        "Manual first-article inspection, job scheduling on whiteboard, quoting from history.",
        "400K, 12 months", "Quote under 4 hours, zero quality escapes, visible job schedule."]},
    {"name": "Restaurant Group", "idea": "Fast-casual restaurant group with 12 locations.", "answers": [
        "Fast-casual restaurant group, dine-in, takeout, catering.", "201-1000 employees",
        "Operations, HR, Finance", "Labor scheduling chaos, food waste, inconsistent catering follow-up.",
        "Customers order in-store, online, or call for catering.",
        "Toast POS, 7shifts, Yelp, DoorDash, spreadsheets", "POS reports and GM judgment",
        "Building weekly schedules, counting inventory, following up catering leads.",
        "300K, 6-12 months", "Labor cost under 28%, food waste cut 30%, catering revenue up 40%."]},
    {"name": "Education Nonprofit", "idea": "Education nonprofit running STEM programs in 25 Title I schools.", "answers": [
        "Education nonprofit, after-school STEM programs in underserved schools.", "11-50 employees",
        "Operations, Marketing, Finance", "Grant reporting takes forever, volunteer chaos, donor follow-up inconsistent.",
        "Schools sign up, we place instructors, run programs, report outcomes to funders.",
        "Salesforce NPSP, Google Workspace, Canva, Excel", "Grant reports from 4 systems manually",
        "Assembling grant outcome reports, coordinating volunteers, tracking donor touches.",
        "Under 100K, 6 months", "Grant reports in 1 day not 2 weeks, zero missed donor follow-ups."]},
    {"name": "Water Utility", "idea": "Municipal water utility serving 120,000 connections.", "answers": [
        "Municipal water utility, treatment and distribution, 120,000 connections.", "201-1000 employees",
        "Operations, Customer Support, Engineering", "Water main breaks, lead service line tracking, meter reading errors.",
        "Customers open accounts by phone. We meter, bill monthly, handle service calls.",
        "Tyler Munis billing, SCADA, GIS, paper work orders, Excel", "SCADA alarms and field reports",
        "Work order dispatching by phone, meter route planning, lead pipe inventory, EPA compliance.",
        "1M, 12 months", "Main break response under 2 hours, 100% lead pipe inventory, EPA reports automated."]},
]

print("=" * 100)
print(f"{'#':<3} {'Scenario':<22} {'Industry':<25} {'Src':<6} {'Primary Outcome':<28} {'Secondary':<28} {'Domain':<10} {'Caps'}")
print("=" * 100)

issues = []

for i, s in enumerate(SCENARIOS, 1):
    session = {
        "business_idea": s["idea"],
        "answers": [{"answer_text": a, "question_id": f"q{j+1}"} for j, a in enumerate(s["answers"])],
    }
    recs = recommend_design(session)

    cap_session = dict(session)
    cap_session["selected_outcomes"] = list(recs["recommended_outcomes"].keys())
    cap_session["selected_ai_systems"] = list(recs["recommended_systems"].keys())
    caps = map_capabilities(cap_session)

    outcomes = list(recs["recommended_outcomes"].keys())
    primary = outcomes[0] if outcomes else "?"
    secondary = outcomes[1] if len(outcomes) > 1 else "-"
    industry = recs.get("industry_label", "?")
    source = recs.get("industry_source", "?")
    domain = caps.get("primary_domain", "?")
    num_caps = len(caps.get("recommended", []))

    print(f"{i:<3} {s['name']:<22} {industry:<25} {source:<6} {primary:<28} {secondary:<28} {domain:<10} {num_caps}")

    # Check for issues
    if primary == "improve_cx" and s["name"] not in ["SaaS HR Platform"]:
        issues.append(f"  #{i} {s['name']}: improve_cx as primary seems wrong")
    if domain == "support" and s["name"] in ["Electric Cooperative", "Freight Brokerage", "Grain Cooperative", "Construction GC", "Metal Fabrication", "Water Utility"]:
        issues.append(f"  #{i} {s['name']}: domain=support but should be operations")
    if source == "seed" and industry == "Professional Services & Consulting" and s["name"] not in ["Accounting Firm", "Law Firm"]:
        issues.append(f"  #{i} {s['name']}: fell back to generic consulting profile")

print()
if issues:
    print("ISSUES DETECTED:")
    for iss in issues:
        print(iss)
else:
    print("NO ISSUES DETECTED")

print()
print("DETAILED RECOMMENDATIONS:")
print()
for i, s in enumerate(SCENARIOS, 1):
    session = {
        "business_idea": s["idea"],
        "answers": [{"answer_text": a, "question_id": f"q{j+1}"} for j, a in enumerate(s["answers"])],
    }
    recs = recommend_design(session)
    print(f"--- {i}. {s['name']} ---")
    for oid, rat in recs["recommended_outcomes"].items():
        print(f"  {rat}")
    for sid, rat in recs["recommended_systems"].items():
        print(f"  {rat}")
    print()
