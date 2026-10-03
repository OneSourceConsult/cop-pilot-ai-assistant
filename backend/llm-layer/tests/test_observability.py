from __future__ import annotations

import json

import httpx
import pytest

from app.config import AppSettings
from app.observability import (
    LlmInteractionMetrics,
    ObservabilityPublisher,
    build_agent_event,
)
from app.schemas import ErrorChatResponse, ErrorResponse, ReadyChatResponse


def _settings(**overrides: object) -> AppSettings:
    base = {
        "llm_api_key": "test-key",
        "llm_model_name": "openai/gpt-4.1-mini",
        "llm_provider_name": "openrouter",
        "llm_max_tokens": 2048,
        "llm_api_base_url": "https://openrouter.ai/api/v1",
        "tool_server_name": "openslice",
        "tool_server_transport": "streamable_http",
        "tool_server_url": "http://127.0.0.1:8003/mcp",
        "product_read_tool_names": "searchOSLProductOfferings",
        "product_write_tool_names": "createProductOrder",
        "observability_agent_id": "cop-pilot-llm-layer",
        "observability_event_view_enabled": True,
    }
    return AppSettings(_env_file=None, **(base | overrides))


class Reply:
    def __init__(
        self,
        *,
        usage_metadata: dict[str, int] | None = None,
        response_metadata: dict[str, object] | None = None,
        tool_calls: list[dict[str, object]] | None = None,
    ) -> None:
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata or {}
        self.tool_calls = tool_calls or []


def _event(settings: AppSettings | None = None):
    resolved = settings or _settings()
    metrics = LlmInteractionMetrics(
        attempted=True,
        input_tokens=120,
        completion_tokens=45,
        selected_tools=["searchOSLProductOfferings"],
    )
    return build_agent_event(
        settings=resolved,
        response=ReadyChatResponse(
            thread_id="0197b3c4-5d6e-7f80-9abc-def012345678",
            status="ready",
            message="Products found.",
        ),
        metrics=metrics,
        latency_ms=850,
    )


def test_llm_metrics_aggregate_usage_and_tool_selection() -> None:
    metrics = LlmInteractionMetrics()
    metrics.record_attempt()
    metrics.record_reply(
        Reply(
            usage_metadata={"input_tokens": 80, "output_tokens": 20},
            response_metadata={"model_name": "provider/model-v2"},
            tool_calls=[{"name": "searchOSLProductOfferings"}],
        )
    )
    metrics.record_attempt()
    metrics.record_reply(
        Reply(
            usage_metadata={"input_tokens": 40, "output_tokens": 25},
            tool_calls=[{"name": "searchOSLProductOfferings"}],
        )
    )

    assert metrics.attempted is True
    assert metrics.input_tokens == 120
    assert metrics.completion_tokens == 45
    assert metrics.model_name == "provider/model-v2"
    assert metrics.selected_tools == ["searchOSLProductOfferings"]


def test_llm_metrics_fall_back_to_provider_token_usage() -> None:
    metrics = LlmInteractionMetrics()
    metrics.record_attempt()
    metrics.record_reply(
        Reply(
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 7,
                }
            }
        )
    )

    assert metrics.input_tokens == 12
    assert metrics.completion_tokens == 7


def test_agent_event_redacts_content_and_maps_error_status() -> None:
    metrics = LlmInteractionMetrics(attempted=True)
    event = build_agent_event(
        settings=_settings(),
        response=ErrorChatResponse(
            thread_id="thread-1",
            status="error",
            message="The assistant could not complete the request.",
            error=ErrorResponse(
                code="llm_request_failed",
                message="The assistant could not complete the request.",
                retryable=True,
                details={
                    "error_type": "BadRequestError",
                    "provider_message": "System message must be at the beginning.",
                },
            ),
        ),
        metrics=metrics,
        latency_ms=35,
    )
    payload = event.model_dump(mode="json", by_alias=True, exclude_none=True)

    assert payload["eventType"] == "error"
    assert payload["success"] is False
    assert payload["inputPrompt"] == "[redacted]"
    assert payload["responseText"] == "[redacted]"
    assert payload["provider"] == "openrouter"
    assert payload["maxTokens"] == 2048
    assert payload["reasoningSteps"] == [
        "llm_request_failed (BadRequestError): System message must be at the beginning."
    ]


def test_disabled_delivery_still_records_event_for_test_view() -> None:
    publisher = ObservabilityPublisher(_settings(observability_events_url=None))

    publisher.enqueue(_event())

    feed = publisher.recent_events()
    assert feed.delivery_enabled is False
    assert feed.delivery_target is None
    assert feed.view_enabled is True
    assert len(feed.events) == 1
    assert feed.events[0].delivery_status == "disabled"


def test_disabled_test_view_does_not_expose_delivery_target() -> None:
    publisher = ObservabilityPublisher(
        _settings(
            observability_events_url="http://observability.test/agent-events",
            observability_event_view_enabled=False,
        )
    )

    feed = publisher.recent_events()

    assert feed.delivery_enabled is True
    assert feed.delivery_target is None
    assert feed.events == []


def test_full_queue_drops_event_without_raising() -> None:
    publisher = ObservabilityPublisher(
        _settings(
            observability_events_url="http://observability.test/agent-events",
            observability_queue_size=1,
        )
    )

    publisher.enqueue(_event())
    publisher.enqueue(_event())

    feed = publisher.recent_events()
    assert [record.delivery_status for record in feed.events] == ["dropped", "queued"]


@pytest.mark.asyncio
async def test_publisher_posts_camel_case_payload() -> None:
    requests: list[dict[str, object]] = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(202)

    publisher = ObservabilityPublisher(
        _settings(observability_events_url="http://observability.test/agent-events"),
        transport=httpx.MockTransport(send),
    )
    await publisher.start()
    publisher.enqueue(_event())
    await publisher.wait_until_idle()

    feed = publisher.recent_events()
    await publisher.stop()

    assert requests[0]["agentId"] == "cop-pilot-llm-layer"
    assert requests[0]["mcpToolSelected"] == "searchOSLProductOfferings"
    assert "reasoningSteps" not in requests[0]
    assert feed.delivery_target == "http://observability.test/agent-events"
    assert feed.events[0].delivery_status == "sent"
    assert feed.events[0].status_code == 202


@pytest.mark.asyncio
async def test_delivery_failure_is_recorded_without_escaping() -> None:
    publisher = ObservabilityPublisher(
        _settings(observability_events_url="http://observability.test/agent-events"),
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    )
    await publisher.start()
    publisher.enqueue(_event())
    await publisher.wait_until_idle()

    feed = publisher.recent_events()
    await publisher.stop()

    assert feed.events[0].delivery_status == "failed"
    assert feed.events[0].status_code == 503
