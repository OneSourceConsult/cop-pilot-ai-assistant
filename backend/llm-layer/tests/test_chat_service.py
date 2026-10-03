from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from app import chat_service
from app.config import AppSettings, get_settings
from app.conversation_store import conversation_store
from app.observability import LlmInteractionMetrics
from app.order_requirements import default_order_dates
from app.prompting import ANSWER_STYLE_POLICY, SAFETY_POLICY, TOOL_USE_POLICY, build_base_system_prompt
from app.runtime import McpFailure, McpOperationError, reset_runtime_caches
from app.schemas import (
    BlockedChatResponse,
    ConfirmChatRequest,
    ErrorChatResponse,
    ErrorResponse,
    ExecutedChatResponse,
    ProductOrderDraft,
)
import app.runtime as runtime
import app.workflow_service as workflow_service


class FakeModel:
    def __init__(self, replies: list[AIMessage]):
        self._replies = list(replies)

    def bind_tools(self, tools: list[object]) -> "FakeModel":
        return self

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        if not self._replies:
            raise AssertionError("No fake model reply configured.")
        return self._replies.pop(0)


class BeginningSystemOnlyModel(FakeModel):
    def __init__(self, replies: list[AIMessage]):
        super().__init__(replies)
        self.message_batches: list[list[object]] = []

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        self.message_batches.append(list(messages))
        assert [index for index, message in enumerate(messages) if isinstance(message, SystemMessage)] == [0]
        return await super().ainvoke(messages)


class ExplodingModel:
    def bind_tools(self, tools: list[object]) -> "ExplodingModel":
        return self

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        raise RuntimeError("The operation was aborted")


class SlowModel:
    def bind_tools(self, tools: list[object]) -> "SlowModel":
        return self

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        await asyncio.Event().wait()
        raise AssertionError("The model invocation should time out.")


class FakeTool:
    def __init__(self, name: str, result: object | Exception):
        self.name = name
        self._result = result
        self.calls: list[object] = []

    async def ainvoke(self, arguments: object) -> object:
        self.calls.append(arguments)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _confirm_payload(draft: ProductOrderDraft) -> ConfirmChatRequest:
    return ConfirmChatRequest(
        draft_id=draft.draft_id,
        fingerprint=draft.fingerprint,
        idempotency_key="0197b3c4-8e9f-7a01-b234-cdef01234567",
    )


def _with_default_dates(arguments: dict[str, object]) -> dict[str, object]:
    start_date, end_date = default_order_dates()
    return arguments | {"startDate": start_date, "endDate": end_date}


@pytest.fixture(autouse=True)
def reset_store() -> None:
    conversation_store._conversations.clear()
    reset_runtime_caches()
    get_settings.cache_clear()


def _patch_model(monkeypatch: pytest.MonkeyPatch, replies: list[AIMessage]) -> None:
    monkeypatch.setattr(runtime, "create_model", lambda settings=None: FakeModel(replies))
    monkeypatch.setattr(workflow_service, "create_model", lambda settings=None: FakeModel(replies))


def _patch_tools(monkeypatch: pytest.MonkeyPatch, tools: list[FakeTool]) -> None:
    async def fake_tools(settings=None) -> chat_service.Toolset:
        return chat_service.Toolset(tools, {tool.name: tool for tool in tools})

    monkeypatch.setattr(runtime, "get_toolset", fake_tools)
    monkeypatch.setattr(workflow_service, "get_toolset", fake_tools)


@pytest.mark.asyncio
async def test_read_only_product_lookup_executes_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("searchOSLProductOfferings", [{"text": '{"items":[{"name":"Starter"}]}'}])
    _patch_model(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"category": "mobile"}, "id": "call-1"}]),
            AIMessage(content="I found the available product offerings."),
        ],
    )
    _patch_tools(monkeypatch, [tool])

    response = await chat_service.run_chat("List product offerings")

    assert response.status == "ready"
    assert tool.calls == [{"category": "mobile"}]
    assert getattr(response, "draft", None) is None
    assert getattr(response, "error", None) is None
    assert response.message == "I found the available product offerings."


@pytest.mark.asyncio
async def test_product_enrichment_keeps_system_message_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    offering_tool = FakeTool(
        "getOSLProductOfferingByProductOfferingId",
        [{"text": '{"id":"offering-1","name":"Managed 5G","productSpecification":{"id":"spec-1"}}'}],
    )
    specification_tool = FakeTool(
        "getOSLProductByProductSpecificationId",
        [{"text": '{"id":"spec-1","name":"Managed 5G specification","productSpecCharacteristic":[]}'}],
    )
    model = BeginningSystemOnlyModel(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": offering_tool.name, "args": {"productOfferingId": "offering-1"}, "id": "call-1"}],
            ),
            AIMessage(content="The offering is ready to configure."),
        ]
    )
    monkeypatch.setattr(workflow_service, "create_model", lambda settings=None: model)
    _patch_tools(monkeypatch, [offering_tool, specification_tool])

    response = await chat_service.run_chat("Prepare Managed 5G")

    assert response.status == "ready"
    second_round_tool_messages = [
        message for message in model.message_batches[1] if isinstance(message, ToolMessage)
    ]
    assert len(second_round_tool_messages) == 1
    assert "Detailed product specification" in str(second_round_tool_messages[0].content)


@pytest.mark.asyncio
async def test_chat_aggregates_llm_usage_across_tool_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("searchOSLProductOfferings", [{"text": '{"items":[{"name":"Starter"}]}'}])
    _patch_model(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[{"name": tool.name, "args": {"category": "mobile"}, "id": "call-1"}],
                usage_metadata={"input_tokens": 80, "output_tokens": 12, "total_tokens": 92},
            ),
            AIMessage(
                content="I found the available product offerings.",
                usage_metadata={"input_tokens": 40, "output_tokens": 18, "total_tokens": 58},
            ),
        ],
    )
    _patch_tools(monkeypatch, [tool])
    telemetry = LlmInteractionMetrics()

    response = await chat_service.run_chat("List product offerings", telemetry=telemetry)

    assert response.status == "ready"
    assert telemetry.input_tokens == 120
    assert telemetry.completion_tokens == 30
    assert telemetry.selected_tools == ["searchOSLProductOfferings"]


@pytest.mark.asyncio
async def test_product_order_request_creates_draft_without_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])

    response = await chat_service.run_chat("Order Premium Fiber")

    assert response.status == "needs_confirmation"
    assert response.draft is not None
    assert getattr(response, "error", None) is None
    assert tool.calls == []


@pytest.mark.asyncio
async def test_confirm_executes_valid_draft_once(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", [{"text": '{"id":"po-1","state":"acknowledged"}'}])
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])

    initial = await chat_service.run_chat("Order Premium Fiber")
    response = await chat_service.confirm_chat(initial.thread_id, _confirm_payload(initial.draft))

    assert response.status == "executed"
    assert response.execution_result is not None
    assert getattr(response, "error", None) is None
    assert len(tool.calls) == 1


@pytest.mark.asyncio
async def test_confirm_without_draft_is_blocked() -> None:
    response = await chat_service.confirm_chat(
        "missing-thread",
        ConfirmChatRequest(draft_id="draft-missing", fingerprint="fp-missing", idempotency_key="0197b3c4-8e9f-7a01-b234-cdef01234567"),
    )
    assert response.status == "blocked"
    assert response.error is not None
    assert response.error.code == "missing_pending_draft"
    assert "no pending product order draft" in response.message.lower()


@pytest.mark.asyncio
async def test_unknown_tool_falls_back_to_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("mystery_write_tool", {"ok": True})
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])

    response = await chat_service.run_chat("Do a secret order")

    assert response.status == "needs_confirmation"
    assert response.draft is not None
    assert response.draft.tool_name == "mystery_write_tool"
    assert getattr(response, "error", None) is None
    assert tool.calls == []


@pytest.mark.asyncio
async def test_missing_required_arguments_still_creates_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(monkeypatch, [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {}, "id": "call-1"}])])
    _patch_tools(monkeypatch, [tool])

    response = await chat_service.run_chat("Order something")

    assert response.status == "needs_confirmation"
    assert response.draft is not None
    assert response.draft.display_name == "Selected product"
    assert getattr(response, "error", None) is None
    assert tool.calls == []


@pytest.mark.asyncio
async def test_selected_offering_blocks_draft_until_required_characteristics_are_present(monkeypatch: pytest.MonkeyPatch) -> None:
    offering_tool = FakeTool(
        "getOSLProductOfferingByProductOfferingId",
        [
            {
                "text": (
                    '{"id":"offering-1","name":"Managed Connectivity",'
                    '"productSpecification":{"productSpecCharacteristic":['
                    '{"name":"bandwidth","minCardinality":1},'
                    '{"name":"installationDate","required":true}]}}'
                )
            }
        ],
    )
    order_tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[{"name": offering_tool.name, "args": {"id": "offering-1"}, "id": "call-1"}]),
            AIMessage(content="", tool_calls=[{"name": order_tool.name, "args": {"bandwidth": "1 Gbps"}, "id": "call-2"}]),
        ],
    )
    _patch_tools(monkeypatch, [offering_tool, order_tool])

    response = await chat_service.run_chat("Order the selected offering")

    assert response.status == "blocked"
    assert response.error is not None
    assert response.error.code == "guardrail_clarify"
    assert "installationDate" in response.message
    assert order_tool.calls == []


@pytest.mark.asyncio
async def test_selected_offering_creates_draft_when_required_characteristics_are_present(monkeypatch: pytest.MonkeyPatch) -> None:
    offering_tool = FakeTool(
        "getOSLProductOfferingByProductOfferingId",
        [{"text": '{"name":"Managed Connectivity","productOfferingCharacteristic":[{"name":"bandwidth","required":true}]}'}],
    )
    order_tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[{"name": offering_tool.name, "args": {"id": "offering-1"}, "id": "call-1"}]),
            AIMessage(content="", tool_calls=[{"name": order_tool.name, "args": {"characteristics": {"bandwidth": "1 Gbps"}}, "id": "call-2"}]),
        ],
    )
    _patch_tools(monkeypatch, [offering_tool, order_tool])

    response = await chat_service.run_chat("Order the selected offering")

    assert response.status == "needs_confirmation"
    assert response.draft is not None
    assert response.draft.display_name == "Managed Connectivity"
    assert response.draft.normalized_arguments["startDate"] in response.message
    assert response.draft.normalized_arguments["endDate"] in response.message
    assert order_tool.calls == []


@pytest.mark.asyncio
async def test_duplicate_confirmation_does_not_execute_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", [{"text": '{"id":"po-1","state":"acknowledged"}'}])
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])

    initial = await chat_service.run_chat("Order Premium Fiber")
    await chat_service.confirm_chat(initial.thread_id, _confirm_payload(initial.draft))
    duplicate = await chat_service.confirm_chat(initial.thread_id, _confirm_payload(initial.draft))

    assert duplicate.status == "executed"
    assert duplicate.execution_result is not None
    assert len(tool.calls) == 1


@pytest.mark.asyncio
async def test_guardrail_outcome_hint_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool.name,
                        "args": {"productName": "Premium Fiber", "guardrail_outcome": "deny"},
                        "id": "call-1",
                    }
                ],
            )
        ],
    )
    _patch_tools(monkeypatch, [tool])

    response = await chat_service.run_chat("Order Premium Fiber")

    assert response.status == "needs_confirmation"
    assert response.draft is not None
    assert response.draft.normalized_arguments == _with_default_dates({"productName": "Premium Fiber"})
    assert getattr(response, "error", None) is None
    assert tool.calls == []


@pytest.mark.asyncio
async def test_mcp_write_tool_failure_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", RuntimeError("backend failed"))
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])

    initial = await chat_service.run_chat("Order Premium Fiber")
    response = await chat_service.confirm_chat(initial.thread_id, _confirm_payload(initial.draft))

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "tool_execution_failed"
    assert response.error.retryable is False
    assert "could not complete the requested operation" in response.message.lower()
    assert len(tool.calls) == 1


@pytest.mark.asyncio
async def test_reset_clears_pending_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])

    initial = await chat_service.run_chat("Order Premium Fiber")
    conversation_store.reset(initial.thread_id)

    blocked = await chat_service.confirm_chat(initial.thread_id, _confirm_payload(initial.draft))
    assert blocked.status == "blocked"


@pytest.mark.asyncio
async def test_llm_provider_failure_returns_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "create_model", lambda settings=None: ExplodingModel())
    monkeypatch.setattr(workflow_service, "create_model", lambda settings=None: ExplodingModel())
    _patch_tools(monkeypatch, [FakeTool("searchOSLProductOfferings", {"ok": True})])

    response = await chat_service.run_chat("What are my products?")

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "llm_request_failed"
    assert response.error.retryable is True
    assert response.error.details["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_llm_timeout_returns_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(runtime, "create_model", lambda settings=None: SlowModel())
    monkeypatch.setattr(workflow_service, "create_model", lambda settings=None: SlowModel())
    _patch_tools(monkeypatch, [])

    response = await chat_service.run_chat("Show product offerings")

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "llm_request_failed"
    assert response.error.retryable is True
    assert response.error.details["error_type"] == "TimeoutError"
    assert "timed out after 0.01 seconds" in response.error.details["provider_message"]


@pytest.mark.asyncio
async def test_read_tool_timeout_returns_normalized_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("searchOSLProductOfferings", {"ok": True})
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"category": "mobile"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])

    async def fail_invoke_tool(*args, **kwargs):
        raise McpOperationError(
            McpFailure(
                code="mcp_timeout",
                trace_status="timeout",
                user_message="The product platform did not respond in time.",
                retryable=True,
                detail_message="MCP read operation timed out.",
            )
        )

    monkeypatch.setattr(workflow_service, "invoke_tool", fail_invoke_tool)

    response = await chat_service.run_chat("List product offerings")

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "mcp_timeout"
    assert response.tool_traces[0].status == "timeout"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_mcp_connection_failure_returns_error_without_llm_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model(monkeypatch, [AIMessage(content="This should not be used.")])

    async def fail_get_toolset(settings=None):
        raise McpOperationError(
            McpFailure(
                code="mcp_unavailable",
                trace_status="unavailable",
                user_message="The product platform is temporarily unavailable.",
                retryable=True,
                detail_message="Connection refused by MCP server.",
                open_circuit=True,
            )
        )

    monkeypatch.setattr(runtime, "get_toolset", fail_get_toolset)
    monkeypatch.setattr(workflow_service, "get_toolset", fail_get_toolset)

    response = await chat_service.run_chat("What products are available?")

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "mcp_unavailable"
    assert response.message == "The product platform is temporarily unavailable."
    assert response.tool_traces[0].tool_name == "mcp_connection"


@pytest.mark.asyncio
async def test_missing_tool_returns_normalized_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": "searchOSLProductOfferings", "args": {"category": "mobile"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [])

    response = await chat_service.run_chat("List product offerings")

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "tool_not_found"
    assert response.tool_traces[0].status == "missing"


def test_stringify_content_handles_langchain_style_list() -> None:
    content = [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
    assert chat_service._text(content) == "hello\nworld"


def test_normalize_tool_result_prefers_text_payload() -> None:
    result = [{"text": "{\"status\":\"ok\"}"}]
    assert chat_service._tool_text(result) == "{\"status\":\"ok\"}"


def test_trim_preview_shortens_long_text() -> None:
    text = "x" * 600
    assert len(chat_service._preview(text, limit=100)) == 100


def test_base_prompt_uses_configured_prompt() -> None:
    settings = AppSettings(
        llm_api_key="test-key",
        llm_model_name="gpt-4.1-mini",
        llm_api_base_url="https://api.openai.com/v1",
        tool_server_url="http://127.0.0.1:8003/mcp",
        chat_system_prompt="base prompt",
        _env_file=None,
    )
    prompt = build_base_system_prompt(settings)
    assert "base prompt" in prompt
    assert TOOL_USE_POLICY in prompt
    assert ANSWER_STYLE_POLICY in prompt
    assert SAFETY_POLICY in prompt
    assert "product discovery and product ordering for network, connectivity, infrastructure, and platform offerings" in prompt
    assert "draft and explicit confirmation flow" in prompt
    assert "Do not ask only for missing order dates" in prompt
    assert "call the order tool" in prompt
    assert "Do not simulate a draft" in prompt
    assert "startDate" in prompt
    assert "endDate" in prompt
    assert "Never ask for irrelevant retail attributes such as color" in prompt
    assert "MCP server" not in prompt


def test_tool_server_headers_are_parsed_from_json() -> None:
    settings = AppSettings(
        llm_api_key="test-key",
        llm_model_name="gpt-4.1-mini",
        llm_api_base_url="https://api.openai.com/v1",
        tool_server_url="http://127.0.0.1:8003/mcp",
        tool_server_headers='{"Accept":"application/json, text/event-stream"}',
        _env_file=None,
    )
    assert settings.tool_server_headers_dict == {"Accept": "application/json, text/event-stream"}


def test_live_osl_product_tool_names_are_allowed_by_default() -> None:
    settings = AppSettings(
        llm_api_key="test-key",
        llm_model_name="gpt-4.1-mini",
        llm_api_base_url="https://api.openai.com/v1",
        tool_server_url="http://127.0.0.1:8003/mcp",
        _env_file=None,
    )
    assert "getOSLProductCatalogs" in settings.product_read_tool_names_set
    assert "getOSLServiceCatalogs" in settings.product_read_tool_names_set
    assert "searchOSLProductOfferings" in settings.product_read_tool_names_set
    assert "createProductOrder" in settings.product_write_tool_names_set


@pytest.mark.asyncio
async def test_retail_style_attributes_still_create_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool.name,
                        "args": {"productName": "Premium Fiber", "color": "blue"},
                        "id": "call-1",
                    }
                ],
            )
        ],
    )
    _patch_tools(monkeypatch, [tool])

    response = await chat_service.run_chat("Order Premium Fiber in blue")

    assert response.status == "needs_confirmation"
    assert response.draft is not None
    assert response.draft.normalized_arguments == _with_default_dates(
        {"productName": "Premium Fiber", "color": "blue"}
    )
    assert getattr(response, "error", None) is None
    assert tool.calls == []


def test_needs_confirmation_contract_requires_draft() -> None:
    with pytest.raises(ValidationError, match="Field required"):
        from app.schemas import NeedsConfirmationChatResponse

        NeedsConfirmationChatResponse(thread_id="t-1", status="needs_confirmation", message="confirm")


def test_blocked_contract_requires_error() -> None:
    with pytest.raises(ValidationError, match="Field required"):
        BlockedChatResponse(thread_id="t-1", status="blocked", message="blocked")


def test_executed_contract_rejects_draft() -> None:
    draft = ProductOrderDraft(
        draft_id="d-1",
        tool_name="createProductOrder",
        display_name="Premium Fiber",
        summary="summary",
        fingerprint="fp-1",
    )
    from app.schemas import ExecutionResult

    result = ExecutionResult(execution_token="e-1", tool_name="createProductOrder", status="executed")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExecutedChatResponse(
            thread_id="t-1",
            status="executed",
            message="done",
            draft=draft,
            execution_result=result,
        )


def test_error_contract_allows_retryable_error_payload() -> None:
    response = ErrorChatResponse(
        thread_id="t-1",
        status="error",
        message="MCP unavailable",
        error=ErrorResponse(code="mcp_unavailable", message="MCP unavailable", retryable=True),
    )

    assert response.error is not None
    assert response.error.retryable is True
