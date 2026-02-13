"""Schema validation for project data structures.

Validates state files, outlines, chapters, and features against
their JSON Schema definitions. All validation is deterministic.
"""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from config.settings import PROJECT_STATE_SCHEMA


def load_schema(schema_path: str | Path) -> dict:
    """Load a JSON Schema file.

    Args:
        schema_path: Path to the schema file.

    Returns:
        The schema dictionary.

    Raises:
        FileNotFoundError: If the schema file does not exist.
    """
    path = Path(schema_path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_against_schema(data: dict, schema_path: str | Path) -> bool:
    """Validate a data dictionary against a JSON Schema.

    Args:
        data: The data to validate.
        schema_path: Path to the JSON Schema file.

    Returns:
        True if validation passes.

    Raises:
        ValidationError: If validation fails (first error only).
    """
    schema = load_schema(schema_path)
    validator = Draft202012Validator(schema)
    validator.validate(data)
    return True


def get_validation_errors(data: dict, schema_path: str | Path) -> list[str]:
    """Return all validation errors for a data dictionary.

    Args:
        data: The data to validate.
        schema_path: Path to the JSON Schema file.

    Returns:
        List of human-readable error messages. Empty if valid.
    """
    schema = load_schema(schema_path)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    return [
        f"{'.'.join(str(p) for p in error.absolute_path) or 'root'}: {error.message}"
        for error in errors
    ]


def validate_project_state(state: dict) -> bool:
    """Validate a project state dictionary against the state schema.

    Args:
        state: The project state dictionary.

    Returns:
        True if valid.

    Raises:
        ValidationError: If validation fails.
    """
    return validate_against_schema(state, PROJECT_STATE_SCHEMA)


def get_state_validation_errors(state: dict) -> list[str]:
    """Return all validation errors for a project state.

    Args:
        state: The project state dictionary.

    Returns:
        List of human-readable error messages.
    """
    return get_validation_errors(state, PROJECT_STATE_SCHEMA)


def is_valid_project_state(state: dict) -> bool:
    """Check if a project state is valid without raising exceptions.

    Args:
        state: The project state dictionary.

    Returns:
        True if valid, False otherwise.
    """
    try:
        validate_project_state(state)
        return True
    except ValidationError:
        return False
