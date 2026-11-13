"""Real Chromium -> HTTP -> runtime -> SQLite acceptance, without account connections.

Run after building frontend/dist: python -m tests.ui_acceptance (from backend).
The model runner is deterministic; no secrets or live assistant data are loaded.
"""
from contextlib import contextmanager
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

import httpx
import uvicorn
from playwright.sync_api import sync_playwright, expect

from harness.api.server import create_app
from harness.application.runtime import Runtime
from harness.core.config import Config

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "ui-test-only-token-not-a-real-credential"


async def synthetic_runner(config, prompt, context, caps):
    caps.on_usage(3, 3)
    return "Synthetic local acceptance result"


def runtime_factory(config, store):
    store.set_setting("nightlyDreamEnabled", False)
    return Runtime(config, store=store, runner=synthetic_runner)


@contextmanager
def serve(config):
    listener = socket.socket()
    listener.bind(("127.0.0.1", config.port))
    config.port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        create_app(config, runtime_factory), log_level="error", access_log=False,
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Acceptance server did not start")
            time.sleep(0.02)
        yield f"http://127.0.0.1:{config.port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("Acceptance server did not stop")


class UIAcceptance(unittest.TestCase):
    def test_local_workspace_round_trip_and_restart(self):
        if not (ROOT / "frontend/dist/index.html").is_file():
            self.fail("Build frontend/dist before running UI acceptance")
        with tempfile.TemporaryDirectory(prefix="harness-ui-") as folder:
            root = Path(folder)
            config = Config(
                root=root, data_dir=root / ".assistant", port=0, token=TOKEN,
                api_key="synthetic-model-no-network", encryption_secret=TOKEN,
                frontend_dir=ROOT / "frontend/dist", browser_channel="chromium",
                run_token_reservation=100, daily_token_limit=100000,
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.set_default_timeout(15000)
                    errors = []
                    failures = []
                    external = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("response", lambda response: failures.append(
                        (response.status, response.url)) if response.status >= 400 else None)
                    # A UI regression must never contact external accounts or services.
                    def local_only(route):
                        if route.request.url.startswith("http://127.0.0.1:"):
                            route.continue_()
                        else:
                            external.append(route.request.url)
                            route.abort()
                    page.route("**/*", local_only)
                    with serve(config) as origin:
                        with httpx.Client(base_url=origin, trust_env=False,
                                          headers={"Authorization": f"Bearer {TOKEN}"}) as api:
                            self.assertEqual(httpx.get(origin + "/api/status", trust_env=False).status_code, 401)
                            page.goto(origin)
                            page.get_by_label("Workspace access token").fill(TOKEN)
                            page.get_by_role("button", name="Connect workspace", exact=True).click()
                            expect(page.get_by_label("Message your assistant")).to_be_visible()

                            def navigate(name):
                                page.get_by_role("navigation", name="Main navigation").get_by_role(
                                    "button", name=name, exact=True).click()
                                expect(page.get_by_role("heading", name=(
                                    "What's on your mind?" if name == "Chat" else name), exact=True)).to_be_visible()
                                page.wait_for_load_state("networkidle")

                            for name in ("Chat", "Goals", "Memory", "History", "People", "Repairs",
                                         "Evidence", "Schedules", "Tasks", "Workflows", "Browser",
                                         "Approvals", "Inbox", "Files", "Activity"):
                                navigate(name)
                            print("Rendered all 15 non-connection screens")

                            navigate("Chat")
                            page.get_by_label("Message your assistant").fill("Acceptance chat marker")
                            page.get_by_role("button", name="Send message", exact=True).click()
                            expect(page.get_by_text("Synthetic local acceptance result", exact=True)).to_be_visible()

                            navigate("Memory")
                            page.get_by_label("Fact or preference").fill("Acceptance memory marker")
                            page.get_by_role("button", name="Save memory", exact=True).click()
                            expect(page.get_by_text("Acceptance memory marker", exact=True)).to_be_visible()
                            page.get_by_role("button", name="Lineage", exact=True).click()
                            expect(page.get_by_role("heading", name="Claim lineage", exact=True)).to_be_visible()
                            page.get_by_role("button", name="Close lineage", exact=True).click()
                            page.get_by_role("button", name="Edit", exact=True).click()
                            page.get_by_label("Fact or preference").fill("Corrected acceptance memory")
                            page.get_by_role("button", name="Save correction", exact=True).click()
                            expect(page.get_by_text("Corrected acceptance memory", exact=True)).to_be_visible()

                            navigate("Files")
                            page.get_by_label("Workspace-relative file path").fill("reports/acceptance.md")
                            page.get_by_label("File contents").fill("Acceptance file marker")
                            page.get_by_role("button", name="Save file", exact=True).click()
                            expect(page.get_by_text("File saved in the assistant workspace.", exact=True)).to_be_visible()
                            page.get_by_label("File contents").fill("")
                            page.get_by_role("button", name="Read file", exact=True).click()
                            expect(page.get_by_label("File contents")).to_have_value("Acceptance file marker")
                            page.get_by_label("Folder", exact=True).fill("reports")
                            page.get_by_role("button", name="List files", exact=True).click()
                            expect(page.get_by_role("button", name="File reports/acceptance.md", exact=False)).to_be_visible()

                            navigate("Goals")
                            page.get_by_label("Goal", exact=True).fill("Acceptance goal")
                            page.get_by_label("Starting steps (one per line, optional)").fill("First step\nSecond step")
                            page.get_by_role("button", name="Create goal", exact=True).click()
                            expect(page.get_by_role("heading", name="Acceptance goal", exact=True)).to_be_visible()
                            page.get_by_role("button", name="Mark complete", exact=True).click()
                            expect(page.get_by_role("button", name="Mark complete", exact=True)).to_be_disabled()

                            navigate("History")
                            page.get_by_label("What are you looking for?").fill("Acceptance")
                            page.get_by_role("button", name="Keyword search", exact=True).click()
                            expect(page.locator(".record-list")).to_contain_text("[Acceptance] chat marker")

                            navigate("People")
                            page.get_by_label("Name", exact=True).fill("Acceptance Person")
                            page.get_by_label("Context", exact=True).fill("Synthetic person, not an account")
                            page.get_by_role("button", name="Save reviewed entry", exact=True).click()
                            expect(page.get_by_role("heading", name="Acceptance Person", exact=False)).to_be_visible()

                            navigate("Workflows")
                            page.get_by_label("What should the team accomplish?").fill("Acceptance workflow")
                            page.get_by_role("button", name="Start workflow", exact=True).click()
                            expect(page.get_by_text("Acceptance workflow", exact=True)).to_be_visible()
                            expect(page.get_by_text("Final answer", exact=True)).to_be_visible(timeout=30000)
                            workflows = api.get("/api/workflows").json()["workflows"]
                            self.assertEqual(workflows[0]["status"], "completed")
                            self.assertTrue(all(node["task"]["attempts"] == 1 for node in workflows[0]["nodes"]))

                            approval = api.post("/api/actions", json={
                                "kind": "skill.save", "summary": "Acceptance procedure",
                                "payload": {"name": "acceptance", "content": "# Reviewed test procedure"},
                            }).json()
                            self.assertFalse((config.data_dir / "workspace/skills/acceptance.md").exists())
                            navigate("Approvals")
                            expect(page.get_by_text("Acceptance procedure", exact=True)).to_be_visible()
                            page.get_by_role("button", name="Approve this exact action", exact=True).click()
                            expect(page.get_by_role("button", name="Approve this exact action", exact=True)).to_have_count(0)
                            self.assertEqual(api.get("/api/approvals").json()["approvals"][0]["status"], "completed")
                            replay = api.post(f"/api/approvals/{approval['id']}/decision", json={
                                "decision": "approved", "expectedDigest": approval["digest"],
                            })
                            self.assertEqual(replay.status_code, 409)

                            navigate("Schedules")
                            page.get_by_label("What should your assistant do?").fill("Acceptance scheduled task")
                            # A due local fixture exercises the actual scheduler without a long timer.
                            page.get_by_label("First run in your local time").fill("2025-01-01T12:00")
                            page.get_by_role("button", name="Create schedule", exact=True).click()
                            expect(page.get_by_text("Acceptance scheduled task", exact=True)).to_be_visible()
                            deadline = time.monotonic() + 15
                            while not any(s["prompt"] == "Acceptance scheduled task" and s["status"] == "completed"
                                          for s in api.get("/api/schedules").json()["schedules"]):
                                self.assertLess(time.monotonic(), deadline, "Scheduled task did not complete")
                                time.sleep(0.1)
                            navigate("Inbox")
                            expect(page.locator(".record-list")).to_contain_text("Synthetic local acceptance result")

                            page.get_by_role("button", name="Dark mode", exact=True).click()
                            expect(page.get_by_role("button", name="Light mode", exact=True)).to_be_visible()
                            page.reload()
                            expect(page.get_by_role("button", name="Light mode", exact=True)).to_be_visible()
                            navigate("Chat")
                            expect(page.get_by_text("Acceptance chat marker", exact=True)).to_be_visible()
                            self.assertEqual(errors, [], "JavaScript errors")
                            self.assertEqual(failures, [], "Unexpected failed HTTP responses")
                            self.assertEqual(external, [], "Unexpected external requests")
                            print("Chat, memory correction/lineage, file read/write/list, goals, history, people,")
                            print("workflow completion, approval/replay, schedule/inbox and theme persistence passed")

                    # Restart the real HTTP server over the same SQLite/workspace files.
                    config.port = 0
                    with serve(config) as origin:
                        with httpx.Client(base_url=origin, trust_env=False,
                                          headers={"Authorization": f"Bearer {TOKEN}"}) as api:
                            self.assertEqual(len(api.get("/api/history").json()["messages"]), 2)
                            self.assertEqual(api.get("/api/memories").json()["memories"][0]["text"], "Corrected acceptance memory")
                            self.assertEqual(api.get("/api/workflows").json()["workflows"][0]["status"], "completed")
                            self.assertEqual(api.get("/api/files/read", params={"path": "reports/acceptance.md"}).json()["content"], "Acceptance file marker")
                            self.assertEqual(api.get("/api/goals").json()["goals"][0]["status"], "completed")
                            self.assertEqual(api.get("/api/schedules").json()["schedules"][0]["status"], "completed")
                            self.assertEqual(api.get("/api/approvals").json()["approvals"][0]["status"], "completed")
                            page.goto(origin)
                            page.get_by_label("Workspace access token").fill(TOKEN)
                            page.get_by_role("button", name="Connect workspace", exact=True).click()
                            page.get_by_role("navigation", name="Main navigation").get_by_role("button", name="Memory", exact=True).click()
                            expect(page.get_by_text("Corrected acceptance memory", exact=True)).to_be_visible()
                            page.get_by_role("button", name="Forget saved memory", exact=True).click()
                            expect(page.get_by_text("Corrected acceptance memory", exact=True)).to_have_count(0)
                            self.assertEqual(api.get("/api/memories").json()["memories"], [])
                            self.assertEqual(len(api.get("/api/history").json()["messages"]), 2)
                            print("Server restart persistence and memory forgetting passed")
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
