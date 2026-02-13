"""Thin wrapper around the OpenAI SDK for LLM calls.

Provides a simple interface for sending messages to GPT models.
All business logic lives elsewhere — this module only handles
the API transport, error wrapping, and availability checks.
"""

from dataclasses import dataclass

from config.settings import LLM_MAX_TOKENS, LLM_MODEL, LLM_TEMPERATURE, OPENAI_API_KEY


class LLMUnavailableError(Exception):
    """Raised when the LLM service is not configured or reachable."""


class LLMClientError(Exception):
    """Raised when the LLM API returns an error."""


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""

    content: str
    model: str
    usage: dict
    stop_reason: str


def is_available() -> bool:
    """Check if the OpenAI API key is configured."""
    return bool(OPENAI_API_KEY)


def chat(
    system_prompt: str,
    messages: list[dict],
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    response_format: dict | None = None,
) -> LLMResponse:
    """Send a conversation to the OpenAI API and return the response.

    Args:
        system_prompt: The system instruction for the conversation.
        messages: List of message dicts with 'role' and 'content' keys.
        model: Model to use (defaults to LLM_MODEL from settings).
        max_tokens: Max tokens in response (defaults to LLM_MAX_TOKENS).
        temperature: Sampling temperature (defaults to LLM_TEMPERATURE).

    Returns:
        LLMResponse with the assistant's reply.

    Raises:
        LLMUnavailableError: If no API key is configured.
        LLMClientError: If the API call fails.
    """
    if not is_available():
        raise LLMUnavailableError("OPENAI_API_KEY is not configured")

    try:
        import openai
    except ImportError as e:
        raise LLMUnavailableError(
            "openai package is not installed. Run: pip install openai"
        ) from e

    model = model or LLM_MODEL
    max_tokens = max_tokens or LLM_MAX_TOKENS
    temperature = temperature if temperature is not None else LLM_TEMPERATURE

    # Build OpenAI messages: system prompt goes as a system message
    openai_messages = [{"role": "system", "content": system_prompt}]
    openai_messages.extend(messages)

    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        create_kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": openai_messages,
        }
        if response_format is not None:
            create_kwargs["response_format"] = response_format
        response = client.chat.completions.create(**create_kwargs)
    except openai.APIError as e:
        raise LLMClientError(f"OpenAI API error: {e}") from e
    except Exception as e:
        raise LLMClientError(f"LLM call failed: {e}") from e

    choice = response.choices[0]
    return LLMResponse(
        content=choice.message.content,
        model=response.model,
        usage={
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
        },
        stop_reason=choice.finish_reason,
    )
