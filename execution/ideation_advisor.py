"""LLM-powered ideation advisor (legacy — retained for potential future use).

Drives a personalized conversation that explores the user's project idea
and fills in the 4 ideation dimensions (business_model, user_problem,
ai_leverage, differentiation) through natural dialogue.

This phase focuses ONLY on understanding the idea — feature extraction
happens in the Feature Discovery phase (see feature_advisor.py).

Falls back to static questions when the LLM is unavailable.
"""

import json
import re
from dataclasses import dataclass, field

from config.settings import LLM_ENABLED
from execution import llm_client

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

DIMENSIONS = ["business_model", "user_problem", "ai_leverage", "differentiation"]


@dataclass
class AdvisorResponse:
    """Structured response from the ideation advisor."""

    bot_message: str
    options: list[str] = field(default_factory=list)
    options_mode: str = "single"
    dimension_updates: dict[str, str] = field(default_factory=dict)
    is_complete: bool = False
    synthesis: dict[str, str] | None = None
    fallback_used: bool = False


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a friendly project advisor who helps users clarify their project idea. \
You ask focused questions to understand what they want to build.

## Your Mission
Have a short, focused conversation (4-6 turns) to understand the user's idea. Your job is to:
1. Understand what they want to build and who it's for
2. Clarify the core problem it solves
3. Understand how AI fits into the solution
4. Identify what makes it different from alternatives
5. Ask 1-2 clear questions per turn — keep it conversational and simple

## CRITICAL RULES FOR OPTIONS
- The "options" field MUST ALWAYS contain 3-5 choices. NEVER return an empty array.
- Options must be specific to their idea — not generic labels
- Do NOT include an "Other" option — the user can always type their own answer
- Use "single" mode (radio buttons) — each question has one best answer
- Options should help the user think, not overwhelm them

## How to Ask Great Questions
- Ask 1-2 focused questions per response
- Reference their SPECIFIC idea in every question
- Keep options clear and mutually exclusive
- Build on what they've already told you — never repeat a topic

### DO NOT (anti-patterns):
- Asking more than 2 questions per turn
- "Give me a one-sentence summary of the business model" (never ask users to summarize)
- "Let's explore the user problem dimension" (never expose internal tracking names)
- Asking the same generic question you'd ask about ANY project
- Suggesting features or extracting features — that happens in the next phase

### DO (good examples):
- "For your AI training builder, who would primarily use this — **corporate training teams** \
or **individual learners** upskilling on their own?"
- "What's the biggest frustration your users face today — is it **finding relevant content**, \
**staying motivated**, or **tracking their progress**?"

## Response Format (JSON only)
You MUST respond with a JSON object. Example:

{"bot_message": "Great idea! For your AI training platform, who would primarily use this — \
corporate training teams managing employee development, or individual learners trying to \
upskill on their own?", \
"options": ["Corporate training teams", "Individual learners", "Educational institutions", \
"Both corporate and individual"], "options_mode": "single", \
"dimension_updates": {}, "is_complete": false, "synthesis": null}

### Field rules:
- **bot_message**: Friendly, specific to their idea. Use **bold** for emphasis. Ask 1-2 questions.
- **options**: 3-5 clickable choices. MUST NOT be empty.
- **options_mode**: Always "single" (radio buttons for clear choices).
- **dimension_updates**: When you have enough info about an area, summarize it yourself: \
{"business_model": "summary"}, {"user_problem": "summary"}, {"ai_leverage": "summary"}, \
or {"differentiation": "summary"}. You decide when an area is covered — don't ask the user.
- **is_complete**: true ONLY when ALL 4 areas are covered.
- **synthesis**: When complete, provide all 4: {"business_model": "...", "user_problem": "...", \
"ai_leverage": "...", "differentiation": "..."}.

## Internal Tracking (do NOT expose to user)
You are silently tracking 4 areas. NEVER mention these names to the user:
- business_model: Who benefits? Revenue model? Internal/external?
- user_problem: What pain point? What's slow/manual/broken today?
- ai_leverage: Which AI capabilities? What's impossible without AI?
- differentiation: What's the edge over alternatives?
Fill these naturally through conversation. Don't walk through them one by one.

## Pacing
- Turns 1-2: Understand the idea and who it's for
- Turns 3-4: Clarify the problem and AI angle
- Turns 5-6: Wrap up remaining areas, set is_complete=true
"""


# ---------------------------------------------------------------------------
# Static fallback questions (used when LLM is unavailable)
# ---------------------------------------------------------------------------

_FALLBACK_STEPS = {
    "business_model": {
        "bot_message": (
            "**Who is this product for?**\n\n"
            "Pick the best fit:"
        ),
        "options": [
            "Small business owners",
            "Enterprise teams",
            "Individual consumers",
            "Developers / technical users",
        ],
        "options_mode": "single",
    },
    "user_problem": {
        "bot_message": (
            "**What's the biggest problem they face?**\n\n"
            "Pick the closest match:"
        ),
        "options": [
            "Too much manual/repetitive work",
            "Data is scattered and hard to find",
            "Decisions take too long without good data",
            "Current tools are too complex or expensive",
        ],
        "options_mode": "single",
    },
    "ai_leverage": {
        "bot_message": (
            "**How would AI help most?**\n\n"
            "Pick the most valuable capability:"
        ),
        "options": [
            "Smart predictions & forecasting",
            "Natural language search & Q&A",
            "Personalized recommendations",
            "Content generation & summarization",
        ],
        "options_mode": "single",
    },
    "differentiation": {
        "bot_message": (
            "**What makes users choose yours over alternatives?**\n\n"
            "Pick the strongest differentiator:"
        ),
        "options": [
            "Faster & simpler than alternatives",
            "AI does what others can't",
            "Built for a specific niche no one serves well",
            "More affordable or accessible",
        ],
        "options_mode": "single",
    },
}


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------

def build_advisor_messages(
    idea: str,
    chat_history: list[dict],
    dimension_state: dict[str, dict],
) -> list[dict]:
    """Build the messages list for the LLM API call.

    Args:
        idea: The user's original project idea.
        chat_history: List of ideation-phase chat messages (role, text).
        dimension_state: Current state of each dimension (status, summary).

    Returns:
        List of message dicts for the API call.
    """
    # Build context preamble for the first message
    dim_status_lines = []
    for dim in DIMENSIONS:
        info = dimension_state.get(dim, {})
        status = info.get("status", "open")
        summary = info.get("summary")
        if status == "answered" and summary:
            dim_status_lines.append(f"- {dim}: ANSWERED — {summary}")
        else:
            dim_status_lines.append(f"- {dim}: NEEDS EXPLORATION")

    turn_number = len(chat_history) // 2 + 1

    context = (
        f"=== USER'S PROJECT IDEA ===\n"
        f"{idea}\n\n"
        f"=== CONVERSATION PROGRESS ===\n"
        f"Turn number: {turn_number}\n"
        + "\n".join(dim_status_lines)
        + "\n\n"
        f"=== INSTRUCTION ===\n"
        f"Ask 1-2 focused questions SPECIFIC to this idea — not generic questions. "
        f"Use single-select options with clear choices."
    )

    messages = []

    # First user message includes the context
    if chat_history:
        first_msg = chat_history[0]
        if first_msg["role"] == "user":
            messages.append({
                "role": "user",
                "content": f"[Context]\n{context}\n\n[User message]\n{first_msg['text']}",
            })
            # Add remaining messages
            for msg in chat_history[1:]:
                role = "user" if msg["role"] == "user" else "assistant"
                messages.append({"role": role, "content": msg["text"]})
        else:
            # First message is bot — start with context as user
            messages.append({
                "role": "user",
                "content": f"[Context]\n{context}\n\nPlease start the ideation conversation.",
            })
            for msg in chat_history:
                role = "user" if msg["role"] == "user" else "assistant"
                messages.append({"role": role, "content": msg["text"]})
    else:
        # No history — ask LLM to start
        messages.append({
            "role": "user",
            "content": f"[Context]\n{context}\n\nPlease start the ideation conversation.",
        })

    # Ensure messages alternate properly (API requirement)
    messages = _ensure_alternating(messages)

    return messages


def _ensure_alternating(messages: list[dict]) -> list[dict]:
    """Ensure messages alternate between user and assistant roles.

    The Anthropic API requires strict alternation. This merges consecutive
    same-role messages.
    """
    if not messages:
        return messages

    result = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == result[-1]["role"]:
            # Merge with previous
            result[-1]["content"] += "\n\n" + msg["content"]
        else:
            result.append(msg)

    # API requires first message to be from user
    if result and result[0]["role"] != "user":
        result.insert(0, {"role": "user", "content": "Please continue."})

    return result


# ---------------------------------------------------------------------------
# JSON response parsing
# ---------------------------------------------------------------------------

def _parse_llm_response(raw: str) -> dict | None:
    """Parse the LLM's JSON response, handling common formatting issues.

    Args:
        raw: Raw text from the LLM.

    Returns:
        Parsed dict, or None if parsing fails.
    """
    text = raw.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    # Validate required fields
    if not isinstance(data, dict):
        return None
    if "bot_message" not in data:
        return None

    return data


def _dict_to_advisor_response(data: dict) -> AdvisorResponse:
    """Convert a parsed JSON dict to an AdvisorResponse."""
    return AdvisorResponse(
        bot_message=data.get("bot_message", ""),
        options=data.get("options", []),
        options_mode=data.get("options_mode", "single"),
        dimension_updates=data.get("dimension_updates", {}),
        is_complete=data.get("is_complete", False),
        synthesis=data.get("synthesis"),
        fallback_used=False,
    )


def _ensure_options(response: AdvisorResponse) -> AdvisorResponse:
    """Ensure the response always has clickable options.

    If the LLM returned empty or missing options, generate contextual
    defaults so the user always sees clickable buttons.

    Args:
        response: The parsed advisor response.

    Returns:
        The response with guaranteed non-empty options (unless is_complete).
    """
    if response.is_complete:
        return response  # Synthesis step doesn't need options

    # Strip any "Other" variants the LLM may still generate
    response.options = [
        opt for opt in response.options
        if not opt.lower().startswith("other")
    ]

    if len(response.options) >= 2:
        return response

    # Options are missing or insufficient — provide contextual fallback
    response.options = [
        "Tell me more about this",
        "That sounds right",
        "I have a different idea",
    ]
    response.options_mode = "single"
    return response


# ---------------------------------------------------------------------------
# Fallback logic
# ---------------------------------------------------------------------------

def get_fallback_response(dimension_state: dict[str, dict]) -> AdvisorResponse:
    """Return a static question for the first unanswered dimension.

    Used when the LLM is unavailable or returns an unparseable response.

    Args:
        dimension_state: Current state of each dimension.

    Returns:
        AdvisorResponse with a static question.
    """
    for dim in DIMENSIONS:
        info = dimension_state.get(dim, {})
        if info.get("status") != "answered":
            step = _FALLBACK_STEPS[dim]
            return AdvisorResponse(
                bot_message=step["bot_message"],
                options=step["options"],
                options_mode=step["options_mode"],
                fallback_used=True,
            )

    # All dimensions answered — signal completion
    return AdvisorResponse(
        bot_message="All dimensions explored! Let me put together a summary.",
        is_complete=True,
        fallback_used=True,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_ideation_response(
    idea: str,
    chat_history: list[dict],
    dimension_state: dict[str, dict],
) -> AdvisorResponse:
    """Get the next ideation conversation response.

    Calls the LLM for a dynamic, personalized response. Falls back to
    static questions if the LLM is unavailable or returns bad data.

    Args:
        idea: The user's original project idea.
        chat_history: List of ideation-phase chat messages.
        dimension_state: Current state of each dimension.

    Returns:
        AdvisorResponse with the bot's next message and any updates.
    """
    if not LLM_ENABLED or not llm_client.is_available():
        return get_fallback_response(dimension_state)

    try:
        messages = build_advisor_messages(
            idea, chat_history, dimension_state,
        )
        llm_response = llm_client.chat(
            system_prompt=SYSTEM_PROMPT,
            messages=messages,
            response_format={"type": "json_object"},
        )

        parsed = _parse_llm_response(llm_response.content)
        if parsed is None:
            return get_fallback_response(dimension_state)

        return _ensure_options(_dict_to_advisor_response(parsed))

    except (llm_client.LLMUnavailableError, llm_client.LLMClientError):
        return get_fallback_response(dimension_state)
