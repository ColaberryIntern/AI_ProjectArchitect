"""Unit tests for session_categorizer (rule-based v1)."""
from __future__ import annotations

import json
from execution.products.library import session_categorizer as sc


def _lists(*pairs):
    """Helper: build candidate list dicts. _lists((1,"Sales"), (2,"Engineering"))"""
    return [{"id": i, "name": n, "completed": False} for (i, n) in pairs]


def test_tokenize_drops_stopwords_and_short():
    toks = sc.tokenize("The quick brown fox jumps over the lazy dog and a cat")
    assert "the" not in toks
    assert "and" not in toks
    assert "quick" in toks
    assert "brown" in toks
    # 'cat' is 3 chars, length-OK and not in stopwords -> kept
    assert "cat" in toks


def test_tokenize_handles_punctuation_and_ampersand():
    toks = sc.tokenize("Sales/Marketing & PR — quarterly review")
    assert "sales" in toks
    assert "marketing" in toks
    assert "quarterly" in toks


def test_expand_via_synonyms_layers_sales_family():
    expanded = sc._expand_via_synonyms({"sales"})
    assert "deal" in expanded
    assert "proposal" in expanded
    assert "client" in expanded


# ── Core categorize() ─────────────────────────────────────────────────


def test_categorize_picks_obvious_match():
    out = sc.categorize(
        session_title="Draft a proposal email for Acme",
        session_snippet="They want pricing on the Q4 enterprise deal",
        candidate_lists=_lists((1, "Sales / Outreach"),
                                                 (2, "Engineering"),
                                                 (3, "Finance")),
    )
    assert out.chosen_list_id == 1
    assert out.chosen_list_name == "Sales / Outreach"
    assert out.confidence > 0
    assert out.should_ask_user is False
    assert any(tok in ("proposal", "pricing", "deal", "sales")
                          for tok in out.matched_tokens)


def test_categorize_no_overlap_asks_user_and_suggests_name():
    out = sc.categorize(
        session_title="Schedule yoga class next Tuesday",
        session_snippet="Bring own mat",
        candidate_lists=_lists((1, "Sales / Outreach"),
                                                 (2, "Engineering")),
    )
    assert out.chosen_list_id is None
    assert out.confidence == 0.0
    assert out.should_ask_user is True
    assert out.suggest_new_list_name  # non-empty


def test_categorize_no_active_lists_asks_user():
    out = sc.categorize(
        session_title="Pricing model",
        session_snippet="",
        candidate_lists=[],
    )
    assert out.should_ask_user is True
    assert "no_active_lists" in out.rationale


def test_categorize_completed_lists_are_skipped():
    out = sc.categorize(
        session_title="Sales call followup",
        session_snippet="",
        candidate_lists=[
            {"id": 1, "name": "Sales / Outreach", "completed": True},
            {"id": 2, "name": "Engineering", "completed": False},
        ],
    )
    # The Sales list is completed -> skipped; Engineering doesn't match
    # -> low confidence + ask user.
    assert out.chosen_list_id != 1


def test_categorize_history_boost_overrides_thin_match(tmp_path, monkeypatch):
    """When the user has previously sent very similar topics to a
    specific list, that list should win even if literal keyword overlap
    is thin."""
    monkeypatch.setattr(sc, "_log_dir", lambda: tmp_path)
    # Seed history: prior topics about "spreadsheet" landed in list id 2
    p = tmp_path / "alice@example.com.jsonl"
    p.write_text(
        json.dumps({
            "ts": "2026-06-06T12:00:00Z",
            "session_title": "Build spreadsheet for revenue Q3 numbers",
            "chosen_list_id": 2, "chosen_list_name": "Finance",
        }) + "\n"
        + json.dumps({
            "ts": "2026-06-06T12:05:00Z",
            "session_title": "Spreadsheet pivot for revenue tracking",
            "chosen_list_id": 2, "chosen_list_name": "Finance",
        }) + "\n",
        encoding="utf-8",
    )
    out = sc.categorize(
        session_title="New spreadsheet for revenue tracking",
        session_snippet="",
        candidate_lists=_lists((1, "Operations"),
                                                 (2, "Finance"),
                                                 (3, "Engineering")),
        user_email="alice@example.com",
    )
    assert out.chosen_list_id == 2
    assert out.history_hits >= 1


# ── Log helpers ───────────────────────────────────────────────────────


def test_log_decision_and_load_recent_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_log_dir", lambda: tmp_path)
    result = sc.CategorizationResult(
        chosen_list_id=42, chosen_list_name="Sales / Outreach",
        confidence=0.8, rationale="matched 4 tokens",
        matched_tokens=["sales", "deal", "client", "proposal"],
    )
    sc.log_decision("bob@example.com",
                                session_title="Test",
                                result=result,
                                bc_project_id=999)
    loaded = sc.load_recent_log("bob@example.com")
    assert len(loaded) == 1
    assert loaded[0]["chosen_list_id"] == 42
    assert loaded[0]["bc_project_id"] == 999


def test_log_override_strong_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_log_dir", lambda: tmp_path)
    sc.log_override(
        "carol@example.com",
        ticket_id=555, old_list_id=1, old_list_name="Sales",
        new_list_id=2, new_list_name="Engineering",
        session_title="Fix the OAuth callback bug",
        reason="this is engineering, not sales",
    )
    loaded = sc.load_recent_log("carol@example.com")
    assert len(loaded) == 1
    assert loaded[0]["event"] == "override"
    assert loaded[0]["new_list_id"] == 2


# ── Transparency render ──────────────────────────────────────────────


def test_render_transparency_block_contains_visible_and_hidden():
    result = sc.CategorizationResult(
        chosen_list_id=42, chosen_list_name="Sales / Outreach",
        confidence=0.72, rationale="matched 3 tokens",
        matched_tokens=["sales", "deal", "proposal"],
        history_hits=2,
        alternatives=[{"id": 7, "name": "Engineering", "score": 1}],
    )
    block = sc.render_transparency_block(result)
    assert "Filed under: Sales / Outreach" in block
    assert "<!-- colaberry_categorization" in block
    assert "0.72" in block
    assert "Engineering" in block  # alternatives are in the comment
    assert "72%" in block  # visible percent


def test_render_transparency_block_empty_when_no_choice():
    result = sc.CategorizationResult()
    assert sc.render_transparency_block(result) == ""
