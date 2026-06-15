"""Unit tests for action recipe matching + Claude Code prompt generation."""
from execution.products.ops import suggestions as S
from execution.products.ops.store import OpsTodo
from execution.products.ops.suggestions import build_suggestion, generate_prompt


def _make(title, desc="", category="unscored"):
    return OpsTodo(
        bc_id=1, bc_project_id=1, bc_project_name="Test Project",
        bc_todolist_id=1, bc_todolist_name="Main",
        title=title, description=desc, category=category,
        bc_app_url="https://3.basecamp.com/x/y",
    )


def test_decision_keyword_matches():
    s = build_suggestion(_make("Please approve the budget"))
    assert s["action_kind"] == "decision"


def test_reply_keyword_matches():
    s = build_suggestion(_make("Reply to Karun's email"))
    assert s["action_kind"] == "reply"


def test_meeting_keyword_matches():
    s = build_suggestion(_make("Schedule the kickoff sync"))
    assert s["action_kind"] == "meeting"


def test_research_keyword_matches():
    s = build_suggestion(_make("Investigate the cost overrun"))
    assert s["action_kind"] == "research"


def test_build_keyword_matches():
    s = build_suggestion(_make("Implement the new auth flow"))
    assert s["action_kind"] == "build"


def test_review_keyword_matches():
    s = build_suggestion(_make("QA the new dashboard"))
    assert s["action_kind"] == "review"


def test_default_recipe_when_no_match():
    s = build_suggestion(_make("Generic placeholder"))
    assert s["action_kind"] == "default"


def test_suggestion_has_required_fields():
    s = build_suggestion(_make("Approve the doc"))
    for k in ("action_kind", "one_line", "steps", "resources", "stop_conditions", "urgency_summary"):
        assert k in s
    assert s["steps"]   # non-empty


def test_generate_prompt_includes_title_and_steps():
    t = _make("Approve the budget", desc="Need sign-off by Friday")
    prompt = generate_prompt(t)
    assert "Approve the budget" in prompt
    assert "Test Project" in prompt
    assert "Need sign-off by Friday" in prompt
    assert "decision" in prompt
    assert "https://3.basecamp.com/x/y" in prompt
    # Steps should be numbered
    assert "1." in prompt


def test_generate_prompt_handles_no_description():
    t = _make("Reply to email")
    prompt = generate_prompt(t)
    assert "Reply to email" in prompt
    # description block should be absent (no orphan "## Description" heading)
    assert "## Description" not in prompt


def test_generate_prompt_handles_no_due_date():
    t = _make("Some task")
    prompt = generate_prompt(t)
    assert "no due date" in prompt


# ── List/project links in the CONTEXT block ────────────────────────────────
# approval-task-dependency-linking.md (Fix A): a title is not a pointer. The
# prompt must link the list (to see siblings + project scale) and project.

def _make_linked(title, desc=""):
    # A realistic BC todo app URL (has the /todos/<id> segment the URL helpers
    # swap) so list_url / project_url derive non-empty.
    return OpsTodo(
        bc_id=7, bc_project_id=123, bc_project_name="Launch PMO",
        bc_todolist_id=42, bc_todolist_name="Outreach",
        title=title, description=desc,
        bc_app_url="https://3.basecamp.com/4567/buckets/123/todos/7",
    )


def test_generate_prompt_includes_list_and_project_urls():
    prompt = generate_prompt(_make_linked("Approve the script"))
    assert "https://3.basecamp.com/4567/buckets/123/todolists/42" in prompt
    assert "https://3.basecamp.com/4567/buckets/123" in prompt


def test_generate_prompt_surfaces_dependency_and_artifact_links():
    desc = (
        "<strong>Depends-on:</strong> https://3.basecamp.com/4567/buckets/123/todos/9 "
        "<strong>Artifact:</strong> https://3.basecamp.com/4567/buckets/123/uploads/55"
    )
    prompt = generate_prompt(_make_linked("Approve the sales call script", desc=desc))
    assert "## Dependency" in prompt
    assert "https://3.basecamp.com/4567/buckets/123/todos/9" in prompt
    assert "https://3.basecamp.com/4567/buckets/123/uploads/55" in prompt


def test_generate_prompt_flags_pending_artifact_as_not_an_approver_delay():
    desc = (
        "<strong>Depends-on:</strong> https://3.basecamp.com/4567/buckets/123/todos/9 "
        "<strong>Artifact:</strong> PENDING"
    )
    prompt = generate_prompt(_make_linked("Approve the sales call script", desc=desc))
    assert "PENDING" in prompt
    assert "approver delay" in prompt.lower()


def test_generate_prompt_reads_basecamp_autolinked_markers():
    # Basecamp autolinks bare URLs on save, so a SYNCED description carries the
    # value as an <a href> anchor, not bare text. The consumer must read the
    # href (a plain [^<]+? capture reads empty here). Regression for 2026-06-15.
    base = "https://3.basecamp.com/4567/buckets/123/todos/9"
    anchor = f'<a rel="noreferrer" class="autolinked" href="{base}">{base}</a>'
    desc = f"<strong>Depends-on:</strong> {anchor}<br><strong>Artifact:</strong> {anchor}"
    prompt = generate_prompt(_make_linked("Approve the sales call script", desc=desc))
    assert "## Dependency" in prompt
    # The URL must surface in the rendered block, not an empty "Drafting task:".
    assert f"**Drafting task:** {base}" in prompt
    assert f"**Artifact:** {base}" in prompt


def test_generate_prompt_no_dependency_block_for_plain_task():
    prompt = generate_prompt(_make_linked("Reply to Karun"))
    assert "## Dependency" not in prompt


# ── F1: human-owned decisions get a recommendation, not a verdict ──────────

_HUMAN_DESC = (
    '<span>HUMAN TASK</span> <strong>Owner:</strong> Ali Muwwakkil '
    '<h3>Objective</h3><p>Confirm cohort cadence and size.</p>'
)


def test_human_owned_decision_reframes_step3_to_recommendation():
    t = _make("Finalize decision on cohort cadence and size", desc=_HUMAN_DESC,
              category="human_required")
    s = build_suggestion(t)
    assert s["action_kind"] == "decision"
    step3 = s["steps"][2].lower()
    assert "recommendation" in step3
    assert "do not post" in step3
    # The named owner is interpolated into the step + the ownership note.
    assert "Ali Muwwakkil" in s["steps"][2]
    assert "Ali Muwwakkil" in s["owner_note"]


def test_human_owned_prompt_has_ownership_section():
    t = _make("Approve the cohort plan", desc=_HUMAN_DESC, category="human_required")
    prompt = generate_prompt(t)
    assert "## Ownership" in prompt
    assert "recommendation for them to confirm" in prompt


def test_non_human_decision_keeps_verdict_wording():
    # A genuinely delegated decision (no HUMAN TASK marker, not human_required)
    # keeps the original verdict step and gets no ownership note.
    t = _make("Approve the vendor contract", desc="Please decide by Friday.")
    s = build_suggestion(t)
    assert s["action_kind"] == "decision"
    assert "verdict, reason, next action" in s["steps"][2]
    assert s["owner_note"] == ""
    assert "## Ownership" not in generate_prompt(t)


def test_human_required_category_alone_triggers_reframe():
    # No "HUMAN TASK" / Owner marker in the body, only the scorer category.
    t = _make("Confirm the launch date", desc="We need to lock this.",
              category="human_required")
    s = build_suggestion(t)
    assert "recommendation" in s["steps"][2].lower()
    # Falls back to the generic owner label when no name is present.
    assert "the owner" in s["steps"][2]


def test_human_marker_without_category_triggers_reframe():
    # PMO stamped HUMAN TASK but scorer hasn't tagged category yet.
    t = _make("Confirm scope", desc="HUMAN TASK <strong>Owner:</strong> Ram Katamaraja")
    s = build_suggestion(t)
    assert "recommendation" in s["steps"][2].lower()
    assert "Ram Katamaraja" in s["steps"][2]


def test_human_required_non_decision_kind_unaffected():
    # Reframe only applies to recipes that declare overrides (decision today).
    # A human_required BUILD task keeps its normal steps + no ownership note.
    # (Description deliberately avoids decision keywords like "confirm"/"approve"
    # so the build recipe wins the match.)
    build_desc = "HUMAN TASK <strong>Owner:</strong> Ali Muwwakkil. Ship the dashboard."
    t = _make("Implement the cohort dashboard", desc=build_desc,
              category="human_required")
    s = build_suggestion(t)
    assert s["action_kind"] == "build"
    assert s["owner_note"] == ""


# ── F2: no recipe references a non-existent skill/tool ─────────────────────

# Skills that actually ship in the harness. A recipe resource of kind "skill"
# must name one of these, or the operator is pointed at something that doesn't
# exist (the decision-record/email-tone-check/agenda-tight class of bug).
_REAL_SKILLS = {"deep-research", "code-review"}
_DEAD_REFS = {"decision-record", "email-tone-check", "agenda-tight", "cb-context-walker"}


def test_no_recipe_references_a_dead_skill():
    for recipe in S._RECIPES + [S._DEFAULT_RECIPE]:
        for r in recipe.get("resources", []):
            if r["kind"] == "skill":
                assert r["name"] in _REAL_SKILLS, (
                    f"recipe '{recipe['kind']}' references unknown skill '{r['name']}'"
                )


def test_known_dead_references_are_gone_everywhere():
    blob = repr(S._RECIPES) + repr(S._DEFAULT_RECIPE) + S._PROMPT_TEMPLATE
    for dead in _DEAD_REFS:
        assert dead not in blob, f"dead reference '{dead}' resurfaced"


def test_decision_recipe_uses_real_capture_tools():
    decision = next(r for r in S._RECIPES if r["kind"] == "decision")
    names = {r["name"] for r in decision["resources"]}
    assert "colaberry_remember" in names
    assert "colaberry_save_doc_to_bc" in names
