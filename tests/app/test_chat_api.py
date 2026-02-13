"""Tests for the chat API endpoints."""

import pytest


class TestChatHistory:
    """Test GET /projects/{slug}/api/chat."""

    def test_get_empty_chat_returns_welcome(self, client, created_project):
        response = client.get(f"/projects/{created_project}/api/chat")
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
        assert len(data["messages"]) >= 1
        # First message should be from bot
        assert data["messages"][0]["role"] == "bot"
        assert "Tell me about" in data["messages"][0]["text"]

    def test_get_chat_after_message(self, client, created_project):
        # Simulate page load first (injects welcome message)
        client.get(f"/projects/{created_project}/api/chat")
        # Send a message
        client.post(
            f"/projects/{created_project}/api/chat",
            json={"message": "I want to build an app"},
        )
        # Get history
        response = client.get(f"/projects/{created_project}/api/chat")
        data = response.json()
        # Should have welcome + user message + bot response
        assert len(data["messages"]) >= 3


class TestChatMessage:
    """Test POST /projects/{slug}/api/chat."""

    def test_send_idea_advances_directly(self, client, created_project):
        response = client.post(
            f"/projects/{created_project}/api/chat",
            json={"message": "Build an AI-powered task manager for remote teams"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "bot_messages" in data
        assert len(data["bot_messages"]) > 0
        assert "field_updates" in data
        assert "raw_idea" in data["field_updates"]
        # Should advance directly to feature discovery (no confirmation step)
        assert data["reload"] is True
        assert data["redirect_url"] is not None
        assert "feature-discovery" in data["redirect_url"]

    def test_missing_message_returns_422(self, client, created_project):
        response = client.post(
            f"/projects/{created_project}/api/chat",
            json={},
        )
        assert response.status_code == 422

    def test_nonexistent_project_returns_404(self, client):
        response = client.post(
            "/projects/nonexistent/api/chat",
            json={"message": "hello"},
        )
        assert response.status_code == 404
