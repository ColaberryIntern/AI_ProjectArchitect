"""Campaign management for the AI Advisory platform.

Provides campaign creation, lead enrollment, and stage tracking.
Campaigns and enrollments are stored in a centralized JSON file.
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


_CAMPAIGNS_DB_PATH = ADVISORY_OUTPUT_DIR / "_campaigns_db.json"


def _safe_replace(src: str, dst: str, retries: int = 3) -> None:
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

# Default campaign stages for the advisory funnel
ADVISORY_CAMPAIGN_STAGES = [
    "Started Advisory",
    "Completed Questions",
    "Generated Results",
    "Captured Lead",
    "Downloaded Report",
    "Booked Strategy Call",
    "Strategy Call Completed",
]

# Seed campaign definition
ADVISORY_CAMPAIGN_SEED = {
    "campaign_name": "AI Workforce Designer",
    "campaign_type": "inbound_advisory",
    "source": "advisory_system",
    "stages": ADVISORY_CAMPAIGN_STAGES,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_campaigns_db() -> dict:
    """Load the campaigns database.

    Returns dict with "campaigns" and "enrollments" keys.
    """
    if not _CAMPAIGNS_DB_PATH.exists():
        return {"campaigns": [], "enrollments": []}
    with open(_CAMPAIGNS_DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_campaigns_db(db: dict) -> None:
    """Atomically save the campaigns database."""
    ADVISORY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(ADVISORY_OUTPUT_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        _safe_replace(tmp_path, str(_CAMPAIGNS_DB_PATH))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def ensure_advisory_campaign() -> dict:
    """Ensure the default advisory campaign exists. Create if missing.

    Returns the campaign dict.
    """
    db = _load_campaigns_db()
    for c in db["campaigns"]:
        if c["campaign_type"] == "inbound_advisory":
            return c

    campaign = {
        "campaign_id": str(uuid4()),
        **ADVISORY_CAMPAIGN_SEED,
        "created_at": _now(),
    }
    db["campaigns"].append(campaign)
    _save_campaigns_db(db)
    return campaign


def get_advisory_campaign() -> dict | None:
    """Get the advisory campaign."""
    db = _load_campaigns_db()
    for c in db["campaigns"]:
        if c["campaign_type"] == "inbound_advisory":
            return c
    return None


def enroll_lead(email: str, campaign_id: str, initial_stage: str = "Started Advisory") -> dict:
    """Enroll a lead into a campaign or return existing enrollment.

    If already enrolled, returns the existing enrollment unchanged.
    """
    db = _load_campaigns_db()

    for enrollment in db["enrollments"]:
        if enrollment["email"] == email and enrollment["campaign_id"] == campaign_id:
            return enrollment

    enrollment = {
        "enrollment_id": str(uuid4()),
        "email": email,
        "campaign_id": campaign_id,
        "current_stage": initial_stage,
        "stage_history": [
            {"stage": initial_stage, "entered_at": _now()},
        ],
        "enrolled_at": _now(),
        "updated_at": _now(),
    }
    db["enrollments"].append(enrollment)
    _save_campaigns_db(db)
    return enrollment


def advance_stage(email: str, campaign_id: str, new_stage: str) -> dict | None:
    """Advance a lead to a new campaign stage.

    Appends to stage_history. Returns updated enrollment or None if not found.
    """
    db = _load_campaigns_db()

    for enrollment in db["enrollments"]:
        if enrollment["email"] == email and enrollment["campaign_id"] == campaign_id:
            if enrollment["current_stage"] == new_stage:
                return enrollment
            enrollment["current_stage"] = new_stage
            enrollment["stage_history"].append({
                "stage": new_stage,
                "entered_at": _now(),
            })
            enrollment["updated_at"] = _now()
            _save_campaigns_db(db)
            return enrollment

    return None


def get_enrollment(email: str, campaign_id: str) -> dict | None:
    """Get a specific enrollment."""
    db = _load_campaigns_db()
    for enrollment in db["enrollments"]:
        if enrollment["email"] == email and enrollment["campaign_id"] == campaign_id:
            return enrollment
    return None


def get_enrollments_by_email(email: str) -> list[dict]:
    """Get all enrollments for a lead."""
    db = _load_campaigns_db()
    return [e for e in db["enrollments"] if e["email"] == email]


def get_enrollments_by_campaign(campaign_id: str) -> list[dict]:
    """Get all enrollments in a campaign."""
    db = _load_campaigns_db()
    return [e for e in db["enrollments"] if e["campaign_id"] == campaign_id]


def get_campaign_stats(campaign_id: str) -> dict:
    """Get aggregate statistics for a campaign."""
    enrollments = get_enrollments_by_campaign(campaign_id)
    stage_counts = {}
    for e in enrollments:
        stage = e["current_stage"]
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    return {
        "total_enrolled": len(enrollments),
        "stage_counts": stage_counts,
    }
