from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
import logging
from time import perf_counter
from typing import Annotated

from fastapi import Body, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.request_auth import incoming_bearer, parse_bearer

from app.config import get_settings
from app.conversation_store import conversation_store
from app.id_utils import uuid7
from app.observability import (
    LlmInteractionMetrics,
    ObservabilityEventsResponse,
    ObservabilityPublisher,
    build_agent_event,
)
from app.prompting import PROMPT_VERSION
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConfirmChatRequest,
    ErrorResponse,
    HealthResponse,
    McpStatusResponse,
    ReadyResponse,
    ResetResponse,
)
from app.runtime import probe_mcp_connection
from app.workflow_service import cancel_chat, confirm_chat, run_chat

logger = logging.getLogger(__name__)


CHAT_REQUEST_EXAMPLES = {
    "discover_products": {
        "summary": "Discover products",
        "description": "Ask for available product offerings without starting a write operation.",
        "value": {"message": "Show me the products available."},
    },
    "create_order_request": {
        "summary": "Create an order",
        "description": "Reuse a previous thread_id and ask the backend to prepare a guarded product-order draft.",
        "value": {
            "message": "Create an order for product X.",
            "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
        },
    },
}

CONFIRM_REQUEST_EXAMPLES = {
    "confirm_order": {
        "summary": "Confirm a reviewed draft",
        "description": "Echo the exact draft details returned by `needs_confirmation` and send a client-generated idempotency key.",
        "value": {
            "draft_id": "draft-0197b3c4-5d6e-7f80-9abc-def012345678",
            "fingerprint": "fp-premium-fiber",
            "idempotency_key": "0197b3c4-8e9f-7a01-b234-cdef01234567",
        },
    }
}

def create_app() -> FastAPI:
    settings = get_settings()
    logger.info("Validated runtime configuration | diagnostics=%s", settings.startup_diagnostics)
    observability_publisher = ObservabilityPublisher(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await observability_publisher.start()
        try:
            yield
        finally:
            await observability_publisher.stop()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.observability_publisher = observability_publisher

    @app.middleware("http")
    async def forward_request_auth(request: Request, call_next):
        try:
            authorization = parse_bearer(request.headers.get("authorization"))
        except ValueError:
            return JSONResponse(status_code=401, content={"detail": "Expected a bearer token."},
                                headers={"WWW-Authenticate": "Bearer"})
        context_token = incoming_bearer.set(authorization)
        try:
            return await call_next(request)
        finally:
            incoming_bearer.reset(context_token)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = uuid7()
        start = perf_counter()
        client = request.client.host if request.client else "unknown"
        logger.info(
            "HTTP request started | request_id=%s method=%s path=%s client=%s",
            request_id,
            request.method,
            request.url.path,
            client,
        )
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((perf_counter() - start) * 1000)
            logger.exception(
                "HTTP request failed | request_id=%s method=%s path=%s duration_ms=%s",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
            )
            raise

        duration_ms = int((perf_counter() - start) * 1000)
        logger.info(
            "HTTP request completed | request_id=%s method=%s path=%s status_code=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    @app.get(
        "/health",
        response_model=HealthResponse,
        summary="Liveness check",
        description="Simple process-level liveness probe. It confirms that the HTTP service is running.",
    )
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/ready",
        response_model=ReadyResponse,
        summary="Readiness check",
        description="Configuration-level readiness probe. It confirms the app booted with valid settings and reports the configured MCP backend metadata.",
    )
    async def ready() -> ReadyResponse:
        return ReadyResponse(
            status="ready",
            app_name=settings.app_name,
            tool_server_name=settings.tool_server_name,
            tool_server_transport=settings.tool_server_transport,
            prompt_version=PROMPT_VERSION,
        )

    @app.get(
        "/v1/runtime/mcp-status",
        response_model=McpStatusResponse,
        summary="Probe MCP runtime connectivity",
        description="Checks whether the configured MCP server is currently reachable and returns the discovered tool inventory when available.",
    )
    async def mcp_status() -> McpStatusResponse:
        configured_target = settings.tool_server_command if settings.tool_server_transport == "stdio" else settings.tool_server_url
        configured_command = settings.tool_server_command if settings.tool_server_transport == "stdio" else None
        configured_args = settings.tool_server_args_list if settings.tool_server_transport == "stdio" else []
        checked_at = datetime.now(UTC)

        probe = await probe_mcp_connection(settings)
        if probe.reachable:
            return McpStatusResponse(
                status="reachable",
                tool_server_name=settings.tool_server_name,
                tool_server_transport=settings.tool_server_transport,
                configured_target=configured_target,
                configured_command=configured_command,
                configured_args=configured_args,
                checked_at=checked_at,
                message="Connected to the configured MCP server.",
                tool_count=probe.tool_count,
                tool_names=probe.tool_names,
            )

        failure = probe.failure
        assert failure is not None
        return McpStatusResponse(
            status="unavailable",
            tool_server_name=settings.tool_server_name,
            tool_server_transport=settings.tool_server_transport,
            configured_target=configured_target,
            configured_command=configured_command,
            configured_args=configured_args,
            checked_at=checked_at,
            message=failure.user_message,
            tool_count=None,
            tool_names=[],
            error=ErrorResponse(
                code=failure.code,
                message=failure.user_message,
                retryable=failure.retryable,
                details={"detail": failure.detail_message},
            ),
        )

    @app.get(
        "/v1/runtime/observability/events",
        response_model=ObservabilityEventsResponse,
        response_model_exclude_none=True,
        summary="List recent observability events",
        description=(
            "Returns the bounded in-memory observability event feed when "
            "OBSERVABILITY_EVENT_VIEW_ENABLED=true. Prompt and response content remain redacted."
        ),
    )
    async def list_observability_events() -> ObservabilityEventsResponse:
        return observability_publisher.recent_events()

    @app.post(
        "/v1/chat",
        response_model=ChatResponse,
        response_model_exclude_none=True,
        summary="Send a chat message",
        description=(
            "Starts a new conversation when `thread_id` is omitted, or continues an existing conversation when "
            "`thread_id` is provided. Read-only requests return `ready`; guarded write intents return "
            "`needs_confirmation` with a draft that must be explicitly confirmed."
        ),
    )
    async def chat(
        payload: Annotated[
            ChatRequest,
            Body(openapi_examples=CHAT_REQUEST_EXAMPLES),
        ],
        include_traces: bool = Query(default=False, description="Include guardrail and MCP diagnostics in the response."),
    ) -> ChatResponse:
        logger.info(
            "Chat request received | thread_id=%s message_preview=%s",
            payload.thread_id or "new",
            payload.message[:160].replace("\n", " "),
        )
        telemetry = LlmInteractionMetrics()
        interaction_started = perf_counter()
        response = await run_chat(
            payload.message,
            payload.thread_id,
            include_traces=include_traces,
            telemetry=telemetry,
        )
        interaction_latency_ms = int((perf_counter() - interaction_started) * 1000)
        if telemetry.attempted:
            observability_publisher.enqueue(
                build_agent_event(
                    settings=settings,
                    response=response,
                    metrics=telemetry,
                    latency_ms=interaction_latency_ms,
                )
            )
        logger.info(
            "Chat request completed | thread_id=%s status=%s trace_count=%s",
            response.thread_id,
            response.status,
            len(response.tool_traces or []),
        )
        return response

    @app.post(
        "/v1/chat/{thread_id}/confirm",
        response_model=ChatResponse,
        response_model_exclude_none=True,
        summary="Confirm a pending draft",
        description=(
            "Executes the current pending product-order draft for the given conversation. "
            "The client must echo the `draft_id` and `fingerprint` it reviewed and supply an "
            "`idempotency_key` so retries remain safe."
        ),
    )
    async def confirm(
        thread_id: str,
        payload: Annotated[
            ConfirmChatRequest,
            Body(openapi_examples=CONFIRM_REQUEST_EXAMPLES),
        ],
        include_traces: bool = Query(default=False, description="Include guardrail and MCP diagnostics in the response."),
    ) -> ChatResponse:
        logger.info("Confirm request received | thread_id=%s", thread_id)
        response = await confirm_chat(thread_id, payload, include_traces=include_traces)
        logger.info(
            "Confirm request completed | thread_id=%s status=%s trace_count=%s",
            response.thread_id,
            response.status,
            len(response.tool_traces or []),
        )
        return response

    @app.post(
        "/v1/chat/{thread_id}/cancel",
        response_model=ChatResponse,
        response_model_exclude_none=True,
        summary="Cancel a pending draft",
        description="Discards the current pending product-order draft for the conversation.",
    )
    async def cancel(
        thread_id: str,
        include_traces: bool = Query(default=False, description="Include guardrail and MCP diagnostics in the response."),
    ) -> ChatResponse:
        logger.info("Cancel request received | thread_id=%s", thread_id)
        response = await cancel_chat(thread_id, include_traces=include_traces)
        logger.info(
            "Cancel request completed | thread_id=%s status=%s trace_count=%s",
            response.thread_id,
            response.status,
            len(response.tool_traces or []),
        )
        return response

    @app.post(
        "/v1/chat/{thread_id}/reset",
        response_model=ResetResponse,
        summary="Reset a conversation",
        description="Clears the in-memory workflow state for the given conversation, including any pending draft.",
    )
    async def reset_chat(thread_id: str) -> ResetResponse:
        logger.info("Reset request received | thread_id=%s", thread_id)
        conversation_store.reset(thread_id)
        logger.info("Reset request completed | thread_id=%s", thread_id)
        return ResetResponse(thread_id=thread_id, status="reset")

    return app
