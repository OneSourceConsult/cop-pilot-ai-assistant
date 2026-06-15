# Deployment

This is the deployable path for the maintained MCP chat API and test console. Use it to build the same artifacts that CI validates.

## Scope

The deployable surfaces are:

- `backend/llm-layer`
- `ui/mcp-test-console`

The backend is a FastAPI service. The frontend is a static Vite bundle that can be served by any static host or reverse proxy.

## CI Expectations

GitHub Actions now validates the MCP-native path on every relevant push and pull request.

Current CI checks:

- backend tests: `python -m pytest -q` in `backend/llm-layer`
- backend package build: `poetry build`
- backend container build: `docker build` in `backend/llm-layer`
- frontend type check: `npm run typecheck`
- frontend production build: `npm run build`
- frontend container build: `docker build` in `ui/mcp-test-console`

Workflow file:

- [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

## Backend Packaging

Backend production image:

- [`backend/llm-layer/Dockerfile`](../backend/llm-layer/Dockerfile)

Example build:

```bash
cd backend/llm-layer
docker build -t llm-layer:latest .
```

Example run:

```bash
docker run --rm -p 8010:8010 \
  --env-file /path/to/backend.env \
  llm-layer:latest
```

Runtime entrypoint:

```text
uvicorn app.main:app --host 0.0.0.0 --port 8010
```

## Frontend Packaging

Frontend production image:

- [`ui/mcp-test-console/Dockerfile`](../ui/mcp-test-console/Dockerfile)

Example build:

```bash
cd ui/mcp-test-console
docker build -t llm-layer-mcp-test-console:latest .
```

Example run:

```bash
docker run --rm -p 8080:8080 llm-layer-mcp-test-console:latest
```

If the frontend is hosted separately from the backend, set the public API URL before building the static bundle:

```bash
VITE_API_BASE_URL=https://api.example.org npm run build
```

The final image serves the static bundle with nginx on port `8080`.

## Environment Requirements

Backend deployment requires the environment described in:

- [`docs/configuration.md`](configuration.md)
- [`backend/llm-layer/.env.example`](../backend/llm-layer/.env.example)

Minimum required production values:

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `MCP_SERVER_NAME`
- `MCP_TRANSPORT`
- `MCP_SERVER_URL` for network transports
- `MCP_SERVER_COMMAND` for `stdio`
- `PRODUCT_READ_TOOL_NAMES`
- `PRODUCT_WRITE_TOOL_NAMES`

Frontend runtime requirements:

- `VITE_API_BASE_URL` is compiled into the static bundle by Vite at build time
- the built frontend must be able to reach the backend API URL
- if the UI is hosted separately, routing or proxy rules must allow calls to `/v1/chat`, `/v1/chat/{thread_id}/confirm`, and `/v1/chat/{thread_id}/reset`
- frontend local env files are development-only inputs and should not be treated as production deployment configuration

## Health And Readiness

Current backend runtime checks:

- liveness endpoint: `GET /health`
- readiness endpoint: `GET /ready`

Current meanings:

- `/health`
  - confirms the HTTP service is running
- `/ready`
  - confirms the app booted with valid configuration
  - returns the configured MCP server name and transport

Current limitation:

- readiness does not yet probe live MCP reachability or LLM provider reachability
- runtime dependency availability is still exercised at request time rather than through a startup probe

This is acceptable for the current v1 path, but deeper dependency readiness should be added later if production orchestration requires it.

## Operational Notes

- The backend validates configuration at startup and fails fast on invalid settings.
- The frontend is static and should be treated as immutable build output.
- Production deploys should use CI-built artifacts or the same documented Docker builds, not ad hoc local commands.
