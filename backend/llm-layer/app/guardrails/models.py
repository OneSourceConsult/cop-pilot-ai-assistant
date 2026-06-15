from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ToolMode = Literal["read", "write", "unknown"]
GuardrailStatus = Literal["allow", "deny", "clarify", "duplicate", "unauthorized", "invalid_confirmation"]


class GuardrailDecision(BaseModel):
    status: GuardrailStatus
    message: str


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
    draft_fingerprint: str
    tool_name: str
    result_preview: str = ""
