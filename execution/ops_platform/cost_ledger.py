"""Cost ledger — per-LLM-call token + USD cost, append-only.

Recorded at the few LLM call sites (execution/llm_client.chat + the 3 ops
direct-client sites: llm_suggest, plan_inference, autopickup) so every call is
captured EXACTLY once (workflow_runner only stores the usage chat() already
recorded — no double count). Forward-only: no backfill, the ledger fills as
calls happen. USD from config/model_prices.json.

`record()` NEVER raises — cost accounting must not break an LLM call.

Storage: output/ops_platform/cost_ledger/{YYYY-MM-DD}.jsonl (append-only).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from config.settings import OUTPUT_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

_LEDGER_DIR = OUTPUT_DIR / "ops_platform" / "cost_ledger"
_PRICES_PATH = PROJECT_ROOT / "config" / "model_prices.json"
_PRICES_CACHE: dict | None = None

# Shown in the UI so a small/empty ledger reads as "new", not "broken".
INSTRUMENTED_SINCE = "2026-06-22"


def _prices() -> dict:
    global _PRICES_CACHE
    if _PRICES_CACHE is None:
        try:
            _PRICES_CACHE = json.loads(_PRICES_PATH.read_text(encoding="utf-8")).get("prices", {})
        except (OSError, json.JSONDecodeError):
            _PRICES_CACHE = {}
    return _PRICES_CACHE


def _price_for(model: str) -> dict:
    """Match the longest price key the model name starts with (OpenAI returns
    dated names like 'gpt-4o-mini-2024-07-18'). Falls back to _default."""
    prices = _prices()
    if model:
        for key in sorted((k for k in prices if k != "_default"), key=len, reverse=True):
            if model.startswith(key):
                return prices[key]
    return prices.get("_default", {"input": 0.0, "output": 0.0})


def compute_usd(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    p = _price_for(model or "")
    usd = (prompt_tokens / 1_000_000) * p.get("input", 0.0) + \
          (completion_tokens / 1_000_000) * p.get("output", 0.0)
    return round(usd, 6)


def record(*, model: str, prompt_tokens: int, completion_tokens: int, source: str,
           capability_id: str | None = None, correlation_id: str | None = None) -> None:
    try:
        pt = int(prompt_tokens or 0)
        ct = int(completion_tokens or 0)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model, "source": source,
            "prompt_tokens": pt, "completion_tokens": ct,
            "usd": compute_usd(pt, ct, model),
            "capability_id": capability_id, "correlation_id": correlation_id,
        }
        _LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).date().isoformat()
        with open(_LEDGER_DIR / f"{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # pragma: no cover - never break the caller
        logger.warning("cost_ledger.record failed", exc_info=True)


def _read(days: int) -> list[dict]:
    out: list[dict] = []
    if not _LEDGER_DIR.exists():
        return out
    today = datetime.now(timezone.utc).date()
    for delta in range(days):
        path = _LEDGER_DIR / f"{(today - timedelta(days=delta)).isoformat()}.jsonl"
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def summary(days: int = 7, *, recent: int = 25) -> dict:
    rows = _read(days)
    total_usd = round(sum(r.get("usd", 0) for r in rows), 4)
    total_tokens = sum(r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in rows)
    by_model: dict = {}
    by_source: dict = {}
    by_day: dict = {}
    for r in rows:
        by_model[r.get("model", "?")] = round(by_model.get(r.get("model", "?"), 0) + r.get("usd", 0), 4)
        by_source[r.get("source", "?")] = round(by_source.get(r.get("source", "?"), 0) + r.get("usd", 0), 4)
        day = (r.get("timestamp") or "")[:10]
        by_day[day] = round(by_day.get(day, 0) + r.get("usd", 0), 4)
    rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return {
        "days": days, "total_usd": total_usd, "total_tokens": total_tokens,
        "calls": len(rows), "by_model": by_model, "by_source": by_source,
        "by_day": dict(sorted(by_day.items())), "recent": rows[:recent],
        "instrumented_since": INSTRUMENTED_SINCE,
    }
