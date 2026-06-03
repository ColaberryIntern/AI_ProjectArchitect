"""HTTP middlewares for the AI_ProjectArchitect platform."""

from .auth_gate import auth_gate_middleware

__all__ = ["auth_gate_middleware"]
