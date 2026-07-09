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
    for k in ("action_kind", "one_line", "deliverable", "steps", "resources",
              "stop_conditions", "urgency_summary"):
        assert k in s
    assert s["steps"]   # non-empty


def test_every_recipe_declares_a_deliverable():
    # The BLUF "You hand back" section answers "what am I producing?" — every
    # recipe (and the default) must name a concrete artifact, or that section
    # renders empty and the prompt is right back to being vague.
    for recipe in S._RECIPES + [S._DEFAULT_RECIPE]:
        d = recipe.get("deliverable", "")
        assert isinstance(d, str) and d.strip(), (
            f"recipe '{recipe['kind']}' has no deliverable"
        )


def test_build_suggestion_surfaces_the_deliverable():
    s = build_suggestion(_make("Approve the budget"))
    assert "verdict" in s["deliverable"].lower()


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


def test_generate_prompt_is_summary_downloads_details_ordered():
    # The prompt must lead with the point: title, then Summary (what you hand
    # back), then Downloads (files early), then the heavy Details only AFTER.
    t = _make("Approve the budget", desc="Need sign-off by Friday")
    prompt = generate_prompt(t)
    assert prompt.startswith("# Approve the budget")
    assert "## Summary" in prompt
    assert "You hand back:" in prompt
    # Deliverable text reaches the reader.
    assert "verdict" in prompt.lower()
    # Structure order: Summary -> Downloads -> Details.
    assert prompt.index("## Summary") < prompt.index("## Downloads") < prompt.index("## Details")
    # The old process-first opener is gone.
    assert "You're helping me work through" not in prompt


def test_generate_prompt_has_no_duplicate_stop_heading():
    # "Stop & escalate" lives in the BLUF header now; the old standalone
    # "When to stop and escalate" section was removed (no duplication).
    prompt = generate_prompt(_make("Approve the budget"))
    assert "### Stop & escalate if" in prompt
    assert "When to stop and escalate" not in prompt


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
    assert "**Ownership:**" in prompt
    assert "recommendation for them to confirm" in prompt


def test_generic_owner_note_has_no_clunky_parenthetical():
    # human_required but no Owner: marker → owner falls back to the generic
    # label. The note must NOT read "(owner: the owner)".
    t = _make("Confirm the launch date", desc="We need to lock this.",
              category="human_required")
    s = build_suggestion(t)
    assert "(owner: the owner)" not in s["owner_note"]
    assert "rests with the owner" in s["owner_note"]
    assert "(owner: the owner)" not in generate_prompt(t)


def test_named_owner_note_keeps_the_name_parenthetical():
    t = _make("Approve the cohort plan", desc=_HUMAN_DESC, category="human_required")
    s = build_suggestion(t)
    assert "(owner: Ali Muwwakkil)" in s["owner_note"]


def test_non_human_decision_keeps_verdict_wording():
    # A genuinely delegated decision (no HUMAN TASK marker, not human_required)
    # keeps the original verdict step and gets no ownership note.
    t = _make("Approve the vendor contract", desc="Please decide by Friday.")
    s = build_suggestion(t)
    assert s["action_kind"] == "decision"
    assert "verdict, reason, next action" in s["steps"][2]
    assert s["owner_note"] == ""
    assert "**Ownership:**" not in generate_prompt(t)


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


# ── persona swaps the "How I want you to work" block ────────────────────────

def test_generate_prompt_default_persona_is_copilot():
    prompt = generate_prompt(_make("Approve the plan"))
    assert "## How I want you to work" in prompt
    # Co-pilot's distinctive paced wording.
    assert "ask if I want to continue or adjust" in prompt


def test_generate_prompt_visual_persona_embeds_browser_instruction():
    prompt = generate_prompt(_make("Approve the plan"), persona="visual")
    assert "OPEN IT IN MY BROWSER" in prompt
    # And the co-pilot default wording is gone.
    assert "ask if I want to continue or adjust" not in prompt


def test_generate_prompt_checklist_persona_embeds_decision_tag():
    assert "[DECISION]" in generate_prompt(_make("Approve the plan"), persona="checklist")


def test_generate_prompt_unknown_persona_falls_back_to_copilot():
    prompt = generate_prompt(_make("Approve the plan"), persona="bogus")
    assert "ask if I want to continue or adjust" in prompt


# ── merge_llm_suggestion: LLM fields folded into the deterministic BLUF base ─

def test_merge_llm_suggestion_overrides_content_keeps_ownership():
    t = _make("Decide on the vendor", desc=_HUMAN_DESC, category="human_required")
    enhanced = {
        "action_kind": "decision",
        "goal_line": "A signed vendor decision memo posted to BC.",
        "specific_steps": ["Choose ONE: (a) Acme (b) Globex (c) hold"],
        "stop_conditions": ["If spend >$50k, escalate to Ram"],
        "summary_paragraph": "Pick the vendor by Friday.",
    }
    s = S.merge_llm_suggestion(t, enhanced)
    # LLM content wins.
    assert s["deliverable"] == "A signed vendor decision memo posted to BC."
    assert s["steps"] == ["Choose ONE: (a) Acme (b) Globex (c) hold"]
    assert s["stop_conditions"] == ["If spend >$50k, escalate to Ram"]
    assert s["summary_paragraph"] == "Pick the vendor by Friday."
    # Deterministic ownership + resources survive.
    assert "Ali Muwwakkil" in s["owner_note"]
    assert s["resources"]


def test_merge_llm_suggestion_rekeys_one_line_on_kind_change():
    # Deterministic match is 'decision' (title has "decide"); the LLM says
    # 'reply'. one_line must follow the LLM kind, not keep the decision verb.
    t = _make("Decide and reply to Karun")
    s = S.merge_llm_suggestion(
        t, {"action_kind": "reply", "goal_line": "A sent reply.",
            "specific_steps": ["Send: 'Hi Karun...'"]},
    )
    assert s["action_kind"] == "reply"
    assert "Make the call" not in s["one_line"]


def test_merge_llm_suggestion_robust_to_partial_result():
    t = _make("Approve the plan")
    base = build_suggestion(t)
    s = S.merge_llm_suggestion(
        t, {"goal_line": "", "specific_steps": [], "stop_conditions": "nope"},
    )
    assert s["steps"] == base["steps"]
    assert s["deliverable"] == base["deliverable"]
    assert s["stop_conditions"] == base["stop_conditions"]


def test_generate_prompt_includes_comments_block_when_present():
    prompt = generate_prompt(
        _make("Reply to Karun"),
        comments="[Karun, 2026-06-10] Can you send the export?",
    )
    assert "## Recent comments" in prompt
    assert "Can you send the export?" in prompt


# ── summary lead + deterministic delivery contract (briefing prompt) ─────────

def test_generate_prompt_leads_with_summary_when_present():
    # The LLM-enhanced summary_paragraph leads the copied prompt as the initial
    # analysis, right under the title and before the action line.
    t = _make("Build the inbox-match wire")
    s = S.merge_llm_suggestion(t, {
        "action_kind": "build",
        "goal_line": "A working inbox-match wire plus a runbook.",
        "specific_steps": ["Open app/wire.py and add match()"],
        "summary_paragraph": "This ticket wires inbox matching for reply counts. Hand back working code plus a .pdf runbook.",
    })
    prompt = generate_prompt(t, suggestion=s)
    assert "This ticket wires inbox matching" in prompt
    assert prompt.index("This ticket wires inbox matching") < prompt.index("This is a **build** task")


def test_generate_prompt_without_summary_still_has_summary_section():
    # No LLM story (deterministic fallback) => the Summary section is still there,
    # leading with what you hand back (no empty gap, action line moves to Details).
    prompt = generate_prompt(_make("Approve the budget"))
    assert prompt.startswith("# Approve the budget\n\n## Summary\n**You hand back:**")
    assert "This is a **decision** task" in prompt   # now in Details


def test_generate_prompt_has_delivery_contract():
    # Every copied prompt carries the deterministic delivery contract: Downloads
    # staging path, a confidence statement, and the ask-before-posting gate.
    prompt = generate_prompt(_make("Approve the budget"))
    assert "## Deliver, then confirm" in prompt
    assert "Downloads folder" in prompt
    assert "before you post anything to Basecamp" in prompt
    assert "confidence" in prompt.lower()


# ── predicted outputs (structured, colored, per-file confidence) ─────────────

def test_normalize_outputs_coerces_type_and_confidence():
    out = S.normalize_outputs([
        {"name": "deck.pptx", "type": "slides", "confidence": "90"},
        {"name": "notes.docx", "confidence": 200},          # over-cap + type inferred
        {"name": "  ", "type": "code"},                     # blank name dropped
        "runbook.md",                                       # bare string
    ])
    assert out == [
        {"name": "deck.pptx", "type": "slides", "confidence": 90},
        {"name": "notes.docx", "type": "doc", "confidence": 100},
        {"name": "runbook.md", "type": "doc", "confidence": 0},
    ]


def test_normalize_outputs_infers_type_from_extension_when_unknown():
    out = S.normalize_outputs([{"name": "engine.py", "type": "bogus"}])
    assert out[0]["type"] == "code"


def test_normalize_outputs_handles_empty_and_garbage():
    assert S.normalize_outputs(None) == []
    assert S.normalize_outputs([123, {"no_name": "x"}]) == []


def test_merge_llm_suggestion_carries_predicted_outputs():
    t = _make("Build the wire")
    s = S.merge_llm_suggestion(t, {
        "action_kind": "build", "goal_line": "A wire.", "specific_steps": ["do it"],
        "predicted_outputs": [{"name": "wire.py", "type": "code", "confidence": 80}],
    })
    assert s["predicted_outputs"] == [{"name": "wire.py", "type": "code", "confidence": 80}]


def test_build_suggestion_has_empty_predicted_outputs():
    assert build_suggestion(_make("Approve the budget"))["predicted_outputs"] == []


def test_generate_prompt_downloads_section_lists_files_when_enabled():
    t = _make("Build the wire")
    s = S.merge_llm_suggestion(t, {
        "action_kind": "build", "goal_line": "A wire.", "specific_steps": ["do it"],
        "predicted_outputs": [
            {"name": "wire.py", "type": "code", "confidence": 80},
            {"name": "runbook.md", "type": "doc", "confidence": 55},
        ],
    })
    prompt = generate_prompt(t, suggestion=s)                  # outputs_in_prompt defaults True
    assert "## Downloads" in prompt
    assert "Create these 2 files FIRST" in prompt
    assert "- wire.py (code, ~80% sure)" in prompt
    assert "- runbook.md (doc, ~55% sure)" in prompt


def test_generate_prompt_downloads_marker_when_disabled():
    # The workspace passes outputs_in_prompt=False (it injects the edited list
    # client-side into the [[DOWNLOADS]] slot), so the server prompt has the
    # marker, not the baked-in file list.
    t = _make("Build the wire")
    s = S.merge_llm_suggestion(t, {
        "action_kind": "build", "goal_line": "A wire.", "specific_steps": ["do it"],
        "predicted_outputs": [{"name": "wire.py", "type": "code", "confidence": 80}],
    })
    prompt = generate_prompt(t, suggestion=s, outputs_in_prompt=False)
    assert "## Downloads\n[[DOWNLOADS]]" in prompt
    assert "wire.py" not in prompt


def test_downloads_block_folder_rule_for_multiple_visuals():
    # More than one visual (image/html/diagram/slides) => group into one folder.
    block = S._downloads_block([
        {"name": "a.png", "type": "image", "confidence": 60},
        {"name": "b.html", "type": "html", "confidence": 60},
    ])
    assert "2 visuals: put them together in ONE named folder" in block
    # An html output pulls in the Colaberry HTML standard + the spec URL.
    assert S.HTML_FORMAT_URL in block


def test_generate_prompt_includes_qa_block():
    t = _make("Approve the resolver change")
    s = S.merge_llm_suggestion(t, {
        "action_kind": "decision", "goal_line": "A signed-off change.", "specific_steps": ["get it"],
        "qa_process": {"target": "the resolver PR", "checks": ["Run the test suite", "Lint + build"]},
    })
    prompt = generate_prompt(t, suggestion=s)
    assert "### Verify (QA) — the resolver PR" in prompt
    assert "- Run the test suite" in prompt


def test_normalize_qa_coerces_shapes():
    assert S.normalize_qa({"target": "x", "checks": ["a", "", "b"]}) == {"target": "x", "checks": ["a", "b"]}
    assert S.normalize_qa(["a", "b"]) == {"target": "", "checks": ["a", "b"]}
    assert S.normalize_qa("just one") == {"target": "", "checks": ["just one"]}
    assert S.normalize_qa(None) == {"target": "", "checks": []}


def test_normalize_outputs_supports_new_types():
    out = S.normalize_outputs([
        {"name": "dash.html"},        # ext -> html
        {"name": "flow.mmd"},         # ext -> diagram
        {"name": "assets/", "type": "folder"},
    ])
    assert [o["type"] for o in out] == ["html", "diagram", "folder"]


def test_generate_prompt_omits_comments_block_when_absent():
    assert "## Recent comments" not in generate_prompt(_make("Reply to Karun"))


# ── BC-description HTML → plain text for the copied prompt ──────────────────

def test_html_to_text_converts_bc_description():
    html = (
        "<div><strong>Why:</strong> build our own <em>lifecycle</em> engine.</div>"
        '<div>See <a href="https://x/y">transcript</a></div>'
        "<ul><li>API-in</li><li>API-out</li></ul>"
        "<div>cost &amp; risk</div>"
    )
    out = S._html_to_text(html)
    # No tag soup left.
    assert "<div>" not in out and "<strong>" not in out and "<a" not in out
    # Emphasis preserved as markdown.
    assert "**Why:**" in out
    assert "*lifecycle*" in out
    # Link becomes text + url; list items get bullets; entities decode.
    assert "transcript (https://x/y)" in out
    assert "- API-in" in out and "- API-out" in out
    assert "cost & risk" in out


def test_html_to_text_passthrough_and_empty():
    assert S._html_to_text("just plain text") == "just plain text"
    assert S._html_to_text("") == ""
    assert S._html_to_text("   ") == ""


def test_generate_prompt_description_is_plain_text_not_html():
    desc = "<div><strong>Done means:</strong> confirmed native.</div>"
    prompt = generate_prompt(_make("Approve the plan", desc=desc))
    assert "## Description" in prompt
    assert "<div>" not in prompt and "<strong>" not in prompt
    assert "**Done means:**" in prompt


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
