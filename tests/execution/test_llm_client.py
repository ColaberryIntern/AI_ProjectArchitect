"""Tests for the LLM client wrapper."""

from unittest.mock import MagicMock, patch

import pytest

from execution.llm_client import (
    LLMClientError,
    LLMResponse,
    LLMUnavailableError,
    chat,
    is_available,
)


class TestIsAvailable:
    """Test API key availability check."""

    def test_available_with_key(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "sk-test-key")
        assert is_available() is True

    def test_unavailable_without_key(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "")
        assert is_available() is False

    def test_unavailable_with_none(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", None)
        assert is_available() is False


class TestChat:
    """Test the chat() function with mocked OpenAI SDK."""

    def test_raises_unavailable_without_key(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "")
        with pytest.raises(LLMUnavailableError, match="not configured"):
            chat("system", [{"role": "user", "content": "hello"}])

    def test_successful_call(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "sk-test")

        # Mock the OpenAI response structure
        mock_message = MagicMock()
        mock_message.content = "Hello back!"

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o-mini"
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = chat(
                system_prompt="You are helpful.",
                messages=[{"role": "user", "content": "Hello"}],
            )

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello back!"
        assert result.model == "gpt-4o-mini"
        assert result.usage["prompt_tokens"] == 10
        assert result.usage["completion_tokens"] == 5
        assert result.stop_reason == "stop"

    def test_uses_default_settings(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr("execution.llm_client.LLM_MODEL", "test-model")
        monkeypatch.setattr("execution.llm_client.LLM_MAX_TOKENS", 512)
        monkeypatch.setattr("execution.llm_client.LLM_TEMPERATURE", 0.5)

        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "test-model"
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            chat("system", [{"role": "user", "content": "test"}])

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["max_tokens"] == 512
        assert call_kwargs["temperature"] == 0.5

    def test_system_prompt_prepended_as_message(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "sk-test")

        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o-mini"
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            chat("Be helpful.", [{"role": "user", "content": "Hi"}])

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "Be helpful."}
        assert messages[1] == {"role": "user", "content": "Hi"}

    def test_custom_params_override_defaults(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "sk-test")

        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "custom-model"
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            chat(
                "system",
                [{"role": "user", "content": "test"}],
                model="custom-model",
                max_tokens=256,
                temperature=0.2,
            )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "custom-model"
        assert call_kwargs["max_tokens"] == 256
        assert call_kwargs["temperature"] == 0.2

    def test_api_error_raises_client_error(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "sk-test")

        mock_openai = MagicMock()
        mock_openai.APIError = type("APIError", (Exception,), {})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_openai.APIError("rate limit")
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            with pytest.raises(LLMClientError, match="OpenAI API error"):
                chat("system", [{"role": "user", "content": "test"}])

    def test_generic_error_raises_client_error(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "sk-test")

        mock_openai = MagicMock()
        mock_openai.APIError = type("APIError", (Exception,), {})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ConnectionError("network down")
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            with pytest.raises(LLMClientError, match="LLM call failed"):
                chat("system", [{"role": "user", "content": "test"}])

    def test_response_format_passed_to_api(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "sk-test")

        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o-mini"
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            chat(
                "system",
                [{"role": "user", "content": "test"}],
                response_format={"type": "json_object"},
            )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_response_format_not_sent_when_none(self, monkeypatch):
        monkeypatch.setattr("execution.llm_client.OPENAI_API_KEY", "sk-test")

        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o-mini"
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            chat("system", [{"role": "user", "content": "test"}])

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "response_format" not in call_kwargs
