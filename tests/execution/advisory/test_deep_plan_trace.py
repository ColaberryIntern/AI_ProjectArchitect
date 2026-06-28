"""Tests for the deterministic requirements-traceability gate."""

import execution.advisory.deep_plan_trace as tr


def _reqs(*specs):
    """specs: (id, priority) tuples."""
    return [{"id": i, "priority": p, "statement": f"req {i}"} for i, p in specs]


def _story(sid, fulfills, release="r0"):
    return {"id": sid, "fulfills": list(fulfills), "release": release}


def _full_set():
    """A clean passing set: 12 stories, 2 reqs (1 must, 1 should), both covered."""
    reqs = _reqs(("REQ-001", "must"), ("REQ-002", "should"))
    stories = []
    # 6 releases x 2 stories = 12, alternating which req each fulfills
    for r in range(6):
        stories.append(_story(f"STORY-{2*r+1:03d}", ["REQ-001"], release=f"r{r}"))
        stories.append(_story(f"STORY-{2*r+2:03d}", ["REQ-002"], release=f"r{r}"))
    return reqs, stories


def test_clean_set_passes_with_no_warnings():
    reqs, stories = _full_set()
    res = tr.validate(reqs, stories)
    assert res["ok"] is True
    assert res["warnings"] == []
    assert res["must_orphans"] == [] and res["should_orphans"] == []
    assert res["rtm"]["REQ-001"]  # covered


def _set_all_to(req_id):
    """12 stories across 6 releases (2 each), every story fulfilling `req_id`."""
    stories = []
    for r in range(6):
        stories.append(_story(f"STORY-{2*r+1:03d}", [req_id], release=f"r{r}"))
        stories.append(_story(f"STORY-{2*r+2:03d}", [req_id], release=f"r{r}"))
    return stories


def test_orphan_must_fails_closed():
    reqs = _reqs(("REQ-001", "must"), ("REQ-002", "should"))
    # healthy shape (12 stories, 2/release) but nothing covers the must REQ-001
    stories = _set_all_to("REQ-002")
    res = tr.validate(reqs, stories)
    assert res["ok"] is False
    assert "REQ-001" in res["must_orphans"]
    assert res["thin_releases"] == [] and res["below_floor"] is False  # isolated


def test_orphan_should_only_warns():
    reqs = _reqs(("REQ-001", "must"), ("REQ-002", "should"))
    # healthy shape; the must is covered, only the should is orphaned
    stories = _set_all_to("REQ-001")
    res = tr.validate(reqs, stories)
    assert res["ok"] is True            # should-orphan does NOT block
    assert "REQ-002" in res["should_orphans"]
    assert res["warnings"]
    assert res["thin_releases"] == [] and res["below_floor"] is False  # isolated


def test_uncited_story_fails_closed():
    reqs, stories = _full_set()
    stories[0]["fulfills"] = []
    res = tr.validate(reqs, stories)
    assert res["ok"] is False
    assert stories[0]["id"].upper() in res["uncited_stories"]


def test_invalid_citation_fails_closed():
    reqs, stories = _full_set()
    stories[0]["fulfills"] = ["REQ-999"]
    res = tr.validate(reqs, stories)
    assert res["ok"] is False
    assert any(c["req"] == "REQ-999" for c in res["invalid_citations"])


def test_below_floor_fails():
    reqs = _reqs(("REQ-001", "must"))
    stories = [_story(f"STORY-{i:03d}", ["REQ-001"], release=f"r{i}") for i in range(3)]
    res = tr.validate(reqs, stories)
    assert res["ok"] is False
    assert res["below_floor"] is True


def test_single_story_release_fails():
    reqs, stories = _full_set()
    stories.append(_story("STORY-099", ["REQ-001"], release="r9"))  # lone story in r9
    res = tr.validate(reqs, stories)
    assert res["ok"] is False
    assert "r9" in res["thin_releases"]


def test_rtm_render_lists_reqs_and_status():
    reqs, stories = _full_set()
    md = tr.render_rtm_md(reqs, stories)
    assert "REQ-001" in md and "REQ-002" in md
    assert "covered" in md
    assert "Gate result" in md


def test_case_insensitive_citation_matches():
    reqs = _reqs(("REQ-001", "must"))
    stories = [_story(f"STORY-{i:03d}", ["req-001"], release=f"r{i//2}") for i in range(12)]
    res = tr.validate(reqs, stories)
    assert res["ok"] is True
    assert res["rtm"]["REQ-001"]
