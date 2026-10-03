from __future__ import annotations

from fastapi.testclient import TestClient

from app.schemas import (
    BlockedChatResponse,
    ConfirmChatRequest,
    ErrorResponse,
    NeedsConfirmationChatResponse,
    ProductOrderDraft,
    ReadyChatResponse,
)


def _configure_env(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("APP_NAME", "LLM Layer")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("MCP_SERVER_NAME", "openslice")
    monkeypatch.setenv("MCP_TRANSPORT", "streamable_http")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:8003/mcp")
    monkeypatch.setenv("PRODUCT_READ_TOOL_NAMES", "searchOSLProductOfferings")
    monkeypatch.setenv("PRODUCT_WRITE_TOOL_NAMES", "createProductOrder")
    get_settings.cache_clear()


def test_openapi_documents_chat_status_enum_and_error_payload(monkeypatch) -> None:
    _configure_env(monkeypatch)
    from app.app_factory import create_app

    client = TestClient(create_app())

    response = client.get("/openapi.json")
    assert response.status_code == 200

    data = response.json()
    chat_request = data["components"]["schemas"]["ChatRequest"]
    confirm_request = data["components"]["schemas"]["ConfirmChatRequest"]
    chat_request_body = data["paths"]["/v1/chat"]["post"]["requestBody"]["content"]["application/json"]
    confirm_operation = data["paths"]["/v1/chat/{thread_id}/confirm"]["post"]
    cancel_operation = data["paths"]["/v1/chat/{thread_id}/cancel"]["post"]

    assert "reset" not in chat_request["properties"]
    assert chat_request["examples"][0] == {"message": "Show me the products available."}
    assert chat_request["examples"][1] == {
        "message": "Create an order for product X.",
        "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
    }
    assert chat_request_body["examples"]["discover_products"]["value"] == {
        "message": "Show me the products available."
    }
    assert chat_request_body["examples"]["create_order_request"]["value"] == {
        "message": "Create an order for product X.",
        "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
    }
    assert confirm_request["examples"][0] == {
        "draft_id": "draft-0197b3c4-5d6e-7f80-9abc-def012345678",
        "fingerprint": "fp-premium-fiber",
        "idempotency_key": "0197b3c4-8e9f-7a01-b234-cdef01234567",
    }
    assert confirm_operation["requestBody"]["content"]["application/json"]["examples"]["confirm_order"]["value"] == {
        "draft_id": "draft-0197b3c4-5d6e-7f80-9abc-def012345678",
        "fingerprint": "fp-premium-fiber",
        "idempotency_key": "0197b3c4-8e9f-7a01-b234-cdef01234567",
    }
    assert "requestBody" not in cancel_operation
    ready_schema = data["components"]["schemas"]["ReadyChatResponse"]
    blocked_schema = data["components"]["schemas"]["BlockedChatResponse"]
    reset_schema = data["components"]["schemas"]["ResetResponse"]
    assert "status" in ready_schema["required"]
    assert "status" in blocked_schema["required"]
    assert "status" in reset_schema["required"]


def test_ready_endpoint_returns_runtime_metadata(monkeypatch) -> None:
    _configure_env(monkeypatch)
    from app.app_factory import create_app

    client = TestClient(create_app())

    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "app_name": "LLM Layer",
        "tool_server_name": "openslice",
        "tool_server_transport": "streamable_http",
        "prompt_version": "v1",
    }


def test_mcp_status_endpoint_reports_reachable_probe(monkeypatch) -> None:
    _configure_env(monkeypatch)
    from app.app_factory import create_app
    import app.app_factory as app_factory
    import app.runtime as runtime

    async def fake_probe(settings=None):
        return runtime.McpProbeResult(reachable=True, tool_count=2, tool_names=["createProductOrder", "searchOSLProductOfferings"])

    monkeypatch.setattr(app_factory, "probe_mcp_connection", fake_probe)
    client = TestClient(create_app())

    response = client.get("/v1/runtime/mcp-status")
    assert response.status_code == 200
    assert response.json() == {
        "status": "reachable",
        "tool_server_name": "openslice",
        "tool_server_transport": "streamable_http",
        "configured_target": "http://127.0.0.1:8003/mcp",
        "configured_command": None,
        "configured_args": [],
        "checked_at": response.json()["checked_at"],
        "message": "Connected to the configured MCP server.",
        "tool_count": 2,
        "tool_names": ["createProductOrder", "searchOSLProductOfferings"],
        "error": None,
    }


def test_create_app_logs_redacted_startup_diagnostics(monkeypatch, caplog) -> None:
    _configure_env(monkeypatch)
    monkeypatch.setenv("MCP_SERVER_HEADERS", '{"Authorization":"Bearer secret","Accept":"application/json"}')
    from app.config import get_settings

    get_settings.cache_clear()

    from app.app_factory import create_app

    caplog.set_level("INFO")
    create_app()

    assert "Validated runtime configuration" in caplog.text
    assert "***redacted***" in caplog.text
    assert "Bearer secret" not in caplog.text


def test_chat_endpoint_returns_documented_confirmation_shape(monkeypatch) -> None:
    _configure_env(monkeypatch)
    from app.app_factory import create_app
    import app.app_factory as app_factory

    async def fake_run_chat(
        message: str,
        thread_id: str | None,
        *,
        include_traces: bool = True,
        telemetry=None,
    ):
        assert message == "Order Premium Fiber"
        assert thread_id is None
        assert include_traces is False
        return NeedsConfirmationChatResponse(
            thread_id="thread-1",
            status="needs_confirmation",
            message="Draft prepared.",
            draft=ProductOrderDraft(
                draft_id="draft-1",
                tool_name="createProductOrder",
                display_name="Premium Fiber",
                normalized_arguments={"productName": "Premium Fiber"},
                summary="Premium Fiber order draft",
                fingerprint="fp-1",
            ),
        )

    monkeypatch.setattr(app_factory, "run_chat", fake_run_chat)
    client = TestClient(create_app())

    response = client.post("/v1/chat", json={"message": "Order Premium Fiber"})
    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "status": "needs_confirmation",
        "message": "Draft prepared.",
        "draft": {
            "draft_id": "draft-1",
            "tool_name": "createProductOrder",
            "display_name": "Premium Fiber",
            "normalized_arguments": {"productName": "Premium Fiber"},
            "summary": "Premium Fiber order draft",
            "fingerprint": "fp-1",
            "requires_confirmation": True,
        },
    }


def test_chat_endpoint_records_redacted_llm_observability_event(monkeypatch) -> None:
    _configure_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENAI_MAX_TOKENS", "2048")
    monkeypatch.setenv("OBSERVABILITY_AGENT_ID", "cop-pilot-llm-layer")
    monkeypatch.setenv("OBSERVABILITY_EVENT_VIEW_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.app_factory import create_app
    import app.app_factory as app_factory

    async def fake_run_chat(
        message: str,
        thread_id: str | None,
        *,
        include_traces: bool = True,
        telemetry=None,
    ):
        assert telemetry is not None
        telemetry.record_attempt()
        telemetry.input_tokens = 20
        telemetry.completion_tokens = 8
        return ReadyChatResponse(
            thread_id="thread-observability",
            status="ready",
            message="Products found.",
        )

    monkeypatch.setattr(app_factory, "run_chat", fake_run_chat)
    client = TestClient(create_app())

    chat_response = client.post("/v1/chat", json={"message": "Show products"})
    events_response = client.get("/v1/runtime/observability/events")

    assert chat_response.status_code == 200
    assert events_response.status_code == 200
    body = events_response.json()
    assert body["delivery_enabled"] is False
    assert "delivery_target" not in body
    assert body["view_enabled"] is True
    assert len(body["events"]) == 1
    payload = body["events"][0]["payload"]
    assert payload["agentId"] == "cop-pilot-llm-layer"
    assert payload["eventType"] == "completion"
    assert payload["provider"] == "openrouter"
    assert payload["inputTokens"] == 20
    assert payload["completionTokens"] == 8
    assert payload["maxTokens"] == 2048
    assert payload["inputPrompt"] == "[redacted]"
    assert payload["responseText"] == "[redacted]"


def test_confirm_endpoint_returns_documented_blocked_error_shape(monkeypatch) -> None:
    _configure_env(monkeypatch)
    from app.app_factory import create_app
    import app.app_factory as app_factory

    async def fake_confirm_chat(thread_id: str, payload: ConfirmChatRequest, *, include_traces: bool = True):
        assert thread_id == "thread-1"
        assert payload == ConfirmChatRequest(
            draft_id="draft-1",
            fingerprint="fp-1",
            idempotency_key="0197b3c4-8e9f-7a01-b234-cdef01234567",
        )
        assert include_traces is False
        return BlockedChatResponse(
            thread_id="thread-1",
            status="blocked",
            message="There is no pending product order draft to confirm.",
            error=ErrorResponse(
                code="missing_pending_draft",
                message="There is no pending product order draft to confirm.",
                retryable=False,
            ),
        )

    monkeypatch.setattr(app_factory, "confirm_chat", fake_confirm_chat)
    client = TestClient(create_app())

    response = client.post(
        "/v1/chat/thread-1/confirm",
        json={
            "draft_id": "draft-1",
            "fingerprint": "fp-1",
            "idempotency_key": "0197b3c4-8e9f-7a01-b234-cdef01234567",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "status": "blocked",
        "message": "There is no pending product order draft to confirm.",
        "error": {
            "code": "missing_pending_draft",
            "message": "There is no pending product order draft to confirm.",
            "retryable": False,
            "details": {},
        },
    }


def test_cancel_endpoint_returns_documented_ready_shape(monkeypatch) -> None:
    _configure_env(monkeypatch)
    from app.app_factory import create_app
    import app.app_factory as app_factory
    from app.schemas import ReadyChatResponse

    async def fake_cancel_chat(thread_id: str, *, include_traces: bool = True):
        assert thread_id == "thread-1"
        assert include_traces is False
        return ReadyChatResponse(
            thread_id="thread-1",
            status="ready",
            message="The product order draft was canceled.",
        )

    monkeypatch.setattr(app_factory, "cancel_chat", fake_cancel_chat)
    client = TestClient(create_app())

    response = client.post("/v1/chat/thread-1/cancel")
    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "status": "ready",
        "message": "The product order draft was canceled.",
    }


def test_mcp_status_endpoint_reports_probe_failure(monkeypatch) -> None:
    _configure_env(monkeypatch)
    from app.app_factory import create_app
    import app.app_factory as app_factory
    import app.runtime as runtime

    async def fake_probe(settings=None):
        return runtime.McpProbeResult(
            reachable=False,
            tool_count=None,
            tool_names=[],
            failure=runtime.McpFailure(
                code="mcp_unavailable",
                trace_status="unavailable",
                user_message="The product platform is temporarily unavailable.",
                retryable=True,
                detail_message="Connection refused by MCP server.",
                open_circuit=True,
            ),
        )

    monkeypatch.setattr(app_factory, "probe_mcp_connection", fake_probe)
    client = TestClient(create_app())

    response = client.get("/v1/runtime/mcp-status")
    assert response.status_code == 200
    assert response.json() == {
        "status": "unavailable",
        "tool_server_name": "openslice",
        "tool_server_transport": "streamable_http",
        "configured_target": "http://127.0.0.1:8003/mcp",
        "configured_command": None,
        "configured_args": [],
        "checked_at": response.json()["checked_at"],
        "message": "The product platform is temporarily unavailable.",
        "tool_count": None,
        "tool_names": [],
        "error": {
            "code": "mcp_unavailable",
            "message": "The product platform is temporarily unavailable.",
            "retryable": True,
            "details": {"detail": "Connection refused by MCP server."},
        },
    }
