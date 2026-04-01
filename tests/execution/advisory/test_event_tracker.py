"""Tests for event tracking."""

import pytest


@pytest.fixture
def advisory_output_dir(monkeypatch, tmp_path):
    """Redirect advisory output to temp directory."""
    import config.settings as settings
    import execution.advisory.event_tracker as et

    advisory_dir = tmp_path / "advisory"
    advisory_dir.mkdir()
    monkeypatch.setattr(settings, "ADVISORY_OUTPUT_DIR", advisory_dir)
    monkeypatch.setattr(et, "_EVENTS_LOG_PATH", advisory_dir / "_events_log.json")
    return advisory_dir


class TestTrackEvent:
    def test_records_event(self, advisory_output_dir):
        from execution.advisory.event_tracker import track_event

        event = track_event("advisory_start_clicked", session_id="s123")
        assert event["event_name"] == "advisory_start_clicked"
        assert event["session_id"] == "s123"
        assert event["event_id"]
        assert event["timestamp"]

    def test_stores_utm_params(self, advisory_output_dir):
        from execution.advisory.event_tracker import track_event

        event = track_event(
            "page_view",
            utm_params={"utm_source": "google", "utm_medium": "cpc"},
        )
        assert event["utm_params"]["utm_source"] == "google"

    def test_multiple_events_accumulate(self, advisory_output_dir):
        from execution.advisory.event_tracker import get_events, track_event

        track_event("event_1")
        track_event("event_2")
        track_event("event_3")
        events = get_events()
        assert len(events) == 3


class TestGetEvents:
    def test_filter_by_session(self, advisory_output_dir):
        from execution.advisory.event_tracker import get_events, track_event

        track_event("e1", session_id="s1")
        track_event("e2", session_id="s2")
        track_event("e3", session_id="s1")
        events = get_events(session_id="s1")
        assert len(events) == 2

    def test_filter_by_email(self, advisory_output_dir):
        from execution.advisory.event_tracker import get_events, track_event

        track_event("e1", email="alice@example.com")
        track_event("e2", email="bob@example.com")
        events = get_events(email="alice@example.com")
        assert len(events) == 1

    def test_filter_by_event_name(self, advisory_output_dir):
        from execution.advisory.event_tracker import get_events, track_event

        track_event("click")
        track_event("view")
        track_event("click")
        events = get_events(event_name="click")
        assert len(events) == 2

    def test_respects_limit(self, advisory_output_dir):
        from execution.advisory.event_tracker import get_events, track_event

        for i in range(10):
            track_event(f"event_{i}")
        events = get_events(limit=5)
        assert len(events) == 5


class TestFunnelStats:
    def test_returns_event_counts(self, advisory_output_dir):
        from execution.advisory.event_tracker import get_funnel_stats, track_event

        track_event("advisory_start_clicked")
        track_event("advisory_start_clicked")
        track_event("advisory_lead_captured")
        stats = get_funnel_stats()
        assert stats["advisory_start_clicked"] == 2
        assert stats["advisory_lead_captured"] == 1
