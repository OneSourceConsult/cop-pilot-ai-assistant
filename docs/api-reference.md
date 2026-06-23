# API Reference

This document covers the current MCP-native backend in `backend/llm-layer`.

Base URL for local development:

```text
http://127.0.0.1:8010
```

## Endpoints

### `GET /health`

Simple health check for the API process.

Response:

```json
{
  "status": "ok"
}
```

### `GET /ready`

Readiness check for the configured backend runtime.

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

### `POST /v1/chat`

Starts or continues a conversation.

Request body:

```json
{
  "message": "List the available product catalogs",
  "thread_id": null,
  "reset": false
}
```

Fields:

- `message`
  - required string
- `thread_id`
  - optional string
  - omit or set `null` to start a new conversation
- `reset`
  - optional boolean
  - resets the conversation state before handling the message

Possible responses:

- `ready`
  - normal answer with no pending confirmation
- `needs_confirmation`
  - a guarded write became a draft
- `blocked`
  - the workflow cannot proceed without correction or clarification
- `executed`
  - not expected from `POST /v1/chat`; writes execute after confirm
- `error`
  - runtime failure such as MCP or LLM/provider problems

Example `ready` response:

```json
{
  "thread_id": "7b44b74b-9a32-4703-89a1-ee7ce4df0c0a",
  "status": "ready",
  "message": "There are currently no published product catalogs available in the platform.",
  "tool_traces": [
    {
      "tool_name": "getOSLProductCatalogs",
      "arguments": {},
      "status": "success",
      "stage": "mcp",
      "result_preview": "[]"
    }
  ],
  "draft": null,
  "pending_action": null,
  "execution_result": null,
  "error": null
}
```

Example `needs_confirmation` response:

```json
{
  "thread_id": "thread-1",
  "status": "needs_confirmation",
  "message": "Draft prepared for product order `Premium Fiber`. Review the draft and confirm before execution.",
  "tool_traces": [
    {
      "tool_name": "createProductOrder",
      "arguments": {
        "productName": "Premium Fiber"
      },
      "status": "allow",
      "stage": "guardrail",
      "result_preview": "Prepare product order via `createProductOrder` for Premium Fiber with 1 parameter(s)."
    }
  ],
  "draft": {
    "draft_id": "draft-thread-1-abc12345",
    "tool_name": "createProductOrder",
    "display_name": "Premium Fiber",
    "normalized_arguments": {
      "productName": "Premium Fiber"
    },
    "summary": "Prepare product order via `createProductOrder` for Premium Fiber with 1 parameter(s).",
    "fingerprint": "abc12345",
    "requires_confirmation": true
  },
  "pending_action": "confirm_product_order",
  "execution_result": null,
  "error": null
}
```

Example `blocked` response:

```json
{
  "thread_id": "thread-1",
  "status": "blocked",
  "message": "Tool 'mystery_write_tool' is not allowed by the product-order policy.",
  "tool_traces": [
    {
      "tool_name": "mystery_write_tool",
      "arguments": {
        "productName": "Premium Fiber"
      },
      "status": "deny",
      "stage": "guardrail",
      "result_preview": "Tool 'mystery_write_tool' is not allowed by the product-order policy."
    }
  ],
  "draft": null,
  "pending_action": null,
  "execution_result": null,
  "error": {
    "code": "tool_not_allowed",
    "message": "There is no pending product order draft to confirm.",
    "retryable": false,
    "details": {}
  }
}
```

Example `error` response:

```json
{
  "thread_id": "thread-1",
  "status": "error",
  "message": "The assistant could not complete the request right now.",
  "tool_traces": [],
  "draft": null,
  "pending_action": null,
  "execution_result": null,
  "error": {
    "code": "llm_request_failed",
    "message": "The assistant could not complete the request right now.",
    "retryable": true,
    "details": {
      "model": "gpt-4.1-mini",
      "error_type": "RuntimeError",
      "provider_message": "The operation was aborted"
    }
  }
}
```

### `GET /v1/runtime/mcp-status`

Checks whether the backend can currently reach the configured MCP server and load its tool list.

Example response when reachable:

```json
{
  "status": "reachable",
  "tool_server_name": "openslice",
  "tool_server_transport": "streamable_http",
  "configured_target": "https://mcp.example.com/mcp",
  "configured_command": null,
  "configured_args": [],
  "message": "Connected to the configured MCP server.",
  "tool_count": 8,
  "tool_names": ["createProductOrder", "searchOSLProductOfferings"],
  "error": null
}
```

The response also includes the configured MCP target so operators can verify which URL or stdio command the backend is actually using. When the MCP server is unavailable, the endpoint still returns `200` with `status: "unavailable"` and a structured `error` payload so operators and the test console can show the failure reason.

### `POST /v1/chat/{thread_id}/confirm`

Confirms or cancels a pending product-order draft.

Request body:

```json
{
  "confirmed": true
}
```

Fields:

- `confirmed`
  - `true` executes the pending draft
  - `false` cancels the pending draft

Possible responses:

- `ready`
  - cancel completed successfully
- `blocked`
  - no pending draft, duplicate confirmation, or guardrail block
- `executed`
  - confirmed write succeeded
- `error`
  - MCP/tool/runtime failure after confirmation

Example `executed` response:

```json
{
  "thread_id": "thread-1",
  "status": "executed",
  "message": "Product order executed for Premium Fiber.",
  "tool_traces": [
    {
      "tool_name": "createProductOrder",
      "arguments": {
        "productName": "Premium Fiber"
      },
      "status": "success",
      "stage": "mcp",
      "result_preview": "{\"id\":\"po-1\",\"state\":\"acknowledged\"}"
    }
  ],
  "draft": null,
  "pending_action": null,
  "execution_result": {
    "execution_token": "exec-123",
    "tool_name": "createProductOrder",
    "status": "executed",
    "result_preview": "{\"id\":\"po-1\",\"state\":\"acknowledged\"}"
  },
  "error": null
}
```

### `POST /v1/chat/{thread_id}/reset`

Clears the in-memory state for a conversation.

Response:

```json
{
  "thread_id": "thread-1",
  "status": "reset"
}
```

## Response Model

The main response contract is `ChatResponse`.

Fields:

- `thread_id`
  - conversation id
- `status`
  - one of:
  - `ready`
  - `needs_confirmation`
  - `blocked`
  - `executed`
  - `error`
- `message`
  - primary user-facing summary
- `tool_traces`
  - best-effort diagnostics for guardrail and MCP activity
- `draft`
  - present only when a write is waiting for confirmation
- `pending_action`
  - currently `confirm_product_order` when `draft` is active
- `execution_result`
  - present after a successful confirmed write
- `error`
  - present for `blocked` and `error` statuses

`error` fields:

- `code`
  - stable machine-readable reason
- `message`
  - human-readable explanation
- `retryable`
  - whether retrying may succeed
- `details`
  - optional structured debugging metadata

## Tool Traces

`tool_traces` entries contain:

- `tool_name`
- `arguments`
- `status`
- `stage`
  - `guardrail`
  - `mcp`
- `result_preview`

These traces are useful for debugging, but clients should treat `status` on the top-level response as the authoritative workflow state.

## Current Error Codes

Examples currently used by the backend:

- `mcp_unavailable`
- `llm_request_failed`
- `tool_not_allowed`
- `tool_not_found`
- `tool_execution_failed`
- `missing_pending_draft`
- `guardrail_clarify`
- `guardrail_deny`
- `guardrail_unauthorized`
- `guardrail_duplicate`

Clients should handle unknown `error.code` values safely because new codes may be added later.
