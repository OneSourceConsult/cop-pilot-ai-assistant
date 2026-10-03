from __future__ import annotations

import hashlib
import json

from app.config import AppSettings
from app.guardrails.client import GuardrailClient
from app.guardrails.models import DraftSummary, ExecutionRecord, GuardrailDecision, ProductOfferingContext, ToolMode
from app.order_requirements import missing_requirement_keys
from app.id_utils import uuid7


class MockGuardrailClient(GuardrailClient):
    def __init__(self, settings: AppSettings):
        self._settings = settings

    def classify_tool(self, tool_name: str) -> ToolMode:
        if tool_name in self._settings.product_read_tool_names_set:
            return "read"
        return "write"

    def validate_product_selection(self, tool_name: str, arguments: dict[str, object]) -> GuardrailDecision:
        return GuardrailDecision(status="allow", message="The product-order policy accepted this draft.")

    def validate_order_parameters(
        self,
        tool_name: str,
        arguments: dict[str, object],
        offering_context: ProductOfferingContext | None,
    ) -> GuardrailDecision:
        if tool_name != "createProductOrder" or offering_context is None:
            return GuardrailDecision(status="allow", message="No offering-specific parameters need validation.")

        missing = missing_requirement_keys(arguments, offering_context.requirements)
        if missing:
            labels = ", ".join(requirement.label for requirement in missing)
            return GuardrailDecision(
                status="clarify",
                message=f"The selected offering requires: {labels}. Please provide the missing value(s).",
            )
        return GuardrailDecision(status="allow", message="The selected offering's required parameters are complete.")

    def build_product_order_draft(self, tool_name: str, arguments: dict[str, object], thread_id: str) -> DraftSummary:
        normalized = self._normalize_arguments(arguments)
        payload = json.dumps({"tool_name": tool_name, "arguments": normalized}, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        display_name = str(
            normalized.get("productOfferingName")
            or normalized.get("productName")
            or normalized.get("name")
            or "Selected product"
        )
        summary = (
            f"Prepare product order via `{tool_name}` for {display_name} with "
            f"{len(normalized)} parameter(s)."
        )
        return DraftSummary(
            draft_id=f"draft-{thread_id}-{fingerprint[:8]}",
            tool_name=tool_name,
            display_name=display_name,
            normalized_arguments=normalized,
            summary=summary,
            fingerprint=fingerprint,
            requires_confirmation=True,
        )

    def authorize_execution(self, draft: DraftSummary, thread_id: str) -> GuardrailDecision:
        return GuardrailDecision(status="allow", message="The product-order policy authorized execution.")

    def check_idempotency(
        self,
        thread_id: str,
        draft_fingerprint: str,
        existing_execution: ExecutionRecord | None,
    ) -> GuardrailDecision:
        if existing_execution and existing_execution.draft_fingerprint == draft_fingerprint:
            return GuardrailDecision(
                status="duplicate",
                message="This product order draft has already been executed for the current conversation.",
            )
        return GuardrailDecision(status="allow", message="No duplicate execution was detected.")

    def register_execution(self, thread_id: str, draft: DraftSummary, idempotency_key: str) -> ExecutionRecord:
        return ExecutionRecord(
            execution_token=uuid7(),
            draft_id=draft.draft_id,
            draft_fingerprint=draft.fingerprint,
            display_name=draft.display_name,
            idempotency_key=idempotency_key,
            tool_name=draft.tool_name,
        )

    @staticmethod
    def _normalize_arguments(arguments: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in arguments.items() if key not in {"guardrail_outcome"}}
