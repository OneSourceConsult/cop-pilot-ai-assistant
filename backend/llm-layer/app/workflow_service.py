from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.config import get_settings
from app.conversation_store import ConversationState, conversation_store
from app.guardrails.models import DraftSummary
from app.guardrails.service import get_guardrail_client
from app.prompting import build_base_system_prompt
from app.response_factory import build_draft_response, build_error, build_execution_response
from app.runtime import McpFailure, McpOperationError, Toolset, create_model, get_toolset, invoke_tool
from app.schemas import ChatResponse, ToolTrace


def text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return "\n".join(parts).strip()
    if content is None:
        return ""
    return str(content)


def tool_result_to_text(result: object) -> str:
    if isinstance(result, list) and result and isinstance(result[0], dict) and "text" in result[0]:
        return str(result[0]["text"])
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=True, default=str)


def preview_text(text: str, limit: int = 500) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def reply_text(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = text_from_content(message.content).strip()
            if text:
                return text
    return "I could not produce a final answer."


def new_conversation_id() -> str:
    return str(uuid.uuid4())


def append_trace(
    traces: list[ToolTrace],
    tool_name: str,
    status: str,
    result_preview: str,
    arguments: dict[str, object] | None = None,
    *,
    stage: str,
) -> None:
    traces.append(
        ToolTrace(
            tool_name=tool_name,
            arguments=arguments or {},
            status=status,
            stage=stage,
            result_preview=preview_text(result_preview),
        )
    )


async def execute_read_tool(
    conversation_id: str,
    conversation: list[BaseMessage],
    traces: list[ToolTrace],
    tool: BaseTool,
    tool_name: str,
    raw_args: object,
    args: dict[str, object],
    tool_call_id: str,
    settings,
) -> ChatResponse | None:
    try:
        result = tool_result_to_text(await invoke_tool(tool, raw_args, settings=settings, allow_retry=True))
        append_trace(traces, tool_name, "success", result, args, stage="mcp")
        conversation.append(ToolMessage(content=result, tool_call_id=tool_call_id, name=tool_name))
        return None
    except McpOperationError as exc:
        return build_mcp_error_response(
            thread_id=conversation_id,
            traces=traces,
            tool_name=tool_name,
            arguments=args,
            failure=exc.failure,
        )


async def maybe_create_draft_response(
    conversation_id: str,
    state: ConversationState,
    traces: list[ToolTrace],
    tool_name: str,
    args: dict[str, object],
) -> ChatResponse | None:
    guardrail = get_guardrail_client()
    validation = guardrail.validate_product_selection(tool_name, args)
    append_trace(traces, tool_name, validation.status, validation.message, args, stage="guardrail")

    if validation.status != "allow":
        state.pending_draft = None
        conversation_store.save_state(conversation_id, state)
        return ChatResponse(
            thread_id=conversation_id,
            status="blocked",
            message=validation.message,
            tool_traces=traces,
            error=build_error(
                f"guardrail_{validation.status}",
                validation.message,
                validation_status=validation.status,
                tool_name=tool_name,
            ),
        )

    draft = guardrail.build_product_order_draft(tool_name, args, conversation_id)
    state.pending_draft = draft
    conversation_store.save_state(conversation_id, state)
    append_trace(traces, tool_name, "allow", draft.summary, draft.normalized_arguments, stage="guardrail")

    return ChatResponse(
        thread_id=conversation_id,
        status="needs_confirmation",
        message=(
            f"Draft prepared for product order `{draft.display_name}`. "
            "Review the draft and confirm before execution."
        ),
        tool_traces=traces,
        draft=build_draft_response(draft),
        pending_action="confirm_product_order",
    )


async def run_chat(message: str, thread_id: str | None = None, reset: bool = False) -> ChatResponse:
    settings = get_settings()
    conversation_id = thread_id or new_conversation_id()

    if reset:
        conversation_store.reset(conversation_id)

    state = conversation_store.get_state(conversation_id)
    history = list(state.messages)
    conversation: list[BaseMessage] = [
        SystemMessage(content=build_base_system_prompt(settings)),
        *history,
        HumanMessage(content=message),
    ]

    traces: list[ToolTrace] = []
    model = create_model(settings)

    try:
        toolset = await get_toolset(settings)
    except McpOperationError as exc:
        return build_mcp_error_response(
            thread_id=conversation_id,
            traces=traces,
            tool_name="mcp_connection",
            arguments={},
            failure=exc.failure,
        )

    model_with_tools = model.bind_tools(toolset.tools)
    guardrail = get_guardrail_client()

    for _ in range(settings.chat_max_tool_rounds):
        try:
            reply = await model_with_tools.ainvoke(conversation)
        except Exception as exc:
            return ChatResponse(
                thread_id=conversation_id,
                status="error",
                message="The assistant could not complete the request right now.",
                tool_traces=traces,
                error=build_error(
                    "llm_request_failed",
                    "The assistant could not complete the request right now.",
                    retryable=True,
                    error_type=type(exc).__name__,
                    provider_message=preview_text(str(exc), 240),
                ),
            )
        conversation.append(reply)
        if not isinstance(reply, AIMessage) or not reply.tool_calls:
            break

        for call in reply.tool_calls:
            name = str(call.get("name", ""))
            raw_args = call.get("args", {}) or {}
            args = raw_args if isinstance(raw_args, dict) else {}
            tool = toolset.by_name.get(name)
            tool_call_id = str(call.get("id", name))

            if tool is None:
                return build_mcp_error_response(
                    thread_id=conversation_id,
                    traces=traces,
                    tool_name=name,
                    arguments=args,
                    failure=McpFailure(
                        code="tool_not_found",
                        trace_status="missing",
                        user_message="The requested platform capability is not available right now.",
                        retryable=False,
                        detail_message=f"Tool '{name}' is not available from the MCP server.",
                    ),
                )

            mode = guardrail.classify_tool(name)
            if mode == "unknown":
                mode = "write"

            if mode == "read":
                response = await execute_read_tool(
                    conversation_id,
                    conversation,
                    traces,
                    tool,
                    name,
                    raw_args,
                    args,
                    tool_call_id,
                    settings,
                )
                if response is not None:
                    state.messages = conversation[1:]
                    conversation_store.save_state(conversation_id, state)
                    return response
                continue

            response = await maybe_create_draft_response(conversation_id, state, traces, name, args)
            state.messages = conversation[1:]
            conversation_store.save_state(conversation_id, state)
            if response is not None:
                return response

    final_message = reply_text(conversation)
    state.messages = conversation[1:]
    conversation_store.save_state(conversation_id, state)

    return ChatResponse(thread_id=conversation_id, status="ready", message=final_message, tool_traces=traces)


async def confirm_chat(thread_id: str, confirmed: bool) -> ChatResponse:
    state = conversation_store.get_state(thread_id)
    traces: list[ToolTrace] = []
    draft = state.pending_draft

    if draft is None:
        return ChatResponse(
            thread_id=thread_id,
            status="blocked",
            message="There is no pending product order draft to confirm.",
            tool_traces=traces,
            error=build_error("missing_pending_draft", "There is no pending product order draft to confirm."),
        )

    if not confirmed:
        state.pending_draft = None
        conversation_store.save_state(thread_id, state)
        return ChatResponse(
            thread_id=thread_id,
            status="ready",
            message="The product order draft was canceled.",
            tool_traces=traces,
        )

    guardrail = get_guardrail_client()
    authorization = guardrail.authorize_execution(draft, thread_id)
    append_trace(
        traces,
        draft.tool_name,
        authorization.status,
        authorization.message,
        draft.normalized_arguments,
        stage="guardrail",
    )
    if authorization.status != "allow":
        return ChatResponse(
            thread_id=thread_id,
            status="blocked",
            message=authorization.message,
            tool_traces=traces,
            draft=build_draft_response(draft),
            pending_action="confirm_product_order",
            error=build_error(
                f"guardrail_{authorization.status}",
                authorization.message,
                validation_status=authorization.status,
                tool_name=draft.tool_name,
            ),
        )

    duplicate_check = guardrail.check_idempotency(thread_id, draft.fingerprint, state.last_execution)
    append_trace(
        traces,
        draft.tool_name,
        duplicate_check.status,
        duplicate_check.message,
        draft.normalized_arguments,
        stage="guardrail",
    )
    if duplicate_check.status != "allow":
        return ChatResponse(
            thread_id=thread_id,
            status="blocked",
            message=duplicate_check.message,
            tool_traces=traces,
            draft=build_draft_response(draft),
            execution_result=build_execution_response(state.last_execution, "duplicate") if state.last_execution else None,
            error=build_error(
                f"guardrail_{duplicate_check.status}",
                duplicate_check.message,
                validation_status=duplicate_check.status,
                tool_name=draft.tool_name,
            ),
        )

    try:
        toolset = await get_toolset()
    except McpOperationError as exc:
        return build_mcp_error_response(
            thread_id=thread_id,
            traces=traces,
            tool_name="mcp_connection",
            arguments=draft.normalized_arguments,
            failure=exc.failure,
            draft=draft,
            pending_action="confirm_product_order",
        )

    tool = toolset.by_name.get(draft.tool_name)
    if tool is None:
        return build_mcp_error_response(
            thread_id=thread_id,
            traces=traces,
            tool_name=draft.tool_name,
            arguments=draft.normalized_arguments,
            failure=McpFailure(
                code="tool_not_found",
                trace_status="missing",
                user_message="The requested platform capability is not available right now.",
                retryable=False,
                detail_message=f"Tool '{draft.tool_name}' is not available from the MCP server.",
            ),
            draft=draft,
            pending_action="confirm_product_order",
        )

    try:
        raw_result = await invoke_tool(tool, draft.normalized_arguments, allow_retry=False)
        result_text = tool_result_to_text(raw_result)
        append_trace(traces, draft.tool_name, "success", result_text, draft.normalized_arguments, stage="mcp")
    except McpOperationError as exc:
        return build_mcp_error_response(
            thread_id=thread_id,
            traces=traces,
            tool_name=draft.tool_name,
            arguments=draft.normalized_arguments,
            failure=exc.failure,
            draft=draft,
            pending_action="confirm_product_order",
        )

    execution = guardrail.register_execution(thread_id, draft)
    execution.result_preview = preview_text(result_text)
    state.last_execution = execution
    state.pending_draft = None
    state.messages.append(HumanMessage(content=f"Confirm product order draft {draft.display_name}"))
    state.messages.append(AIMessage(content=f"Product order executed for {draft.display_name}."))
    conversation_store.save_state(thread_id, state)

    return ChatResponse(
        thread_id=thread_id,
        status="executed",
        message=f"Product order executed for {draft.display_name}.",
        tool_traces=traces,
        execution_result=build_execution_response(execution, "executed"),
    )


def build_mcp_error_response(
    *,
    thread_id: str,
    traces: list[ToolTrace],
    tool_name: str,
    arguments: dict[str, object],
    failure: McpFailure,
    draft: DraftSummary | None = None,
    pending_action: str | None = None,
) -> ChatResponse:
    append_trace(traces, tool_name, failure.trace_status, failure.detail_message, arguments, stage="mcp")
    return ChatResponse(
        thread_id=thread_id,
        status="error",
        message=failure.user_message,
        tool_traces=traces,
        draft=build_draft_response(draft) if draft else None,
        pending_action=pending_action,
        error=build_error(failure.code, failure.user_message, retryable=failure.retryable, tool_name=tool_name),
    )
