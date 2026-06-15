# LLM Layer Console

Small Vue app for manually testing the llm-layer backend. It is not the product UI; it is a developer/operator console for checking conversation state, guardrail decisions, and MCP tool traces.

## What It Shows

- assistant messages returned by `POST /v1/chat`
- current backend `thread_id`
- pending product-order drafts
- confirm/cancel buttons for guarded writes
- execution result after confirmation
- guardrail and MCP traces, when available
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

## Expected Backend Contract

The console expects the backend response shape documented in [docs/api-reference.md](../../docs/api-reference.md). The fields it actively uses are:

- `thread_id`
- `status`
- `message`
- `draft`
- `pending_action`
- `execution_result`
- `tool_traces`
- `error`

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
- `src/useMcpConversation.ts`: conversation state machine and backend calls
- `src/api.ts`: HTTP client functions
- `src/types.ts`: backend response types
- `Dockerfile`: nginx-served static bundle

## Limitations

- No authentication is implemented in this console.
- Conversation state is stored by the backend, not the browser.
- This app is for validation and debugging; production access control should live outside this static bundle.
- Trace panels can expose operational details, so do not publish this console broadly without an access-control layer.
