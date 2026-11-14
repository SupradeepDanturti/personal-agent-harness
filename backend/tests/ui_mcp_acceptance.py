"""Chromium -> real owner API -> encrypted registry, with an offline MCP session."""
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import unittest

import httpx
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from playwright.sync_api import sync_playwright, expect

from harness.application.runtime import Runtime
from harness.core.config import Config
from tests.ui_acceptance import TOKEN, serve, synthetic_runner

ROOT = Path(__file__).resolve().parents[2]
SERVER_URL = "https://example.com/mcp"
SECRET = "synthetic-mcp-token-not-a-real-credential"


class OfflineMCP:
    def __init__(self):
        self.calls = []

    async def initialize(self):
        return None

    async def list_tools(self, *args, **kwargs):
        return ListToolsResult(tools=[
            Tool(name="lookup_note", description="Look up a synthetic note.", inputSchema={"type": "object", "properties": {"query": {"type": "string"}}}),
            Tool(name="save_note", description="Create a synthetic note.", inputSchema={"type": "object", "properties": {"text": {"type": "string"}}}),
        ])

    async def call_tool(self, name, arguments=None, *args, **kwargs):
        self.calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text="Synthetic tool result")])


class MCPUIAcceptance(unittest.TestCase):
    def test_discover_explicit_grants_restart_and_remove(self):
        expect.set_options(timeout=15000)
        self.assertTrue((ROOT / "frontend/dist/index.html").is_file(), "Build frontend first")
        session = OfflineMCP()

        @asynccontextmanager
        async def fake_session(connection):
            yield session

        def factory(config, store):
            store.set_setting("nightlyDreamEnabled", False)
            runtime = Runtime(config, store=store, runner=synthetic_runner)
            runtime.mcp.session_factory = fake_session
            return runtime

        with tempfile.TemporaryDirectory(prefix="harness-mcp-ui-") as folder:
            root = Path(folder)
            config = Config(root=root, data_dir=root / ".assistant", port=0, token=TOKEN,
                encryption_secret="synthetic-mcp-encryption-secret", api_key="synthetic-model-no-network",
                frontend_dir=ROOT / "frontend/dist", run_token_reservation=100, daily_token_limit=100000)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page()
                    page.set_default_timeout(15000)
                    errors, external, failures = [], [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("response", lambda response: failures.append((response.status, response.url)) if response.status >= 400 else None)
                    def local_only(route):
                        if route.request.url.startswith("http://127.0.0.1:"):
                            route.continue_()
                        else:
                            external.append(route.request.url)
                            route.abort()
                    page.route("**/*", local_only)

                    def connect(origin):
                        page.goto(origin)
                        page.get_by_label("Workspace access token").fill(TOKEN)
                        page.get_by_role("button", name="Connect workspace", exact=True).click()
                        page.get_by_role("navigation", name="Main navigation").get_by_role("button", name="Connections", exact=True).click()
                        expect(page.get_by_role("heading", name="MCP servers", exact=True)).to_be_visible()

                    with serve(config, factory) as origin:
                        with httpx.Client(base_url=origin, trust_env=False, headers={"Authorization": f"Bearer {TOKEN}"}) as api:
                            connect(origin)
                            page.get_by_label("MCP server name", exact=True).fill("Synthetic notes")
                            page.get_by_label("MCP server URL", exact=True).fill(SERVER_URL)
                            page.get_by_label("MCP bearer token (optional)", exact=True).fill(SECRET)
                            page.get_by_role("button", name="Add MCP server", exact=True).click()
                            server = page.get_by_label("MCP server Synthetic notes", exact=True)
                            expect(server).to_be_visible()
                            expect(page.get_by_label("MCP bearer token (optional)", exact=True)).to_have_value("")
                            self.assertNotIn(SECRET, api.get("/api/mcp").text)
                            page.get_by_role("button", name="Discover MCP tools", exact=True).click()
                            expect(page.get_by_label("Access for lookup_note", exact=True)).to_have_value("disabled")
                            expect(page.get_by_label("Access for save_note", exact=True)).to_have_value("disabled")
                            self.assertEqual(session.calls, [])
                            page.get_by_label("Access for lookup_note", exact=True).select_option("read")
                            page.get_by_label("Access for save_note", exact=True).select_option("approval")
                            page.get_by_role("button", name="Save MCP tool access", exact=True).click()
                            expect(server.get_by_text("Tool access saved.", exact=True)).to_be_visible()
                            tools = api.get("/api/mcp").json()["connections"][0]["tools"]
                            self.assertEqual({tool["name"]: tool["access"] for tool in tools}, {"lookup_note": "read", "save_note": "approval"})
                            page.set_viewport_size({"width": 390, "height": 844})
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
                            page.set_viewport_size({"width": 1280, "height": 900})
                    config.port = 0
                    with serve(config, factory) as origin:
                        connect(origin)
                        expect(page.get_by_label("Access for lookup_note", exact=True)).to_have_value("read")
                        expect(page.get_by_label("Access for save_note", exact=True)).to_have_value("approval")
                        page.get_by_label("Access for lookup_note", exact=True).select_option("disabled")
                        page.get_by_label("Access for save_note", exact=True).select_option("disabled")
                        page.get_by_role("button", name="Save MCP tool access", exact=True).click()
                        expect(page.get_by_label("MCP server Synthetic notes", exact=True).get_by_text("Tool access saved.", exact=True)).to_be_visible()
                        page.get_by_role("button", name="Remove MCP server", exact=True).click()
                        expect(page.get_by_label("MCP server Synthetic notes", exact=True)).to_have_count(0)
                    self.assertEqual(session.calls, [])
                    self.assertEqual(errors, [])
                    self.assertEqual(failures, [])
                    self.assertEqual(external, [])
                    print("MCP discovery, explicit grants, encrypted restart, revoke, removal and mobile layout passed")
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
