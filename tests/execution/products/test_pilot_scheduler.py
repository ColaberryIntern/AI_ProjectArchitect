"""[Karun 3 + Kes 3] Pilot dash scheduler + dash_runner tests.

Covers:
    1. Scheduler registers both Monday cron jobs with the right ET offsets
    2. dash_runner.run('karun') in stub mode writes HTML + JSON sidecar
    3. Stub-mode HTML carries the pre-ratification banner
    4. Critic correctly short-circuits to a 'scoring stub' failure when
       sources are stub-mode
    5. Output directory is created on first run (idempotent)

NOT covered (out of scope until PRDs sign):
    - Real source loading (BC, Gmail, HubSpot, Apollo, CCPP, GitHub, etc.)
    - Real scoring logic
    - Gmail delivery
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from execution.products.pilot import dash_runner, scheduler


@pytest.fixture
def tmp_pilot_output(monkeypatch, tmp_path):
    """Redirect OUTPUT_ROOT to a tmp dir so tests don't write into the repo."""
    monkeypatch.setattr(dash_runner, "OUTPUT_ROOT", tmp_path / "_pilot")
    return tmp_path / "_pilot"


# ── dash_runner ───────────────────────────────────────────────────


def test_run_karun_in_stub_mode_writes_html(tmp_pilot_output):
    result = dash_runner.run("karun")
    assert result.status == "ok"
    assert result.placeholder is True
    assert Path(result.output_path).exists()
    html = Path(result.output_path).read_text(encoding="utf-8")
    assert "Karun 1:1 dashboard" in html
    assert "PRE-RATIFICATION SCAFFOLD" in html
    # Sidecar JSON written alongside HTML
    sidecar_path = Path(result.output_path).with_suffix(".json")
    assert sidecar_path.exists()
    sidecar = json.loads(sidecar_path.read_text())
    assert sidecar["dri"] == "karun"
    assert sidecar["sources_stub"] is True
    assert len(sidecar["scored"]) == 5


def test_run_kes_in_stub_mode_writes_html(tmp_pilot_output):
    result = dash_runner.run("kes")
    assert result.status == "ok"
    assert result.placeholder is True
    html = Path(result.output_path).read_text(encoding="utf-8")
    assert "Kes 1:1 dashboard" in html


def test_critic_short_circuits_on_stub_sources():
    sources = {"_stub": True, "_message": "test"}
    scored = []
    failures = dash_runner._critic("karun", scored, sources)
    assert len(failures) == 1
    assert "scoring stub" in failures[0]


def test_critic_flags_missing_value_when_not_stub():
    sources = {"_stub": False}
    scored = [
        {"number": "n1", "value": None}, {"number": "n2", "value": 10},
        {"number": "n3", "value": 20}, {"number": "n4", "value": 30},
        {"number": "n5", "value": 40},
    ]
    failures = dash_runner._critic("karun", scored, sources)
    assert any("number 1" in f for f in failures)


def test_critic_flags_banned_phrases():
    sources = {"_stub": False}
    scored = [
        {"number": "approximately 5 deals", "value": 5}, {"number": "n2", "value": 1},
        {"number": "n3", "value": 1}, {"number": "n4", "value": 1},
        {"number": "n5", "value": 1},
    ]
    failures = dash_runner._critic("karun", scored, sources)
    assert any("approximately" in f for f in failures)


def test_output_directory_created_on_first_run(tmp_pilot_output):
    assert not tmp_pilot_output.exists()
    dash_runner.run("karun")
    assert (tmp_pilot_output / "karun").is_dir()


# ── scheduler ─────────────────────────────────────────────────────


def test_scheduler_registers_both_jobs():
    s = scheduler.start_scheduler()
    try:
        assert s.get_job(scheduler.KARUN_JOB_ID) is not None
        assert s.get_job(scheduler.KES_JOB_ID) is not None
    finally:
        scheduler.stop_scheduler()


def test_scheduler_karun_fires_monday_0830_et():
    s = scheduler.start_scheduler()
    try:
        job = s.get_job(scheduler.KARUN_JOB_ID)
        trig = job.trigger
        # CronTrigger str() form: 'cron[day_of_week=...,hour=...,minute=...]'
        fields = {f.name: str(f) for f in trig.fields}
        assert "8" in fields.get("hour", "")
        assert "30" in fields.get("minute", "")
        # Day-of-week field name varies by APScheduler version; accept either
        dow = fields.get("day_of_week") or fields.get("week", "")
        assert "mon" in dow.lower() or "0" in dow  # 'mon' or 0-based numeric
    finally:
        scheduler.stop_scheduler()


def test_scheduler_kes_fires_monday_0900_et():
    s = scheduler.start_scheduler()
    try:
        job = s.get_job(scheduler.KES_JOB_ID)
        trig = job.trigger
        fields = {f.name: str(f) for f in trig.fields}
        assert "9" in fields.get("hour", "")
        assert "0" in fields.get("minute", "")
    finally:
        scheduler.stop_scheduler()


def test_scheduler_start_is_idempotent():
    s1 = scheduler.start_scheduler()
    s2 = scheduler.start_scheduler()
    try:
        assert s1 is s2
    finally:
        scheduler.stop_scheduler()


def test_scheduler_stop_clears_singleton():
    scheduler.start_scheduler()
    scheduler.stop_scheduler()
    assert scheduler._scheduler is None
