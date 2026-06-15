from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from app import chat_service
from app.config import AppSettings
from app.conversation_store import conversation_store
from app.prompting import PROMPT_FAMILY, PROMPT_VERSION, build_base_system_prompt, build_mcp_unavailable_system_prompt
from app.runtime import McpFailure, McpOperationError, Toolset, reset_runtime_caches
import app.runtime as runtime
import app.workflow_service as workflow_service


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


@pytest.fixture(autouse=True)
def reset_store() -> None:
    conversation_store._conversations.clear()
    reset_runtime_caches()


def _load_corpus() -> list[dict[str, object]]:
    corpus_path = Path(__file__).resolve().parents[1] / "evals" / "prompt_eval_corpus.json"
    return json.loads(corpus_path.read_text(encoding="utf-8"))


CORPUS = _load_corpus()


def _settings() -> AppSettings:
    return AppSettings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model_name="gpt-4.1-mini",
        llm_api_base_url="https://api.openai.com/v1",
        tool_server_name="openslice",
        tool_server_transport="streamable_http",
        tool_server_url="http://127.0.0.1:8003/mcp",
        product_read_tool_names="searchOSLProductOfferings",
        product_write_tool_names="createProductOrder",
    )


def _patch_model(monkeypatch: pytest.MonkeyPatch, replies: list[AIMessage]) -> None:
    monkeypatch.setattr(runtime, "create_model", lambda settings=None: FakeModel(replies))
    monkeypatch.setattr(workflow_service, "create_model", lambda settings=None: FakeModel(replies))


def _patch_tools(monkeypatch: pytest.MonkeyPatch, tools: list[FakeTool]) -> None:
    async def fake_tools(settings=None) -> chat_service.Toolset:
        return Toolset(tools, {tool.name: tool for tool in tools})

    monkeypatch.setattr(runtime, "get_toolset", fake_tools)
    monkeypatch.setattr(workflow_service, "get_toolset", fake_tools)


@pytest.mark.parametrize("scenario", CORPUS, ids=lambda item: str(item["id"]))
def test_prompt_corpus_requirements_are_present(scenario: dict[str, object]) -> None:
    settings = _settings()
    prompt = (
        build_mcp_unavailable_system_prompt(settings)
        if scenario["prompt_kind"] == "unavailable"
        else build_base_system_prompt(settings)
    )

    for fragment in scenario["required_prompt_fragments"]:
        assert str(fragment) in prompt


def test_prompt_version_identifiers_are_stable() -> None:
    assert PROMPT_FAMILY == "mcp-native-product-order"
    assert PROMPT_VERSION == "v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [scenario for scenario in CORPUS if scenario["eval_type"] != "mcp_unavailable"],
    ids=lambda item: str(item["id"]),
)
async def test_prompt_eval_corpus_workflow_scenarios(monkeypatch: pytest.MonkeyPatch, scenario: dict[str, object]) -> None:
    eval_type = str(scenario["eval_type"])
    request = str(scenario["request"])

    if eval_type == "read":
        tool = FakeTool("searchOSLProductOfferings", [{"text": '{"items":[{"name":"Starter"}]}'}])
        final_reply = AIMessage(content="I found product options for your site.")
        _patch_model(
            monkeypatch,
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": tool.name, "args": {"query": request}, "id": "call-1"}],
                ),
                final_reply,
            ],
        )
        _patch_tools(monkeypatch, [tool])
    elif eval_type == "write":
        tool = FakeTool("createProductOrder", {"id": "po-1"})
        _patch_model(
            monkeypatch,
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": tool.name, "args": {"productName": "Premium Fiber"}, "id": "call-1"}],
                )
            ],
        )
        _patch_tools(monkeypatch, [tool])
    else:
        tool = FakeTool("createProductOrder", {"id": "po-1"})
        _patch_model(
            monkeypatch,
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": tool.name, "args": {}, "id": "call-1"}],
                )
            ],
        )
        _patch_tools(monkeypatch, [tool])

    response = await chat_service.run_chat(request)

    assert response.status == scenario["expected_status"]
    if response.tool_traces:
        assert response.tool_traces[0].tool_name == scenario["expected_tool_name"]
    if scenario.get("expected_error_code"):
        assert response.error is not None
        assert response.error.code == scenario["expected_error_code"]
    for term in scenario["forbidden_response_terms"]:
        assert str(term).lower() not in response.message.lower()


@pytest.mark.asyncio
async def test_prompt_eval_corpus_mcp_unavailable_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = next(item for item in CORPUS if item["eval_type"] == "mcp_unavailable")
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

    response = await chat_service.run_chat(str(scenario["request"]))

    assert response.status == scenario["expected_status"]
    assert response.error is not None
    assert response.error.code == scenario["expected_error_code"]
    assert response.tool_traces[0].tool_name == scenario["expected_tool_name"]
    for term in scenario["forbidden_response_terms"]:
        assert str(term).lower() not in response.message.lower()
