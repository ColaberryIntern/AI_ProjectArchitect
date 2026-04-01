"""Simple event tracking for the AI Advisory platform.

Tracks user actions (CTA clicks, page views, conversions) in an
append-only JSON log file. Preserves UTM params for attribution.
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


_EVENTS_LOG_PATH = ADVISORY_OUTPUT_DIR / "_events_log.json"


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def track_event(
    event_name: str,
    session_id: str = "",
    email: str = "",
    properties: dict | None = None,
    utm_params: dict | None = None,
) -> dict:
    """Record a tracking event.

    Args:
        event_name: Name of the event (e.g., "advisory_start_clicked").
        session_id: Advisory session ID if applicable.
        email: Lead email if known.
        properties: Additional event properties.
        utm_params: UTM attribution params (source, medium, campaign, content, term).

    Returns:
        The recorded event dict.
    """
    event = {
        "event_id": str(uuid4()),
        "event_name": event_name,
        "session_id": session_id,
        "email": email,
        "properties": properties or {},
        "utm_params": utm_params or {},
        "timestamp": _now(),
    }

    _append_event(event)
    return event


def get_events(
    session_id: str = "",
    email: str = "",
    event_name: str = "",
    limit: int = 100,
) -> list[dict]:
    """Query events with optional filters.

    Returns most recent events first.
    """
    events = _load_events()

    if session_id:
        events = [e for e in events if e.get("session_id") == session_id]
    if email:
        events = [e for e in events if e.get("email") == email]
    if event_name:
        events = [e for e in events if e.get("event_name") == event_name]

    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return events[:limit]


def get_funnel_stats() -> dict:
    """Get aggregate funnel statistics.

    Returns counts for each event type.
    """
    events = _load_events()
    counts = {}
    for e in events:
        name = e.get("event_name", "unknown")
        counts[name] = counts.get(name, 0) + 1
    return counts


def _load_events() -> list[dict]:
    """Load the events log."""
    if not _EVENTS_LOG_PATH.exists():
        return []
    with open(_EVENTS_LOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _append_event(event: dict) -> None:
    """Append an event to the log."""
    ADVISORY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    events = _load_events()
    events.append(event)

    fd, tmp_path = tempfile.mkstemp(dir=str(ADVISORY_OUTPUT_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2, ensure_ascii=False)
        _safe_replace(tmp_path, str(_EVENTS_LOG_PATH))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
