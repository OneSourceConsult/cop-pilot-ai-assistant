# Configuration

This document covers the current environment variables for the MCP-native path.

Runtime files:

- `backend/llm-layer/.env`: backend runtime file, created locally and not committed
- `backend/llm-layer/.env.example`: backend runtime template
- `ui/mcp-test-console/.env.example`: frontend local-development template

## Production Backend Shape

```env
APP_NAME=LLM Layer
APP_HOST=0.0.0.0
APP_PORT=8010
APP_CORS_ORIGINS=*

OPENAI_BASE_URL=
OPENAI_API_KEY=
OPENAI_MODEL=
OPENAI_TEMPERATURE=0
LLM_HTTP_REFERER=
LLM_APPLICATION_NAME=

MCP_SERVER_NAME=openslice
MCP_TRANSPORT=streamable_http
MCP_SERVER_URL=https://mcp.example.com/mcp
MCP_SERVER_COMMAND=
MCP_SERVER_ARGS=
MCP_SERVER_HEADERS={"Accept":"application/json, text/event-stream"}
MCP_CONNECT_TIMEOUT_SECONDS=5
MCP_READ_TIMEOUT_SECONDS=20
MCP_READ_RETRIES=1
MCP_RETRY_BACKOFF_SECONDS=0.5
MCP_CIRCUIT_BREAKER_SECONDS=20

PRODUCT_READ_TOOL_NAMES=getOSLProductCatalogs,getOSLServiceCatalogs,getOSLProductCategories,getOSLProductOfferingsInCategory,getOSLProductOfferingByProductOfferingId,getOSLProductByProductSpecificationId,searchOSLProductOfferings,getProductOrder
PRODUCT_WRITE_TOOL_NAMES=createProductOrder

CHAT_MAX_TOOL_ROUNDS=6
CHAT_SYSTEM_PROMPT=You are a concise product operations copilot. Help users explore products and prepare guarded product orders through the connected platform.
```

## Local Development Split

Local development uses separate files:

- backend runtime: `backend/llm-layer/.env`
- backend starter template: `backend/llm-layer/.env.example`
- frontend starter template: `ui/mcp-test-console/.env.example`

`VITE_API_BASE_URL` is frontend-only and should not be mixed into the backend production runtime file.

## LLM Settings

### `OPENAI_BASE_URL`

- OpenAI-compatible API base URL
- required
- no runtime default
- must be a valid `http` or `https` URL

### `OPENAI_API_KEY`

- API key for the LLM provider
- required
- startup fails if missing

### `OPENAI_MODEL`

- model name passed to the OpenAI-compatible client
- required
- no runtime default

### `OPENAI_TEMPERATURE`

- temperature passed to the chat model
- current default:

```text
0
```

### `LLM_HTTP_REFERER`

- optional outbound `HTTP-Referer` header for providers that expect it
- no runtime default; set it explicitly only when your LLM provider requires it

### `LLM_APPLICATION_NAME`

- optional outbound `X-Title` header for providers that expect it
- no runtime default; set it explicitly only when your LLM provider requires it

## Frontend Setting

### `VITE_API_BASE_URL`

- base URL used by `ui/mcp-test-console`
- current default:

```text
http://127.0.0.1:8010
```

- this is a Vite env variable
- it controls where the test console sends API requests
- it belongs in the frontend local env file, not the backend runtime file

## MCP Server Settings

### `MCP_SERVER_NAME`

- logical name for the MCP server connection
- current default:

```text
openslice
```

- required

### `MCP_TRANSPORT`

- transport type used by the MCP client
- allowed values:
  - `streamable_http`
  - `sse`
  - `stdio`

- current default:

```text
streamable_http
```

### `MCP_SERVER_URL`

- network URL for MCP server transports
- required when transport is `streamable_http` or `sse`
- no runtime default; startup fails for network transports when this is missing or invalid

### `MCP_SERVER_COMMAND`

- command used when transport is `stdio`
- required only for `stdio`

### `MCP_SERVER_ARGS`

- comma-separated argument list used with `MCP_SERVER_COMMAND`
- only used for `stdio`

### `MCP_SERVER_HEADERS`

- JSON object of extra headers for network transports
- current default:

```json
{"Accept":"application/json, text/event-stream"}
```

- must be valid JSON
- must decode to an object

### `MCP_CONNECT_TIMEOUT_SECONDS`

- timeout used while establishing or refreshing the MCP tool connection
- current default:

```text
5
```

### `MCP_READ_TIMEOUT_SECONDS`

- timeout used for individual MCP tool execution
- current default:

```text
20
```

### `MCP_READ_RETRIES`

- number of bounded retries for safe read-only MCP operations
- current default:

```text
1
```

- write operations are never retried automatically

### `MCP_RETRY_BACKOFF_SECONDS`

- base backoff delay applied between safe read retries
- current default:

```text
0.5
```

### `MCP_CIRCUIT_BREAKER_SECONDS`

- cooldown window after availability failures before the backend will try MCP again
- current default:

```text
20
```

## Product Workflow Settings

### `PRODUCT_READ_TOOL_NAMES`

- comma-separated list of MCP tools classified as safe reads
- current default includes:
  - `getOSLProductCatalogs`
  - `getOSLServiceCatalogs`
  - `getOSLProductCategories`
  - `getOSLProductOfferingsInCategory`
  - `getOSLProductOfferingByProductOfferingId`
  - `getOSLProductByProductSpecificationId`
  - `searchOSLProductOfferings`
  - `getProductOrder`

- these tools execute immediately

### `PRODUCT_WRITE_TOOL_NAMES`

- comma-separated list of guarded write tools
- current default:
  - `createProductOrder`

- these tools do not execute on first request
- they go through `draft -> confirm -> execute`

## Chat Flow Settings

### `CHAT_MAX_TOOL_ROUNDS`

- maximum number of LLM tool-selection rounds per chat request
- current default:

```text
6
```

- validated between `1` and `12`

### `CHAT_SYSTEM_PROMPT`

- base system prompt for the MCP-native chat path
- can be overridden from env

## Startup Validation

The backend validates configuration before serving traffic.

Current checks:

- `APP_NAME` must be set
- `APP_HOST` must be set
- `OPENAI_API_KEY` must be set
- `OPENAI_MODEL` must be set
- `CHAT_SYSTEM_PROMPT` must be set
- `MCP_SERVER_NAME` must be set
- `OPENAI_BASE_URL` must be a valid `http` or `https` URL
- `LLM_HTTP_REFERER`, when set, must be a valid `http` or `https` URL
- `MCP_SERVER_URL` must be empty for `stdio` transport
- `MCP_SERVER_COMMAND` must be empty for network transports
- `MCP_SERVER_ARGS` must be empty for network transports
- `MCP_SERVER_HEADERS` are only allowed for network transports
- MCP reads use bounded retry/backoff behavior
- MCP writes are never retried automatically
- MCP availability failures open a short-lived circuit breaker
- startup logs include a redacted configuration diagnostic snapshot
- `MCP_TRANSPORT` must be one of `sse`, `stdio`, or `streamable_http`
- `MCP_SERVER_URL` must be a valid `http` or `https` URL for network transports
- `MCP_SERVER_COMMAND` must be set for `stdio`
- `MCP_SERVER_HEADERS` must be valid JSON for network transports
- `PRODUCT_READ_TOOL_NAMES` must not be empty
- `PRODUCT_WRITE_TOOL_NAMES` must not be empty
- read and write tool lists must not overlap

## Recommended Local Setup

For the current live MCP server:

```env
APP_NAME=LLM Layer
APP_HOST=0.0.0.0
APP_PORT=8010
APP_CORS_ORIGINS=*

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=YOUR_KEY
OPENAI_MODEL=gpt-4.1-mini
OPENAI_TEMPERATURE=0
LLM_HTTP_REFERER=http://127.0.0.1:4173
LLM_APPLICATION_NAME=llm-layer-mcp-test-console

MCP_SERVER_NAME=openslice
MCP_TRANSPORT=streamable_http
MCP_SERVER_URL=http://10.1.0.75:31015/mcp
MCP_SERVER_HEADERS={"Accept":"application/json, text/event-stream"}
MCP_CONNECT_TIMEOUT_SECONDS=5
MCP_READ_TIMEOUT_SECONDS=20
MCP_READ_RETRIES=1
MCP_RETRY_BACKOFF_SECONDS=0.5
MCP_CIRCUIT_BREAKER_SECONDS=20

PRODUCT_READ_TOOL_NAMES=getOSLProductCatalogs,getOSLServiceCatalogs,getOSLProductCategories,getOSLProductOfferingsInCategory,getOSLProductOfferingByProductOfferingId,getOSLProductByProductSpecificationId,searchOSLProductOfferings,getProductOrder
PRODUCT_WRITE_TOOL_NAMES=createProductOrder

CHAT_MAX_TOOL_ROUNDS=6
```

Frontend local env:

```env
VITE_API_BASE_URL=http://127.0.0.1:8010
```

## Notes

- `backend/llm-layer/.env` is the effective local runtime file for the backend.
- `backend/llm-layer/.env.example` is the backend runtime template and local development starter.
- `ui/mcp-test-console/.env.example` is the frontend local development starter.
- root `.env.example` is a convenience reference for local development, not the file the backend reads directly.
- the frontend and backend are configured independently; `VITE_API_BASE_URL` affects only the test console.
- the current allowlists are intentionally configuration-driven because the guardrail layer classifies tools from these env values.
