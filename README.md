# COP-PILOT LLM Layer

This repository contains the current LLM layer used to test a product-focused MCP workflow.

The maintained runtime has two parts:

- `backend/llm-layer`: FastAPI service that calls an LLM and an MCP server.
- `ui/mcp-test-console`: Vue console for manual end-to-end checks.

Removed legacy modules are not part of the supported path. If you are changing behavior, start in one of the two directories above.

## Simple Diagram

```mermaid
flowchart LR
    User[User in Test Console] --> UI[ui/mcp-test-console]
    UI --> API[backend/llm-layer
FastAPI workflow]
    API --> LLM[OpenAI-compatible LLM]
    API --> MCP[Configured MCP server
OpenSlice / TMF tools]
    API -. best-effort redacted events .-> OBS[LLM observability dashboard]
    MCP --> API
    LLM --> API
    API --> UI

    API --> Draft[Draft + confirm flow
for write tools]
    Draft --> UI
```

## What The Backend Does

The API accepts chat messages and lets the model call configured MCP tools. Product discovery tools run immediately. Product-order tools are intercepted, normalized into a draft, and executed only after the client confirms the draft.

When no order dates are provided, the draft uses today as the start date and the same date one year later as the end date. Both dates remain visible for review before confirmation.

The write path is intentionally conservative:

- product-order writes return `needs_confirmation`
- when live offering detail exposes mandatory characteristics, incomplete orders are blocked and the assistant asks only for those missing values
- clients must echo the reviewed draft and send an idempotency key on confirm
- same-key confirmation retries replay the same executed result safely
- write-tool execution is never retried automatically

The local guardrail implementation sits behind an interface so it can be replaced later without changing the HTTP contract.

LLM-backed chat requests also create one redacted observability event. Token usage is aggregated across
tool-selection rounds and forwarded through a bounded background queue, so dashboard failures never
change or delay the chat response. The test console can optionally display recent events with separate
LLM execution, approval, and observability forwarding states. See [`docs/observability.md`](docs/observability.md).

## Request Flow

1. `POST /v1/chat` receives a user message.
2. The model may answer directly or request MCP tool calls.
3. Read tools run against the configured MCP server.
4. When an offering-detail response contains required characteristics, the backend stores them in the conversation and the assistant asks only for missing values.
5. A complete write request becomes a draft and returns `needs_confirmation`.
6. The UI or client calls `POST /v1/chat/{thread_id}/confirm` with `draft_id`, `fingerprint`, and `idempotency_key`, or calls `POST /v1/chat/{thread_id}/cancel` to discard the draft.
7. The backend rechecks authorization/idempotency and executes the write tool once when confirmed.

## Run Locally

Backend:

```bash
cd backend/llm-layer
poetry install
cp .env.example .env
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
```

Frontend:

```bash
cd ui/mcp-test-console
npm ci
cp .env.example .env
npm run dev
```

Open the UI at `http://localhost:4173`.

## Verify Changes

Backend:

```bash
cd backend/llm-layer
poetry run python -m pytest -q
poetry build
docker build -t llm-layer:local .
```

Frontend:

```bash
cd ui/mcp-test-console
npm run typecheck
npm run build
docker build -t llm-layer-mcp-test-console:local .
```

## Configuration Rules

- Keep backend runtime values in `backend/llm-layer/.env`.
- Keep frontend target values in `ui/mcp-test-console/.env`.
- Do not commit secrets or real API keys.
- `MCP_SERVER_URL` is required for `streamable_http` and `sse` transports.
- `MCP_SERVER_COMMAND` is required for `stdio` transport.
- `PRODUCT_READ_TOOL_NAMES` and `PRODUCT_WRITE_TOOL_NAMES` define the execution policy.

## Useful Docs

- [backend/llm-layer/README.md](backend/llm-layer/README.md)
- [ui/mcp-test-console/README.md](ui/mcp-test-console/README.md)
- [docs/local-development.md](docs/local-development.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/api-reference.md](docs/api-reference.md)
- [docs/deployment.md](docs/deployment.md)
