# LLM Layer Console

Small Vue app for manually testing the llm-layer backend. It is not the product UI; it is a developer/operator console for testing chat, monitoring model activity, and checking service connections.

## What It Shows

- assistant messages returned by `POST /v1/chat`
- pending product-order drafts
- confirm/cancel buttons for guarded writes
- execution result after confirmation
- guardrail and MCP traces, when available
- recent redacted LLM activity and observability forwarding status, when enabled
- request failures from the backend client

## Run Locally

```bash
npm ci
cp .env.example .env
npm run dev
```

The default backend URL is `http://127.0.0.1:8010`. Override it in `.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8010
```

Open `http://localhost:4173`.

To enable the Activity view, set this in the backend environment:

```env
OBSERVABILITY_EVENT_VIEW_ENABLED=true
```

The Activity view can display locally generated events even when `OBSERVABILITY_EVENTS_URL` is empty.
In that case the UI labels them as local-only events.

The Activity view refreshes automatically and keeps LLM execution, approval, and observability forwarding
as separate statuses. Its metrics follow the active search, status, and date filters. Expand an activity card
to inspect identifiers, HTTP acknowledgement, tool selection, and available failure details.
Failed chat requests show a short provider cause when one is available.
Endpoint configuration and its latest HTTP outcome appear in
the separate Connections view alongside the MCP product service.

## Expected Backend Contract

The console expects the backend response shape documented in [docs/api-reference.md](../../docs/api-reference.md). The fields it actively uses are:

- `thread_id`
- `status`
- `message`
- `draft`
- `execution_result`
- `tool_traces`
- `error`

For confirmation, the console now sends:

- `draft_id`
- `fingerprint`
- `idempotency_key`

Supported statuses:

- `ready`: normal response, no pending write
- `needs_confirmation`: draft is waiting for user confirmation
- `blocked`: policy or workflow blocked the request
- `executed`: confirmed write completed
- `error`: backend could not complete the request

## Build And Check

```bash
npm run typecheck
npm run build
docker build -t llm-layer-mcp-test-console:local .
```

`VITE_API_BASE_URL` is baked into the static bundle at build time. Set it before `npm run build` if the deployed console must call a non-local backend.

## Important Files

- `src/McpTestConsole.vue`: view state and rendering
- `src/McpTestConsole.css`: console layout and visual system
- `src/useMcpConversation.ts`: conversation state machine and backend calls
- `src/chatApi.ts`: HTTP client functions
- `src/types.ts`: backend response types
- `Dockerfile`: nginx-served static bundle

## Limitations

- No authentication is implemented in this console.
- Conversation state is stored by the backend, not the browser.
- This app is for validation and debugging; production access control should live outside this static bundle.
- Trace panels can expose operational details, so do not publish this console broadly without an access-control layer.
- The Activity view does not contain prompts or responses, but it does expose conversation and event IDs, token counts, model names, tool names, provider failure summaries, and forwarding errors.
