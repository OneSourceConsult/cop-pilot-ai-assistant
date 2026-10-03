# LLM Layer

FastAPI service for the product-focused MCP chat flow. It owns the HTTP contract, conversation state, LLM calls, MCP tool execution, and the guarded write workflow.

## Responsibilities

The backend is responsible for:

- accepting chat requests from a client or the test console
- building the product-only system prompt
- calling the configured OpenAI-compatible chat model
- executing approved read tools through the configured MCP server
- turning write-tool calls into product-order drafts
- requiring explicit confirmation before a write tool executes
- optionally returning traces that explain guardrail and MCP activity
- publishing redacted, best-effort LLM interaction events outside the response path

It does not persist conversations to a database. Conversation and pending-draft state are in memory for this version, so restarting the process clears active threads.

## Local Run

```bash
poetry install
cp .env.example .env
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
```

Health checks:

```bash
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/ready
```

## HTTP Endpoints

- `GET /health`: process is alive
- `GET /ready`: configuration loaded and app booted
- `GET /v1/runtime/mcp-status`: live MCP reachability and tool-loading probe
- `GET /v1/runtime/observability/events`: optional bounded event feed for the test console
- `POST /v1/chat`: submit a new message or continue a thread
- `POST /v1/chat/{thread_id}/confirm`: execute the pending draft by sending the reviewed `draft_id`, `fingerprint`, and an `idempotency_key`
- `POST /v1/chat/{thread_id}/cancel`: cancel the pending draft
- `POST /v1/chat/{thread_id}/reset`: clear thread state

The response contract is documented in [docs/api-reference.md](../../docs/api-reference.md). Keep status values stable; clients branch on them.

## Workflow

1. `POST /v1/chat` stores or loads the thread.
2. The model receives the prompt and known conversation messages.
3. If the model requests a configured read tool, the backend calls MCP and appends the result.
4. Product-offering detail responses are inspected for characteristics marked `required`, `mandatory`, or with `minCardinality > 0`; that context is kept in the thread.
5. If the model requests `createProductOrder`, the backend fills missing order dates with a one-year period from today, then blocks any incomplete offering-specific parameters.
6. A complete write request becomes a draft rather than executing immediately.
7. The client reviews the draft and calls `confirm` with the matching `draft_id`, `fingerprint`, and a client-generated `idempotency_key`, or calls `cancel`.
8. Confirmation rechecks authorization and idempotency, then executes the write tool once.

Client-visible statuses:

- `ready`: no pending action
- `needs_confirmation`: draft created and waiting for client action
- `blocked`: policy, validation, duplicate request, or missing state stopped the workflow
- `executed`: confirmed write completed
- `error`: runtime failure from the LLM, MCP server, or backend execution path

## Guardrail Behavior

The local guardrail layer classifies tools from configuration:

- `PRODUCT_READ_TOOL_NAMES`: tools that may run during chat
- `PRODUCT_WRITE_TOOL_NAMES`: tools that must become drafts first

Write handling includes:

- classify configured read tools as immediate actions
- route every other tool through the draft-confirmation flow
- normalize draft arguments
- derive required product characteristics from the selected offering's live detail or specification response
- block a product-order draft until those discovered characteristics are supplied
- authorize before execution
- replay safe retries when the same confirmed draft is retried with the same idempotency key
- block conflicting confirmation attempts for stale or mismatched draft data

The implementation is deterministic and intentionally replaceable. The HTTP contract should not change when a future guardrail service replaces it.

## Configuration That Fails Startup

Startup validation is strict so bad deploys fail before serving requests. The most common failures are:

- `OPENAI_API_KEY` is empty
- `OPENAI_MODEL` is empty
- `OPENAI_BASE_URL` is empty or not an `http` or `https` URL
- `MCP_TRANSPORT` is not `streamable_http`, `sse`, or `stdio`
- `MCP_SERVER_URL` is missing for network transports
- `MCP_SERVER_COMMAND` is missing for `stdio`
- `MCP_SERVER_HEADERS` is not a JSON object
- read/write tool allowlists are empty or overlap

Use [docs/configuration.md](../../docs/configuration.md) when changing environment variables.

## Code Map

- `app/app_factory.py`: FastAPI app wiring and route handlers
- `app/config.py`: typed settings, parsing, validation, diagnostics
- `app/runtime.py`: LLM and MCP client setup
- `app/observability.py`: LLM usage aggregation, event mapping, and non-blocking delivery
- `app/workflow_service.py`: chat loop, tool execution, confirmation flow
- `app/guardrails/`: guardrail interface and local implementation
- `app/response_factory.py`: stable response and error payloads
- `app/conversation_store.py`: in-memory threads and pending drafts
- `app/prompting.py`: prompt text and prompt metadata
- `app/chat_service.py`: compatibility import surface for older tests/imports

Prefer changing workflow behavior in `workflow_service.py` and response shapes in `response_factory.py`; avoid spreading contract logic into route handlers.

## Checks

```bash
poetry run python -m pytest -q
poetry build
docker build -t llm-layer:local .
```

The test suite covers read-tool execution, draft creation, confirmation, idempotent confirmation retries, permissive default guardrails, MCP execution failures, prompt metadata, runtime config validation, and thread reset behavior.
