"""Chat API routes for the conversational interface."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.chat_engine import get_step_definition, get_welcome_message, process_message
from app.dependencies import get_project_state
from execution.state_manager import (
    append_chat_message,
    get_chat_step,
    get_extracted_features,
    save_state,
)

router = APIRouter()


class ChatMessageRequest(BaseModel):
    message: str


@router.post("/api/chat")
async def send_chat_message(request: Request, slug: str, body: ChatMessageRequest):
    """Process a user chat message and return the bot response."""
    state = get_project_state(slug)
    result = process_message(state, slug, body.message)
    return JSONResponse(content=result)


@router.get("/api/chat")
async def get_chat_history(request: Request, slug: str):
    """Return the full chat history for restoring on page load."""
    state = get_project_state(slug)

    # Ensure chat key exists for older projects
    if "chat" not in state:
        state["chat"] = {
            "messages": [],
            "current_step": f"{state['current_phase']}.welcome",
            "context": {},
        }

    messages = state["chat"]["messages"]

    # If no messages yet, inject the welcome message
    if not messages:
        welcome = get_welcome_message(state)
        if welcome:
            append_chat_message(state, "bot", welcome)
            save_state(state, slug)
            messages = state["chat"]["messages"]

    response_data = {"messages": messages}

    # Include extracted features for feature_discovery phase
    current_phase = state.get("current_phase")
    if current_phase == "feature_discovery":
        extracted = get_extracted_features(state)
        if extracted:
            response_data["extracted_features"] = extracted

    return JSONResponse(content=response_data)
