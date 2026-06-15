from __future__ import annotations

from app.guardrails.models import DraftSummary, ExecutionRecord
from app.schemas import ErrorResponse, ExecutionResult, ProductOrderDraft


def build_error(code: str, message: str, *, retryable: bool = False, **details: object) -> ErrorResponse:
    return ErrorResponse(code=code, message=message, retryable=retryable, details=details)


def build_draft_response(draft: DraftSummary) -> ProductOrderDraft:
    return ProductOrderDraft(**draft.model_dump())


def build_execution_response(record: ExecutionRecord, status: str) -> ExecutionResult:
    return ExecutionResult(
        execution_token=record.execution_token,
        tool_name=record.tool_name,
        status=status,
        result_preview=record.result_preview,
    )
