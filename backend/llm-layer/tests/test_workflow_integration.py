from __future__ import annotations

from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.config import get_settings
from app.conversation_store import conversation_store
from app.guardrails.client import GuardrailClient
from app.guardrails.models import DraftSummary, ExecutionRecord, GuardrailDecision
from app.runtime import Toolset, reset_runtime_caches


class FakeModel:
    def __init__(self, replies: Iterable[AIMessage]):
        self._replies = list(replies)

    def bind_tools(self, tools: list[object]) -> "FakeModel":
        return self

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        if not self._replies:
            raise AssertionError("No fake model reply configured.")
        return self._replies.pop(0)


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


class StubGuardrailClient(GuardrailClient):
    def __init__(
        self,
        *,
        selection_status: str = "allow",
        selection_message: str = "Selection allowed.",
        authorization_status: str = "allow",
        authorization_message: str = "Execution authorized.",
    ) -> None:
        self.selection_status = selection_status
        self.selection_message = selection_message
        self.authorization_status = authorization_status
        self.authorization_message = authorization_message
        self.validate_calls: list[tuple[str, dict[str, object]]] = []
        self.authorize_calls: list[tuple[str, str]] = []

    def classify_tool(self, tool_name: str) -> str:
        if tool_name == "searchOSLProductOfferings":
            return "read"
        if tool_name == "createProductOrder":
            return "write"
        return "unknown"

    def validate_product_selection(self, tool_name: str, arguments: dict[str, object]) -> GuardrailDecision:
        self.validate_calls.append((tool_name, dict(arguments)))
        return GuardrailDecision(status=self.selection_status, message=self.selection_message)

    def build_product_order_draft(self, tool_name: str, arguments: dict[str, object], thread_id: str) -> DraftSummary:
        display_name = str(arguments.get("productName", "Selected product"))
        return DraftSummary(
            draft_id=f"draft-{thread_id}",
            tool_name=tool_name,
            display_name=display_name,
            normalized_arguments=dict(arguments),
            summary=f"Prepare {display_name} via {tool_name}.",
            fingerprint=f"fp-{display_name.lower().replace(' ', '-')}",
        )

    def authorize_execution(self, draft: DraftSummary, thread_id: str) -> GuardrailDecision:
        self.authorize_calls.append((thread_id, draft.tool_name))
        return GuardrailDecision(status=self.authorization_status, message=self.authorization_message)

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

    def register_execution(self, thread_id: str, draft: DraftSummary) -> ExecutionRecord:
        return ExecutionRecord(
            execution_token=f"exec-{thread_id}",
            draft_fingerprint=draft.fingerprint,
            tool_name=draft.tool_name,
        )


@pytest.fixture(autouse=True)
def reset_state() -> None:
    conversation_store._conversations.clear()
    reset_runtime_caches()
    get_settings.cache_clear()


def _configure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("MCP_SERVER_NAME", "openslice")
    monkeypatch.setenv("MCP_TRANSPORT", "streamable_http")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:8003/mcp")
    monkeypatch.setenv("PRODUCT_READ_TOOL_NAMES", "searchOSLProductOfferings")
    monkeypatch.setenv("PRODUCT_WRITE_TOOL_NAMES", "createProductOrder")
    get_settings.cache_clear()


def _patch_model(monkeypatch: pytest.MonkeyPatch, replies: list[AIMessage]) -> None:
    import app.runtime as runtime
    import app.workflow_service as workflow_service

    monkeypatch.setattr(runtime, "create_model", lambda settings=None: FakeModel(replies))
    monkeypatch.setattr(workflow_service, "create_model", lambda settings=None: FakeModel(replies))


def _patch_tools(monkeypatch: pytest.MonkeyPatch, tools: list[FakeTool]) -> None:
    import app.runtime as runtime
    import app.workflow_service as workflow_service

    async def fake_tools(settings=None) -> Toolset:
        return Toolset(tools, {tool.name: tool for tool in tools})

    monkeypatch.setattr(runtime, "get_toolset", fake_tools)
    monkeypatch.setattr(workflow_service, "get_toolset", fake_tools)


def _patch_guardrail(monkeypatch: pytest.MonkeyPatch, guardrail: StubGuardrailClient) -> None:
    import app.guardrails.service as guardrail_service
    import app.workflow_service as workflow_service

    monkeypatch.setattr(guardrail_service, "get_guardrail_client", lambda: guardrail)
    monkeypatch.setattr(workflow_service, "get_guardrail_client", lambda: guardrail)


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _configure_env(monkeypatch)
    from app.app_factory import create_app

    return TestClient(create_app())


def test_read_only_discovery_flow_returns_ready_with_mcp_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("searchOSLProductOfferings", [{"text": '{"items":[{"name":"Starter"}]}'}])
    _patch_model(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"category": "mobile"}, "id": "call-1"}]),
            AIMessage(content="I found the available offerings."),
        ],
    )
    _patch_tools(monkeypatch, [tool])
    _patch_guardrail(monkeypatch, StubGuardrailClient())

    client = _client(monkeypatch)
    response = client.post("/v1/chat", json={"message": "List product offerings"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["draft"] is None
    assert body["pending_action"] is None
    assert body["execution_result"] is None
    assert body["tool_traces"] == [
        {
            "tool_name": "searchOSLProductOfferings",
            "arguments": {"category": "mobile"},
            "status": "success",
            "stage": "mcp",
            "result_preview": '{"items":[{"name":"Starter"}]}',
        }
    ]
    assert tool.calls == [{"category": "mobile"}]


def test_order_draft_flow_creates_pending_confirmation_without_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    guardrail = StubGuardrailClient(selection_message="Selection approved for draft.")
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])
    _patch_guardrail(monkeypatch, guardrail)

    client = _client(monkeypatch)
    response = client.post("/v1/chat", json={"message": "Order Premium Fiber"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_confirmation"
    assert body["draft"]["tool_name"] == "createProductOrder"
    assert body["draft"]["display_name"] == "Premium Fiber"
    assert body["pending_action"] == "confirm_product_order"
    assert body["execution_result"] is None
    assert body["tool_traces"] == [
        {
            "tool_name": "createProductOrder",
            "arguments": {"productName": "Premium Fiber"},
            "status": "allow",
            "stage": "guardrail",
            "result_preview": "Selection approved for draft.",
        },
        {
            "tool_name": "createProductOrder",
            "arguments": {"productName": "Premium Fiber"},
            "status": "allow",
            "stage": "guardrail",
            "result_preview": "Prepare Premium Fiber via createProductOrder.",
        },
    ]
    assert tool.calls == []
    assert guardrail.validate_calls == [("createProductOrder", {"productName": "Premium Fiber"})]


def test_confirmation_executes_pending_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", [{"text": '{"id":"po-1","state":"acknowledged"}'}])
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])
    _patch_guardrail(monkeypatch, StubGuardrailClient())

    client = _client(monkeypatch)
    initial = client.post("/v1/chat", json={"message": "Order Premium Fiber"}).json()
    response = client.post(f"/v1/chat/{initial['thread_id']}/confirm", json={"confirmed": True})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["draft"] is None
    assert body["pending_action"] is None
    assert body["execution_result"] == {
        "execution_token": f"exec-{initial['thread_id']}",
        "tool_name": "createProductOrder",
        "status": "executed",
        "result_preview": '{"id":"po-1","state":"acknowledged"}',
    }
    assert [trace["status"] for trace in body["tool_traces"]] == ["allow", "allow", "success"]
    assert tool.calls == [{"productName": "Premium Fiber"}]


def test_rejecting_confirmation_cancels_pending_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])
    _patch_guardrail(monkeypatch, StubGuardrailClient())

    client = _client(monkeypatch)
    initial = client.post("/v1/chat", json={"message": "Order Premium Fiber"}).json()
    response = client.post(f"/v1/chat/{initial['thread_id']}/confirm", json={"confirmed": False})

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": initial["thread_id"],
        "status": "ready",
        "message": "The product order draft was canceled.",
        "tool_traces": [],
        "draft": None,
        "pending_action": None,
        "execution_result": None,
        "error": None,
    }
    assert tool.calls == []


def test_duplicate_confirmation_after_execution_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", [{"text": '{"id":"po-1","state":"acknowledged"}'}])
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])
    _patch_guardrail(monkeypatch, StubGuardrailClient())

    client = _client(monkeypatch)
    initial = client.post("/v1/chat", json={"message": "Order Premium Fiber"}).json()
    client.post(f"/v1/chat/{initial['thread_id']}/confirm", json={"confirmed": True})
    duplicate = client.post(f"/v1/chat/{initial['thread_id']}/confirm", json={"confirmed": True})

    assert duplicate.status_code == 200
    body = duplicate.json()
    assert body["status"] == "blocked"
    assert body["error"]["code"] == "missing_pending_draft"
    assert body["draft"] is None
    assert body["execution_result"] is None


def test_guardrail_rejection_blocks_draft_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])
    _patch_guardrail(
        monkeypatch,
        StubGuardrailClient(selection_status="deny", selection_message="Order denied by policy."),
    )

    client = _client(monkeypatch)
    response = client.post("/v1/chat", json={"message": "Order Premium Fiber"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["error"]["code"] == "guardrail_deny"
    assert body["pending_action"] is None
    assert body["draft"] is None
    assert body["tool_traces"] == [
        {
            "tool_name": "createProductOrder",
            "arguments": {"productName": "Premium Fiber"},
            "status": "deny",
            "stage": "guardrail",
            "result_preview": "Order denied by policy.",
        }
    ]
    assert tool.calls == []


def test_unauthorized_confirmation_keeps_draft_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])
    _patch_guardrail(
        monkeypatch,
        StubGuardrailClient(
            authorization_status="unauthorized",
            authorization_message="Execution blocked by authorization policy.",
        ),
    )

    client = _client(monkeypatch)
    initial = client.post("/v1/chat", json={"message": "Order Premium Fiber"}).json()
    response = client.post(f"/v1/chat/{initial['thread_id']}/confirm", json={"confirmed": True})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["error"]["code"] == "guardrail_unauthorized"
    assert body["pending_action"] == "confirm_product_order"
    assert body["draft"]["display_name"] == "Premium Fiber"
    assert body["execution_result"] is None
    assert body["tool_traces"] == [
        {
            "tool_name": "createProductOrder",
            "arguments": {"productName": "Premium Fiber"},
            "status": "unauthorized",
            "stage": "guardrail",
            "result_preview": "Execution blocked by authorization policy.",
        }
    ]
    assert tool.calls == []


def test_execution_failure_returns_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", RuntimeError("backend failed"))
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])
    _patch_guardrail(monkeypatch, StubGuardrailClient())

    client = _client(monkeypatch)
    initial = client.post("/v1/chat", json={"message": "Order Premium Fiber"}).json()
    response = client.post(f"/v1/chat/{initial['thread_id']}/confirm", json={"confirmed": True})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "tool_execution_failed"
    assert body["error"]["retryable"] is False
    assert body["draft"]["display_name"] == "Premium Fiber"
    assert body["pending_action"] == "confirm_product_order"
    assert body["execution_result"] is None
    assert body["tool_traces"][-1]["status"] == "error"


def test_reset_during_pending_draft_clears_confirmation_state(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool("createProductOrder", {"id": "po-1"})
    _patch_model(
        monkeypatch,
        [AIMessage(content="", tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}])],
    )
    _patch_tools(monkeypatch, [tool])
    _patch_guardrail(monkeypatch, StubGuardrailClient())

    client = _client(monkeypatch)
    initial = client.post("/v1/chat", json={"message": "Order Premium Fiber"}).json()
    reset = client.post(f"/v1/chat/{initial['thread_id']}/reset")
    confirm = client.post(f"/v1/chat/{initial['thread_id']}/confirm", json={"confirmed": True})

    assert reset.status_code == 200
    assert reset.json() == {"thread_id": initial["thread_id"], "status": "reset"}
    assert confirm.status_code == 200
    assert confirm.json()["error"]["code"] == "missing_pending_draft"
