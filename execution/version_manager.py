"""Version tracking for outlines and documents.

Manages version history, active version lookup, and version comparison.
"""

from datetime import datetime, timezone


def create_version(state: dict, change_summary: str) -> dict:
    """Increment version and record in history.

    Args:
        state: The project state dictionary.
        change_summary: Brief description of what changed.

    Returns:
        The updated state dictionary.
    """
    current_version = state["outline"]["version"]
    new_version = current_version + 1

    state["outline"]["version"] = new_version
    state["version_history"].append(
        {
            "version": new_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "change_summary": change_summary,
        }
    )

    # Update document version string
    state["document"]["version"] = f"v{new_version}"

    return state


def get_active_version(state: dict) -> int:
    """Return the currently active outline version number.

    Args:
        state: The project state dictionary.

    Returns:
        The active version number.
    """
    return state["outline"]["version"]


def get_version_history(state: dict) -> list[dict]:
    """Return the complete version history.

    Args:
        state: The project state dictionary.

    Returns:
        List of version history entries.
    """
    return state["version_history"]


def get_document_version_string(state: dict) -> str:
    """Return the document version string (e.g., 'v1').

    Args:
        state: The project state dictionary.

    Returns:
        The version string.
    """
    return state["document"]["version"]


def compare_versions(state: dict, v1: int, v2: int) -> dict:
    """Compare two versions from the history.

    Args:
        state: The project state dictionary.
        v1: First version number.
        v2: Second version number.

    Returns:
        Dict with version details and comparison.

    Raises:
        ValueError: If either version is not found.
    """
    history = state["version_history"]

    entry_v1 = None
    entry_v2 = None
    for entry in history:
        if entry["version"] == v1:
            entry_v1 = entry
        if entry["version"] == v2:
            entry_v2 = entry

    if entry_v1 is None:
        raise ValueError(f"Version {v1} not found in history")
    if entry_v2 is None:
        raise ValueError(f"Version {v2} not found in history")

    return {
        "v1": entry_v1,
        "v2": entry_v2,
        "versions_between": abs(v2 - v1) - 1,
    }
