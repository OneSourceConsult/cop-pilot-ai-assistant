from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI

from app.config import AppSettings, get_settings


@dataclass(frozen=True)
class Toolset:
    tools: list[BaseTool]
    by_name: dict[str, BaseTool]


@dataclass(frozen=True)
class McpFailure:
    code: str
    trace_status: str
    user_message: str
    retryable: bool
    detail_message: str
    open_circuit: bool = False


class McpOperationError(RuntimeError):
    def __init__(self, failure: McpFailure):
        super().__init__(failure.detail_message)
        self.failure = failure


@dataclass(frozen=True)
class McpProbeResult:
    reachable: bool
    tool_count: int | None
    tool_names: list[str]
    failure: McpFailure | None = None


@dataclass(frozen=True)
class McpAuthToken:
    header_value: str
    expires_at: float
    cache_key: str


_toolset: Toolset | None = None
_circuit_open_until: float = 0.0
_circuit_failure: McpFailure | None = None
_mcp_auth_token: McpAuthToken | None = None


def create_model(settings: AppSettings | None = None) -> ChatOpenAI:
    resolved = settings or get_settings()
    return ChatOpenAI(
        model=resolved.llm_model_name,
        base_url=resolved.llm_api_base_url,
        api_key=resolved.llm_api_key,
        temperature=resolved.llm_temperature,
        default_headers=resolved.llm_default_headers,
    )


async def get_toolset(settings: AppSettings | None = None) -> Toolset:
    global _toolset

    if _toolset is not None:
        return _toolset

    resolved = settings or get_settings()
    _raise_if_circuit_open()
    client = MultiServerMCPClient({resolved.tool_server_name: _connection_options(resolved)})

    try:
        tools = await asyncio.wait_for(client.get_tools(), timeout=resolved.mcp_connect_timeout_seconds)
    except Exception as exc:
        failure = classify_mcp_exception(exc, phase="connect")
        _record_failure(failure, resolved)
        raise McpOperationError(failure) from exc

    _reset_circuit()
    _toolset = Toolset(tools, {tool.name: tool for tool in tools})
    return _toolset


async def probe_mcp_connection(settings: AppSettings | None = None) -> McpProbeResult:
    resolved = settings or get_settings()

    try:
        _raise_if_circuit_open()
    except McpOperationError as exc:
        return McpProbeResult(reachable=False, tool_count=None, tool_names=[], failure=exc.failure)

    client = MultiServerMCPClient({resolved.tool_server_name: _connection_options(resolved)})
    try:
        tools = await asyncio.wait_for(client.get_tools(), timeout=resolved.mcp_connect_timeout_seconds)
    except Exception as exc:
        failure = classify_mcp_exception(exc, phase="connect")
        _record_failure(failure, resolved)
        return McpProbeResult(reachable=False, tool_count=None, tool_names=[], failure=failure)

    global _toolset
    _reset_circuit()
    _toolset = Toolset(tools, {tool.name: tool for tool in tools})
    return McpProbeResult(
        reachable=True,
        tool_count=len(tools),
        tool_names=sorted(tool.name for tool in tools),
    )


async def invoke_tool(
    tool: BaseTool,
    arguments: object,
    *,
    settings: AppSettings | None = None,
    allow_retry: bool,
) -> object:
    resolved = settings or get_settings()
    attempts = resolved.mcp_read_retries + 1 if allow_retry else 1
    last_failure: McpFailure | None = None

    for attempt in range(1, attempts + 1):
        _raise_if_circuit_open()
        try:
            result = await asyncio.wait_for(tool.ainvoke(arguments), timeout=resolved.mcp_read_timeout_seconds)
        except Exception as exc:
            failure = classify_mcp_exception(exc, phase="read" if allow_retry else "write")
            last_failure = failure
            if attempt >= attempts or not allow_retry or not failure.retryable:
                if failure.open_circuit:
                    _record_failure(failure, resolved)
                raise McpOperationError(failure) from exc
            await asyncio.sleep(resolved.mcp_retry_backoff_seconds * attempt)
            continue

        if _is_malformed_result(result):
            failure = McpFailure(
                code="mcp_malformed_response",
                trace_status="malformed",
                user_message="The product platform returned an invalid response.",
                retryable=True,
                detail_message="The MCP tool result could not be normalized into a stable response payload.",
            )
            last_failure = failure
            if attempt >= attempts or not allow_retry:
                raise McpOperationError(failure)
            await asyncio.sleep(resolved.mcp_retry_backoff_seconds * attempt)
            continue

        _reset_circuit()
        return result

    raise McpOperationError(last_failure or _circuit_failure or _circuit_open_failure())


def classify_mcp_exception(exc: Exception, *, phase: str) -> McpFailure:
    if isinstance(exc, McpOperationError):
        return exc.failure

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return McpFailure(
            code="mcp_timeout",
            trace_status="timeout",
            user_message="The product platform did not respond in time.",
            retryable=True,
            detail_message=f"MCP {phase} operation timed out.",
            open_circuit=True,
        )

    detail = f"{type(exc).__name__}: {exc}"
    lowered = str(exc).lower()

    if any(token in lowered for token in _UNAVAILABLE_HINTS):
        return McpFailure(
            code="mcp_unavailable",
            trace_status="unavailable",
            user_message="The product platform is temporarily unavailable.",
            retryable=True,
            detail_message=detail,
            open_circuit=True,
        )

    if any(token in lowered for token in _MALFORMED_HINTS):
        return McpFailure(
            code="mcp_malformed_response",
            trace_status="malformed",
            user_message="The product platform returned an invalid response.",
            retryable=True,
            detail_message=detail,
        )

    return McpFailure(
        code="tool_execution_failed",
        trace_status="error",
        user_message="The product platform could not complete the requested operation.",
        retryable=phase != "write",
        detail_message=detail,
    )


def reset_runtime_caches() -> None:
    global _toolset, _mcp_auth_token
    _toolset = None
    _mcp_auth_token = None
    _reset_circuit()


def _connection_options(settings: AppSettings) -> dict[str, object]:
    options: dict[str, object] = {"transport": settings.tool_server_transport}

    if settings.tool_server_transport == "stdio":
        if not settings.tool_server_command:
            raise ValueError("MCP_SERVER_COMMAND is required when MCP_TRANSPORT=stdio.")
        options["command"] = settings.tool_server_command
        options["args"] = settings.tool_server_args_list
        return options

    options["url"] = settings.tool_server_url
    headers = settings.tool_server_headers_dict
    auth_header = _resolve_mcp_authorization_header(settings)
    if auth_header:
        headers["Authorization"] = auth_header
    if headers:
        options["headers"] = headers

    if settings.tool_server_transport == "streamable_http":
        options["timeout"] = timedelta(seconds=settings.mcp_connect_timeout_seconds)
        options["sse_read_timeout"] = timedelta(seconds=settings.mcp_read_timeout_seconds)
    else:
        options["timeout"] = settings.mcp_connect_timeout_seconds
        options["sse_read_timeout"] = settings.mcp_read_timeout_seconds

    return options


def _record_failure(failure: McpFailure, settings: AppSettings) -> None:
    global _toolset, _circuit_open_until, _circuit_failure
    _toolset = None
    _circuit_failure = failure
    if failure.open_circuit:
        _circuit_open_until = monotonic() + settings.mcp_circuit_breaker_seconds


def _raise_if_circuit_open() -> None:
    if monotonic() < _circuit_open_until:
        raise McpOperationError(_circuit_failure or _circuit_open_failure())


def _reset_circuit() -> None:
    global _circuit_open_until, _circuit_failure
    _circuit_open_until = 0.0
    _circuit_failure = None


def _circuit_open_failure() -> McpFailure:
    return McpFailure(
        code="mcp_unavailable",
        trace_status="unavailable",
        user_message="The product platform is temporarily unavailable.",
        retryable=True,
        detail_message="The MCP circuit breaker is currently open after recent availability failures.",
        open_circuit=True,
    )


def _is_malformed_result(result: object) -> bool:
    if isinstance(result, list):
        if not result:
            return False
        first = result[0]
        if isinstance(first, dict) and "text" in first and not isinstance(first["text"], str):
            return True
    return False


def _resolve_mcp_authorization_header(settings: AppSettings) -> str | None:
    if settings.mcp_auth_mode == "none":
        return None
    if settings.mcp_auth_mode == "static_bearer":
        return _normalize_bearer_token(settings.mcp_auth_token or "")
    return _get_cached_oauth_header(settings)


def _normalize_bearer_token(token: str) -> str:
    normalized = token.strip()
    if normalized.lower().startswith("bearer "):
        return normalized
    return f"Bearer {normalized}"


def _get_cached_oauth_header(settings: AppSettings) -> str:
    global _mcp_auth_token

    cache_key = _oauth_cache_key(settings)
    if _mcp_auth_token and _mcp_auth_token.cache_key == cache_key and monotonic() < _mcp_auth_token.expires_at:
        return _mcp_auth_token.header_value

    token = _fetch_oauth_header(settings, cache_key)
    _mcp_auth_token = token
    return token.header_value


def _oauth_cache_key(settings: AppSettings) -> str:
    return "|".join(
        [
            settings.mcp_auth_mode,
            settings.mcp_auth_token_url or "",
            settings.mcp_auth_client_id or "",
            settings.mcp_auth_username or "",
            settings.mcp_auth_scope or "",
            settings.mcp_auth_audience or "",
        ]
    )


def _fetch_oauth_header(settings: AppSettings, cache_key: str) -> McpAuthToken:
    form_data = {
        "client_id": settings.mcp_auth_client_id or "",
        "grant_type": "password" if settings.mcp_auth_mode == "oauth_password" else "client_credentials",
    }
    if settings.mcp_auth_client_secret:
        form_data["client_secret"] = settings.mcp_auth_client_secret
    if settings.mcp_auth_scope:
        form_data["scope"] = settings.mcp_auth_scope
    if settings.mcp_auth_audience:
        form_data["audience"] = settings.mcp_auth_audience
    if settings.mcp_auth_mode == "oauth_password":
        form_data["username"] = settings.mcp_auth_username or ""
        form_data["password"] = settings.mcp_auth_password or ""

    request = Request(
        settings.mcp_auth_token_url or "",
        data=urlencode(form_data).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))

    access_token = str(body.get("access_token", "")).strip()
    if not access_token:
        raise ValueError("The MCP auth token endpoint did not return an access_token.")

    token_type = str(body.get("token_type", "Bearer")).strip() or "Bearer"
    expires_in = _parse_token_lifetime(body.get("expires_in"), fallback_seconds=300)
    refresh_window = max(expires_in - settings.mcp_auth_refresh_skew_seconds, 1)
    return McpAuthToken(
        header_value=f"{token_type} {access_token}",
        expires_at=monotonic() + refresh_window,
        cache_key=cache_key,
    )


def _parse_token_lifetime(raw_value: object, *, fallback_seconds: int) -> int:
    if raw_value is None:
        return fallback_seconds
    try:
        lifetime = int(raw_value)
    except (TypeError, ValueError):
        return fallback_seconds
    return lifetime if lifetime > 0 else fallback_seconds


_UNAVAILABLE_HINTS = (
    "connection refused",
    "connecterror",
    "connect error",
    "server disconnected",
    "temporarily unavailable",
    "service unavailable",
    "network is unreachable",
    "name or service not known",
    "nodename nor servname provided",
    "all connection attempts failed",
    "connection reset",
    "closed resource",
    "broken pipe",
)

_MALFORMED_HINTS = (
    "decode",
    "invalid json",
    "malformed",
    "unexpected response",
    "parse error",
)
