"""Chromium authentication recovery against an isolated real API and synthetic token.

Build frontend/dist, then run from backend: python -m tests.ui_auth_acceptance.
"""
from pathlib import Path
import tempfile
import unittest

from playwright.sync_api import expect, sync_playwright

from harness.core.config import Config
from tests.ui_acceptance import ROOT, TOKEN, serve


class AuthUIAcceptance(unittest.TestCase):
    def test_validation_rejection_reconnect_and_late_responses(self):
        self.assertTrue((ROOT / "frontend/dist/index.html").is_file(), "Build frontend/dist first")
        with tempfile.TemporaryDirectory(prefix="harness-auth-ui-") as folder:
            root = Path(folder)
            config = Config(
                root=root, data_dir=root / ".assistant", port=0, token=TOKEN,
                api_key="synthetic-model-no-network", encryption_secret=TOKEN,
                frontend_dir=ROOT / "frontend/dist", browser_channel="chromium",
                run_token_reservation=100, daily_token_limit=100000,
            )
            with serve(config) as origin, sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.set_default_timeout(15000)
                    errors, api_paths = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: api_paths.append(
                        request.url.split("/api", 1)[1]) if "/api/" in request.url else None)

                    def local_only(route):
                        if route.request.url.startswith(origin):
                            route.continue_()
                        else:
                            route.abort()

                    page.route("**/*", local_only)
                    page.goto(origin)
                    field = page.get_by_label("Workspace access token", exact=True)
                    connect = page.get_by_role("button", name="Connect workspace", exact=True)
                    rejected = "Workspace token was rejected or changed. Reconnect your workspace."
                    field.fill("synthetic-invalid-token")
                    connect.click()
                    expect(page.get_by_role("alert")).to_have_text(rejected)
                    expect(field).to_be_visible()
                    self.assertEqual(api_paths, ["/status"], "Invalid tokens must never mount connected views")
                    self.assertIsNone(page.evaluate("sessionStorage.getItem('harness-token')"))

                    # Network failure is distinct from an authentication rejection.
                    page.route("**/api/status", lambda route: route.abort("connectionrefused"))
                    field.fill(TOKEN)
                    connect.click()
                    expect(page.get_by_role("alert")).to_contain_text("Cannot reach your assistant.")
                    expect(page.get_by_role("alert")).not_to_contain_text("rejected")
                    expect(connect).to_be_enabled()
                    page.unroute("**/api/status")

                    # A held status response proves the token is not persisted early,
                    # and both the field and button stay disabled during validation.
                    pending_status = []
                    page.route("**/api/status", lambda route: pending_status.append(route))
                    connect.click()
                    expect(connect).to_be_disabled()
                    expect(field).to_be_disabled()
                    self.assertIsNone(page.evaluate("sessionStorage.getItem('harness-token')"))
                    self.assertEqual(len(pending_status), 1)
                    pending_status.pop().continue_()
                    page.unroute("**/api/status")
                    expect(page.get_by_label("Choose chat")).to_be_visible()
                    expect(page.get_by_role("button", name="Disconnect workspace", exact=True)).to_be_visible()

                    # Keep one old-session response in flight, then reject a second
                    # request from any view. It must immediately return to login.
                    pending_events = []
                    event_requests = []

                    def events(route):
                        event_requests.append(route)
                        if len(event_requests) == 1:
                            pending_events.append(route)
                        elif len(event_requests) == 2:
                            route.fulfill(status=401, json={"error": "Unauthorized"})
                        else:
                            route.continue_()

                    page.route("**/api/events", events)
                    page.get_by_role("navigation", name="Main navigation").get_by_role(
                        "button", name="Activity", exact=True).click()
                    page.get_by_role("button", name="Refresh", exact=True).click()
                    expect(field).to_be_visible()
                    expect(page.get_by_role("alert")).to_have_text(rejected)
                    self.assertIsNone(page.evaluate("sessionStorage.getItem('harness-token')"))
                    field.fill(TOKEN)
                    connect.click()
                    expect(page.get_by_role("heading", name="Activity log", exact=True)).to_be_visible()
                    expect(page.get_by_role("button", name="Disconnect workspace", exact=True)).to_be_visible()

                    # Same token, different session generation: the late rejection
                    # must not sign out the successfully reconnected owner.
                    with page.expect_response(lambda response: response.url.endswith("/api/events") and response.status == 401):
                        pending_events.pop().fulfill(status=401, json={"error": "Unauthorized"})
                    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    expect(field).not_to_be_visible()
                    expect(page.get_by_role("button", name="Disconnect workspace", exact=True)).to_be_visible()
                    self.assertEqual(page.evaluate("sessionStorage.getItem('harness-token')"), TOKEN)
                    api_paths.clear()
                    page.evaluate("sessionStorage.setItem('harness-token', 'synthetic-stale-token')")
                    page.reload()
                    expect(field).to_be_visible()
                    expect(page.get_by_role("alert")).to_have_text(rejected)
                    self.assertEqual(api_paths, ["/status"], "Remembered tokens also require validation before mounting views")
                    self.assertIsNone(page.evaluate("sessionStorage.getItem('harness-token')"))
                    self.assertEqual(errors, [])
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
