from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.config import get_settings
from app.conversation_store import ConversationState, conversation_store
from app.guardrails.models import DraftSummary
from app.guardrails.service import get_guardrail_client
from app.id_utils import uuid7
from app.observability import LlmInteractionMetrics
from app.order_requirements import (
    apply_order_date_defaults,
    model_safe_offering_result,
    offering_context_from_result,
    platform_managed_characteristic_names,
    product_specification_id_from_result,
)
from app.prompting import build_base_system_prompt, build_offering_context_prompt
from app.response_factory import build_draft_response, build_error, build_execution_response
from app.runtime import McpFailure, McpOperationError, Toolset, create_model, get_toolset, invoke_tool
from app.schemas import (
    BlockedChatResponse,
    ChatResponse,
    ConfirmChatRequest,
    ErrorChatResponse,
    ExecutedChatResponse,
    NeedsConfirmationChatResponse,
    ReadyChatResponse,
    ToolTrace,
)


logger = logging.getLogger(__name__)


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
    return uuid7()


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


def visible_traces(traces: list[ToolTrace], include_traces: bool) -> list[ToolTrace] | None:
    if include_traces:
        return traces
    return None


def remove_platform_managed_characteristics_from_conversation(
    conversation: list[BaseMessage], platform_managed_names: set[str]
) -> None:
    if not platform_managed_names:
        return
    for index, message in enumerate(conversation):
        if isinstance(message, ToolMessage):
            conversation[index] = ToolMessage(
                content=model_safe_offering_result(text_from_content(message.content), platform_managed_names),
                tool_call_id=message.tool_call_id,
                name=message.name,
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
    include_traces: bool,
    state: ConversationState,
    toolset: Toolset,
) -> ChatResponse | None:
    try:
        result = tool_result_to_text(await invoke_tool(tool, raw_args, settings=settings, allow_retry=True))
        append_trace(traces, tool_name, "success", result, args, stage="mcp")
        specification_result: str | None = None
        specification_id = product_specification_id_from_result(result)
        specification_tool = toolset.by_name.get("getOSLProductByProductSpecificationId")
        if specification_id and specification_tool is not None:
            specification_args = {"productSpecId": specification_id}
            try:
                specification_result = tool_result_to_text(
                    await invoke_tool(specification_tool, specification_args, settings=settings, allow_retry=True)
                )
                append_trace(
                    traces,
                    specification_tool.name,
                    "success",
                    specification_result,
                    specification_args,
                    stage="mcp",
                )
            except McpOperationError as exc:
                append_trace(
                    traces,
                    specification_tool.name,
                    exc.failure.trace_status,
                    exc.failure.detail_message,
                    specification_args,
                    stage="mcp",
                )

        platform_managed_names = platform_managed_characteristic_names(result)
        if specification_result is not None:
            platform_managed_names.update(platform_managed_characteristic_names(specification_result))
        remove_platform_managed_characteristics_from_conversation(conversation, platform_managed_names)
        offering_context = offering_context_from_result(specification_result or result)
        if offering_context is not None:
            state.offering_context = offering_context
        tool_content = [model_safe_offering_result(result, platform_managed_names)]
        if specification_result is not None:
            tool_content.append(
                "Detailed product specification from the live platform:\n"
                f"{model_safe_offering_result(specification_result, platform_managed_names)}"
            )
        if offering_context is not None:
            tool_content.append(build_offering_context_prompt(offering_context))
        conversation.append(
            ToolMessage(
                content="\n\n".join(tool_content),
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        )
        return None
    except McpOperationError as exc:
        return build_mcp_error_response(
            thread_id=conversation_id,
            traces=traces,
            tool_name=tool_name,
            arguments=args,
            failure=exc.failure,
            include_traces=include_traces,
        )


async def maybe_create_draft_response(
    conversation_id: str,
    state: ConversationState,
    traces: list[ToolTrace],
    tool_name: str,
    args: dict[str, object],
    include_traces: bool,
) -> ChatResponse | None:
    guardrail = get_guardrail_client()
    args, defaulted_dates = apply_order_date_defaults(tool_name, args)
    if tool_name == "createProductOrder" and state.offering_context is not None:
        parameter_validation = guardrail.validate_order_parameters(tool_name, args, state.offering_context)
        append_trace(traces, tool_name, parameter_validation.status, parameter_validation.message, args, stage="guardrail")
        if parameter_validation.status != "allow":
            state.pending_draft = None
            conversation_store.save_state(conversation_id, state)
            return BlockedChatResponse(
                thread_id=conversation_id,
                status="blocked",
                message=parameter_validation.message,
                tool_traces=visible_traces(traces, include_traces),
                error=build_error(
                    f"guardrail_{parameter_validation.status}",
                    parameter_validation.message,
                    validation_status=parameter_validation.status,
                    tool_name=tool_name,
                ),
            )
    validation = guardrail.validate_product_selection(tool_name, args)
    append_trace(traces, tool_name, validation.status, validation.message, args, stage="guardrail")

    if validation.status != "allow":
        state.pending_draft = None
        conversation_store.save_state(conversation_id, state)
        return BlockedChatResponse(
            thread_id=conversation_id,
            status="blocked",
            message=validation.message,
            tool_traces=visible_traces(traces, include_traces),
            error=build_error(
                f"guardrail_{validation.status}",
                validation.message,
                validation_status=validation.status,
                tool_name=tool_name,
            ),
        )

    draft = guardrail.build_product_order_draft(tool_name, args, conversation_id)
    if draft.display_name == "Selected product" and state.offering_context is not None:
        offering_name = state.offering_context.offering_name
        draft = draft.model_copy(
            update={
                "display_name": offering_name,
                "summary": f"Prepare product order via `{tool_name}` for {offering_name}.",
            }
        )
    state.pending_draft = draft
    conversation_store.save_state(conversation_id, state)
    append_trace(traces, tool_name, "allow", draft.summary, draft.normalized_arguments, stage="guardrail")

    start_date = draft.normalized_arguments.get("startDate")
    end_date = draft.normalized_arguments.get("endDate")
    date_message = f"This draft uses {start_date} to {end_date}. " if start_date and end_date else ""
    if defaulted_dates:
        reason = "No dates were provided" if len(defaulted_dates) == 2 else "The missing date was filled automatically"
        date_message = (
            f"{reason}, so this draft uses {draft.normalized_arguments['startDate']} "
            f"to {draft.normalized_arguments['endDate']}. "
        )

    return NeedsConfirmationChatResponse(
        thread_id=conversation_id,
        status="needs_confirmation",
        message=(
            f"Draft prepared for product order `{draft.display_name}`. "
            f"{date_message}Review the draft and confirm before execution."
        ),
        tool_traces=visible_traces(traces, include_traces),
        draft=build_draft_response(draft),
    )


async def run_chat(
    message: str,
    thread_id: str | None = None,
    *,
    include_traces: bool = True,
    telemetry: LlmInteractionMetrics | None = None,
) -> ChatResponse:
    settings = get_settings()
    conversation_id = thread_id or new_conversation_id()
    resolved_telemetry = telemetry or LlmInteractionMetrics()

    state = conversation_store.get_state(conversation_id)
    history = list(state.messages)
    conversation: list[BaseMessage] = [
        SystemMessage(content="\n\n".join(filter(None, [build_base_system_prompt(settings), build_offering_context_prompt(state.offering_context)]))),
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
            include_traces=include_traces,
        )

    model_with_tools = model.bind_tools(toolset.tools)
    guardrail = get_guardrail_client()

    for _ in range(settings.chat_max_tool_rounds):
        resolved_telemetry.record_attempt()
        try:
            reply = await asyncio.wait_for(
                model_with_tools.ainvoke(conversation),
                timeout=settings.llm_request_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return ErrorChatResponse(
                thread_id=conversation_id,
                status="error",
                message="The assistant could not complete the request in time.",
                tool_traces=visible_traces(traces, include_traces),
                error=build_error(
                    "llm_request_failed",
                    "The assistant could not complete the request in time.",
                    retryable=True,
                    error_type="TimeoutError",
                    provider_message=(
                        f"LLM request timed out after {settings.llm_request_timeout_seconds:g} seconds."
                    ),
                ),
            )
        except Exception as exc:
            return ErrorChatResponse(
                thread_id=conversation_id,
                status="error",
                message="The assistant could not complete the request right now.",
                tool_traces=visible_traces(traces, include_traces),
                error=build_error(
                    "llm_request_failed",
                    "The assistant could not complete the request right now.",
                    retryable=True,
                    error_type=type(exc).__name__,
                    provider_message=preview_text(str(exc), 240),
                ),
            )
        resolved_telemetry.record_reply(reply)
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
                    include_traces=include_traces,
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
                    include_traces,
                    state,
                    toolset,
                )
                if response is not None:
                    state.messages = conversation[1:]
                    conversation_store.save_state(conversation_id, state)
                    return response
                continue

            response = await maybe_create_draft_response(conversation_id, state, traces, name, args, include_traces)
            state.messages = conversation[1:]
            conversation_store.save_state(conversation_id, state)
            if response is not None:
                return response

    final_message = reply_text(conversation)
    state.messages = conversation[1:]
    conversation_store.save_state(conversation_id, state)

    return ReadyChatResponse(
        thread_id=conversation_id,
        status="ready",
        message=final_message,
        tool_traces=visible_traces(traces, include_traces),
    )


async def confirm_chat(
    thread_id: str,
    payload: ConfirmChatRequest,
    *,
    include_traces: bool = True,
) -> ChatResponse:
    state = conversation_store.get_state(thread_id)
    traces: list[ToolTrace] = []
    draft = state.pending_draft

    if draft is None:
        last_execution = state.last_execution
        if (
            last_execution is not None
            and last_execution.draft_id == payload.draft_id
            and last_execution.draft_fingerprint == payload.fingerprint
            and last_execution.idempotency_key == payload.idempotency_key
        ):
            return ExecutedChatResponse(
                thread_id=thread_id,
                status="executed",
                message=f"Product order executed for {last_execution.display_name}.",
                tool_traces=visible_traces(traces, include_traces),
                execution_result=build_execution_response(last_execution),
            )

        return BlockedChatResponse(
            thread_id=thread_id,
            status="blocked",
            message="There is no pending product order draft to confirm.",
            tool_traces=visible_traces(traces, include_traces),
            error=build_error("missing_pending_draft", "There is no pending product order draft to confirm."),
        )

    if payload.draft_id != draft.draft_id or payload.fingerprint != draft.fingerprint:
        return BlockedChatResponse(
            thread_id=thread_id,
            status="blocked",
            message="The confirmation details do not match the current pending draft.",
            tool_traces=visible_traces(traces, include_traces),
            draft=build_draft_response(draft),
            error=build_error(
                "invalid_confirmation",
                "The confirmation details do not match the current pending draft.",
                draft_id=draft.draft_id,
                fingerprint=draft.fingerprint,
            ),
        )

    if state.last_execution is not None and state.last_execution.idempotency_key == payload.idempotency_key:
        if (
            state.last_execution.draft_id == payload.draft_id
            and state.last_execution.draft_fingerprint == payload.fingerprint
        ):
            return ExecutedChatResponse(
                thread_id=thread_id,
                status="executed",
                message=f"Product order executed for {state.last_execution.display_name}.",
                tool_traces=visible_traces(traces, include_traces),
                execution_result=build_execution_response(state.last_execution),
            )
        return BlockedChatResponse(
            thread_id=thread_id,
            status="blocked",
            message="The idempotency key was already used for a different confirmation request.",
            tool_traces=visible_traces(traces, include_traces),
            draft=build_draft_response(draft),
            error=build_error(
                "idempotency_key_reused",
                "The idempotency key was already used for a different confirmation request.",
            ),
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
        return BlockedChatResponse(
            thread_id=thread_id,
            status="blocked",
            message=authorization.message,
            tool_traces=visible_traces(traces, include_traces),
            draft=build_draft_response(draft),
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
        details: dict[str, object] = {}
        if state.last_execution is not None:
            details["execution_token"] = state.last_execution.execution_token
        return BlockedChatResponse(
            thread_id=thread_id,
            status="blocked",
            message=duplicate_check.message,
            tool_traces=visible_traces(traces, include_traces),
            draft=build_draft_response(draft),
            error=build_error(
                f"guardrail_{duplicate_check.status}",
                duplicate_check.message,
                validation_status=duplicate_check.status,
                tool_name=draft.tool_name,
                **details,
            ),
        )

    logger.info(
        "product_order_confirm_start thread_id=%s draft_id=%s tool=%s argument_keys=%s offering_ids=%s",
        thread_id,
        draft.draft_id,
        draft.tool_name,
        sorted(draft.normalized_arguments),
        sorted((draft.normalized_arguments.get("offeringsWithCharacteristics") or {}).keys()),
    )

    try:
        toolset = await get_toolset()
    except McpOperationError as exc:
        logger.exception(
            "product_order_mcp_connection_failed thread_id=%s draft_id=%s code=%s detail=%s",
            thread_id,
            draft.draft_id,
            exc.failure.code,
            exc.failure.detail_message,
        )
        return build_mcp_error_response(
            thread_id=thread_id,
            traces=traces,
            tool_name="mcp_connection",
            arguments=draft.normalized_arguments,
            failure=exc.failure,
            draft=draft,
            include_traces=include_traces,
        )

    tool = toolset.by_name.get(draft.tool_name)
    logger.info(
        "product_order_toolset_loaded thread_id=%s draft_id=%s tool_count=%s requested_tool=%s tool_found=%s",
        thread_id,
        draft.draft_id,
        len(toolset.tools),
        draft.tool_name,
        tool is not None,
    )
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
            include_traces=include_traces,
        )

    try:
        logger.info(
            "product_order_mcp_invoke_start thread_id=%s draft_id=%s tool=%s",
            thread_id,
            draft.draft_id,
            draft.tool_name,
        )
        raw_result = await invoke_tool(tool, draft.normalized_arguments, allow_retry=False)
        result_text = tool_result_to_text(raw_result)
        logger.info(
            "product_order_mcp_invoke_success thread_id=%s draft_id=%s tool=%s result_type=%s result_length=%s",
            thread_id,
            draft.draft_id,
            draft.tool_name,
            type(raw_result).__name__,
            len(result_text),
        )
        append_trace(traces, draft.tool_name, "success", result_text, draft.normalized_arguments, stage="mcp")
    except McpOperationError as exc:
        logger.exception(
            "product_order_mcp_invoke_failed thread_id=%s draft_id=%s tool=%s code=%s detail=%s",
            thread_id,
            draft.draft_id,
            draft.tool_name,
            exc.failure.code,
            exc.failure.detail_message,
        )
        return build_mcp_error_response(
            thread_id=thread_id,
            traces=traces,
            tool_name=draft.tool_name,
            arguments=draft.normalized_arguments,
            failure=exc.failure,
            draft=draft,
            include_traces=include_traces,
        )

    execution = guardrail.register_execution(thread_id, draft, payload.idempotency_key)
    execution.result_preview = preview_text(result_text)
    state.last_execution = execution
    state.pending_draft = None
    state.messages.append(HumanMessage(content=f"Confirm product order draft {draft.display_name}"))
    state.messages.append(AIMessage(content=f"Product order executed for {draft.display_name}."))
    conversation_store.save_state(thread_id, state)

    return ExecutedChatResponse(
        thread_id=thread_id,
        status="executed",
        message=f"Product order executed for {draft.display_name}.",
        tool_traces=visible_traces(traces, include_traces),
        execution_result=build_execution_response(execution),
    )


async def cancel_chat(thread_id: str, *, include_traces: bool = True) -> ChatResponse:
    state = conversation_store.get_state(thread_id)

    if state.pending_draft is None:
        return BlockedChatResponse(
            thread_id=thread_id,
            status="blocked",
            message="There is no pending product order draft to cancel.",
            tool_traces=visible_traces([], include_traces),
            error=build_error("missing_pending_draft", "There is no pending product order draft to cancel."),
        )

    state.pending_draft = None
    conversation_store.save_state(thread_id, state)
    return ReadyChatResponse(
        thread_id=thread_id,
        status="ready",
        message="The product order draft was canceled.",
        tool_traces=visible_traces([], include_traces),
    )


def build_mcp_error_response(
    *,
    thread_id: str,
    traces: list[ToolTrace],
    tool_name: str,
    arguments: dict[str, object],
    failure: McpFailure,
    draft: DraftSummary | None = None,
    include_traces: bool,
) -> ChatResponse:
    append_trace(traces, tool_name, failure.trace_status, failure.detail_message, arguments, stage="mcp")
    return ErrorChatResponse(
        thread_id=thread_id,
        status="error",
        message=failure.user_message,
        tool_traces=visible_traces(traces, include_traces),
        draft=build_draft_response(draft) if draft else None,
        error=build_error(failure.code, failure.user_message, retryable=failure.retryable, tool_name=tool_name),
    )
