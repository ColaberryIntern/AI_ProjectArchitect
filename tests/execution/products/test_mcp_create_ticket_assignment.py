"""Tests for create_ticket's due-date + assignee fields."""

import execution.products.library.mcp_tools as mt


def test_due_on_passthrough():
    extra = mt._assignment_fields(object(), 7463955, {"due_on": "2026-08-10"})
    assert extra == {"due_on": "2026-08-10"}


def test_explicit_assignee_ids_coerced():
    extra = mt._assignment_fields(object(), 7463955, {"assignee_ids": ["111", 222, "x"]})
    assert extra["assignee_ids"] == [111, 222]


def test_assign_to_me_resolves_operator(monkeypatch):
    import execution.advisory.basecamp_build_writer as bw
    monkeypatch.setattr(bw, "resolve_operator_bc_person_id", lambda u, b: 999)
    extra = mt._assignment_fields(object(), 7463955, {"assign_to_me": True, "due_on": "2026-08-10"})
    assert extra["assignee_ids"] == [999]
    assert extra["due_on"] == "2026-08-10"


def test_empty_when_nothing_requested():
    assert mt._assignment_fields(object(), 7463955, {}) == {}


def test_assign_to_me_unresolved_is_silent(monkeypatch):
    import execution.advisory.basecamp_build_writer as bw
    monkeypatch.setattr(bw, "resolve_operator_bc_person_id", lambda u, b: None)
    extra = mt._assignment_fields(object(), 7463955, {"assign_to_me": True})
    assert extra == {}                              # no assignee resolved → no field
