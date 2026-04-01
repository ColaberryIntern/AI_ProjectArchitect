"""Pydantic models for the System Design Contract API response."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ContractProject(BaseModel):
    name: str
    slug: str
    created_at: str = ""
    updated_at: str = ""


class ContractFeature(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = ""
    layer: str = ""  # "functional" or "architectural"
    build_order: int | None = None


class ContractSkill(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = ""
    tags: list[str] = []


class ContractMCPServer(BaseModel):
    id: str
    name: str
    description: str = ""
    purpose: str = ""
    source_url: str = ""
    tags: list[str] = []


class ArchitectureSummary(BaseModel):
    style: str | None = None
    deployment_type: str | None = None
    ai_depth: str | None = None
    mvp_scope: str | None = None


class ValidationIssue(BaseModel):
    check: str
    severity: str  # "error" or "warning"
    message: str
    related_ids: list[str] = []


class ValidationResult(BaseModel):
    is_valid: bool
    issues: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    confidence: float = Field(ge=0.0, le=1.0)


class BuildReadinessResult(BaseModel):
    ready: bool
    missing_components: list[str] = []
    risk_level: Literal["low", "medium", "high"]
    details: dict = {}


class SystemDesignContract(BaseModel):
    project: ContractProject
    features: list[ContractFeature] = []
    skills: list[ContractSkill] = []
    architecture: ArchitectureSummary
    mcp_servers: list[ContractMCPServer] = []
    intelligence_goals: list[dict] = []
    validation: ValidationResult
    build_readiness: BuildReadinessResult
    generated_at: str
