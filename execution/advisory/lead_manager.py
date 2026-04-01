"""Lead management for the AI Advisory platform.

Provides a structured Lead model stored in a centralized leads database
(JSON file), with upsert-by-email semantics, campaign enrollment,
and advisory session linking.
"""

import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from config.settings import ADVISORY_OUTPUT_DIR


_LEADS_DB_PATH = ADVISORY_OUTPUT_DIR / "_leads_db.json"


def _safe_replace(src: str, dst: str, retries: int = 3) -> None:
    """Replace dst with src, retrying on PermissionError (Windows/OneDrive)."""
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(0.2 * (attempt + 1))
            else:
                shutil.copy2(src, dst)
                os.remove(src)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_leads_db() -> list[dict]:
    """Load the full leads database."""
    if not _LEADS_DB_PATH.exists():
        return []
    with open(_LEADS_DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_leads_db(leads: list[dict]) -> None:
    """Atomically save the leads database."""
    ADVISORY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(ADVISORY_OUTPUT_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2, ensure_ascii=False)
        _safe_replace(tmp_path, str(_LEADS_DB_PATH))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def create_lead(
    email: str,
    name: str = "",
    company: str = "",
    role: str = "",
    industry: str = "",
    company_size: str = "",
    source: str = "advisory",
) -> dict:
    """Create a new lead record.

    Returns the created lead dict.
    """
    lead = {
        "lead_id": str(uuid4()),
        "email": email,
        "name": name,
        "company": company,
        "role": role,
        "industry": industry,
        "company_size": company_size,
        "source": source,
        "created_at": _now(),
        "updated_at": _now(),
        "advisory_session_ids": [],
        "campaign_enrollments": [],
        "events": [],
        "metadata": {},
        "pdf_paths": [],
        "lead_score": None,
        "recommended_offer": None,
        "sales_intelligence": None,
    }
    leads = _load_leads_db()
    leads.append(lead)
    _save_leads_db(leads)
    return lead


def upsert_lead(
    email: str,
    name: str = "",
    company: str = "",
    role: str = "",
    industry: str = "",
    company_size: str = "",
    source: str = "advisory",
) -> dict:
    """Create or update a lead by email.

    If a lead with this email exists, update non-empty fields.
    Otherwise, create a new lead.

    Returns the lead dict.
    """
    leads = _load_leads_db()
    existing = _find_by_email(leads, email)

    if existing:
        if name:
            existing["name"] = name
        if company:
            existing["company"] = company
        if role:
            existing["role"] = role
        if industry:
            existing["industry"] = industry
        if company_size:
            existing["company_size"] = company_size
        existing["updated_at"] = _now()
        _save_leads_db(leads)
        return existing

    lead = create_lead(email, name, company, role, industry, company_size, source)
    return lead


def get_lead_by_email(email: str) -> dict | None:
    """Find a lead by email address."""
    leads = _load_leads_db()
    return _find_by_email(leads, email)


def get_lead_by_id(lead_id: str) -> dict | None:
    """Find a lead by ID."""
    for lead in _load_leads_db():
        if lead["lead_id"] == lead_id:
            return lead
    return None


def link_advisory_session(email: str, session_id: str) -> dict | None:
    """Link an advisory session to a lead."""
    leads = _load_leads_db()
    lead = _find_by_email(leads, email)
    if not lead:
        return None
    if session_id not in lead["advisory_session_ids"]:
        lead["advisory_session_ids"].append(session_id)
        lead["updated_at"] = _now()
        _save_leads_db(leads)
    return lead


def add_lead_metadata(email: str, metadata: dict) -> dict | None:
    """Merge metadata into a lead's metadata dict."""
    leads = _load_leads_db()
    lead = _find_by_email(leads, email)
    if not lead:
        return None
    lead["metadata"].update(metadata)
    lead["updated_at"] = _now()
    _save_leads_db(leads)
    return lead


def attach_pdf(email: str, pdf_path: str) -> dict | None:
    """Attach a PDF file path to a lead."""
    leads = _load_leads_db()
    lead = _find_by_email(leads, email)
    if not lead:
        return None
    if pdf_path not in lead["pdf_paths"]:
        lead["pdf_paths"].append(pdf_path)
        lead["updated_at"] = _now()
        _save_leads_db(leads)
    return lead


def update_revenue_intelligence(email: str, score: dict, offer: dict, sales_ctx: dict) -> dict | None:
    """Update a lead's revenue intelligence fields.

    Args:
        email: Lead email.
        score: Output from lead_scoring_engine.score_lead().
        offer: Output from offer_router.classify_lead().
        sales_ctx: Output from sales_intelligence.generate_sales_context().

    Returns:
        Updated lead dict, or None if not found.
    """
    leads = _load_leads_db()
    lead = _find_by_email(leads, email)
    if not lead:
        return None
    lead["lead_score"] = score
    lead["recommended_offer"] = offer
    lead["sales_intelligence"] = sales_ctx
    lead["updated_at"] = _now()
    _save_leads_db(leads)
    return lead


def list_all_leads() -> list[dict]:
    """Return all leads."""
    return _load_leads_db()


def _find_by_email(leads: list[dict], email: str) -> dict | None:
    """Find a lead by email in the given list."""
    email_lower = email.lower().strip()
    for lead in leads:
        if lead["email"].lower().strip() == email_lower:
            return lead
    return None
