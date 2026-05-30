"""Unit tests for execution/semantic_judge.py.

These tests exercise the wrapper logic — caching, JSON parsing, graceful
degradation when the LLM is not configured — by stubbing
``execution.llm_client``. The judge itself is non-deterministic, so we
do not test prompt content here.
"""

import json

import pytest

from execution import semantic_judge
from execution.llm_client import LLMClientError, LLMResponse


@pytest.fixture(autouse=True)
def reset_cache():
    semantic_judge.clear_cache()
    yield
    semantic_judge.clear_cache()


@pytest.fixture
def mock_chat_ok(monkeypatch):
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return LLMResponse(
            content=json.dumps({
                "results": [
                    {"id": "AC-1", "score": 3, "reason": "fully testable"},
                    {"id": "AC-2", "score": 1, "reason": "vague then-clause"},
                ]
            }),
            model="gpt-4o-mini",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            stop_reason="stop",
        )

    monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: True)
    monkeypatch.setattr(semantic_judge.llm_client, "chat", fake_chat)
    return calls


class TestScoreAcceptanceCriteria:
    def test_returns_judge_payload(self, mock_chat_ok):
        criteria = [
            {"id": "AC-1", "given": "g", "when": "w", "then": "t"},
            {"id": "AC-2", "given": "g2", "when": "w2", "then": "t2"},
        ]
        result = semantic_judge.score_acceptance_criteria(criteria)
        assert result.status == "ok"
        assert result.data["results"][0]["score"] == 3
        assert result.data["results"][1]["score"] == 1

    def test_empty_criteria_no_llm_call(self, mock_chat_ok):
        result = semantic_judge.score_acceptance_criteria([])
        assert result.status == "ok"
        assert result.data == {"results": []}
        assert mock_chat_ok == []  # no LLM call

    def test_caches_identical_input(self, mock_chat_ok):
        criteria = [{"id": "AC-1", "given": "g", "when": "w", "then": "t"}]
        semantic_judge.score_acceptance_criteria(criteria)
        semantic_judge.score_acceptance_criteria(criteria)
        assert len(mock_chat_ok) == 1  # second call hit cache

    def test_skipped_when_llm_unavailable(self, monkeypatch):
        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: False)
        criteria = [{"id": "AC-1", "given": "g", "when": "w", "then": "t"}]
        result = semantic_judge.score_acceptance_criteria(criteria)
        assert result.status == "skipped"
        assert "OPENAI_API_KEY" in (result.reason or "")

    def test_error_on_invalid_json(self, monkeypatch):
        def fake_chat(**kwargs):
            return LLMResponse(
                content="not json",
                model="gpt-4o-mini",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                stop_reason="stop",
            )

        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: True)
        monkeypatch.setattr(semantic_judge.llm_client, "chat", fake_chat)
        criteria = [{"id": "AC-1", "given": "g", "when": "w", "then": "t"}]
        result = semantic_judge.score_acceptance_criteria(criteria)
        assert result.status == "error"
        assert "JSON" in (result.reason or "")

    def test_error_on_llm_client_error(self, monkeypatch):
        def fake_chat(**kwargs):
            raise LLMClientError("rate limited")

        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: True)
        monkeypatch.setattr(semantic_judge.llm_client, "chat", fake_chat)
        criteria = [{"id": "AC-1", "given": "g", "when": "w", "then": "t"}]
        result = semantic_judge.score_acceptance_criteria(criteria)
        assert result.status == "error"
        assert "rate limited" in (result.reason or "")


class TestEvaluateChapterInternTest:
    def test_returns_judge_payload(self, monkeypatch):
        def fake_chat(**kwargs):
            return LLMResponse(
                content=json.dumps({
                    "inputs": {"answered": True, "evidence": "POST /login"},
                    "outputs": {"answered": True, "evidence": "200 OK"},
                    "test_scenario": {"answered": True, "evidence": "..."},
                    "definition_of_done": {"answered": False, "evidence": None},
                }),
                model="gpt-4o-mini",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                stop_reason="stop",
            )

        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: True)
        monkeypatch.setattr(semantic_judge.llm_client, "chat", fake_chat)

        result = semantic_judge.evaluate_chapter_intern_test(
            "Chapter text here.", linked_requirements=[]
        )
        assert result.status == "ok"
        assert result.data["inputs"]["answered"] is True
        assert result.data["definition_of_done"]["answered"] is False

    def test_skipped_without_llm(self, monkeypatch):
        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: False)
        result = semantic_judge.evaluate_chapter_intern_test("text", [])
        assert result.status == "skipped"
