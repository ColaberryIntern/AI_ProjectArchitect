"""Tests for the problem analysis engine."""

from execution.advisory.problem_analyzer import analyze_problems, get_domain_label


def _make_session(idea="", answers=None):
    if answers is None:
        answers = []
    return {"business_idea": idea, "answers": answers}


def _make_answers(**overrides):
    defaults = {
        "q1_business_overview": "Logistics company",
        "q4_bottlenecks": "Manual routing and dispatch",
        "q8_manual_processes": "Data entry",
    }
    defaults.update(overrides)
    return [
        {"question_id": qid, "question_text": "Q", "answer_text": text}
        for qid, text in defaults.items()
    ]


class TestAnalyzeProblems:
    def test_returns_required_fields(self):
        result = analyze_problems(_make_session("test"))
        assert "primary_problem" in result
        assert "secondary_problems" in result
        assert "domain_weights" in result
        assert "confidence" in result

    def test_operations_dominant_for_logistics(self):
        session = _make_session(
            idea="We run a logistics company with delivery routing and dispatch problems",
            answers=_make_answers(
                q4_bottlenecks="Manual routing, dispatch delays, driver coordination is all done by hand",
                q8_manual_processes="Route planning, delivery scheduling, fleet tracking",
            ),
        )
        result = analyze_problems(session)
        assert result["primary_problem"] == "operations"
        assert result["primary_weight"] > 0.3

    def test_sales_dominant_for_sales_company(self):
        session = _make_session(
            idea="B2B SaaS with slow lead conversion and pipeline problems",
            answers=_make_answers(
                q4_bottlenecks="Lead follow-ups are inconsistent, pipeline is stalled, low conversion rate",
                q8_manual_processes="Manual outreach, proposal creation, deal tracking",
            ),
        )
        result = analyze_problems(session)
        assert result["primary_problem"] == "sales"

    def test_support_dominant_for_service_company(self):
        session = _make_session(
            idea="Customer service company with slow response times",
            answers=_make_answers(
                q4_bottlenecks="Ticket backlog, long wait times, customer complaints",
                q8_manual_processes="Ticket categorization, response drafting",
            ),
        )
        result = analyze_problems(session)
        assert result["primary_problem"] == "support"

    def test_balanced_when_no_strong_signal(self):
        session = _make_session(idea="general business", answers=[])
        result = analyze_problems(session)
        # Should still return something
        assert result["domain_weights"]
        max_weight = max(result["domain_weights"].values())
        assert max_weight < 0.5  # No single domain dominates

    def test_confidence_scales_with_dominance(self):
        strong = _make_session(
            idea="logistics routing dispatch delivery shipping warehouse fleet",
            answers=_make_answers(q4_bottlenecks="routing delays dispatch problems"),
        )
        weak = _make_session(idea="general business", answers=[])
        strong_result = analyze_problems(strong)
        weak_result = analyze_problems(weak)
        assert strong_result["confidence"] > weak_result["confidence"]

    def test_detected_keywords(self):
        session = _make_session(idea="We need better lead scoring and pipeline management")
        result = analyze_problems(session)
        assert "sales" in result["detected_keywords"]


class TestGetDomainLabel:
    def test_known_domains(self):
        assert "Operations" in get_domain_label("operations")
        assert "Sales" in get_domain_label("sales")

    def test_unknown_domain(self):
        label = get_domain_label("unknown_thing")
        assert label  # Should return something
