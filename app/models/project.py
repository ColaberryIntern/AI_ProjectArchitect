"""Pydantic models for project management."""

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    project_name: str = Field(..., min_length=1, max_length=200)
