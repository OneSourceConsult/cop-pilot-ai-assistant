import json
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "LLM Layer"
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8010, alias="APP_PORT", ge=1, le=65535)
    app_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"], alias="APP_CORS_ORIGINS")

    llm_api_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    llm_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    llm_model_name: str = Field(default="", alias="OPENAI_MODEL")
    llm_temperature: float = Field(default=0, alias="OPENAI_TEMPERATURE")
    llm_http_referer: str | None = Field(default=None, alias="LLM_HTTP_REFERER")
    llm_application_name: str | None = Field(default=None, alias="LLM_APPLICATION_NAME")

    tool_server_name: str = Field(default="openslice", alias="MCP_SERVER_NAME")
    tool_server_transport: str = Field(default="streamable_http", alias="MCP_TRANSPORT")
    tool_server_url: str = Field(default="", alias="MCP_SERVER_URL")
    tool_server_command: str | None = Field(default=None, alias="MCP_SERVER_COMMAND")
    tool_server_args: Annotated[list[str], NoDecode] = Field(default_factory=list, alias="MCP_SERVER_ARGS")
    tool_server_headers: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=lambda: {"Accept": "application/json, text/event-stream"},
        alias="MCP_SERVER_HEADERS",
    )
    mcp_auth_mode: str = Field(default="none", alias="MCP_AUTH_MODE")
    mcp_auth_token: str | None = Field(default=None, alias="MCP_AUTH_TOKEN")
    mcp_auth_token_url: str | None = Field(default=None, alias="MCP_AUTH_TOKEN_URL")
    mcp_auth_client_id: str | None = Field(default=None, alias="MCP_AUTH_CLIENT_ID")
    mcp_auth_client_secret: str | None = Field(default=None, alias="MCP_AUTH_CLIENT_SECRET")
    mcp_auth_username: str | None = Field(default=None, alias="MCP_AUTH_USERNAME")
    mcp_auth_password: str | None = Field(default=None, alias="MCP_AUTH_PASSWORD")
    mcp_auth_scope: str | None = Field(default=None, alias="MCP_AUTH_SCOPE")
    mcp_auth_audience: str | None = Field(default=None, alias="MCP_AUTH_AUDIENCE")
    mcp_auth_refresh_skew_seconds: int = Field(default=30, alias="MCP_AUTH_REFRESH_SKEW_SECONDS", ge=0, le=3600)
    mcp_connect_timeout_seconds: float = Field(default=5.0, alias="MCP_CONNECT_TIMEOUT_SECONDS", gt=0, le=60)
    mcp_read_timeout_seconds: float = Field(default=20.0, alias="MCP_READ_TIMEOUT_SECONDS", gt=0, le=120)
    mcp_read_retries: int = Field(default=1, alias="MCP_READ_RETRIES", ge=0, le=3)
    mcp_retry_backoff_seconds: float = Field(default=0.5, alias="MCP_RETRY_BACKOFF_SECONDS", ge=0, le=10)
    mcp_circuit_breaker_seconds: float = Field(default=20.0, alias="MCP_CIRCUIT_BREAKER_SECONDS", gt=0, le=300)
    product_read_tool_names: Annotated[set[str], NoDecode] = Field(
        default_factory=lambda: {
            "getOSLProductCatalogs",
            "getOSLServiceCatalogs",
            "getOSLProductCategories",
            "getOSLProductOfferingsInCategory",
            "getOSLProductOfferingByProductOfferingId",
            "getOSLProductByProductSpecificationId",
            "searchOSLProductOfferings",
            "getProductOrder",
        },
        alias="PRODUCT_READ_TOOL_NAMES",
    )
    product_write_tool_names: Annotated[set[str], NoDecode] = Field(
        default_factory=lambda: {"createProductOrder", "createServiceOrder"},
        alias="PRODUCT_WRITE_TOOL_NAMES",
    )

    chat_max_tool_rounds: int = Field(default=6, ge=1, le=12)
    chat_system_prompt: str = (
        "You are a concise product operations copilot. Help users explore network and platform "
        "product offerings and prepare guarded product orders through the connected platform. "
        "Treat live platform results as the source of truth and do not invent them."
    )

    @field_validator("app_name", "app_host", "llm_model_name", "tool_server_name", "chat_system_prompt", mode="before")
    @classmethod
    def _strip_required_text(cls, value: object) -> str:
        return str(value).strip() if value is not None else ""

    @field_validator("tool_server_transport", mode="before")
    @classmethod
    def _strip_transport(cls, value: object) -> str:
        return str(value).strip() if value is not None else ""

    @field_validator(
        "llm_http_referer",
        "llm_application_name",
        "tool_server_command",
        "mcp_auth_token",
        "mcp_auth_token_url",
        "mcp_auth_client_id",
        "mcp_auth_client_secret",
        "mcp_auth_username",
        "mcp_auth_password",
        "mcp_auth_scope",
        "mcp_auth_audience",
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("mcp_auth_mode", mode="before")
    @classmethod
    def _strip_auth_mode(cls, value: object) -> str:
        return str(value).strip().lower() if value is not None else "none"

    @field_validator("app_cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> list[str]:
        if value is None:
            return ["*"]
        if isinstance(value, list):
            return [str(origin).strip() for origin in value if str(origin).strip()]
        raw = str(value).strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @field_validator("tool_server_args", mode="before")
    @classmethod
    def _parse_tool_server_args(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(arg).strip() for arg in value if str(arg).strip()]
        raw = str(value).strip()
        if not raw:
            return []
        return [arg.strip() for arg in raw.split(",") if arg.strip()]

    @field_validator("tool_server_headers", mode="before")
    @classmethod
    def _parse_tool_server_headers(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return {str(key): str(header_value) for key, header_value in value.items()}
        raw = str(value).strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("MCP_SERVER_HEADERS must be valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("MCP_SERVER_HEADERS must decode to a JSON object.")
        return {str(key): str(header_value) for key, header_value in parsed.items()}

    @field_validator("product_read_tool_names", "product_write_tool_names", mode="before")
    @classmethod
    def _parse_tool_name_set(cls, value: object) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, (set, list, tuple)):
            return {str(name).strip() for name in value if str(name).strip()}
        raw = str(value).strip()
        if not raw:
            return set()
        return {name.strip() for name in raw.split(",") if name.strip()}

    @property
    def llm_default_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.llm_http_referer:
            headers["HTTP-Referer"] = self.llm_http_referer
        if self.llm_application_name:
            headers["X-Title"] = self.llm_application_name
        return headers

    @property
    def cors_origins(self) -> list[str]:
        return list(self.app_cors_origins)

    @property
    def tool_server_args_list(self) -> list[str]:
        return list(self.tool_server_args)

    @property
    def tool_server_headers_dict(self) -> dict[str, str]:
        return dict(self.tool_server_headers)

    @property
    def product_read_tool_names_set(self) -> set[str]:
        return set(self.product_read_tool_names)

    @property
    def product_write_tool_names_set(self) -> set[str]:
        return set(self.product_write_tool_names)

    @property
    def startup_diagnostics(self) -> dict[str, object]:
        if self.tool_server_transport == "stdio":
            tool_server_target = self.tool_server_command
        else:
            tool_server_target = self.tool_server_url

        return {
            "app_name": self.app_name,
            "app_host": self.app_host,
            "app_port": self.app_port,
            "cors_origins": self.cors_origins,
            "llm_api_base_url": self.llm_api_base_url,
            "llm_api_key": self._redact_secret(self.llm_api_key),
            "llm_model_name": self.llm_model_name,
            "llm_http_referer": self.llm_http_referer,
            "llm_application_name": self.llm_application_name,
            "tool_server_name": self.tool_server_name,
            "tool_server_transport": self.tool_server_transport,
            "tool_server_target": tool_server_target,
            "tool_server_args": self.tool_server_args_list,
            "tool_server_headers": self._redact_headers(self.tool_server_headers_dict),
            "mcp_auth_mode": self.mcp_auth_mode,
            "mcp_auth_token_url": self.mcp_auth_token_url,
            "mcp_auth_client_id": self.mcp_auth_client_id,
            "mcp_auth_client_secret": self._redact_secret(self.mcp_auth_client_secret),
            "mcp_auth_username": self.mcp_auth_username,
            "mcp_auth_password": self._redact_secret(self.mcp_auth_password),
            "mcp_auth_scope": self.mcp_auth_scope,
            "mcp_auth_audience": self.mcp_auth_audience,
            "mcp_auth_refresh_skew_seconds": self.mcp_auth_refresh_skew_seconds,
            "mcp_connect_timeout_seconds": self.mcp_connect_timeout_seconds,
            "mcp_read_timeout_seconds": self.mcp_read_timeout_seconds,
            "mcp_read_retries": self.mcp_read_retries,
            "mcp_retry_backoff_seconds": self.mcp_retry_backoff_seconds,
            "mcp_circuit_breaker_seconds": self.mcp_circuit_breaker_seconds,
            "product_read_tool_count": len(self.product_read_tool_names),
            "product_write_tool_count": len(self.product_write_tool_names),
            "chat_max_tool_rounds": self.chat_max_tool_rounds,
        }

    @model_validator(mode="after")
    def validate_runtime(self) -> "AppSettings":
        self._require_non_empty("APP_NAME", self.app_name)
        self._require_non_empty("APP_HOST", self.app_host)
        self._require_non_empty("OPENAI_API_KEY", self.llm_api_key)
        self._require_non_empty("OPENAI_MODEL", self.llm_model_name)
        self._require_non_empty("OPENAI_BASE_URL", self.llm_api_base_url)
        self._require_non_empty("MCP_SERVER_NAME", self.tool_server_name)
        self._require_non_empty("CHAT_SYSTEM_PROMPT", self.chat_system_prompt)
        self._validate_url("OPENAI_BASE_URL", self.llm_api_base_url)
        if self.llm_http_referer:
            self._validate_url("LLM_HTTP_REFERER", self.llm_http_referer)

        if self.tool_server_transport not in {"sse", "stdio", "streamable_http"}:
            raise ValueError("MCP_TRANSPORT must be one of 'sse', 'stdio', or 'streamable_http'.")

        if self.mcp_auth_mode not in {"none", "static_bearer", "oauth_client_credentials", "oauth_password"}:
            raise ValueError(
                "MCP_AUTH_MODE must be one of 'none', 'static_bearer', 'oauth_client_credentials', or 'oauth_password'."
            )

        if self.tool_server_transport == "stdio":
            self._require_non_empty("MCP_SERVER_COMMAND", self.tool_server_command)
            if self.tool_server_url.strip():
                raise ValueError("MCP_SERVER_URL must be empty when MCP_TRANSPORT=stdio.")
            if self.tool_server_headers:
                raise ValueError("MCP_SERVER_HEADERS are only supported for network transports.")
            if self.mcp_auth_mode != "none":
                raise ValueError("MCP_AUTH_MODE must be 'none' when MCP_TRANSPORT=stdio.")
        else:
            self._validate_url("MCP_SERVER_URL", self.tool_server_url)
            if self.tool_server_command:
                raise ValueError("MCP_SERVER_COMMAND must be empty when MCP_TRANSPORT uses a network endpoint.")
            if self.tool_server_args:
                raise ValueError("MCP_SERVER_ARGS must be empty when MCP_TRANSPORT uses a network endpoint.")
            self._validate_mcp_auth()

        if not self.product_read_tool_names:
            raise ValueError("PRODUCT_READ_TOOL_NAMES must define at least one tool.")

        if not self.product_write_tool_names:
            raise ValueError("PRODUCT_WRITE_TOOL_NAMES must define at least one tool.")

        overlapping = self.product_read_tool_names & self.product_write_tool_names
        if overlapping:
            names = ", ".join(sorted(overlapping))
            raise ValueError(f"Read/write tool lists must not overlap. Conflicting tools: {names}.")

        return self

    @staticmethod
    def _require_non_empty(name: str, value: str | None) -> None:
        if value is None or not value.strip():
            raise ValueError(f"{name} must be set.")

    @staticmethod
    def _validate_url(name: str, value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{name} must be a valid http or https URL.")

    def _validate_mcp_auth(self) -> None:
        if self.mcp_auth_mode == "none":
            return

        if self.mcp_auth_mode == "static_bearer":
            self._require_non_empty("MCP_AUTH_TOKEN", self.mcp_auth_token)
            return

        self._require_non_empty("MCP_AUTH_TOKEN_URL", self.mcp_auth_token_url)
        self._require_non_empty("MCP_AUTH_CLIENT_ID", self.mcp_auth_client_id)
        assert self.mcp_auth_token_url is not None
        self._validate_url("MCP_AUTH_TOKEN_URL", self.mcp_auth_token_url)

        if self.mcp_auth_mode == "oauth_client_credentials":
            return

        self._require_non_empty("MCP_AUTH_USERNAME", self.mcp_auth_username)
        self._require_non_empty("MCP_AUTH_PASSWORD", self.mcp_auth_password)

    @staticmethod
    def _redact_secret(value: str | None) -> str | None:
        if value is None:
            return None
        return "***redacted***" if value.strip() else ""

    @classmethod
    def _redact_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        return {
            key: "***redacted***" if cls._is_secret_header(key) else value
            for key, value in headers.items()
        }

    @staticmethod
    def _is_secret_header(name: str) -> bool:
        normalized = name.strip().lower()
        return any(token in normalized for token in ("authorization", "api-key", "x-api-key", "token", "secret", "cookie"))


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
