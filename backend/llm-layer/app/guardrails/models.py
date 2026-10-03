from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ToolMode = Literal["read", "write", "unknown"]
GuardrailStatus = Literal["allow", "deny", "clarify", "duplicate", "unauthorized", "invalid_confirmation"]


class GuardrailDecision(BaseModel):
    status: GuardrailStatus
    message: str


class ProductRequirement(BaseModel):
    """A required characteristic discovered from a product offering or specification."""

    key: str
    label: str


class ProductOfferingContext(BaseModel):
    """The selected offering and the required characteristics advertised by the platform."""

    offering_id: str | None = None
    offering_name: str = "Selected product"
    requirements: list[ProductRequirement] = Field(default_factory=list)


class DraftSummary(BaseModel):
    draft_id: str
    tool_name: str
    display_name: str
    normalized_arguments: dict[str, object] = Field(default_factory=dict)
    summary: str
    fingerprint: str
    requires_confirmation: bool = True


class ExecutionRecord(BaseModel):
    execution_token: str
    draft_id: str
    draft_fingerprint: str
    display_name: str
    idempotency_key: str
    tool_name: str
    result_preview: str = ""
