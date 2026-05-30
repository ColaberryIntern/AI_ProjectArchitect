"""End-to-end build test for the spec-driven upgrade.

Feeds an ~8K-word idea PRD through:
  1. profile_generator.generate_profile (real LLM call)
  2. lock_outline (writes requirements.json)
  3. auto_builder.run_auto_build (full chapter generation + spec gates)

Reports actual numbers: token usage, wall-clock time per phase, spec
gate results, document length, citation density, total cost.

Usage:
    python -m scripts.real_build_test [chapters] [depth] [min_words]

Defaults: 11 chapters, professional depth, min_words override 2500.

The "8K" is the INPUT idea size, not chapter output size. The min_words
override (default 2500) replaces Professional's stock 5000 — gpt-4o-mini
delivers ~1700-2200 words/chapter naturally and a 5000 target triggers
retry storms with diminishing returns. 2500 keeps the word floor at
875 (35% of min), which actual chapter output exceeds without retries.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from config import settings as _settings
from config.settings import OUTPUT_DIR
from execution.auto_builder import run_auto_build
from execution.profile_generator import generate_profile
from execution.requirements_writer import read_requirements
from execution.state_manager import (
    add_feature,
    advance_phase,
    approve_features,
    initialize_state,
    lock_outline,
    save_state,
    set_build_depth_mode,
    set_intelligence_goals,
    set_outline_sections,
)


SLUG = "spec-test-freight"

# OpenAI pricing per 1M tokens (input, output) — used for cost reporting only.
# Add new models here when the project upgrades. Values pulled from
# https://openai.com/api/pricing — keep in sync.
MODEL_PRICING = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-2024-08-06": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-nano": (0.10, 0.40),
}


def _model_pricing(model: str) -> tuple[float, float]:
    """Return (input_per_M, output_per_M). Defaults to gpt-4o-mini if unknown."""
    return MODEL_PRICING.get(model, MODEL_PRICING["gpt-4o-mini"])


def build_features() -> list[dict]:
    """Eleven core features promoted to must-priority Requirements,
    covering the freight financial OS PRD end-to-end."""
    return [
        {
            "id": "REQ-001",
            "name": "BOL Document Ingestion",
            "description": "Accept BOL/POD PDFs, extract load number and weight, link to a shipment.",
            "rationale": "Billing cannot start until documents are validated; this is the gate.",
            "type": "core",
            "problem_mapped_to": "document_driven_gating",
            "build_order": 1,
            "requirement_type": "functional",
            "actor": "broker",
            "action": "upload a BOL PDF for a delivered load",
            "value": "the system extracts load metadata and unblocks invoicing",
            "priority": "must",
            "dependencies": [],
            "acceptance_criteria": [
                {
                    "id": "AC-001-1",
                    "given": "an authenticated broker with shipment SHP-1234 in delivered state",
                    "when": "they POST /api/documents with a BOL PDF referencing SHP-1234",
                    "then": "the response is 201, document.load_number == 'L-1234' is extracted within 5s, and shipment.invoiceable becomes true",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "performance",
                    "metric": "p95 OCR-to-extracted-fields latency",
                    "threshold": "< 8s",
                    "verification": "k6 load test against /api/documents at 50 rps",
                }
            ],
        },
        {
            "id": "REQ-002",
            "name": "Accessorial Charge Engine",
            "description": "Compute accessorial charges (detention, layover, lumper) per contract rules.",
            "rationale": "Mis-priced accessorials are the largest cause of disputes and short-pay.",
            "type": "core",
            "problem_mapped_to": "accessorial_complexity",
            "build_order": 2,
            "requirement_type": "functional",
            "actor": "system",
            "action": "compute accessorial charges from delivery telemetry",
            "value": "the invoice line items match contract math exactly",
            "priority": "must",
            "dependencies": ["REQ-001"],
            "acceptance_criteria": [
                {
                    "id": "AC-002-1",
                    "given": "shipment SHP-1234 with 4h detention, customer contract C-99 (detention rate $75/h)",
                    "when": "the engine runs accessorial computation for SHP-1234",
                    "then": "an accessorial line of $300.00 with code 'DETN' and contract_ref C-99 is persisted and traceable",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "reliability",
                    "metric": "accessorial calc determinism",
                    "threshold": ">= 99.99%",
                    "verification": "replay test over 10K historical shipments",
                }
            ],
        },
        {
            "id": "REQ-003",
            "name": "Invoice Composition",
            "description": "Compose customer invoices from line haul + accessorial lines with audit trail.",
            "rationale": "Faster correct invoices reduce DSO and minimize disputes.",
            "type": "core",
            "problem_mapped_to": "asymmetric_invoicing",
            "build_order": 3,
            "requirement_type": "functional",
            "actor": "broker",
            "action": "issue an invoice for a delivered shipment",
            "value": "the customer receives a correct, payable invoice within 24h of POD",
            "priority": "must",
            "dependencies": ["REQ-001", "REQ-002"],
            "acceptance_criteria": [
                {
                    "id": "AC-003-1",
                    "given": "shipment SHP-1234 with line haul $1,200 and accessorial $300.00",
                    "when": "the broker issues an invoice via POST /api/invoices for SHP-1234",
                    "then": "an invoice INV-* is created with total $1,500.00, status 'issued', and an audit row recording {actor, timestamp, sha256(payload)}",
                    "measurable": True,
                }
            ],
            "nfr": [],
        },
        {
            "id": "REQ-004",
            "name": "Carrier Identity Verification",
            "description": "Verify carrier identity (MC#, USDOT, payee bank) before settlement payment.",
            "rationale": "Prevents double-brokering and identity-theft fraud — industry standard 2-5% of settlements.",
            "type": "core",
            "problem_mapped_to": "fraud_risk",
            "build_order": 4,
            "requirement_type": "functional",
            "actor": "system",
            "action": "verify carrier identity before issuing settlement payment",
            "value": "fraudulent payments are blocked at the gate",
            "priority": "must",
            "dependencies": [],
            "acceptance_criteria": [
                {
                    "id": "AC-004-1",
                    "given": "carrier C-501 with MC# 123456 and a recent payee bank change (within 72h)",
                    "when": "the system attempts to process settlement S-99",
                    "then": "settlement is held with status 'pending_verification', a fraud-risk audit row is written, and a human-review notification is queued",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "security",
                    "metric": "audit-row immutability",
                    "threshold": "WORM, SOC 2 Type II compliant",
                    "verification": "annual SOC 2 audit",
                }
            ],
        },
        {
            "id": "REQ-005",
            "name": "Dispute Evidence Binder",
            "description": "Auto-assemble evidence binder (BOL, POD, contract, comms log) for any disputed invoice line.",
            "rationale": "Disputes are normal process; a 30-min binder vs 8-hour manual collection saves 90% labor.",
            "type": "core",
            "problem_mapped_to": "dispute_normality",
            "build_order": 5,
            "requirement_type": "functional",
            "actor": "ar_clerk",
            "action": "open a dispute case for a customer-flagged invoice line",
            "value": "evidence is collected automatically and delivered to the disputing customer",
            "priority": "must",
            "dependencies": ["REQ-001", "REQ-003"],
            "acceptance_criteria": [
                {
                    "id": "AC-005-1",
                    "given": "invoice INV-77 with disputed line for accessorial DETN ($300)",
                    "when": "the AR clerk opens dispute case D-4 for INV-77 line 2",
                    "then": "an evidence binder is generated within 30s containing: BOL.pdf, POD.pdf, contract C-99 excerpt, telemetry log, and is sharable via signed URL valid for 7 days",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "performance",
                    "metric": "binder assembly time",
                    "threshold": "< 30s p95",
                    "verification": "load test with 1K parallel disputes",
                }
            ],
        },
        {
            "id": "REQ-006",
            "name": "AR Orchestration & Collections",
            "description": "Track invoice aging, prioritize collection actions, apply payments, and reconcile against bank.",
            "rationale": "Faster, prioritized collections compress DSO by 12-15 days; automated payment matching saves clerical time.",
            "type": "core",
            "problem_mapped_to": "dso_compression",
            "build_order": 6,
            "requirement_type": "functional",
            "actor": "ar_clerk",
            "action": "view aged-AR queue and trigger collection actions per priority",
            "value": "DSO drops 12-15 days within 90 days of go-live",
            "priority": "must",
            "dependencies": ["REQ-003"],
            "acceptance_criteria": [
                {
                    "id": "AC-006-1",
                    "given": "an open invoice INV-201 aged 45 days for customer C-77 with prior on-time history",
                    "when": "the orchestrator runs the daily collection prioritizer",
                    "then": "INV-201 is placed in tier 'soft_reminder' and a templated email is queued; no escalation occurs",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "performance",
                    "metric": "p95 daily prioritizer run time for 50K open invoices",
                    "threshold": "< 90s",
                    "verification": "scheduled benchmark on production-shape dataset",
                }
            ],
        },
        {
            "id": "REQ-007",
            "name": "AP Orchestration & Settlement",
            "description": "Compute carrier settlement (per-load + per-period adjustments), pay via ACH/wire/QuickPay, reconcile against bank.",
            "rationale": "Settlement leakage is industry-standard 2-5% of payments; deterministic computation cuts it to <0.3%.",
            "type": "core",
            "problem_mapped_to": "settlement_leakage",
            "build_order": 7,
            "requirement_type": "functional",
            "actor": "ap_clerk",
            "action": "approve a carrier settlement and schedule payment",
            "value": "the carrier is paid the correct amount on the contracted day",
            "priority": "must",
            "dependencies": ["REQ-002", "REQ-004"],
            "acceptance_criteria": [
                {
                    "id": "AC-007-1",
                    "given": "carrier C-501 with verified identity and 12 delivered loads totalling $14,200 net of advances",
                    "when": "the AP clerk approves settlement S-99 for the period",
                    "then": "an ACH file is generated with the correct payee bank, amount $14,200, settlement state moves to 'sent', and an audit row records {actor, timestamp, sha256(payload)}",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "reliability",
                    "metric": "settlement-file generation determinism",
                    "threshold": ">= 99.99%",
                    "verification": "replay test over 100K historical settlements",
                }
            ],
        },
        {
            "id": "REQ-008",
            "name": "Financial Control Tower",
            "description": "Live executive dashboard: DSO, dispute rate trends, leakage, margin by lane, working capital, fraud savings.",
            "rationale": "The controller and CFO need real-time visibility to manage thin-margin operations; a single pane of glass replaces six tabs.",
            "type": "core",
            "problem_mapped_to": "leadership_visibility",
            "build_order": 8,
            "requirement_type": "functional",
            "actor": "controller",
            "action": "view rolling 13-week DSO and dispute rate trends with drill-down by customer",
            "value": "leadership identifies and corrects margin leaks within hours, not weeks",
            "priority": "must",
            "dependencies": ["REQ-003", "REQ-005", "REQ-006", "REQ-007"],
            "acceptance_criteria": [
                {
                    "id": "AC-008-1",
                    "given": "12 weeks of invoice + dispute history for 50 active customers",
                    "when": "the controller loads the control tower dashboard",
                    "then": "the DSO trend chart renders within 3s p95 with a per-customer drill-down link, and drift alerts fire when DSO worsens by >= 10% week-over-week",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "performance",
                    "metric": "p95 dashboard render latency",
                    "threshold": "< 3s",
                    "verification": "synthetic monitoring with realistic data shape",
                }
            ],
        },
        {
            "id": "REQ-009",
            "name": "Integration Layer",
            "description": "Vendor-specific adapters for TMS (McLeod, Aljex, MercuryGate, Revenova), accounting (QBO, Sage, NetSuite), banking, and FMCSA.",
            "rationale": "Most brokerages run on a TMS we don't own; integration depth is the highest-leverage moat.",
            "type": "core",
            "problem_mapped_to": "integration_reality",
            "build_order": 9,
            "requirement_type": "functional",
            "actor": "system",
            "action": "translate a McLeod EDI 214 status update into a canonical shipment.delivered event",
            "value": "downstream services (billing, AR) work without TMS-specific code",
            "priority": "must",
            "dependencies": [],
            "acceptance_criteria": [
                {
                    "id": "AC-009-1",
                    "given": "a McLeod EDI 214 message with status code 'X3' for shipment SHP-9000",
                    "when": "the McLeod adapter processes the message",
                    "then": "a 'shipment.delivered' event is emitted to the canonical event stream within 2s with shipment_id=SHP-9000 and source='mcleod'; the original EDI is archived for audit",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "reliability",
                    "metric": "adapter event-loss rate",
                    "threshold": "< 0.001%",
                    "verification": "weekly reconciliation of TMS shipment count vs. canonical event count",
                }
            ],
        },
        {
            "id": "REQ-010",
            "name": "Security, Compliance & Audit",
            "description": "SOC 2 Type II posture: encryption, access controls, immutable audit logs (WORM, 7-year retention), MFA, anomaly detection.",
            "rationale": "Brokerage data is sensitive; SOC 2 is increasingly a sales prerequisite from enterprise shippers and banks.",
            "type": "core",
            "problem_mapped_to": "compliance_floor",
            "build_order": 10,
            "requirement_type": "constraint",
            "actor": "system",
            "action": "write a tamper-evident audit row for every state transition that touches money",
            "value": "the audit trail is reproducible from raw events for any external auditor or regulator",
            "priority": "must",
            "dependencies": [],
            "acceptance_criteria": [
                {
                    "id": "AC-010-1",
                    "given": "a settlement S-100 transitioning from 'in_review' to 'approved'",
                    "when": "the orchestrator records the transition",
                    "then": "a WORM audit row is written within 100ms containing actor, timestamp, sha256(payload before and after), Merkle-anchored hash; rewriting or deleting the row is impossible at the storage layer",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "compliance",
                    "metric": "audit log retention",
                    "threshold": "7 years WORM, SOC 2 Type II annual attestation",
                    "verification": "annual external audit",
                }
            ],
        },
        {
            "id": "REQ-011",
            "name": "Multi-Tenancy & Onboarding",
            "description": "Logical tenant isolation at DB row-level, tenant config UI with four-eyes approval, 90-day onboarding playbook.",
            "rationale": "Each brokerage is a tenant; cross-tenant data access is impossible by design and must be enforced at the database boundary.",
            "type": "core",
            "problem_mapped_to": "tenant_safety",
            "build_order": 11,
            "requirement_type": "constraint",
            "actor": "system",
            "action": "reject any database query that does not include a tenant_id filter scoped to the authenticated principal",
            "value": "a tenant cannot see another tenant's data even if application code has a bug",
            "priority": "must",
            "dependencies": [],
            "acceptance_criteria": [
                {
                    "id": "AC-011-1",
                    "given": "a service authenticated as tenant T-1 attempts to read invoice INV-999 owned by tenant T-2",
                    "when": "the query reaches the database",
                    "then": "row-level security returns zero rows; an audit row is written with tenant_id=T-1, attempted=T-2, action='cross_tenant_read_denied'",
                    "measurable": True,
                }
            ],
            "nfr": [
                {
                    "category": "security",
                    "metric": "cross-tenant data exposure rate",
                    "threshold": "0 (zero) — must be impossible by design",
                    "verification": "quarterly penetration test specifically targeting tenant isolation",
                }
            ],
        },
    ]


def build_outline_sections() -> list[dict]:
    return [
        {"index": 1, "title": "Document Ingestion & Validation", "type": "required",
         "summary": "BOL/POD upload, OCR extraction, validation gates, and the unblocking of invoiceability for a delivered shipment."},
        {"index": 2, "title": "Accessorial Charges & Contract Rules", "type": "required",
         "summary": "Detention/layover/lumper computation per customer contract, idempotent line generation, and audit-grade traceability."},
        {"index": 3, "title": "Invoice Composition & Issuance", "type": "required",
         "summary": "Compose correct customer invoices from line haul plus accessorials, persist, and emit an immutable audit trail."},
        {"index": 4, "title": "Carrier Trust & Fraud Detection", "type": "required",
         "summary": "MC#/USDOT verification, payee-bank change detection, hold-and-review state machine, and SOC 2 audit immutability."},
        {"index": 5, "title": "Dispute Evidence & Resolution", "type": "required",
         "summary": "Auto-assembly of multi-source evidence binders, signed-URL sharing, and the dispute lifecycle from open to resolved."},
        {"index": 6, "title": "AR Orchestration & Collections", "type": "required",
         "summary": "Aged-AR queue, prioritized collection actions, payment matching, and bank reconciliation that drives DSO compression."},
        {"index": 7, "title": "AP Orchestration & Settlement", "type": "required",
         "summary": "Carrier settlement computation across per-load and per-period adjustments, ACH/wire/QuickPay rails, and bank reconciliation."},
        {"index": 8, "title": "Financial Control Tower", "type": "required",
         "summary": "Live executive dashboard for DSO, dispute trends, leakage, and margin — the day-to-day operating instrument for controller and CFO."},
        {"index": 9, "title": "Integration Layer", "type": "required",
         "summary": "Vendor-specific adapters for TMS (McLeod, Aljex, MercuryGate, Revenova), accounting systems, banking partners, and FMCSA databases."},
        {"index": 10, "title": "Security, Compliance & Audit", "type": "required",
         "summary": "SOC 2 Type II posture, immutable WORM audit log, MFA, encryption everywhere, anomaly detection, and incident response plan."},
        {"index": 11, "title": "Multi-Tenancy & Onboarding", "type": "required",
         "summary": "Tenant isolation enforced at the database row-level, four-eyes config workflow, and the 90-day customer onboarding playbook."},
    ]


def setup_traces(state: dict, sections: list[dict]) -> None:
    """Link each Requirement to its outline section by id (title)."""
    section_titles = [s["title"] for s in sections]
    for i, feature in enumerate(state["features"]["core"]):
        if i < len(section_titles):
            feature["traces_to"] = {
                "outline_section_id": section_titles[i],
                "chapter_ids": [],
                "problem_id": feature.get("problem_mapped_to"),
            }


def reset_output_dir() -> None:
    """Best-effort wipe of prior test artifacts. On Windows, OneDrive
    sometimes holds file handles briefly — retry a few times before
    giving up. If we still can't wipe, move out of the way."""
    target = OUTPUT_DIR / SLUG
    if not target.exists():
        return
    for attempt in range(3):
        try:
            shutil.rmtree(target)
            return
        except (PermissionError, OSError) as e:
            if attempt == 2:
                # Last resort: rename the dir out of the way and continue.
                # The build will create a fresh one alongside.
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                fallback = target.parent / f"{SLUG}-orphan-{stamp}"
                try:
                    target.rename(fallback)
                    print(f"  [warn] could not delete {target}; renamed to {fallback.name}")
                    return
                except OSError:
                    print(f"  [warn] could not reset {target}: {e}; continuing anyway")
                    return
            time.sleep(1)


def count_citations(text: str) -> dict:
    req_cites = re.findall(r"\[REQ-\d+\]", text)
    ac_cites = re.findall(r"\[AC-\d+-\d+\]", text)
    return {
        "req_total": len(req_cites),
        "req_unique": len(set(req_cites)),
        "ac_total": len(ac_cites),
    }


def force_min_words(depth: str, target: int) -> int | None:
    """Override the depth's min_words. Returns the original for restore."""
    from execution import build_depth as bd
    if depth not in bd.DEPTH_MODES:
        return None
    original = bd.DEPTH_MODES[depth]["min_words"]
    bd.DEPTH_MODES[depth]["min_words"] = target
    return original


def restore_min_words(depth: str, original: int | None) -> None:
    if original is None:
        return
    from execution import build_depth as bd
    bd.DEPTH_MODES[depth]["min_words"] = original


def main(n_chapters: int = 11, depth: str = "professional",
         min_words: int | None = 2500,
         idea_path: str = "scripts/test_idea_8k.txt") -> int:
    print("=" * 72)
    print("REAL BUILD TEST — spec-driven pipeline (LIVE LLM CALLS)")
    print("=" * 72)

    idea_file = Path(idea_path)
    if not idea_file.exists():
        print(f"ERROR: idea file not found: {idea_file}")
        return 1

    idea_text = idea_file.read_text(encoding="utf-8")
    word_count = len(idea_text.split())
    print(f"Idea input:    {idea_path} ({word_count} words, {len(idea_text):,} chars)")
    print(f"Chapters:      {n_chapters}, depth: {depth} (min_words override: {min_words})")
    print()

    original_min = None
    if min_words is not None:
        original_min = force_min_words(depth, min_words)

    try:
        return _run(idea_path, idea_text, word_count, n_chapters, depth)
    finally:
        if original_min is not None:
            restore_min_words(depth, original_min)


def _run(idea_path: str, idea_text: str, word_count: int,
         n_chapters: int, depth: str) -> int:
    """The main pipeline run — extracted so the caller can wrap with
    try/finally for min_words restoration."""

    reset_output_dir()

    # Phase 1 — idea_intake: generate_profile against the 8K idea
    print("[1/4] Phase: idea_intake (generate_profile)")
    t_intake = time.monotonic()
    profile_data = generate_profile(idea_text)
    intake_elapsed = time.monotonic() - t_intake
    print(f"  Wall-clock: {intake_elapsed:.1f}s")
    print(f"  Fields generated: {list(profile_data.get('fields', {}).keys())}")
    print(f"  Derived: tc={len(profile_data['derived']['technical_constraints'])}, "
          f"nfr={len(profile_data['derived']['non_functional_requirements'])}, "
          f"metrics={len(profile_data['derived']['success_metrics'])}, "
          f"risks={len(profile_data['derived']['risk_assessment'])}, "
          f"ucs={len(profile_data['derived']['core_use_cases'])}")
    print()

    # Phase 2 — bootstrap state with the real profile output
    print("[2/4] Phase: outline_generation + lock_outline")
    t_bootstrap = time.monotonic()
    state = initialize_state("Spec Test Freight OS", blueprint="standard")
    state["project"]["slug"] = SLUG
    state["idea"] = {
        "original_raw": idea_text,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    # Wire generate_profile output into state.project_profile
    profile_state = {}
    for field_name, field_data in profile_data["fields"].items():
        recommended = field_data.get("recommended") or (
            field_data.get("options", [{}])[0].get("value", "") if field_data.get("options") else ""
        )
        profile_state[field_name] = {
            "selected": recommended,
            "confidence": field_data.get("confidence", 0.0),
            "confirmed": True,
            "options": field_data.get("options", []),
        }
    profile_state.update({
        "technical_constraints": profile_data["derived"]["technical_constraints"],
        "non_functional_requirements": profile_data["derived"]["non_functional_requirements"],
        "success_metrics": profile_data["derived"]["success_metrics"],
        "risk_assessment": profile_data["derived"]["risk_assessment"],
        "core_use_cases": profile_data["derived"]["core_use_cases"],
        "intelligence_goals": [],
        "build_depth_mode": depth,
        # Lock the technology stack so every chapter uses the SAME database,
        # runtime, deployment target, etc. Without this, the LLM independently
        # re-decides per chapter (e.g. MongoDB in ch1, Postgres in ch11) and
        # produces a self-contradictory build guide.
        "frozen_architecture": {
            "database": "PostgreSQL 15 with row-level security for multi-tenancy",
            "runtime": "Python 3.11 + FastAPI",
            "event_bus": "AWS Kinesis Data Streams (canonical events) + MSK for high-throughput streams",
            "object_storage": "AWS S3 with Object Lock (WORM) for audit logs and uploaded documents",
            "search_index": "AWS OpenSearch for full-text and analytics queries",
            "compute": "Containerized stateless services on AWS EKS (Kubernetes), multi-AZ, us-east-1 primary + us-west-2 warm secondary",
            "ai_inference": "AWS Bedrock for OCR with self-hosted fallback for cost control",
            "auth": "OAuth 2.0 with rotating tokens; service-to-service uses signed JWTs with short TTLs",
            "frontend": "TypeScript + React for the Financial Control Tower; the rest of the platform is API-only",
            "ci_cd": "GitHub Actions for build and test, ArgoCD for GitOps deployment to EKS",
            "observability": "OpenTelemetry traces, Prometheus metrics, Grafana dashboards, per-tenant log indices",
            "package_managers": "uv for Python deps, pnpm for the TypeScript frontend",
        },
    })
    state["project_profile"] = profile_state
    state["features"] = {
        "core": build_features()[:n_chapters],
        "optional": [],
        "approved": True,
        "catalog": [],
    }
    sections = build_outline_sections()[:n_chapters]
    set_outline_sections(state, sections)
    setup_traces(state, sections)
    set_build_depth_mode(state, depth)
    set_intelligence_goals(state, [])
    advance_phase(state, "feature_discovery")
    advance_phase(state, "outline_generation")
    advance_phase(state, "outline_approval")
    lock_outline(state)
    advance_phase(state, "chapter_build")
    save_state(state, SLUG)
    bootstrap_elapsed = time.monotonic() - t_bootstrap
    print(f"  Wall-clock: {bootstrap_elapsed:.1f}s")
    artifact = OUTPUT_DIR / SLUG / "specs" / "requirements.json"
    if artifact.exists():
        doc = json.loads(artifact.read_text(encoding="utf-8"))
        print(f"  [ok] requirements.json: {doc['summary']['total']} reqs, "
              f"{doc['summary']['by_priority'].get('must', 0)} must, "
              f"{doc['summary']['with_acceptance_criteria']} with AC, "
              f"{doc['summary']['with_nfr']} with NFR")
    print()

    # Phase 3 — auto-build (chapter generation + spec gates)
    print("[3/4] Phase: chapter_build + spec gates (LIVE)")
    t_build = time.monotonic()
    events = []
    last_chapter = 0
    for event in run_auto_build(state, SLUG):
        events.append(event)
        elapsed = time.monotonic() - t_build
        if event.event_type == "chapter" and event.chapter_index != last_chapter:
            last_chapter = event.chapter_index
            print(f"  [{elapsed:5.1f}s] ch{event.chapter_index}/{event.total_chapters}: {event.message}")
        elif event.event_type == "scoring" and event.chapter_index > 0:
            d = event.data
            print(f"           score: {d.get('score')}/100, {d.get('word_count')} words, "
                  f"{d.get('tokens_used')} tokens, {d.get('latency_ms')}ms")
        elif event.event_type == "scoring" and event.chapter_index == 0:
            print(f"  [doc]   {event.message}")
        elif event.event_type == "phase":
            print(f"  [{elapsed:5.1f}s] PHASE: {event.message}")
        elif event.event_type == "complete":
            print(f"  [{elapsed:5.1f}s] COMPLETE: {event.message}")
        elif event.event_type == "error":
            print(f"  [error] {event.message}")
    build_elapsed = time.monotonic() - t_build
    print()

    # Phase 4 — Report all numbers
    print("[4/4] Numbers")
    print()

    # Token totals from chapter scoring events. The auto_builder emits
    # an aggregated ``tokens_used`` per chapter (prompt + completion),
    # plus a document-level summary on the chapter_index=0 scoring event
    # that has prompt/completion split if available.
    chapter_total_tokens = sum(
        e.data.get("tokens_used", 0) for e in events
        if e.event_type == "scoring" and e.chapter_index > 0
    )
    # Try to pull split from doc-level scoring event
    doc_data = next(
        (e.data for e in events if e.event_type == "scoring" and e.chapter_index == 0),
        {},
    )
    chapter_input = doc_data.get("total_input_tokens") or doc_data.get("prompt_tokens", 0)
    chapter_output = doc_data.get("total_output_tokens") or doc_data.get("completion_tokens", 0)
    # Document scoring summary event has aggregate metrics
    doc_scoring = next(
        (e for e in events if e.event_type == "scoring" and e.chapter_index == 0),
        None,
    )

    # Spec gates
    spec_report = state.get("quality", {}).get("spec_report", {})
    print("Spec gate results:")
    print(f"  all_passed: {spec_report.get('all_passed')}")
    rc = spec_report.get("requirement_coverage") or {}
    print(f"  Requirement Coverage: passed={rc.get('passed')}, "
          f"orphans={len(rc.get('orphaned', []))}")
    ac = spec_report.get("ac_testability") or {}
    print(f"  AC Testability:       status={ac.get('status')}, "
          f"passed={ac.get('passed')}, failing={len(ac.get('failing', []))}")
    if ac.get("failing"):
        for f in ac["failing"][:3]:
            r = f.get("reason", "")[:90]
            print(f"    - {f.get('ac_id')}: score={f.get('score')}: {r}")
    intern = spec_report.get("chapter_intern_semantic") or {}
    per_ch = intern.get("per_chapter", [])
    passed_ch = sum(1 for c in per_ch if c.get("passed"))
    print(f"  Chapter Intern Test:  {passed_ch}/{len(per_ch)} chapters pass")
    for c in per_ch:
        ch_id = c.get("chapter_id")
        issues = c.get("issues", [])
        status = "PASS" if c.get("passed") else "FAIL"
        if issues:
            print(f"    - ch{ch_id} {status}: {issues[0][:80]}")
        else:
            print(f"    - ch{ch_id} {status}")
    print()

    # Citation density
    print("Citation density:")
    total_words = 0
    total_req_cites = 0
    total_ac_cites = 0
    for ch in state["chapters"]:
        ch_path = Path(ch["content_path"]) if ch.get("content_path") else None
        if ch_path and ch_path.exists():
            text = ch_path.read_text(encoding="utf-8")
            cites = count_citations(text)
            wc = len(text.split())
            total_words += wc
            total_req_cites += cites["req_total"]
            total_ac_cites += cites["ac_total"]
            print(f"  ch{ch['index']}: {wc:>5} words, "
                  f"{cites['req_total']:>2} REQ refs ({cites['req_unique']} unique), "
                  f"{cites['ac_total']:>2} AC refs")
    print(f"  TOTAL: {total_words} words, {total_req_cites} REQ refs, {total_ac_cites} AC refs")
    print()

    # Final summary
    print("=" * 72)
    print("FINAL NUMBERS")
    print("=" * 72)
    print(f"  Idea input size:           {word_count} words ({len(idea_text):,} chars)")
    print(f"  Chapters built:            {len(state['chapters'])}")
    print(f"  Document total words:      {total_words}")
    print(f"  REQ citations (total):     {total_req_cites}")
    print(f"  AC citations (total):      {total_ac_cites}")
    print()
    print(f"  Wall-clock:")
    print(f"    intake (profile gen):    {intake_elapsed:5.1f}s")
    print(f"    bootstrap + lock:        {bootstrap_elapsed:5.1f}s")
    print(f"    chapter build + gates:   {build_elapsed:5.1f}s")
    total_wall = intake_elapsed + bootstrap_elapsed + build_elapsed
    print(f"    TOTAL:                   {total_wall:5.1f}s ({total_wall/60:.2f} min)")
    print()
    # Use the model actually configured for this run (env var or default).
    active_model = _settings.LLM_MODEL
    in_rate, out_rate = _model_pricing(active_model)

    print(f"  Tokens (chapter generation only, judges not metered here):")
    print(f"    chapter_total_tokens:    {chapter_total_tokens:,} (sum of per-chapter `tokens_used`)")
    if chapter_input or chapter_output:
        print(f"    input:                   {chapter_input:,}")
        print(f"    output:                  {chapter_output:,}")
        cost = (chapter_input / 1_000_000 * in_rate) + (chapter_output / 1_000_000 * out_rate)
        print(f"  Estimated cost:            ${cost:.4f} ({active_model} @ ${in_rate:.2f}/${out_rate:.2f} per 1M, chapter calls only)")
    else:
        # Approximate cost from chapter_total_tokens assuming a 30/70 in:out
        # split typical for our prompt sizes.
        approx_in = int(chapter_total_tokens * 0.30)
        approx_out = chapter_total_tokens - approx_in
        approx_cost = (approx_in / 1_000_000 * in_rate) + (approx_out / 1_000_000 * out_rate)
        print(f"    (approx in/out 30/70: {approx_in:,} / {approx_out:,})")
        print(f"  Estimated cost:            ~${approx_cost:.4f} ({active_model} @ ${in_rate:.2f}/${out_rate:.2f} per 1M, chapter calls only)")
    print()
    print(f"  Output dir: {OUTPUT_DIR / SLUG}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    d = sys.argv[2] if len(sys.argv) > 2 else "professional"
    mw_arg = int(sys.argv[3]) if len(sys.argv) > 3 else 2500
    raise SystemExit(main(n, d, mw_arg))
