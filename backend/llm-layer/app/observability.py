from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import AppSettings
from app.id_utils import uuid7
from app.schemas import ChatResponse


logger = logging.getLogger(__name__)

DeliveryStatus = Literal["queued", "sent", "failed", "dropped", "disabled"]
REDACTED_CONTENT = "[redacted]"


class AgentEvent(BaseModel):
    """Outbound event contract consumed by the observability dashboard."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    timestamp: datetime
    agent_id: str = Field(alias="agentId")
    conversation_id: str = Field(alias="conversationId")
    event_type: str = Field(alias="eventType")
    model: str
    provider: str
    input_tokens: int = Field(alias="inputTokens", ge=0)
    completion_tokens: int = Field(alias="completionTokens", ge=0)
    max_tokens: int = Field(alias="maxTokens", ge=1)
    latency_ms: int = Field(alias="latencyMs", ge=0)
    success: bool
    input_prompt: str = Field(alias="inputPrompt")
    response_text: str = Field(alias="responseText")
    reasoning_steps: list[str] | None = Field(default=None, alias="reasoningSteps")
    mcp_tool_selected: str | None = Field(default=None, alias="mcpToolSelected")
    tool_selection_reasoning: str | None = Field(default=None, alias="toolSelectionReasoning")
    tool_selection_confidence: dict[str, float] | None = Field(
        default=None,
        alias="toolSelectionConfidence",
    )


class ObservabilityEventRecord(BaseModel):
    """Local delivery state exposed only through the optional test view."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    recorded_at: datetime
    delivery_status: DeliveryStatus
    status_code: int | None = None
    error: str | None = None
    payload: AgentEvent


class ObservabilityEventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_enabled: bool
    delivery_target: str | None = None
    view_enabled: bool
    events: list[ObservabilityEventRecord] = Field(default_factory=list)


@dataclass
class LlmInteractionMetrics:
    """Aggregated metadata for every model call made during one chat request."""

    attempted: bool = False
    input_tokens: int = 0
    completion_tokens: int = 0
    model_name: str | None = None
    selected_tools: list[str] = field(default_factory=list)

    def record_attempt(self) -> None:
        self.attempted = True

    def record_reply(self, reply: object) -> None:
        usage = getattr(reply, "usage_metadata", None)
        if isinstance(usage, Mapping) and (
            "input_tokens" in usage or "output_tokens" in usage
        ):
            self.input_tokens += _non_negative_int(usage.get("input_tokens"))
            self.completion_tokens += _non_negative_int(usage.get("output_tokens"))
        else:
            response_metadata = getattr(reply, "response_metadata", None)
            token_usage = _token_usage_from_response_metadata(response_metadata)
            self.input_tokens += _non_negative_int(
                token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
            )
            self.completion_tokens += _non_negative_int(
                token_usage.get("completion_tokens") or token_usage.get("output_tokens")
            )

        response_metadata = getattr(reply, "response_metadata", None)
        if isinstance(response_metadata, Mapping):
            resolved_model = response_metadata.get("model_name") or response_metadata.get("model")
            if resolved_model:
                self.model_name = str(resolved_model)

        tool_calls = getattr(reply, "tool_calls", None)
        if not isinstance(tool_calls, list):
            return
        for tool_call in tool_calls:
            if not isinstance(tool_call, Mapping):
                continue
            name = str(tool_call.get("name", "")).strip()
            if name and name not in self.selected_tools:
                self.selected_tools.append(name)


def build_agent_event(
    *,
    settings: AppSettings,
    response: ChatResponse,
    metrics: LlmInteractionMetrics,
    latency_ms: int,
) -> AgentEvent:
    selected_tool = metrics.selected_tools[0] if len(metrics.selected_tools) == 1 else None
    event_type = "completion" if response.status == "ready" else response.status

    return AgentEvent(
        timestamp=datetime.now(UTC),
        agent_id=settings.observability_agent_id,
        conversation_id=response.thread_id,
        event_type=event_type,
        model=metrics.model_name or settings.llm_model_name,
        provider=settings.llm_provider_name,
        input_tokens=metrics.input_tokens,
        completion_tokens=metrics.completion_tokens,
        max_tokens=settings.llm_max_tokens,
        latency_ms=max(0, latency_ms),
        success=response.status in {"ready", "needs_confirmation"},
        input_prompt=REDACTED_CONTENT,
        response_text=REDACTED_CONTENT,
        reasoning_steps=_failure_details(response),
        mcp_tool_selected=selected_tool,
    )


def _failure_details(response: ChatResponse) -> list[str] | None:
    error = getattr(response, "error", None)
    if error is None:
        return None

    details = error.details
    error_type = str(details.get("error_type", "")).strip()
    provider_message = " ".join(str(details.get("provider_message", "")).split())
    cause = provider_message or error.message
    prefix = f"{error.code} ({error_type})" if error_type else error.code
    return [f"{prefix}: {cause}"[:320]]


class ObservabilityPublisher:
    """Best-effort queue that keeps dashboard delivery out of the chat path."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._events_url = settings.observability_events_url
        self._request_timeout_seconds = settings.observability_request_timeout_seconds
        self._view_enabled = settings.observability_event_view_enabled
        self._transport = transport
        self._queue: asyncio.Queue[ObservabilityEventRecord] = asyncio.Queue(
            maxsize=settings.observability_queue_size
        )
        self._recent_events: deque[ObservabilityEventRecord] = deque(
            maxlen=settings.observability_recent_event_limit
        )
        self._client: httpx.AsyncClient | None = None
        self._worker: asyncio.Task[None] | None = None

    @property
    def delivery_enabled(self) -> bool:
        return self._events_url is not None

    async def start(self) -> None:
        if not self.delivery_enabled or self._worker is not None:
            return
        self._client = httpx.AsyncClient(
            timeout=self._request_timeout_seconds,
            transport=self._transport,
        )
        self._worker = asyncio.create_task(self._run(), name="observability-event-publisher")

    async def stop(self) -> None:
        worker = self._worker
        if worker is not None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._queue.join(),
                    timeout=self._request_timeout_seconds,
                )
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            self._worker = None

        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def wait_until_idle(self, timeout_seconds: float = 1.0) -> None:
        await asyncio.wait_for(self._queue.join(), timeout=timeout_seconds)

    def enqueue(self, event: AgentEvent) -> str:
        record = ObservabilityEventRecord(
            event_id=uuid7(),
            recorded_at=datetime.now(UTC),
            delivery_status="queued" if self.delivery_enabled else "disabled",
            payload=event,
        )
        if self._view_enabled:
            self._recent_events.append(record)

        if not self.delivery_enabled:
            return record.event_id

        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            record.delivery_status = "dropped"
            record.error = "The observability delivery queue is full."
            logger.warning(
                "Observability event dropped | event_id=%s event_type=%s reason=queue_full",
                record.event_id,
                event.event_type,
            )
        return record.event_id

    def recent_events(self) -> ObservabilityEventsResponse:
        events = []
        if self._view_enabled:
            events = [record.model_copy(deep=True) for record in reversed(self._recent_events)]
        return ObservabilityEventsResponse(
            delivery_enabled=self.delivery_enabled,
            delivery_target=self._events_url if self._view_enabled else None,
            view_enabled=self._view_enabled,
            events=events,
        )

    async def _run(self) -> None:
        assert self._client is not None
        assert self._events_url is not None

        while True:
            record = await self._queue.get()
            try:
                response = await self._client.post(
                    self._events_url,
                    json=record.payload.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    ),
                )
                record.status_code = response.status_code
                response.raise_for_status()
                record.delivery_status = "sent"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                record.delivery_status = "failed"
                record.error = _error_preview(exc)
                logger.warning(
                    "Observability event delivery failed | event_id=%s event_type=%s error_type=%s",
                    record.event_id,
                    record.payload.event_type,
                    type(exc).__name__,
                )
            finally:
                self._queue.task_done()


def _token_usage_from_response_metadata(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    token_usage = value.get("token_usage") or value.get("usage")
    return token_usage if isinstance(token_usage, Mapping) else {}


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _error_preview(exc: Exception, limit: int = 240) -> str:
    compact = " ".join(str(exc).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
