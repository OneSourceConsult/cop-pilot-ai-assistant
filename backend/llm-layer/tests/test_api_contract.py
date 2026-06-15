from __future__ import annotations

from fastapi.testclient import TestClient

from app.schemas import ChatResponse, ErrorResponse, ProductOrderDraft


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
    chat_response = data["components"]["schemas"]["ChatResponse"]
    status = chat_response["properties"]["status"]
    error = chat_response["properties"]["error"]

    assert status["default"] == "ready"
    assert status["enum"] == ["ready", "needs_confirmation", "blocked", "executed", "error"]
    assert error["anyOf"][0]["$ref"].endswith("/ErrorResponse")


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

    async def fake_run_chat(message: str, thread_id: str | None, reset: bool) -> ChatResponse:
        assert message == "Order Premium Fiber"
        assert thread_id is None
        assert reset is False
        return ChatResponse(
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
            pending_action="confirm_product_order",
        )

    monkeypatch.setattr(app_factory, "run_chat", fake_run_chat)
    client = TestClient(create_app())

    response = client.post("/v1/chat", json={"message": "Order Premium Fiber"})
    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "status": "needs_confirmation",
        "message": "Draft prepared.",
        "tool_traces": [],
        "draft": {
            "draft_id": "draft-1",
            "tool_name": "createProductOrder",
            "display_name": "Premium Fiber",
            "normalized_arguments": {"productName": "Premium Fiber"},
            "summary": "Premium Fiber order draft",
            "fingerprint": "fp-1",
            "requires_confirmation": True,
        },
        "pending_action": "confirm_product_order",
        "execution_result": None,
        "error": None,
    }


def test_confirm_endpoint_returns_documented_blocked_error_shape(monkeypatch) -> None:
    _configure_env(monkeypatch)
    from app.app_factory import create_app
    import app.app_factory as app_factory

    async def fake_confirm_chat(thread_id: str, confirmed: bool) -> ChatResponse:
        assert thread_id == "thread-1"
        assert confirmed is True
        return ChatResponse(
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

    response = client.post("/v1/chat/thread-1/confirm", json={"confirmed": True})
    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "status": "blocked",
        "message": "There is no pending product order draft to confirm.",
        "tool_traces": [],
        "draft": None,
        "pending_action": None,
        "execution_result": None,
        "error": {
            "code": "missing_pending_draft",
            "message": "There is no pending product order draft to confirm.",
            "retryable": False,
            "details": {},
        },
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
