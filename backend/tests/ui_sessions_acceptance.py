"""Real Chromium session UX against isolated SQLite and a synthetic model runner.

Build frontend/dist first, then run: python -m tests.ui_sessions_acceptance.
No account credentials or real workspace files are used.
"""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import time
import unittest

import httpx
from playwright.sync_api import sync_playwright, expect

from harness.application.runtime import Runtime
from harness.core.config import Config
from tests.ui_acceptance import ROOT, TOKEN, serve


@contextmanager
def temporary_workspace():
    directory = tempfile.TemporaryDirectory(prefix="harness-session-ui-")
    try:
        yield directory.name
    finally:
        # Windows file scanners can briefly hold newly projected memory files.
        # Keep cleanup mandatory, but tolerate a bounded release delay.
        for attempt in range(8):
            try:
                directory.cleanup()
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.25)


class SessionUIAcceptance(unittest.TestCase):
    def test_resumable_sessions_and_mobile_layout(self):
        self.assertTrue((ROOT / "frontend/dist/index.html").is_file(), "Build frontend/dist first")
        calls = []
        seeded = False

        async def runner(config, prompt, context, caps):
            calls.append(prompt)
            caps.on_usage(3, 3)
            return "Synthetic session response"

        def factory(config, store):
            nonlocal seeded
            store.set_setting("nightlyDreamEnabled", False)
            if not seeded:
                for index in range(64):
                    store.add_message("user" if index % 2 == 0 else "assistant",
                                      f"Older session message {index:02d}")
                seeded = True
            return Runtime(config, store=store, runner=runner)

        with temporary_workspace() as folder:
            root = Path(folder)
            config = Config(
                root=root, data_dir=root / ".assistant", port=0, token=TOKEN,
                api_key="synthetic-model-no-network", encryption_secret=TOKEN,
                frontend_dir=ROOT / "frontend/dist", browser_channel="chromium",
                run_token_reservation=100, daily_token_limit=100000,
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.set_default_timeout(15000)
                errors = []
                failed_responses = []
                external = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: failed_responses.append(
                    (response.status, response.url)) if response.status >= 400 else None)

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
                    expect(page.get_by_label("Choose chat")).to_be_enabled()

                def button(name):
                    return page.get_by_role("button", name=name, exact=True)

                def send(text):
                    page.get_by_label("Message your assistant").fill(text)
                    button("Send message").click()
                    expect(page.get_by_role("log", name="Conversation")).to_contain_text(text)
                    expect(button("Send message")).to_be_visible()
                    expect(button("New chat")).to_be_enabled()

                try:
                    with serve(config, factory) as origin:
                        with httpx.Client(base_url=origin, trust_env=False,
                                          headers={"Authorization": f"Bearer {TOKEN}"}) as api:
                            connect(origin)
                            selector = page.get_by_label("Choose chat")
                            expect(selector).to_have_value("legacy")
                            log = page.get_by_role("log", name="Conversation")
                            expect(log.locator(".message")).to_have_count(50)
                            expect(log).not_to_contain_text("Older session message 00")
                            button("Load older messages").click()
                            expect(log.locator(".message")).to_have_count(64)
                            expect(log).to_contain_text("Older session message 00")
                            self.assertEqual(calls, [], "Reading and paginating must not call the model")

                            button("New chat").click()
                            expect(selector).not_to_have_value("legacy")
                            expect(button("New chat")).to_be_enabled()
                            first_id = selector.input_value()
                            expect(log.locator(".message")).to_have_count(0)
                            send("First resumable conversation marker")
                            expect(log).to_contain_text("Synthetic session response")
                            expect(page.locator(".chat-context summary")).to_contain_text("characters")
                            page.locator(".chat-context summary").click()
                            expect(page.locator(".chat-context")).to_contain_text("Character counts are not token counts")

                            button("Rename chat").click()
                            page.get_by_label("Chat title", exact=True).fill("Trip planning")
                            button("Save title").click()
                            expect(selector.locator("option:checked")).to_contain_text("Trip planning")
                            button("New chat").click()
                            expect(selector).not_to_have_value(first_id)
                            expect(button("New chat")).to_be_enabled()
                            second_id = selector.input_value()
                            send("Second independent conversation marker")
                            expect(log).not_to_contain_text("First resumable conversation marker")
                            selector.select_option(first_id)
                            expect(log).to_contain_text("First resumable conversation marker")
                            expect(log).not_to_contain_text("Second independent conversation marker")
                            page.reload()
                            expect(page.get_by_label("Choose chat")).to_have_value(first_id)
                            expect(log).to_contain_text("First resumable conversation marker")

                            button("Archive chat").click()
                            expect(selector).not_to_have_value(first_id)
                            expect(button("Show archived chats")).to_be_enabled()
                            button("Show archived chats").click()
                            expect(selector).to_have_value(first_id)
                            expect(log).to_contain_text("First resumable conversation marker")
                            expect(page.get_by_label("Message your assistant")).to_be_disabled()
                            button("Restore chat").click()
                            expect(selector.locator("option")).to_have_count(1)
                            expect(selector.locator("option")).to_have_text("No archived chats")
                            button("Show active chats").click()
                            expect(selector).to_be_enabled()
                            selector.select_option(first_id)
                            expect(log).to_contain_text("First resumable conversation marker")
                            self.assertEqual(len(calls), 2, "Session management must not call the model")

                            page.set_viewport_size({"width": 390, "height": 844})
                            button("Show chats").click()
                            expect(selector).to_be_visible()
                            expect(button("New chat")).to_be_visible()
                            button("Close chats").click()
                            expect(page.get_by_label("Message your assistant")).to_be_visible()
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
                            page.set_viewport_size({"width": 1280, "height": 900})

                            # A session ID saved by a different workspace must recover locally.
                            page.evaluate("sessionStorage.setItem('assistant.currentChat', 'missing-workspace-session')")
                            page.reload()
                            expect(selector).to_have_value("legacy")
                            expect(log.locator(".message")).to_have_count(50)
                            self.assertEqual(len(calls), 2)
                            sessions = api.get("/api/chat-sessions").json()["sessions"]
                            self.assertEqual(len(sessions), 3)
                            self.assertTrue(any(item["id"] == first_id and item["title"] == "Trip planning" for item in sessions))

                            # Saved teams are editable data until the user explicitly starts a run.
                            saved_team = api.post("/api/workflow-templates", json={
                                "name": "Evidence team", "specialists": [
                                    {"role": "Evidence researcher", "prompt": "Find primary evidence."},
                                    {"role": "Risk reviewer", "prompt": "Challenge assumptions."},
                                ],
                            }).json()
                            page.get_by_role("navigation", name="Main navigation").get_by_role(
                                "button", name="Workflows", exact=True).click()
                            team_selector = page.get_by_label("Saved team template")
                            expect(team_selector.locator("option")).to_have_count(2)
                            team_selector.select_option(saved_team["id"])
                            expect(page.get_by_label("Specialist 1 role", exact=True)).to_have_value("Evidence researcher")
                            expect(page.get_by_role("textbox", name="Specialist 2 assignment", exact=True)).to_have_value("Challenge assumptions.")
                            page.get_by_label("Specialist 1 role", exact=True).fill("Reviewed researcher")
                            page.get_by_role("textbox", name="Specialist 1 assignment", exact=True).fill("Find evidence for the reviewed goal.")
                            page.get_by_label("Template name", exact=True).fill("Reviewed evidence team")
                            button("Save team template").click()
                            expect(page.get_by_text("Team template saved. Review and start it whenever you are ready.", exact=True)).to_be_visible()
                            self.assertEqual(len(calls), 2, "Saving and selecting templates must not call the model")
                            templates = api.get("/api/workflow-templates").json()["templates"]
                            self.assertEqual(len(templates), 2)
                            self.assertEqual(next(item for item in templates if item["id"] == saved_team["id"])["specialists"][0]["role"],
                                             "Evidence researcher", "Editing a selected team must preserve its saved source")
                            page.get_by_label("What should the team accomplish?", exact=True).fill("Reviewed team acceptance goal")
                            button("Start workflow").click()
                            expect(page.get_by_text("Final answer", exact=True)).to_be_visible(timeout=30000)
                            workflows = api.get("/api/workflows").json()["workflows"]
                            self.assertEqual(workflows[0]["status"], "completed")
                            self.assertEqual(workflows[0]["nodes"][0]["role"], "Reviewed researcher")
                            self.assertEqual(workflows[0]["nodes"][0]["prompt"], "Find evidence for the reviewed goal.")
                            self.assertEqual(workflows[0]["nodes"][1]["role"], "Risk reviewer")
                            completed_calls = len(calls)

                    config.port = 0
                    with serve(config, factory) as origin:
                        with httpx.Client(base_url=origin, trust_env=False,
                                          headers={"Authorization": f"Bearer {TOKEN}"}) as api:
                            sessions = api.get("/api/chat-sessions").json()["sessions"]
                            self.assertEqual(len(sessions), 3)
                            self.assertEqual(api.get(f"/api/chat-sessions/{first_id}/messages").json()["messages"][0]["content"],
                                             "First resumable conversation marker")
                            self.assertEqual(api.get(f"/api/chat-sessions/{second_id}/messages").json()["messages"][0]["content"],
                                             "Second independent conversation marker")
                            connect(origin)
                            page.get_by_label("Choose chat").select_option(first_id)
                            expect(page.get_by_role("log", name="Conversation")).to_contain_text("First resumable conversation marker")
                            expect(page.get_by_label("Choose chat").locator("option:checked")).to_contain_text("Trip planning")
                            self.assertEqual(len(calls), completed_calls, "Server restart and resume must not call the model")
                            self.assertEqual(len(api.get("/api/workflow-templates").json()["templates"]), 2)
                            self.assertEqual(api.get("/api/workflows").json()["workflows"][0]["status"], "completed")
                    self.assertEqual(errors, [])
                    self.assertEqual(failed_responses, [])
                    self.assertEqual(external, [])
                    print("Session create/send/switch/rename/archive/restore/restart, pagination and 390px layout passed")
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
