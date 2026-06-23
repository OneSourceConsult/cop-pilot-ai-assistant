from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import AppSettings


def _settings(**overrides: object) -> AppSettings:
    base = {
        "llm_api_key": "test-key",
        "llm_model_name": "gpt-4.1-mini",
        "llm_api_base_url": "https://api.openai.com/v1",
        "tool_server_name": "openslice",
        "tool_server_transport": "streamable_http",
        "tool_server_url": "http://127.0.0.1:8003/mcp",
        "product_read_tool_names": "tmf_list_product_offerings",
        "product_write_tool_names": "tmf_create_product_order",
    }
    return AppSettings(_env_file=None, **(base | overrides))


def test_valid_streamable_http_settings_pass_validation() -> None:
    settings = _settings()
    assert settings.tool_server_transport == "streamable_http"


def test_network_transport_requires_explicit_url() -> None:
    with pytest.raises(ValidationError, match="MCP_SERVER_URL must be a valid http or https URL"):
        _settings(tool_server_url="")


def test_missing_api_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY must be set"):
        _settings(llm_api_key=" ")


def test_missing_model_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="OPENAI_MODEL must be set"):
        _settings(llm_model_name=" ")


def test_missing_llm_base_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="OPENAI_BASE_URL must be set"):
        _settings(llm_api_base_url=" ")


def test_invalid_llm_base_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="OPENAI_BASE_URL must be a valid http or https URL"):
        _settings(llm_api_base_url="not-a-url")


def test_invalid_mcp_transport_is_rejected() -> None:
    with pytest.raises(ValidationError, match="MCP_TRANSPORT must be one of 'sse', 'stdio', or 'streamable_http'"):
        _settings(tool_server_transport="socket")


def test_stdio_transport_requires_command() -> None:
    with pytest.raises(ValidationError, match="MCP_SERVER_COMMAND must be set"):
        _settings(tool_server_transport="stdio", tool_server_command=" ")


def test_stdio_transport_rejects_url_configuration() -> None:
    with pytest.raises(ValidationError, match="MCP_SERVER_URL must be empty when MCP_TRANSPORT=stdio"):
        _settings(tool_server_transport="stdio", tool_server_url="http://127.0.0.1:8003/mcp", tool_server_command="python")


def test_stdio_transport_rejects_network_headers() -> None:
    with pytest.raises(ValidationError, match="MCP_SERVER_HEADERS are only supported for network transports"):
        _settings(
            tool_server_transport="stdio",
            tool_server_url="",
            tool_server_command="python",
            tool_server_headers='{"Authorization":"Bearer secret"}',
        )


def test_sse_transport_requires_valid_url() -> None:
    with pytest.raises(ValidationError, match="MCP_SERVER_URL must be a valid http or https URL"):
        _settings(tool_server_url="stdio://openslice")


def test_network_transport_rejects_stdio_command() -> None:
    with pytest.raises(ValidationError, match="MCP_SERVER_COMMAND must be empty when MCP_TRANSPORT uses a network endpoint"):
        _settings(tool_server_command="python -m server")


def test_network_transport_rejects_stdio_args() -> None:
    with pytest.raises(ValidationError, match="MCP_SERVER_ARGS must be empty when MCP_TRANSPORT uses a network endpoint"):
        _settings(tool_server_args="--serve,--port,9000")


def test_invalid_mcp_headers_are_rejected() -> None:
    with pytest.raises(ValidationError, match="MCP_SERVER_HEADERS must be valid JSON"):
        _settings(tool_server_headers="{invalid}")


def test_invalid_mcp_auth_mode_is_rejected() -> None:
    with pytest.raises(ValidationError, match="MCP_AUTH_MODE must be one of"):
        _settings(mcp_auth_mode="apikey")


def test_static_bearer_auth_requires_token() -> None:
    with pytest.raises(ValidationError, match="MCP_AUTH_TOKEN must be set"):
        _settings(mcp_auth_mode="static_bearer", mcp_auth_token=" ")


def test_oauth_password_auth_requires_token_url_and_credentials() -> None:
    with pytest.raises(ValidationError, match="MCP_AUTH_TOKEN_URL must be set"):
        _settings(mcp_auth_mode="oauth_password", mcp_auth_token_url=None)

    with pytest.raises(ValidationError, match="MCP_AUTH_USERNAME must be set"):
        _settings(
            mcp_auth_mode="oauth_password",
            mcp_auth_token_url="http://127.0.0.1:8080/token",
            mcp_auth_client_id="copilot",
            mcp_auth_username=" ",
            mcp_auth_password="admin",
        )


def test_stdio_transport_rejects_mcp_auth_modes() -> None:
    with pytest.raises(ValidationError, match="MCP_AUTH_MODE must be 'none' when MCP_TRANSPORT=stdio"):
        _settings(
            tool_server_transport="stdio",
            tool_server_url="",
            tool_server_command="python",
            tool_server_headers={},
            mcp_auth_mode="static_bearer",
            mcp_auth_token="secret",
        )


def test_invalid_llm_http_referer_is_rejected() -> None:
    with pytest.raises(ValidationError, match="LLM_HTTP_REFERER must be a valid http or https URL"):
        _settings(llm_http_referer="not-a-url")


def test_llm_headers_default_to_empty() -> None:
    settings = _settings(llm_http_referer=None, llm_application_name=None)

    assert settings.llm_default_headers == {}


def test_invalid_mcp_read_retries_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(mcp_read_retries=4)


def test_read_tool_list_cannot_be_empty() -> None:
    with pytest.raises(ValidationError, match="PRODUCT_READ_TOOL_NAMES must define at least one tool"):
        _settings(product_read_tool_names=" ")


def test_write_tool_list_cannot_be_empty() -> None:
    with pytest.raises(ValidationError, match="PRODUCT_WRITE_TOOL_NAMES must define at least one tool"):
        _settings(product_write_tool_names=" ")


def test_read_and_write_tool_lists_cannot_overlap() -> None:
    with pytest.raises(ValidationError, match="Read/write tool lists must not overlap"):
        _settings(
            product_read_tool_names="tmf_list_product_offerings,tmf_create_product_order",
            product_write_tool_names="tmf_create_product_order",
        )


def test_startup_diagnostics_redact_secrets() -> None:
    settings = _settings(
        tool_server_headers='{"Authorization":"Bearer secret","Accept":"application/json"}',
        mcp_auth_mode="oauth_password",
        mcp_auth_token_url="http://127.0.0.1:8080/token",
        mcp_auth_client_id="copilot",
        mcp_auth_client_secret="super-secret",
        mcp_auth_username="admin",
        mcp_auth_password="admin-pass",
    )

    assert settings.startup_diagnostics["llm_api_key"] == "***redacted***"
    assert settings.startup_diagnostics["tool_server_headers"] == {
        "Authorization": "***redacted***",
        "Accept": "application/json",
    }
    assert settings.startup_diagnostics["mcp_auth_client_secret"] == "***redacted***"
    assert settings.startup_diagnostics["mcp_auth_password"] == "***redacted***"
    assert settings.startup_diagnostics["mcp_read_retries"] == 1
