"""Chat conversation engine for deterministic pipeline.

Drives a ChatGPT-like conversational interface. Phase 1 (idea intake) captures
a single free-text idea. Phase 2 (feature discovery) is form-based with an
optional chat assistant. Later phases use scripted guidance tips.
"""

from app.dependencies import PHASE_URLS
from execution.state_manager import (
    add_feature,
    advance_phase,
    append_chat_message,
    approve_features,
    get_chat_step,
    get_extracted_features,
    record_idea,
    save_state,
    set_chat_step,
)


# ---------------------------------------------------------------------------
# Welcome messages per phase (shown once on page load, before any user input)
# ---------------------------------------------------------------------------

PHASE_WELCOME = {
    "idea_intake": (
        "Tell me about the project you want to build."
    ),
    "feature_discovery": (
        "Your idea has been captured! Use the feature catalog "
        "to select the features you need."
    ),
    "outline_generation": (
        "You're building the document outline. Each of the 7 sections needs "
        "a title and a brief summary. I can help if you have questions."
    ),
    "outline_approval": (
        "Review the outline above. When you're happy with it, lock it to "
        "begin writing chapters."
    ),
    "chapter_build": (
        "Time to write! Select a chapter from the sidebar and fill in "
        "the Purpose, Design Intent, and Implementation Guidance."
    ),
    "quality_gates": (
        "Run the quality gates to check your document for completeness, "
        "clarity, and build readiness."
    ),
    "final_assembly": (
        "Almost done! Review the pre-assembly checklist, then click "
        "Assemble to generate your final build guide."
    ),
    "complete": (
        "Congratulations! Your build guide is ready. "
        "You can download it using the button on the left."
    ),
}


# ---------------------------------------------------------------------------
# Confirmation detection
# ---------------------------------------------------------------------------

_YES_WORDS = {"yes", "yep", "yeah", "y", "correct", "right", "confirm", "looks good",
              "that's right", "thats right", "ok", "okay", "sure", "perfect", "good",
              "approved", "approve", "done", "lgtm"}

_DONE_WORDS = {"done", "that's all", "thats all", "no more", "nothing else",
               "none", "nope", "n", "skip", "no",
               "done - review features", "done - lock and continue",
               "done - no optional features", "done - approve features"}

LOCK_SIGNAL = "__LOCK_FEATURES__"


def _is_confirmation(text: str) -> bool:
    """Check if user text is an affirmative confirmation."""
    cleaned = text.strip().lower().rstrip("!.,")
    return cleaned in _YES_WORDS


def _is_done(text: str) -> bool:
    """Check if user text signals 'done' / 'no more'."""
    cleaned = text.strip().lower().rstrip("!.,")
    return cleaned in _DONE_WORDS


# ---------------------------------------------------------------------------
# Phase 1: Idea Intake flow
# ---------------------------------------------------------------------------

IDEA_INTAKE_FLOW = {
    "idea_intake.welcome": {
        "type": "extract_idea",
    },
}


# ---------------------------------------------------------------------------
# Phase 2: Feature Discovery flow (form-based with guidance chat)
# ---------------------------------------------------------------------------

FEATURE_DISCOVERY_FLOW = {
    "feature_discovery.welcome": {
        "type": "guidance",
        "phase": "feature_discovery",
        "tips": [
            "Select features from the catalog on the left. Check all that apply!",
            "Click **Show Me More Features** to see additional options.",
            "When you're done, click **Save & Continue** to move to Outline Generation.",
        ],
    },
}


# ---------------------------------------------------------------------------
# Phase 3-8: Guidance flows (chat as assistant, not driver)
# ---------------------------------------------------------------------------

GUIDANCE_TIPS = {
    "outline_generation": [
        "Each section needs a clear **title** and a 1-2 sentence **summary**.",
        "Tip: Section titles should be action-oriented (e.g., 'Authentication System' not 'Section 3').",
        "Make sure each section maps to at least one core feature.",
        "When you're done editing sections, click **Validate** to check for issues.",
    ],
    "outline_approval": [
        "Review the outline carefully — once locked, sections become chapters.",
        "If something feels off, use **Unlock** to make changes before locking.",
        "A locked outline generates chapter entries automatically.",
    ],
    "chapter_build": [
        "Each chapter has 3 fields: **Purpose**, **Design Intent**, and **Implementation Guidance**.",
        "Keep each field focused — Purpose explains why, Design explains how, Guidance gives specifics.",
        "Quality gates run automatically when you submit a chapter.",
        "All chapters must be approved before moving to the next phase.",
    ],
    "quality_gates": [
        "Quality gates check: **Completeness**, **Clarity**, **Build Readiness**, **Anti-Vagueness**, and the **Intern Test**.",
        "If a gate fails, review the specific issues and revise the affected chapters.",
        "All 5 gates must pass before you can advance to final assembly.",
    ],
    "final_assembly": [
        "The checklist shows three requirements: all chapters approved, quality gates passed, and outline integrity.",
        "Once all checks are green, click **Assemble** to generate the final document.",
        "You can download the assembled build guide as a Markdown file.",
    ],
    "complete": [
        "Your build guide is ready! Click the download button to save it.",
        "You can always return to this page to download it again.",
    ],
}

GUIDANCE_FLOW = {}
for phase, tips in GUIDANCE_TIPS.items():
    GUIDANCE_FLOW[f"{phase}.welcome"] = {
        "type": "guidance",
        "phase": phase,
        "tips": tips,
    }


# ---------------------------------------------------------------------------
# Engine core
# ---------------------------------------------------------------------------

ALL_FLOWS = [IDEA_INTAKE_FLOW, FEATURE_DISCOVERY_FLOW, GUIDANCE_FLOW]


def get_welcome_message(state: dict) -> str | None:
    """Return the welcome bot message for the current phase."""
    phase = state.get("current_phase", "idea_intake")
    return PHASE_WELCOME.get(phase)


def process_message(state: dict, slug: str, user_message: str) -> dict:
    """Process a user chat message and return the bot response."""
    append_chat_message(state, "user", user_message)

    # Fast-track: lock features and advance to outline generation
    if user_message == LOCK_SIGNAL:
        result = _handle_lock_features(state, slug)
        save_state(state, slug)
        return result

    step_id = get_chat_step(state)
    step = _get_step(step_id)

    if step is None:
        bot_msg = "I'm not sure what to do here. Try using the form on the left."
        append_chat_message(state, "bot", bot_msg)
        save_state(state, slug)
        return _response([bot_msg])

    result = _execute_step(state, slug, step_id, step, user_message)
    save_state(state, slug)
    return result


def _get_step(step_id: str) -> dict | None:
    """Look up a step definition by its ID across all flows."""
    for flow in ALL_FLOWS:
        if step_id in flow:
            return flow[step_id]
    return None


def get_step_definition(step_id: str) -> dict | None:
    """Public accessor for step definitions (used by chat API for pending_options)."""
    return _get_step(step_id)


def _execute_step(state: dict, slug: str, step_id: str, step: dict, user_message: str) -> dict:
    """Execute a single conversation step based on its type."""
    step_type = step.get("type")

    if step_type == "extract_idea":
        return _handle_extract_idea(state, step, user_message, slug)

    if step_type == "confirmation":
        return _handle_confirmation(state, step, user_message, slug)

    if step_type == "guidance":
        return _handle_guidance(state, step, user_message)

    # Fallback: static bot message
    if "bot_message" in step:
        bot_msg = step["bot_message"]
        append_chat_message(state, "bot", bot_msg)
        if step.get("next_step"):
            set_chat_step(state, step["next_step"])
        return _response([bot_msg])

    return _response(["I'm not sure how to handle that. Try the form on the left."])


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------

def _handle_extract_idea(state, step, user_message, slug):
    """Phase 1: Capture idea and advance directly to feature discovery."""
    record_idea(state, user_message)
    advance_phase(state, "feature_discovery")
    set_chat_step(state, "feature_discovery.welcome")

    bot_msg = (
        "Got it! I've captured your project idea. "
        "Head over to the feature catalog to select what your product needs!"
    )
    append_chat_message(state, "bot", bot_msg)
    redirect_url = f"/projects/{slug}/{PHASE_URLS['feature_discovery']}"
    return _response(
        [bot_msg], {"raw_idea": user_message},
        reload=True, redirect_url=redirect_url,
    )


def _handle_confirmation(state, step, user_message, slug):
    """Handle a yes/no confirmation step."""
    reload = False
    redirect_url = None
    if _is_confirmation(user_message):
        bot_msg = step["confirmed_message"]
        append_chat_message(state, "bot", bot_msg)
        set_chat_step(state, step["confirmed_next"])
    else:
        bot_msg = step["denied_message"]
        append_chat_message(state, "bot", bot_msg)
        set_chat_step(state, step["denied_next"])

    return _response([bot_msg], reload=reload, redirect_url=redirect_url)


def _handle_lock_features(state: dict, slug: str) -> dict:
    """Fast-track: lock current features and advance to outline generation.

    Works from feature_discovery phase only. Approves core features
    and advances to outline_generation. Blocks if no features exist.
    """
    phase = state.get("current_phase")

    if phase != "feature_discovery":
        bot_msg = "Lock is only available during Feature Discovery."
        append_chat_message(state, "bot", bot_msg)
        return _response([bot_msg])

    # Block if no features exist
    if not state["features"]["core"]:
        bot_msg = (
            "I can't lock yet — there are no features selected. "
            "Please select features from the catalog first."
        )
        append_chat_message(state, "bot", bot_msg)
        return _response([bot_msg])

    approve_features(state)
    advance_phase(state, "outline_generation")

    core_count = len(state["features"]["core"])
    optional_count = len(state["features"]["optional"])
    bot_msg = (
        f"Features locked! {core_count} core"
        + (f" and {optional_count} optional" if optional_count else "")
        + " features approved.\n\nMoving to **Outline Generation**."
    )
    append_chat_message(state, "bot", bot_msg)
    set_chat_step(state, "outline_generation.welcome")
    redirect_url = f"/projects/{slug}/{PHASE_URLS['outline_generation']}"
    return _response([bot_msg], reload=True, redirect_url=redirect_url)


def _handle_guidance(state, step, user_message):
    """Phase 2-8: Provide contextual tips and guidance."""
    tips = step.get("tips", [])
    # Cycle through tips based on how many user messages we've seen
    phase_msg_count = sum(
        1 for m in state["chat"]["messages"]
        if m["role"] == "user"
    )
    tip_index = (phase_msg_count - 1) % len(tips) if tips else 0
    bot_msg = tips[tip_index] if tips else "Use the form on the left to continue."
    append_chat_message(state, "bot", bot_msg)
    return _response([bot_msg])


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def _response(
    bot_messages: list[str],
    field_updates: dict | None = None,
    actions: list[str] | None = None,
    reload: bool = False,
    redirect_url: str | None = None,
    options: list[str] | None = None,
    options_mode: str = "single",
    extracted_features: list[dict] | None = None,
    show_lock_hint: bool = False,
) -> dict:
    """Build a standardized response dict."""
    result = {
        "bot_messages": bot_messages,
        "field_updates": field_updates or {},
        "actions": actions or [],
        "reload": reload,
        "redirect_url": redirect_url,
    }
    if options:
        result["options"] = options
        result["options_mode"] = options_mode
    if extracted_features is not None:
        result["extracted_features"] = extracted_features
    if show_lock_hint:
        result["show_lock_hint"] = True
    return result
