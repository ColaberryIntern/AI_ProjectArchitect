"""Blueprint loader for project architecture templates.

Loads blueprint JSON files from config/blueprints/ and provides
a clean API for accessing blueprint configuration throughout the system.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BLUEPRINTS_DIR = Path(__file__).parent

# Valid blueprint IDs — must match JSON filenames in config/blueprints/
VALID_BLUEPRINT_IDS = ("standard", "autonomous")
DEFAULT_BLUEPRINT_ID = "standard"

# In-memory cache (loaded once)
_cache: dict[str, dict] = {}


def _load_blueprint_file(blueprint_id: str) -> dict:
    """Load a single blueprint JSON file by ID.

    Args:
        blueprint_id: The blueprint identifier (matches filename without .json).

    Returns:
        The parsed blueprint dictionary.

    Raises:
        FileNotFoundError: If the blueprint file does not exist.
        json.JSONDecodeError: If the file contains invalid JSON.
    """
    path = BLUEPRINTS_DIR / f"{blueprint_id}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_blueprint(blueprint_id: str) -> dict:
    """Get a blueprint by ID, with caching.

    Args:
        blueprint_id: One of VALID_BLUEPRINT_IDS.

    Returns:
        The blueprint configuration dictionary.

    Raises:
        ValueError: If the blueprint_id is not valid.
    """
    if blueprint_id not in VALID_BLUEPRINT_IDS:
        raise ValueError(
            f"Invalid blueprint: {blueprint_id}. "
            f"Must be one of {VALID_BLUEPRINT_IDS}"
        )

    if blueprint_id not in _cache:
        _cache[blueprint_id] = _load_blueprint_file(blueprint_id)

    return _cache[blueprint_id]


def resolve_blueprint(blueprint_id: str | None) -> str:
    """Resolve a blueprint ID, falling back to default.

    Args:
        blueprint_id: Blueprint ID or None.

    Returns:
        A valid blueprint ID string.
    """
    if blueprint_id and blueprint_id in VALID_BLUEPRINT_IDS:
        return blueprint_id
    return DEFAULT_BLUEPRINT_ID


def get_forced_depth_mode(blueprint_id: str) -> str | None:
    """Return the forced depth mode for a blueprint, or None if user chooses.

    Args:
        blueprint_id: A valid blueprint ID.

    Returns:
        Depth mode string (e.g. 'enterprise') or None.
    """
    bp = get_blueprint(blueprint_id)
    return bp.get("forced_depth_mode")


def get_feature_seeds(blueprint_id: str) -> list[dict]:
    """Return the feature seeds for a blueprint.

    Args:
        blueprint_id: A valid blueprint ID.

    Returns:
        List of feature seed dicts with id, name, description, category.
    """
    bp = get_blueprint(blueprint_id)
    return bp.get("feature_seeds", [])


def get_outline_context(blueprint_id: str) -> str:
    """Return the outline generation context for a blueprint.

    Args:
        blueprint_id: A valid blueprint ID.

    Returns:
        Context string to inject into outline LLM prompt (empty for standard).
    """
    bp = get_blueprint(blueprint_id)
    return bp.get("outline_context", "")


def get_chapter_context(blueprint_id: str) -> str:
    """Return the chapter generation context for a blueprint.

    Args:
        blueprint_id: A valid blueprint ID.

    Returns:
        Context string to inject into chapter LLM prompt (empty for standard).
    """
    bp = get_blueprint(blueprint_id)
    return bp.get("chapter_context", "")


def get_architecture_context(blueprint_id: str) -> str:
    """Return the architecture context summary for a blueprint.

    Args:
        blueprint_id: A valid blueprint ID.

    Returns:
        Architecture summary string (empty for standard).
    """
    bp = get_blueprint(blueprint_id)
    return bp.get("architecture_context", "")


def get_all_blueprints() -> list[dict]:
    """Return all available blueprints as a list for UI display.

    Returns:
        List of dicts with id, label, description, forced_depth_mode.
    """
    result = []
    for bp_id in VALID_BLUEPRINT_IDS:
        bp = get_blueprint(bp_id)
        result.append({
            "id": bp["id"],
            "label": bp["label"],
            "description": bp["description"],
            "forced_depth_mode": bp.get("forced_depth_mode"),
        })
    return result
