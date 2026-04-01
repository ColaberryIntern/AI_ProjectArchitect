"""Tests for enterprise sync payload builder."""

from execution.advisory.enterprise_sync import build_lead_payload


class TestBuildLeadPayload:
    def test_uses_business_idea_for_description(self):
        session = {
            "session_id": "test-123",
            "business_idea": "AI-powered logistics platform",
            "answers": [],
            "lead": {},
        }
        payload = build_lead_payload(session)
        assert payload["recommendation"]["description"] == "AI-powered logistics platform"

    def test_falls_back_to_q1_answer_when_no_business_idea(self):
        session = {
            "session_id": "test-123",
            "business_idea": "",
            "answers": [
                {
                    "question_id": "q1_business_overview",
                    "question_text": "What does your business do?",
                    "answer_text": "We run a freight coordination company",
                },
            ],
            "lead": {},
        }
        payload = build_lead_payload(session)
        assert payload["recommendation"]["description"] == "We run a freight coordination company"

    def test_default_description_when_no_idea_at_all(self):
        session = {
            "session_id": "test-123",
            "business_idea": "",
            "answers": [],
            "lead": {},
        }
        payload = build_lead_payload(session)
        assert payload["recommendation"]["description"] == "Advisory session completed"

    def test_questions_and_answers_preserve_pairing(self):
        session = {
            "session_id": "test-123",
            "business_idea": "Test",
            "answers": [
                {"question_id": "q1", "question_text": "What do you do?", "answer_text": "Logistics"},
                {"question_id": "q3", "question_text": "Which departments?", "answer_text": "Sales, Ops"},
                {"question_id": "q4", "question_text": "Bottlenecks?", "answer_text": "Manual data entry"},
            ],
            "lead": {},
        }
        payload = build_lead_payload(session)
        qa = payload["recommendation"]["metadata"]["questions_and_answers"]
        assert len(qa) == 3
        assert qa[0]["question"] == "What do you do?"
        assert qa[0]["answer"] == "Logistics"
        assert qa[1]["question"] == "Which departments?"
        assert qa[1]["answer"] == "Sales, Ops"
        assert qa[2]["question"] == "Bottlenecks?"
        assert qa[2]["answer"] == "Manual data entry"
