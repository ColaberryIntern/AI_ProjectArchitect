"""Structured error responses for the ops platform router.

All non-trivial route failures return a JSON body matching:

    {
        "error": {
            "code": "<short_machine_code>",
            "message": "<human-readable summary>",
            "details": <optional dict or list>,
            "correlation_id": "<uuid for log lookup>"
        }
    }

Use ``raise OpsError(...)`` from any route; the router converts it to a
FastAPI ``HTTPException`` with this body shape via ``handle_ops_error()``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass
class OpsError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: object | None = None
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not self.correlation_id:
            self.correlation_id = str(uuid.uuid4())

    def to_dict(self) -> dict:
        body: dict = {"error": {
            "code": self.code,
            "message": self.message,
            "correlation_id": self.correlation_id,
        }}
        if self.details is not None:
            body["error"]["details"] = self.details
        return body


def not_found(entity_type: str, entity_id: str) -> OpsError:
    return OpsError(
        code=f"{entity_type}.not_found",
        message=f"{entity_type} '{entity_id}' not found",
        status_code=404,
    )


def invalid_input(message: str, *, details=None) -> OpsError:
    return OpsError(code="input.invalid", message=message, status_code=400, details=details)


def conflict(message: str, *, details=None) -> OpsError:
    return OpsError(code="state.conflict", message=message, status_code=409, details=details)
