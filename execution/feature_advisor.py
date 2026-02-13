"""LLM-powered feature advisor for the Feature Discovery phase.

Drives a multi-select conversation that helps users discover and define
product features through AI-suggested options. Extracts features from
every conversation turn.

Falls back to static feature questions when the LLM is unavailable.
"""

import json
import re
from dataclasses import dataclass, field

from config.settings import LLM_ENABLED
from execution import llm_client

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FeatureAdvisorResponse:
    """Structured response from the feature advisor."""

    bot_message: str
    options: list[str] = field(default_factory=list)
    options_mode: str = "multi"
    is_complete: bool = False
    features_extracted: list[dict] = field(default_factory=list)
    fallback_used: bool = False


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

FEATURE_SYSTEM_PROMPT = """\
You are a product feature analyst who helps users discover and define features \
for their AI-powered product. You suggest specific capabilities and let the user \
pick the ones that fit.

## Your Mission
Have a focused conversation (3-6 turns) to identify the product's features. You have:
- The user's original project idea
- A summary of who it's for, what problem it solves, and how AI fits in

Your job is to suggest concrete product features and let the user select which ones matter.

## CRITICAL: Feature Extraction (EVERY TURN)
You MUST extract product features in EVERY response where capabilities are discussed.
- Return them in "features_extracted" as: [{"name": "Short name", "description": "One sentence"}]
- When the user selects capabilities from options, those ARE features — extract them
- When you suggest capabilities and the user agrees, extract those too
- Extract 1-4 NEW features per turn (not already in "Previously extracted features")
- By the end of discovery, you should have extracted 8-15 features total
- Features must be specific product capabilities, not abstract concepts
- If genuinely no new features emerge, return an empty list — but this should be rare

## CRITICAL RULES FOR OPTIONS
- The "options" field MUST ALWAYS contain 3-5 choices. NEVER return an empty array.
- Options must be specific product features — not generic labels
- Do NOT include an "Other" option — the user can always type their own answer
- DEFAULT to "multi" mode (checkboxes) so users can pick several features at once
- Options should inspire the user — show them what's possible for THEIR specific product

## How to Suggest Features
- Suggest 3-5 concrete features per turn as options
- Reference the user's SPECIFIC idea and ideation context in every suggestion
- Suggest AI-powered capabilities: adaptive algorithms, anomaly detection, predictive analytics, \
NLP-powered search, recommendation engines, auto-categorization, content generation, \
sentiment analysis, workflow automation, computer vision, conversational AI, etc.
- Also suggest non-AI features: user management, dashboards, notifications, integrations, etc.
- Build on what they've already selected — don't repeat features

### DO (good examples):
- "Based on your AI training platform for individual learners, here are some core features \
to consider. Which ones are essential?"
- "Great choices! Now let's think about the **user experience** features. Which of these \
would make your platform stand out?"

### DO NOT (anti-patterns):
- Suggesting only AI features — products need practical features too
- Repeating features the user already selected
- Suggesting abstract concepts like "good UX" instead of specific features

## Response Format (JSON only)
You MUST respond with a JSON object. Example:

{"bot_message": "For your AI training platform, here are some **core capabilities** to consider. \
Which ones are must-haves?", \
"options": ["Resume/LinkedIn analysis for skill assessment", "Personalized learning path generator", \
"AI-powered quiz creation", "Progress dashboard with analytics", "Real-time chat tutor"], \
"options_mode": "multi", "is_complete": false, \
"features_extracted": [{"name": "Resume analysis", "description": "Analyzes uploaded resumes to assess current skills"}]}

### Field rules:
- **bot_message**: Friendly, specific to their product. Use **bold** for emphasis.
- **options**: 3-5 feature suggestions as checkboxes. MUST NOT be empty.
- **options_mode**: Always "multi" (checkboxes for feature selection).
- **is_complete**: true when 8+ features have been extracted OR the user signals they're done.
- **features_extracted**: MUST extract features discussed or selected this turn.

## Pacing
- Turn 1: Suggest core product features based on the idea
- Turn 2-3: Suggest AI-powered and UX features
- Turn 4-5: Fill in gaps (integrations, analytics, admin features)
- Turn 6+: If 8+ features extracted, set is_complete=true
"""


# ---------------------------------------------------------------------------
# Static fallback questions (used when LLM is unavailable)
# ---------------------------------------------------------------------------

_FALLBACK_STEPS = [
    {
        "bot_message": (
            "**What core features does your product need?**\n\n"
            "Select all that apply:"
        ),
        "options": [
            "User registration & profiles",
            "Dashboard with analytics",
            "AI-powered recommendations",
            "Content creation tools",
            "Search & filtering",
        ],
        "options_mode": "multi",
    },
    {
        "bot_message": (
            "**What AI capabilities would add the most value?**\n\n"
            "Select all that interest you:"
        ),
        "options": [
            "Natural language processing",
            "Predictive analytics",
            "Content generation",
            "Pattern recognition",
            "Personalization engine",
        ],
        "options_mode": "multi",
    },
    {
        "bot_message": (
            "**What about user experience features?**\n\n"
            "Select all that matter:"
        ),
        "options": [
            "Real-time notifications",
            "Progress tracking",
            "Collaboration tools",
            "Mobile-responsive design",
            "Export/download capabilities",
        ],
        "options_mode": "multi",
    },
    {
        "bot_message": (
            "**Any integration or admin features needed?**\n\n"
            "Select all that apply:"
        ),
        "options": [
            "Third-party API integrations",
            "Admin management panel",
            "Reporting & export tools",
            "Role-based access control",
        ],
        "options_mode": "multi",
    },
]


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------

def build_feature_messages(
    idea: str,
    ideation_summary: str,
    chat_history: list[dict],
    extracted_features: list[dict] | None = None,
) -> list[dict]:
    """Build the messages list for the LLM API call.

    Args:
        idea: The user's original project idea.
        ideation_summary: Summary from ideation (4 dimensions).
        chat_history: List of feature-discovery-phase chat messages.
        extracted_features: Features already extracted (to avoid duplicates).

    Returns:
        List of message dicts for the API call.
    """
    turn_number = len(chat_history) // 2 + 1

    features_section = ""
    if extracted_features:
        feature_lines = [f"- {f['name']}: {f.get('description', '')}" for f in extracted_features]
        features_section = (
            "\n=== PREVIOUSLY EXTRACTED FEATURES ===\n"
            + "\n".join(feature_lines)
            + "\n"
        )

    context = (
        f"=== USER'S PROJECT IDEA ===\n"
        f"{idea}\n\n"
        f"=== IDEATION SUMMARY ===\n"
        f"{ideation_summary}\n\n"
        f"=== CONVERSATION PROGRESS ===\n"
        f"Turn number: {turn_number}\n"
        + features_section
        + "\n"
        f"=== INSTRUCTION ===\n"
        f"Suggest 3-5 concrete product features as multi-select options. "
        f"Extract features from the user's selections. "
        f"Reference their specific idea and context."
    )

    messages = []

    if chat_history:
        first_msg = chat_history[0]
        if first_msg["role"] == "user":
            messages.append({
                "role": "user",
                "content": f"[Context]\n{context}\n\n[User message]\n{first_msg['text']}",
            })
            for msg in chat_history[1:]:
                role = "user" if msg["role"] == "user" else "assistant"
                messages.append({"role": role, "content": msg["text"]})
        else:
            messages.append({
                "role": "user",
                "content": f"[Context]\n{context}\n\nPlease start the feature discovery conversation.",
            })
            for msg in chat_history:
                role = "user" if msg["role"] == "user" else "assistant"
                messages.append({"role": role, "content": msg["text"]})
    else:
        messages.append({
            "role": "user",
            "content": f"[Context]\n{context}\n\nPlease start the feature discovery conversation.",
        })

    messages = _ensure_alternating(messages)
    return messages


def _ensure_alternating(messages: list[dict]) -> list[dict]:
    """Ensure messages alternate between user and assistant roles."""
    if not messages:
        return messages

    result = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == result[-1]["role"]:
            result[-1]["content"] += "\n\n" + msg["content"]
        else:
            result.append(msg)

    if result and result[0]["role"] != "user":
        result.insert(0, {"role": "user", "content": "Please continue."})

    return result


# ---------------------------------------------------------------------------
# JSON response parsing
# ---------------------------------------------------------------------------

def _parse_feature_response(raw: str) -> dict | None:
    """Parse the LLM's JSON response, handling common formatting issues."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None
    if "bot_message" not in data:
        return None

    return data


def _dict_to_feature_response(data: dict) -> FeatureAdvisorResponse:
    """Convert a parsed JSON dict to a FeatureAdvisorResponse."""
    raw_features = data.get("features_extracted", [])
    features = []
    if isinstance(raw_features, list):
        for f in raw_features:
            if isinstance(f, dict) and f.get("name"):
                features.append({
                    "name": f["name"],
                    "description": f.get("description", ""),
                })
    return FeatureAdvisorResponse(
        bot_message=data.get("bot_message", ""),
        options=data.get("options", []),
        options_mode=data.get("options_mode", "multi"),
        is_complete=data.get("is_complete", False),
        features_extracted=features,
        fallback_used=False,
    )


def _ensure_options(response: FeatureAdvisorResponse) -> FeatureAdvisorResponse:
    """Ensure the response always has clickable options."""
    if response.is_complete:
        return response

    # Strip any "Other" variants the LLM may still generate
    response.options = [
        opt for opt in response.options
        if not opt.lower().startswith("other")
    ]

    if len(response.options) >= 2:
        return response

    response.options = [
        "AI-powered analytics",
        "User management system",
        "Integration capabilities",
        "Automated workflows",
    ]
    response.options_mode = "multi"
    return response


# ---------------------------------------------------------------------------
# Fallback logic
# ---------------------------------------------------------------------------

def get_feature_fallback_response(turn_number: int = 0) -> FeatureAdvisorResponse:
    """Return a static feature question based on the turn number.

    Used when the LLM is unavailable or returns an unparseable response.

    Args:
        turn_number: The current conversation turn (0-indexed).

    Returns:
        FeatureAdvisorResponse with a static question.
    """
    if turn_number >= len(_FALLBACK_STEPS):
        return FeatureAdvisorResponse(
            bot_message="I've suggested all the feature categories I can think of. Let me compile your selections.",
            is_complete=True,
            fallback_used=True,
        )

    step = _FALLBACK_STEPS[turn_number]
    return FeatureAdvisorResponse(
        bot_message=step["bot_message"],
        options=step["options"],
        options_mode=step["options_mode"],
        fallback_used=True,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_feature_response(
    idea: str,
    ideation_summary: str,
    chat_history: list[dict],
    extracted_features: list[dict] | None = None,
) -> FeatureAdvisorResponse:
    """Get the next feature discovery conversation response.

    Calls the LLM for dynamic feature suggestions. Falls back to
    static questions if the LLM is unavailable or returns bad data.

    Args:
        idea: The user's original project idea.
        ideation_summary: Summary from ideation (4 dimensions).
        chat_history: List of feature-phase chat messages.
        extracted_features: Features already extracted (to avoid duplicates).

    Returns:
        FeatureAdvisorResponse with the bot's next message and any features.
    """
    turn_number = len(chat_history) // 2

    if not LLM_ENABLED or not llm_client.is_available():
        return get_feature_fallback_response(turn_number)

    try:
        messages = build_feature_messages(
            idea, ideation_summary, chat_history, extracted_features,
        )
        llm_response = llm_client.chat(
            system_prompt=FEATURE_SYSTEM_PROMPT,
            messages=messages,
            response_format={"type": "json_object"},
        )

        parsed = _parse_feature_response(llm_response.content)
        if parsed is None:
            return get_feature_fallback_response(turn_number)

        return _ensure_options(_dict_to_feature_response(parsed))

    except (llm_client.LLMUnavailableError, llm_client.LLMClientError):
        return get_feature_fallback_response(turn_number)


# ---------------------------------------------------------------------------
# Retroactive feature extraction (safety net)
# ---------------------------------------------------------------------------

_FEATURE_EXTRACTION_PROMPT = """\
You are a product analyst. Given the conversation below about a project idea, \
extract ALL concrete product features that were discussed, suggested, or agreed upon.

Return a JSON object with a single key "features":
{"features": [{"name": "Short feature name", "description": "One sentence about what it does"}, ...]}

Rules:
- Extract 5-15 features from the conversation
- Features must be specific product capabilities (not abstract concepts like "good UX")
- Include features the user selected, agreed to, or discussed positively
- Include features the advisor suggested that weren't rejected
- Each feature name should be 2-5 words
"""


def extract_features_from_conversation(
    idea: str,
    chat_history: list[dict],
) -> list[dict]:
    """Extract features retroactively from a completed feature conversation.

    Used as a safety net when the per-turn extraction didn't capture features.

    Args:
        idea: The user's original project idea.
        chat_history: List of feature-phase chat messages.

    Returns:
        List of feature dicts: [{"name": "...", "description": "..."}, ...]
    """
    if not LLM_ENABLED or not llm_client.is_available():
        return []

    if not chat_history:
        return []

    conversation_text = f"PROJECT IDEA: {idea}\n\nCONVERSATION:\n"
    for msg in chat_history:
        role = "User" if msg["role"] == "user" else "Advisor"
        conversation_text += f"{role}: {msg['text']}\n\n"

    try:
        llm_response = llm_client.chat(
            system_prompt=_FEATURE_EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": conversation_text}],
            response_format={"type": "json_object"},
        )

        text = llm_response.content.strip()
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            return []

        if not isinstance(data, dict):
            return []

        raw_features = data.get("features", [])
        features = []
        if isinstance(raw_features, list):
            for f in raw_features:
                if isinstance(f, dict) and f.get("name"):
                    features.append({
                        "name": f["name"],
                        "description": f.get("description", ""),
                    })
        return features

    except (llm_client.LLMUnavailableError, llm_client.LLMClientError):
        return []
