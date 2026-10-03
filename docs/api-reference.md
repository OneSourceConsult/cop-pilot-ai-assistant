# API Reference

This document describes the current MCP-native backend in `backend/llm-layer`.

Base URL for local development:

```text
http://127.0.0.1:8010
```

## Workflow Summary

1. `POST /v1/chat` starts a new conversation or continues an existing one.
2. Read-only requests return `status: "ready"`.
3. Guarded write requests return `status: "needs_confirmation"` with a draft.
4. The client confirms that exact draft through `POST /v1/chat/{thread_id}/confirm`.
5. The client may cancel a pending draft through `POST /v1/chat/{thread_id}/cancel`.
6. The client may clear all in-memory state through `POST /v1/chat/{thread_id}/reset`.

The current API keeps workflow outcomes inside `200 OK` responses with status-specific payloads. Transport-level error semantics such as `409 Conflict` for stale confirmations are not fully introduced yet.

## Endpoints

### `GET /health`

Process liveness probe.

Response:

```json
{
  "status": "ok"
}
```

### `GET /ready`

Configuration-level readiness probe. It confirms that the service booted with valid settings and reports the configured MCP backend metadata.

Response:

```json
{
  "status": "ready",
  "app_name": "LLM Layer",
  "tool_server_name": "openslice",
  "tool_server_transport": "streamable_http",
  "prompt_version": "v1"
}
```

### `GET /v1/runtime/mcp-status`

Live MCP connectivity probe for the configured backend target.

Example response when reachable:

```json
{
  "status": "reachable",
  "tool_server_name": "openslice",
  "tool_server_transport": "streamable_http",
  "configured_target": "https://mcp.example.com/mcp",
  "configured_command": null,
  "configured_args": [],
  "checked_at": "2026-06-30T09:15:27.000000Z",
  "message": "Connected to the configured MCP server.",
  "tool_count": 8,
  "tool_names": ["createProductOrder", "searchOSLProductOfferings"],
  "error": null
}
```

Example response when unavailable:

```json
{
  "status": "unavailable",
  "tool_server_name": "openslice",
  "tool_server_transport": "streamable_http",
  "configured_target": "https://mcp.example.com/mcp",
  "configured_command": null,
  "configured_args": [],
  "checked_at": "2026-06-30T09:15:31.000000Z",
  "message": "The product platform is temporarily unavailable.",
  "tool_count": null,
  "tool_names": [],
  "error": {
    "code": "mcp_unavailable",
    "message": "The product platform is temporarily unavailable.",
    "retryable": true,
    "details": {
      "detail": "Connection refused by MCP server."
    }
  }
}
```

### `GET /v1/runtime/observability/events`

Returns the bounded recent-event feed used by the test console. The feed is populated only when
`OBSERVABILITY_EVENT_VIEW_ENABLED=true`. The optional `delivery_target` is also withheld when this
operator view is disabled.

```json
{
  "delivery_enabled": true,
  "delivery_target": "http://observability.internal:5000/agent-events",
  "view_enabled": true,
  "events": [
    {
      "event_id": "0197b3c4-8e9f-7a01-b234-cdef01234567",
      "recorded_at": "2026-07-29T12:00:00Z",
      "delivery_status": "sent",
      "status_code": 202,
      "payload": {
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
        "responseText": "[redacted]"
      }
    }
  ]
}
```

Prompt and response fields contain only the constant `[redacted]`. See
[`docs/observability.md`](observability.md) for delivery and event-scope details.

### `POST /v1/chat`

Starts a new conversation when `thread_id` is omitted, or continues an existing one when `thread_id` is supplied.

Query parameters:

- `include_traces`
  - optional boolean
  - defaults to `false`
  - when `true`, includes lightweight guardrail and MCP diagnostics in `tool_traces`

Request body fields:

- `message`
  - required string
  - the user request for product discovery or guarded write preparation
- `thread_id`
  - optional UUIDv7 string
  - reuse a previous `thread_id` to continue the same conversation

Example 1: discover products

```json
{
  "message": "Show me the products available."
}
```

Example 2: prepare an order in an existing conversation

```json
{
  "message": "Create an order for product X.",
  "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678"
}
```

Possible workflow responses:

- `ready`
  - normal conversational response with no pending draft
- `needs_confirmation`
  - a guarded write draft is waiting for explicit confirmation
- `blocked`
  - validation or policy prevented the requested action
- `error`
  - runtime failure such as MCP or LLM/provider issues

Example `ready` response:

```json
{
  "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
  "status": "ready",
  "message": "There are currently no published product catalogs available in the platform."
}
```

Example `needs_confirmation` response:

```json
{
  "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
  "status": "needs_confirmation",
  "message": "Draft prepared for product order `Premium Fiber`. Review the draft and confirm before execution.",
  "draft": {
    "draft_id": "draft-0197b3c4-5d6e-7f80-9abc-def012345678-abc12345",
    "tool_name": "createProductOrder",
    "display_name": "Premium Fiber",
    "normalized_arguments": {
      "productName": "Premium Fiber"
    },
    "summary": "Prepare product order via `createProductOrder` for Premium Fiber with 1 parameter(s).",
    "fingerprint": "abc12345",
    "requires_confirmation": true
  }
}
```

### `POST /v1/chat/{thread_id}/confirm`

Executes the current pending draft for the conversation. The client must echo the exact draft it reviewed and provide a client-generated idempotency key so safe retries can return the same execution result.

Query parameters:

- `include_traces`
  - optional boolean
  - defaults to `false`

Request body:

```json
{
  "draft_id": "draft-0197b3c4-5d6e-7f80-9abc-def012345678-abc12345",
  "fingerprint": "abc12345",
  "idempotency_key": "0197b3c4-8e9f-7a01-b234-cdef01234567"
}
```

Meaning of the fields:

- `draft_id`
  - required string returned by the previous `needs_confirmation` response
- `fingerprint`
  - required string returned by the previous `needs_confirmation` response
- `idempotency_key`
  - required client-generated UUIDv7 string
  - reuse the same key only when retrying the exact same confirmation request

Example `executed` response:

```json
{
  "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
  "status": "executed",
  "message": "Product order executed for Premium Fiber.",
  "execution_result": {
    "execution_token": "0197b3c4-9abc-7def-8012-34567890abcd",
    "tool_name": "createProductOrder",
    "status": "executed",
    "result_preview": "{\"id\":\"po-1\",\"state\":\"acknowledged\"}"
  }
}
```

Example `blocked` response for mismatched confirmation data:

```json
{
  "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
  "status": "blocked",
  "message": "The confirmation details do not match the current pending draft.",
  "draft": {
    "draft_id": "draft-0197b3c4-5d6e-7f80-9abc-def012345678-abc12345",
    "tool_name": "createProductOrder",
    "display_name": "Premium Fiber",
    "normalized_arguments": {
      "productName": "Premium Fiber"
    },
    "summary": "Prepare product order via `createProductOrder` for Premium Fiber with 1 parameter(s).",
    "fingerprint": "abc12345",
    "requires_confirmation": true
  },
  "error": {
    "code": "invalid_confirmation",
    "message": "The confirmation details do not match the current pending draft.",
    "retryable": false,
    "details": {
      "draft_id": "draft-0197b3c4-5d6e-7f80-9abc-def012345678-abc12345",
      "fingerprint": "abc12345"
    }
  }
}
```

### `POST /v1/chat/{thread_id}/cancel`

Cancels the current pending draft for the conversation.

This endpoint has no request body.

Example response:

```json
{
  "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
  "status": "ready",
  "message": "The product order draft was canceled."
}
```

### `POST /v1/chat/{thread_id}/reset`

Clears the in-memory workflow state for the conversation, including any pending draft.

Response:

```json
{
  "thread_id": "0197b3c4-5d6e-7f80-9abc-def012345678",
  "status": "reset"
}
```

## Response Shapes

The backend uses status-specific payloads with `status` as the required discriminator:

- `ready`
  - `thread_id`
  - `status`
  - `message`
  - optional `tool_traces`
- `needs_confirmation`
  - `thread_id`
  - `status`
  - `message`
  - `draft`
  - optional `tool_traces`
- `blocked`
  - `thread_id`
  - `status`
  - `message`
  - `error`
  - optional `draft`
  - optional `tool_traces`
- `executed`
  - `thread_id`
  - `status`
  - `message`
  - `execution_result`
  - optional `tool_traces`
- `error`
  - `thread_id`
  - `status`
  - `message`
  - `error`
  - optional `draft`
  - optional `tool_traces`

## Diagnostics

When `include_traces=true`, `tool_traces` entries may include:

- `tool_name`
- `arguments`
- `status`
- `stage`
  - `guardrail`
  - `mcp`
- `result_preview`

Clients should treat the top-level `status` as the authoritative workflow state and `tool_traces` as optional diagnostics only.

## Current Error Codes

Examples currently used by the backend:

- `mcp_unavailable`
- `llm_request_failed`
- `tool_not_found`
- `tool_execution_failed`
- `missing_pending_draft`
- `invalid_confirmation`
- `idempotency_key_reused`
- `guardrail_clarify`
- `guardrail_deny`
- `guardrail_unauthorized`
- `guardrail_duplicate`

Clients should handle unknown `error.code` values safely because new codes may be added later.
