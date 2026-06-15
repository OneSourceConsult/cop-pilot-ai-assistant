from typing import Literal

from pydantic import BaseModel, Field, model_validator


ChatStatus = Literal["ready", "needs_confirmation", "blocked", "executed", "error"]
PendingAction = Literal["confirm_product_order"]
McpConnectionStatus = Literal["reachable", "unavailable"]


class ProductOrderDraft(BaseModel):
    """Normalized draft returned before a guarded product-order write is executed."""

    draft_id: str
    tool_name: str
    display_name: str
    normalized_arguments: dict[str, object] = Field(default_factory=dict)
    summary: str
    fingerprint: str
    requires_confirmation: bool = True


class ExecutionResult(BaseModel):
    """Execution outcome for a confirmed write operation."""

    execution_token: str
    tool_name: str
    status: str
    result_preview: str = ""


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str | None = None
    reset: bool = False


class ConfirmRequest(BaseModel):
    confirmed: bool


class ToolTrace(BaseModel):
    """Trace entry for guardrail or MCP tool activity visible to the client."""

    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    status: str
    stage: str = "mcp"
    result_preview: str = ""


class ErrorResponse(BaseModel):
    """Stable non-happy-path payload returned alongside blocked or error statuses."""

    code: str = Field(description="Machine-readable error code suitable for client branching.")
    message: str = Field(description="Human-readable explanation aligned with the top-level message.")
    retryable: bool = Field(default=False, description="Whether retrying the same action may succeed.")
    details: dict[str, object] = Field(
        default_factory=dict,
        description="Optional structured metadata for debugging or UI hints.",
    )


class McpStatusResponse(BaseModel):
    """Live MCP connectivity status for the configured backend connection."""

    status: McpConnectionStatus
    tool_server_name: str
    tool_server_transport: str
    configured_target: str
    configured_command: str | None = None
    configured_args: list[str] = Field(default_factory=list)
    message: str
    tool_count: int | None = None
    tool_names: list[str] = Field(default_factory=list)
    error: ErrorResponse | None = None


class ChatResponse(BaseModel):
    """Stable MCP chat response contract for all workflow states."""

    thread_id: str
    status: ChatStatus = "ready"
    message: str = Field(description="Primary user-facing summary for the current workflow state.")
    tool_traces: list[ToolTrace] = Field(default_factory=list)
    draft: ProductOrderDraft | None = None
    pending_action: PendingAction | None = None
    execution_result: ExecutionResult | None = None
    error: ErrorResponse | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> "ChatResponse":
        if self.status == "ready":
            self._require_absent("draft", self.draft)
            self._require_absent("pending_action", self.pending_action)
            self._require_absent("execution_result", self.execution_result)
            self._require_absent("error", self.error)
            return self

        if self.status == "needs_confirmation":
            self._require_present("draft", self.draft)
            self._require_present("pending_action", self.pending_action)
            self._require_absent("execution_result", self.execution_result)
            self._require_absent("error", self.error)
            return self

        if self.status == "executed":
            self._require_absent("draft", self.draft)
            self._require_absent("pending_action", self.pending_action)
            self._require_present("execution_result", self.execution_result)
            self._require_absent("error", self.error)
            return self

        if self.status == "blocked":
            self._require_present("error", self.error)
            return self

        self._require_present("error", self.error)
        self._require_absent("execution_result", self.execution_result)
        return self

    @staticmethod
    def _require_present(name: str, value: object | None) -> None:
        if value is None:
            raise ValueError(f"{name} is required when returning this status.")

    @staticmethod
    def _require_absent(name: str, value: object | None) -> None:
        if value is not None:
            raise ValueError(f"{name} must be omitted when returning this status.")


class ResetResponse(BaseModel):
    thread_id: str
    status: str
