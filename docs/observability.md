# LLM Observability Events

The LLM Layer publishes best-effort interaction events to the configured observability dashboard.
Dashboard delivery is passive: chat behavior, response latency, health, and readiness do not depend on
the dashboard being reachable.

## Event Scope

One event is created for each `POST /v1/chat` request that attempts at least one LLM call.

- multiple LLM calls during MCP tool-selection rounds are aggregated into one event
- MCP failures that happen before the first LLM call do not create an event
- LLM timeouts and provider failures create an unsuccessful event
- confirm, cancel, and reset operations do not create events because they do not call the LLM

Event types use the final chat workflow status:

- `ready` becomes `completion`
- `needs_confirmation`, `blocked`, and `error` keep their status value

`success` is `true` for `completion` and `needs_confirmation`; it is `false` for `blocked` and `error`.

## Delivery Behavior

The request path only attempts a non-blocking enqueue into a bounded in-memory queue.

- dashboard HTTP delivery happens in a background worker
- delivery uses a short configurable timeout
- requests are not retried
- network and HTTP failures are logged without prompt or response content
- events are dropped when the queue is full
- queued events may be lost when the process exits
- observability is not part of `/health` or `/ready`

This is intentionally best-effort rather than a durable audit log.

## Payload

The dashboard receives the required camel-case fields from the current informal contract.
Token usage is aggregated across all LLM calls made for the interaction.

```json
{
  "timestamp": "2026-07-29T12:00:00Z",
  "agentId": "cop-pilot-llm-layer",
  "conversationId": "0197b3c4-5d6e-7f80-9abc-def012345678",
  "eventType": "completion",
  "model": "openrouter/free",
  "provider": "openrouter",
  "inputTokens": 120,
  "completionTokens": 45,
  "maxTokens": 1024,
  "latencyMs": 850,
  "success": true,
  "inputPrompt": "[redacted]",
  "responseText": "[redacted]",
  "mcpToolSelected": "searchOSLProductOfferings"
}
```

Prompt and response fields contain only the constant `[redacted]`. This satisfies the dashboard's
non-empty-field validation without sending conversation content. Failed interactions use the optional
`reasoningSteps` field for a short error code and provider cause. Internal model reasoning,
tool-selection reasoning, and confidence values are not sent. `mcpToolSelected` is sent only when the
interaction selected exactly one distinct MCP tool.

The model name reported by the provider is preferred when available. Otherwise the configured
`OPENAI_MODEL` value is used. Missing provider token metadata is represented as zero.

## Test Console View

Set:

```env
OBSERVABILITY_EVENT_VIEW_ENABLED=true
```

The backend retains a bounded in-memory view of recent events and their delivery states. The test console
loads this feed from:

```text
GET /v1/runtime/observability/events
```

Possible delivery states are `queued`, `sent`, `failed`, `dropped`, and `disabled`. When the outbound URL
is empty, the test view can still display locally generated events with delivery state `disabled`.
When the test view is enabled, the response also includes `delivery_target` if outbound delivery is
configured, allowing the console to show exactly where events are being sent. The console derives
recent event, delivered, problem, and token statistics from this bounded feed. Failed interactions are
shown separately from event-delivery failures, including the short provider cause when available. It refreshes
automatically while the Activity or Connections view is open.

Keep the view disabled in production unless operator access is protected. It exposes conversation IDs,
token counts, model names, tool names, latency, provider failure summaries, and delivery errors, but never
prompt or response content.
