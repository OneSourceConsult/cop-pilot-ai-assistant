from __future__ import annotations

import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.conversation_store import conversation_store
from app.prompting import PROMPT_VERSION
from app.schemas import ChatRequest, ChatResponse, ConfirmRequest, ErrorResponse, McpStatusResponse, ResetResponse
from app.runtime import probe_mcp_connection
from app.workflow_service import confirm_chat, run_chat

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    logger.info("Validated runtime configuration | diagnostics=%s", settings.startup_diagnostics)
    app = FastAPI(title=settings.app_name)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = uuid4().hex[:8]
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

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        return {
            "status": "ready",
            "app_name": settings.app_name,
            "tool_server_name": settings.tool_server_name,
            "tool_server_transport": settings.tool_server_transport,
            "prompt_version": PROMPT_VERSION,
        }

    @app.get("/v1/runtime/mcp-status", response_model=McpStatusResponse)
    async def mcp_status() -> McpStatusResponse:
        configured_target = settings.tool_server_command if settings.tool_server_transport == "stdio" else settings.tool_server_url
        configured_command = settings.tool_server_command if settings.tool_server_transport == "stdio" else None
        configured_args = settings.tool_server_args_list if settings.tool_server_transport == "stdio" else []

        probe = await probe_mcp_connection(settings)
        if probe.reachable:
            return McpStatusResponse(
                status="reachable",
                tool_server_name=settings.tool_server_name,
                tool_server_transport=settings.tool_server_transport,
                configured_target=configured_target,
                configured_command=configured_command,
                configured_args=configured_args,
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

    @app.post("/v1/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest) -> ChatResponse:
        logger.info(
            "Chat request received | thread_id=%s reset=%s message_preview=%s",
            payload.thread_id or "new",
            payload.reset,
            payload.message[:160].replace("\n", " "),
        )
        response = await run_chat(payload.message, payload.thread_id, payload.reset)
        logger.info(
            "Chat request completed | thread_id=%s status=%s trace_count=%s",
            response.thread_id,
            response.status,
            len(response.tool_traces),
        )
        return response

    @app.post("/v1/chat/{thread_id}/confirm", response_model=ChatResponse)
    async def confirm(thread_id: str, payload: ConfirmRequest) -> ChatResponse:
        logger.info("Confirm request received | thread_id=%s confirmed=%s", thread_id, payload.confirmed)
        response = await confirm_chat(thread_id, payload.confirmed)
        logger.info(
            "Confirm request completed | thread_id=%s status=%s trace_count=%s",
            response.thread_id,
            response.status,
            len(response.tool_traces),
        )
        return response

    @app.post("/v1/chat/{thread_id}/reset", response_model=ResetResponse)
    async def reset_chat(thread_id: str) -> ResetResponse:
        logger.info("Reset request received | thread_id=%s", thread_id)
        conversation_store.reset(thread_id)
        logger.info("Reset request completed | thread_id=%s", thread_id)
        return ResetResponse(thread_id=thread_id, status="reset")

    return app
