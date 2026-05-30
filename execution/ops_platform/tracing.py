"""Lightweight distributed tracing.

Scope honesty
-------------
- Always-on: spans persist to ``output/ops_platform/tracing/{date}.jsonl``.
  This works without OpenTelemetry; reads survive restart.
- Optional: when ``opentelemetry-sdk`` is installed AND ``configure_otel()``
  is called, spans are also exported via the supplied tracer provider.
  Operators wire OTLP / Jaeger / Zipkin exporters in their bootstrap code —
  this module never instantiates the exporter for you.

Span model
----------
A Span is one operation with:
  trace_id, span_id, parent_span_id, name, started_at, finished_at,
  attributes (free-form dict), status ("ok" | "error"), error_message.

Use the context manager:

    with tracing.span("workflow.run", attributes={"capability_id": "x"}) as s:
        ...
        s.set_attribute("rows_processed", 42)

Parent/child relationships flow through ``contextvars`` so nested spans
inside a single async task pick up the right parent without manual wiring.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR

logger = logging.getLogger(__name__)

_TRACING_DIR = OUTPUT_DIR / "ops_platform" / "tracing"
_CURRENT_SPAN: contextvars.ContextVar = contextvars.ContextVar("ops_current_span", default=None)
_LOCK = threading.Lock()


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    started_at: str
    finished_at: str | None = None
    duration_ms: float | None = None
    attributes: dict = field(default_factory=dict)
    status: str = "ok"
    error_message: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def set_attribute(self, key: str, value) -> None:
        self.attributes[key] = value


# ── Optional OpenTelemetry ─────────────────────────────────────────────


_OTEL_TRACER = None


def configure_otel(tracer_provider) -> None:
    """Wire an OpenTelemetry tracer. Operators must install
    ``opentelemetry-sdk`` and configure their exporter; this module just
    stores the provider.
    """
    global _OTEL_TRACER
    try:
        from opentelemetry import trace as otel_trace
        otel_trace.set_tracer_provider(tracer_provider)
        _OTEL_TRACER = otel_trace.get_tracer("ops_platform")
    except Exception:
        logger.warning("OpenTelemetry not available; tracing.configure_otel ignored")


def is_otel_active() -> bool:
    return _OTEL_TRACER is not None


# ── Public API ─────────────────────────────────────────────────────────


@contextmanager
def span(name: str, *, attributes: dict | None = None,
          trace_id: str | None = None):
    parent = _CURRENT_SPAN.get()
    parent_id = parent.span_id if parent else None
    span_obj = Span(
        trace_id=trace_id or (parent.trace_id if parent else uuid.uuid4().hex),
        span_id=uuid.uuid4().hex,
        parent_span_id=parent_id,
        name=name,
        started_at=datetime.now(timezone.utc).isoformat(),
        attributes=dict(attributes or {}),
    )
    token = _CURRENT_SPAN.set(span_obj)
    start_mono = time.monotonic()
    otel_span = None
    if _OTEL_TRACER is not None:
        try:
            otel_span = _OTEL_TRACER.start_span(name, attributes=span_obj.attributes)
        except Exception:
            otel_span = None
    try:
        yield span_obj
    except Exception as e:
        span_obj.status = "error"
        span_obj.error_message = str(e)[:300]
        raise
    finally:
        span_obj.finished_at = datetime.now(timezone.utc).isoformat()
        span_obj.duration_ms = round((time.monotonic() - start_mono) * 1000, 2)
        _persist(span_obj)
        _CURRENT_SPAN.reset(token)
        if otel_span is not None:
            try:
                otel_span.end()
            except Exception:
                pass


def current_trace_id() -> str | None:
    s = _CURRENT_SPAN.get()
    return s.trace_id if s else None


def list_recent(*, days: int = 1, limit: int = 200) -> list[dict]:
    if not _TRACING_DIR.exists():
        return []
    out: list[dict] = []
    files = sorted(_TRACING_DIR.glob("*.jsonl"), reverse=True)
    for p in files[:days + 1]:
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    if len(out) >= limit:
                        return out
        except OSError:
            continue
    return out


def trace_tree(trace_id: str) -> list[dict]:
    """Return all spans belonging to a trace, ordered by started_at."""
    rows = [r for r in list_recent(days=2, limit=10000) if r.get("trace_id") == trace_id]
    rows.sort(key=lambda r: r.get("started_at", ""))
    return rows


# ── Internal ───────────────────────────────────────────────────────────


def _persist(span_obj: Span) -> None:
    _TRACING_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    path = _TRACING_DIR / f"{day}.jsonl"
    try:
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(span_obj.to_dict(), ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("tracing append failed for %s", path, exc_info=True)
