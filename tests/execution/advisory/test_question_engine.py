"""Tests for the advisory question engine (10-question version)."""

import pytest

from execution.advisory.question_engine import (
    ADVISORY_QUESTIONS,
    SYSTEM_INTEGRATION_OPTIONS,
    TOTAL_QUESTIONS,
    get_all_questions,
    get_answer_by_question_id,
    get_answers_by_category,
    get_next_question,
    get_progress,
    get_question,
    is_complete,
)


class TestQuestionData:
    def test_has_10_questions(self):
        assert TOTAL_QUESTIONS == 10
        assert len(ADVISORY_QUESTIONS) == 10

    def test_all_questions_have_required_fields(self):
        for q in ADVISORY_QUESTIONS:
            assert "id" in q
            assert "index" in q
            assert "category" in q
            assert "text" in q
            assert "help_text" in q

    def test_question_ids_are_unique(self):
        ids = [q["id"] for q in ADVISORY_QUESTIONS]
        assert len(ids) == len(set(ids))

    def test_question_indices_are_sequential(self):
        for i, q in enumerate(ADVISORY_QUESTIONS):
            assert q["index"] == i

    def test_system_integration_options_exist(self):
        assert len(SYSTEM_INTEGRATION_OPTIONS) >= 6


class TestGetQuestion:
    def test_valid_index(self):
        q = get_question(0)
        assert q is not None
        assert q["id"] == "q1_business_overview"

    def test_last_index(self):
        q = get_question(9)
        assert q is not None
        assert q["id"] == "q10_success_vision"

    def test_out_of_range_returns_none(self):
        assert get_question(-1) is None
        assert get_question(10) is None

    def test_get_all_questions(self):
        assert len(get_all_questions()) == 10


class TestGetNextQuestion:
    def test_returns_first_for_new_session(self):
        session = {"current_question_index": 0, "answers": []}
        q = get_next_question(session)
        assert q is not None
        assert q["index"] == 0

    def test_returns_none_when_complete(self):
        session = {"current_question_index": 10, "answers": [{"q": "a"}] * 10}
        assert get_next_question(session) is None


class TestIsComplete:
    def test_incomplete(self):
        assert is_complete({"answers": [{"q": "a"}] * 5}) is False

    def test_complete(self):
        assert is_complete({"answers": [{"q": "a"}] * 10}) is True

    def test_empty(self):
        assert is_complete({"answers": []}) is False


class TestGetProgress:
    def test_empty(self):
        p = get_progress({"answers": []})
        assert p["current_index"] == 0
        assert p["total"] == 10
        assert p["percent"] == 0

    def test_halfway(self):
        p = get_progress({"answers": [{"q": "a"}] * 5})
        assert p["percent"] == 25  # 5/10 * 50% = 25%

    def test_questions_done_no_design(self):
        p = get_progress({"answers": [{"q": "a"}] * 10})
        assert p["percent"] == 50  # Questions = 50% of flow
        assert p["is_complete"] is True

    def test_after_design_step(self):
        p = get_progress({"answers": [{"q": "a"}] * 10, "selected_outcomes": ["x"]})
        assert p["percent"] == 70

    def test_after_capabilities(self):
        p = get_progress({"answers": [{"q": "a"}] * 10, "selected_capabilities": ["cap1"]})
        assert p["percent"] == 90


class TestAnswerLookup:
    def test_find_by_id(self):
        session = {"answers": [
            {"question_id": "q1_business_overview", "answer_text": "Logistics"},
            {"question_id": "q4_bottlenecks", "answer_text": "Manual routing"},
        ]}
        answer = get_answer_by_question_id(session, "q4_bottlenecks")
        assert answer["answer_text"] == "Manual routing"

    def test_not_found(self):
        assert get_answer_by_question_id({"answers": []}, "q1") is None

    def test_by_category(self):
        session = {"answers": [
            {"question_id": "q1_business_overview", "answer_text": "Logistics"},
            {"question_id": "q4_bottlenecks", "answer_text": "Slow"},
        ]}
        ops = get_answers_by_category(session, "operations")
        assert len(ops) == 1
        assert ops[0]["answer_text"] == "Slow"
