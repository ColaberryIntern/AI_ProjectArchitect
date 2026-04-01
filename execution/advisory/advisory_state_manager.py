"""State engine for AI Advisory sessions.

Manages advisory session JSON files: create, load, save, update answers,
set generated outputs, and capture leads.

Mirrors the patterns of execution/state_manager.py — functions only,
atomic writes via temp file + rename, JSON file persistence.
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


VALID_STATUSES = ["idea_input", "questioning", "generating", "complete", "gated"]


def _now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _session_dir(session_id: str) -> Path:
    """Return the directory for a session."""
    return ADVISORY_OUTPUT_DIR / session_id


def _state_path(session_id: str) -> Path:
    """Return the path to a session's state file."""
    return _session_dir(session_id) / "advisory_state.json"


def _leads_index_path() -> Path:
    """Return the path to the global leads index file."""
    return ADVISORY_OUTPUT_DIR / "_leads_index.json"


def initialize_session(business_idea: str) -> dict:
    """Create a new advisory session and persist it.

    Args:
        business_idea: The raw business idea text from the user.

    Returns:
        The initialized session dict.
    """
    session_id = str(uuid4())
    session = {
        "session_id": session_id,
        "status": "idea_input",
        "created_at": _now(),
        "updated_at": _now(),
        "business_idea": business_idea,
        "current_question_index": 0,
        "answers": [],
        "email": None,
        "pending_follow_up": None,
        "skipped_questions": [],
        "selected_outcomes": [],
        "selected_ai_systems": [],
        "selected_systems": [],
        "selected_capabilities": [],
        "capability_recommendations": None,
        "agents": None,
        "capability_map": None,
        "org_structure": None,
        "impact_model": None,
        "maturity_score": None,
        "lead": None,
        "pdf_path": None,
        "linked_project_slug": None,
    }
    save_session(session)
    return session


def load_session(session_id: str) -> dict:
    """Load a session from disk.

    Args:
        session_id: UUID string identifying the session.

    Returns:
        The session dict.

    Raises:
        FileNotFoundError: If the session does not exist.
    """
    path = _state_path(session_id)
    if not path.exists():
        raise FileNotFoundError(f"Advisory session not found: {session_id}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_session(session: dict) -> None:
    """Atomically write a session to disk.

    Uses temp file + rename to prevent corruption on crash.
    """
    session_id = session["session_id"]
    session["updated_at"] = _now()
    dir_path = _session_dir(session_id)
    dir_path.mkdir(parents=True, exist_ok=True)

    target = _state_path(session_id)
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)
        _safe_replace(tmp_path, str(target))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


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
                # Last resort: copy + remove
                shutil.copy2(src, dst)
                os.remove(src)


def session_exists(session_id: str) -> bool:
    """Check if a session exists on disk."""
    return _state_path(session_id).exists()


def record_answer(session: dict, question_id: str, question_text: str, answer_text: str) -> dict:
    """Record an answer to a question and advance the index.

    Args:
        session: The session dict (mutated in place).
        question_id: ID of the question being answered.
        question_text: Text of the question.
        answer_text: The user's answer.

    Returns:
        The updated session dict.
    """
    answer = {
        "question_id": question_id,
        "question_text": question_text,
        "answer_text": answer_text,
        "answered_at": _now(),
    }
    session["answers"].append(answer)
    session["current_question_index"] = len(session["answers"])
    save_session(session)
    return session


def set_selected_systems(session: dict, systems: list[str]) -> dict:
    """Set the selected integration systems."""
    session["selected_systems"] = systems
    save_session(session)
    return session


def set_selected_outcomes(session: dict, outcome_ids: list[str]) -> dict:
    """Set the selected business outcomes."""
    session["selected_outcomes"] = outcome_ids
    save_session(session)
    return session


def set_selected_ai_systems(session: dict, system_ids: list[str]) -> dict:
    """Set the selected AI systems."""
    session["selected_ai_systems"] = system_ids
    save_session(session)
    return session


def set_selected_capabilities(session: dict, capability_ids: list[str]) -> dict:
    """Set the selected business capabilities."""
    session["selected_capabilities"] = capability_ids
    save_session(session)
    return session


def set_capability_recommendations(session: dict, recommendations: dict) -> dict:
    """Set the capability recommendations from the mapper."""
    session["capability_recommendations"] = recommendations
    save_session(session)
    return session


def set_agents(session: dict, agents: list[dict]) -> dict:
    """Set the generated agent architecture."""
    session["agents"] = agents
    save_session(session)
    return session


def set_capability_map(session: dict, capability_map: dict) -> dict:
    """Set the business capability map."""
    session["capability_map"] = capability_map
    save_session(session)
    return session


def set_org_structure(session: dict, org_nodes: list[dict]) -> dict:
    """Set the AI organization structure."""
    session["org_structure"] = org_nodes
    save_session(session)
    return session


def set_impact_model(session: dict, impact: dict) -> dict:
    """Set the financial impact model."""
    session["impact_model"] = impact
    save_session(session)
    return session


def set_maturity_score(session: dict, score: dict) -> dict:
    """Set the maturity score."""
    session["maturity_score"] = score
    save_session(session)
    return session


def record_lead(session: dict, name: str, email: str, company: str, role: str) -> dict:
    """Capture lead information on the session.

    Also appends to the global leads index.
    """
    lead = {
        "name": name,
        "email": email,
        "company": company,
        "role": role,
        "captured_at": _now(),
    }
    session["lead"] = lead
    save_session(session)
    append_lead_to_index(session["session_id"], lead)
    return session


def set_pdf_path(session: dict, path: str) -> dict:
    """Record the path to the generated PDF."""
    session["pdf_path"] = path
    save_session(session)
    return session


def set_linked_project(session: dict, project_slug: str) -> dict:
    """Link this advisory session to a Project Architect project."""
    session["linked_project_slug"] = project_slug
    save_session(session)
    return session


def advance_status(session: dict, new_status: str) -> dict:
    """Advance the session to a new status.

    Args:
        session: The session dict.
        new_status: Must be one of VALID_STATUSES.

    Returns:
        The updated session dict.

    Raises:
        ValueError: If the status is invalid.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}. Must be one of {VALID_STATUSES}")
    session["status"] = new_status
    save_session(session)
    return session


def append_lead_to_index(session_id: str, lead: dict) -> None:
    """Append a lead entry to the global leads index file."""
    ADVISORY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    index_path = _leads_index_path()

    leads = []
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            leads = json.load(f)

    entry = {**lead, "session_id": session_id}
    leads.append(entry)

    fd, tmp_path = tempfile.mkstemp(dir=str(ADVISORY_OUTPUT_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2, ensure_ascii=False)
        _safe_replace(tmp_path, str(index_path))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_leads_index() -> list[dict]:
    """Load the global leads index."""
    index_path = _leads_index_path()
    if not index_path.exists():
        return []
    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_sessions() -> list[str]:
    """List all advisory session IDs."""
    if not ADVISORY_OUTPUT_DIR.exists():
        return []
    return [
        d.name
        for d in ADVISORY_OUTPUT_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    ]
