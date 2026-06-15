from __future__ import annotations

from abc import ABC, abstractmethod

from app.guardrails.models import DraftSummary, ExecutionRecord, GuardrailDecision, ToolMode


class GuardrailClient(ABC):
    @abstractmethod
    def classify_tool(self, tool_name: str) -> ToolMode:
        raise NotImplementedError

    @abstractmethod
    def validate_product_selection(self, tool_name: str, arguments: dict[str, object]) -> GuardrailDecision:
        raise NotImplementedError

    @abstractmethod
    def build_product_order_draft(self, tool_name: str, arguments: dict[str, object], thread_id: str) -> DraftSummary:
        raise NotImplementedError

    @abstractmethod
    def authorize_execution(self, draft: DraftSummary, thread_id: str) -> GuardrailDecision:
        raise NotImplementedError

    @abstractmethod
    def check_idempotency(
        self,
        thread_id: str,
        draft_fingerprint: str,
        existing_execution: ExecutionRecord | None,
    ) -> GuardrailDecision:
        raise NotImplementedError

    @abstractmethod
    def register_execution(self, thread_id: str, draft: DraftSummary) -> ExecutionRecord:
        raise NotImplementedError
