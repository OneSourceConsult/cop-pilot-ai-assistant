import asyncio
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from app import runtime
from app.config import AppSettings
from app.request_auth import incoming_bearer, parse_bearer


class RequestAuthTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return AppSettings(_env_file=None, OPENAI_API_KEY="test", OPENAI_MODEL="test",
                           OPENAI_BASE_URL="https://example.invalid", MCP_SERVER_URL="http://mcp.invalid/mcp")

    async def test_concurrent_users_and_anonymous_cache_are_isolated(self):
        runtime.reset_runtime_caches()
        settings = self.settings()

        class Client:
            def __init__(self, connections):
                self.header = connections[settings.tool_server_name].get("headers", {}).get("Authorization")

            async def get_tools(self):
                await asyncio.sleep(0)
                return [SimpleNamespace(name="probe", credential=self.header)]

        async def fetch(header):
            marker = incoming_bearer.set(header)
            try:
                result = await runtime.get_toolset(settings)
                return result.by_name["probe"].credential
            finally:
                incoming_bearer.reset(marker)

        with patch.object(runtime, "MultiServerMCPClient", Client):
            self.assertIsNone(await fetch(None))
            self.assertEqual(await asyncio.gather(fetch("Bearer alice"), fetch("Bearer bob")),
                             ["Bearer alice", "Bearer bob"])
            self.assertEqual(await fetch("Bearer refreshed"), "Bearer refreshed")
            self.assertIsNone(await fetch(None))
        self.assertIsNone(incoming_bearer.get())

    async def test_request_token_overrides_configured_credentials(self):
        settings = self.settings()
        settings.mcp_auth_mode = "static_bearer"
        settings.mcp_auth_token = "service"
        settings.tool_server_headers = {"authorization": "Bearer stale"}
        marker = incoming_bearer.set("Bearer user")
        try:
            headers = runtime._connection_options(settings)["headers"]
            self.assertEqual(headers, {"Authorization": "Bearer user"})
        finally:
            incoming_bearer.reset(marker)

    async def test_probe_does_not_cache_user_credentials(self):
        runtime.reset_runtime_caches()
        marker = incoming_bearer.set("Bearer user")
        try:
            with patch.object(runtime.MultiServerMCPClient, "get_tools", return_value=[]):
                await runtime.probe_mcp_connection(self.settings())
            self.assertIsNone(runtime._toolset)
        finally:
            incoming_bearer.reset(marker)

    def test_header_validation(self):
        self.assertEqual(parse_bearer("bearer token"), "Bearer token")
        self.assertIsNone(parse_bearer(None))
        for header in ("", "Basic token", "Bearer", "Bearer one two"):
            with self.assertRaises(ValueError):
                parse_bearer(header)

    async def test_oauth_renews_before_write_without_replaying_it(self):
        runtime.reset_runtime_caches()
        settings = self.settings()
        settings.mcp_auth_mode = "oauth_client_credentials"
        settings.mcp_auth_token_url = "https://example.invalid/token"
        settings.mcp_auth_client_id = "test-client"
        calls = []
        clock = [100.0]
        issued = []

        def issue(settings, key):
            header = "Bearer token-" + str(len(issued) + 1)
            issued.append(header)
            return runtime.McpAuthToken(header, clock[0] + 60, key)

        class Client:
            def __init__(self, connections):
                self.header = connections[settings.tool_server_name]["headers"]["Authorization"]

            async def get_tools(self):
                async def invoke(arguments):
                    calls.append(self.header)
                    return "ok"
                return [SimpleNamespace(name="createProductOrder", ainvoke=invoke)]

        with patch.object(runtime, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(runtime, "_fetch_oauth_header", side_effect=issue), \
             patch.object(runtime, "MultiServerMCPClient", Client):
            original = (await runtime.get_toolset(settings)).by_name["createProductOrder"]
            self.assertIsNone(runtime._toolset)
            clock[0] = 200.0
            await runtime.invoke_tool(original, {}, settings=settings, allow_retry=False)
        self.assertEqual(issued, ["Bearer token-1", "Bearer token-2"])
        self.assertEqual(calls, ["Bearer token-2"])
        runtime.reset_runtime_caches()

    def test_http_chat_and_confirmation_forward_and_clear_context(self):
        from fastapi.testclient import TestClient
        from app import app_factory
        from app.schemas import ReadyChatResponse

        observed = []

        async def workflow(*args, **kwargs):
            observed.append(incoming_bearer.get())
            return ReadyChatResponse(thread_id="test", status="ready", message="ok")

        with patch.object(app_factory, "get_settings", return_value=self.settings()), \
             patch.object(app_factory, "run_chat", workflow), \
             patch.object(app_factory, "confirm_chat", workflow):
            with TestClient(app_factory.create_app()) as client:
                response = client.post("/v1/chat", json={"message": "test"},
                                       headers={"Authorization": "Bearer first"})
                self.assertEqual(response.status_code, 200)
                response = client.post("/v1/chat/test/confirm",
                                       json={"draft_id": "draft", "fingerprint": "fp", "idempotency_key": "key"},
                                       headers={"Authorization": "Bearer refreshed"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.post("/v1/chat", json={"message": "test"}).status_code, 200)
                self.assertEqual(client.post("/v1/chat", json={"message": "test"},
                                             headers={"Authorization": "Basic invalid"}).status_code, 401)
        self.assertEqual(observed, ["Bearer first", "Bearer refreshed", None])
        self.assertIsNone(incoming_bearer.get())


if __name__ == "__main__":
    unittest.main()
