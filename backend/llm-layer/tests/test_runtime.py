from __future__ import annotations

import asyncio

import pytest

import app.runtime as runtime
from app.config import AppSettings
from app.runtime import McpOperationError, Toolset, invoke_tool, reset_runtime_caches


class FakeTool:
    def __init__(self, responses: list[object | Exception]):
        self._responses = list(responses)
        self.calls: list[object] = []
        self.name = "fake"

    async def ainvoke(self, arguments: object) -> object:
        self.calls.append(arguments)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses: list[object | Exception]):
        self._responses = list(responses)

    async def get_tools(self) -> list[object]:
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _settings(**overrides: object) -> AppSettings:
    base = {
        "llm_api_key": "test-key",
        "llm_model_name": "gpt-4.1-mini",
        "llm_api_base_url": "https://api.openai.com/v1",
        "tool_server_name": "openslice",
        "tool_server_transport": "streamable_http",
        "tool_server_url": "http://127.0.0.1:8003/mcp",
        "product_read_tool_names": "searchOSLProductOfferings",
        "product_write_tool_names": "createProductOrder",
        "mcp_read_retries": 1,
        "mcp_retry_backoff_seconds": 0,
        "mcp_circuit_breaker_seconds": 30,
    }
    return AppSettings(_env_file=None, **(base | overrides))


@pytest.fixture(autouse=True)
def reset_runtime_state() -> None:
    reset_runtime_caches()


@pytest.mark.asyncio
async def test_invoke_tool_retries_safe_read_once() -> None:
    tool = FakeTool([TimeoutError("too slow"), [{"text": '{"ok":true}'}]])

    result = await invoke_tool(tool, {"category": "mobile"}, settings=_settings(), allow_retry=True)

    assert result == [{"text": '{"ok":true}'}]
    assert tool.calls == [{"category": "mobile"}, {"category": "mobile"}]


@pytest.mark.asyncio
async def test_invoke_tool_does_not_retry_write() -> None:
    tool = FakeTool([TimeoutError("too slow"), [{"text": '{"ok":true}'}]])

    with pytest.raises(McpOperationError) as exc_info:
        await invoke_tool(tool, {"productName": "Premium Fiber"}, settings=_settings(), allow_retry=False)

    assert exc_info.value.failure.code == "mcp_timeout"
    assert tool.calls == [{"productName": "Premium Fiber"}]


@pytest.mark.asyncio
async def test_invoke_tool_reports_malformed_response() -> None:
    tool = FakeTool([[{"text": 123}]])

    with pytest.raises(McpOperationError) as exc_info:
        await invoke_tool(tool, {"category": "mobile"}, settings=_settings(mcp_read_retries=0), allow_retry=True)

    assert exc_info.value.failure.code == "mcp_malformed_response"
    assert exc_info.value.failure.trace_status == "malformed"


@pytest.mark.asyncio
async def test_get_toolset_opens_circuit_after_unavailable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first_client = FakeClient([RuntimeError("connection refused by remote host")])
    second_client = FakeClient([[type("Tool", (), {"name": "searchOSLProductOfferings"})()]])
    clients = [first_client, second_client]

    monkeypatch.setattr(runtime, "MultiServerMCPClient", lambda *args, **kwargs: clients.pop(0))
    settings = _settings()

    with pytest.raises(McpOperationError) as first_error:
        await runtime.get_toolset(settings)

    with pytest.raises(McpOperationError) as second_error:
        await runtime.get_toolset(settings)

    assert first_error.value.failure.code == "mcp_unavailable"
    assert second_error.value.failure.code == "mcp_unavailable"
    assert clients == [second_client]


def test_classify_timeout_uses_normalized_category() -> None:
    failure = runtime.classify_mcp_exception(asyncio.TimeoutError(), phase="read")

    assert failure.code == "mcp_timeout"
    assert failure.trace_status == "timeout"
    assert failure.retryable is True
