from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


McpConnectionStatus = Literal["reachable", "unavailable"]


class ProductOrderDraft(BaseModel):
    """Normalized draft returned before a guarded product-order write is executed."""

    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(description="Draft identifier for the pending product-order proposal.")
    tool_name: str
    display_name: str
    normalized_arguments: dict[str, object] = Field(default_factory=dict)
    summary: str
    fingerprint: str
    requires_confirmation: bool = True


class ExecutionResult(BaseModel):
    """Execution outcome for a confirmed write operation."""

    model_config = ConfigDict(extra="forbid")

    execution_token: str = Field(description="Execution identifier. UUIDv7 for sortable confirmed-write tracking.")
    tool_name: str
    status: Literal["executed"]
    result_preview: str = ""


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"message": "Show me the products available."},
                {
                    "message": "Create an order for product X.",
                    "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
                },
            ]
        }
    )

    message: str = Field(
        min_length=1,
        description="User request for product discovery or guarded product-order preparation.",
        examples=["Show me the products available."],
    )
    thread_id: str | None = Field(
        default=None,
        description="Optional conversation identifier returned by a previous chat response. Reuse it only when continuing an existing thread or calling confirm, cancel, or reset. Expected format: UUIDv7.",
        examples=["0197b3c4-5d6e-7f80-9abc-def012345678"],
    )


class ConfirmChatRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "draft_id": "draft-0197b3c4-5d6e-7f80-9abc-def012345678",
                    "fingerprint": "fp-premium-fiber",
                    "idempotency_key": "0197b3c4-8e9f-7a01-b234-cdef01234567",
                }
            ]
        },
    )

    draft_id: str = Field(
        min_length=1,
        description="Draft identifier returned in the previous `needs_confirmation` response.",
        examples=["draft-0197b3c4-5d6e-7f80-9abc-def012345678"],
    )
    fingerprint: str = Field(
        min_length=1,
        description="Draft fingerprint returned in the previous `needs_confirmation` response. Must match the pending draft exactly.",
        examples=["fp-premium-fiber"],
    )
    idempotency_key: str = Field(
        min_length=1,
        description="Client-generated UUIDv7 key that makes confirmation retries safe.",
        examples=["0197b3c4-8e9f-7a01-b234-cdef01234567"],
    )


class ToolTrace(BaseModel):
    """Trace entry for guardrail or MCP tool activity visible to the client."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    status: str
    stage: str = "mcp"
    result_preview: str = ""


class ErrorResponse(BaseModel):
    """Stable non-happy-path payload returned alongside blocked or error statuses."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Machine-readable error code suitable for client branching.")
    message: str = Field(description="Human-readable explanation aligned with the top-level message.")
    retryable: bool = Field(default=False, description="Whether retrying the same action may succeed.")
    details: dict[str, object] = Field(
        default_factory=dict,
        description="Optional structured metadata for debugging or UI hints.",
    )


class McpStatusResponse(BaseModel):
    """Live MCP connectivity status for the configured backend connection."""

    model_config = ConfigDict(extra="forbid")

    status: McpConnectionStatus
    tool_server_name: str
    tool_server_transport: str
    configured_target: str
    configured_command: str | None = None
    configured_args: list[str] = Field(default_factory=list)
    checked_at: datetime
    message: str
    tool_count: int | None = None
    tool_names: list[str] = Field(default_factory=list)
    error: ErrorResponse | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


class ReadyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"]
    app_name: str
    tool_server_name: str
    tool_server_transport: str
    prompt_version: str


class ChatResponseBase(BaseModel):
    """Shared fields for all workflow responses."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(
        description="Conversation identifier generated as UUIDv7.",
        examples=["0197b3c4-5d6e-7f80-9abc-def012345678"],
    )
    message: str = Field(description="Primary user-facing summary for the current workflow state.")
    tool_traces: list[ToolTrace] | None = Field(
        default=None,
        description="Optional diagnostics included only when `include_traces=true` is requested.",
    )


class ReadyChatResponse(ChatResponseBase):
    status: Literal["ready"]


class NeedsConfirmationChatResponse(ChatResponseBase):
    status: Literal["needs_confirmation"]
    draft: ProductOrderDraft


class BlockedChatResponse(ChatResponseBase):
    status: Literal["blocked"]
    error: ErrorResponse
    draft: ProductOrderDraft | None = None


class ExecutedChatResponse(ChatResponseBase):
    status: Literal["executed"]
    execution_result: ExecutionResult


class ErrorChatResponse(ChatResponseBase):
    status: Literal["error"]
    error: ErrorResponse
    draft: ProductOrderDraft | None = None


ChatResponse = Annotated[
    ReadyChatResponse | NeedsConfirmationChatResponse | BlockedChatResponse | ExecutedChatResponse | ErrorChatResponse,
    Field(discriminator="status"),
]


class ResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(description="Conversation identifier generated as UUIDv7.")
    status: Literal["reset"]
