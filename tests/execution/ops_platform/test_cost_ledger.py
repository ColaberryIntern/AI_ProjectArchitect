"""Tests for the LLM cost ledger. Isolated to tmp_path."""

import pytest

from execution.ops_platform import cost_ledger


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(cost_ledger, "_LEDGER_DIR", tmp_path / "cost")
    yield


def test_compute_usd_known_model():
    # gpt-4o-mini = 0.15 input + 0.60 output per 1M tokens
    usd = cost_ledger.compute_usd(1_000_000, 1_000_000, "gpt-4o-mini")
    assert round(usd, 2) == 0.75


def test_price_prefix_match_picks_longest_key():
    # Dated model names must match the longest prefix key, not a shorter one.
    mini = cost_ledger.compute_usd(1_000_000, 0, "gpt-4o-mini-2024-07-18")
    full = cost_ledger.compute_usd(1_000_000, 0, "gpt-4o-2024-11-20")
    assert round(mini, 2) == 0.15   # gpt-4o-mini, not gpt-4o
    assert round(full, 2) == 2.50   # gpt-4o


def test_unknown_model_uses_default():
    assert cost_ledger.compute_usd(1_000_000, 0, "totally-unknown") > 0


def test_record_and_summary():
    cost_ledger.record(model="gpt-4o-mini", prompt_tokens=1000, completion_tokens=500, source="ops_suggest")
    cost_ledger.record(model="gpt-4o", prompt_tokens=2000, completion_tokens=1000, source="llm_client")
    s = cost_ledger.summary(7)
    assert s["calls"] == 2
    assert s["total_usd"] > 0
    assert s["total_tokens"] == 4500
    assert "gpt-4o-mini" in s["by_model"] and "ops_suggest" in s["by_source"]
    assert len(s["recent"]) == 2 and s["instrumented_since"]


def test_record_never_raises_on_bad_input():
    cost_ledger.record(model=None, prompt_tokens="x", completion_tokens=None, source="t")  # type: ignore
    # No exception == pass; and nothing valid was written.
    assert cost_ledger.summary(7)["calls"] == 0


def test_summary_empty_is_zero():
    s = cost_ledger.summary(7)
    assert s["calls"] == 0 and s["total_usd"] == 0
