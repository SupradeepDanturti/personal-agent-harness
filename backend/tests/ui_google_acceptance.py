"""Real Chromium -> API/SQLite -> mocked Google, with no account access."""
from pathlib import Path
import tempfile
import unittest

import httpx
from playwright.sync_api import sync_playwright, expect

from harness.core.config import Config
from tests.google_fixture import GoogleFixture, SHEET_ID
from tests.ui_acceptance import serve, TOKEN

ROOT = Path(__file__).resolve().parents[2]


class GoogleUIAcceptance(unittest.TestCase):
    def test_registered_range_raw_approval_discovery_and_restart(self):
        self.assertTrue((ROOT / "frontend/dist/index.html").is_file(), "Build frontend before UI acceptance")
        fixture = GoogleFixture()
        with tempfile.TemporaryDirectory(prefix="harness-google-ui-") as folder:
            root = Path(folder)
            config = Config(root=root, data_dir=root / ".assistant", port=0, token=TOKEN,
                encryption_secret="synthetic-credential-secret-123456", api_key="synthetic-model-no-network",
                frontend_dir=ROOT / "frontend/dist", run_token_reservation=100, daily_token_limit=100000)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.set_default_timeout(15000)
                    errors, external, failures = [], [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("response", lambda response: failures.append(response.status) if response.status >= 400 else None)
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
                        expect(page.get_by_text("Connected account: owner@example.invalid", exact=True)).to_be_visible()

                    with serve(config, fixture.runtime) as origin:
                        with httpx.Client(base_url=origin, trust_env=False, headers={"Authorization": "Bearer " + TOKEN}) as api:
                            connect(origin)
                            page.get_by_label("Spreadsheet name", exact=True).fill("UI Budget")
                            page.get_by_label("Spreadsheet URL or ID", exact=True).fill(SHEET_ID)
                            page.get_by_label("Allowed registration range", exact=True).fill("Budget!A1:B2")
                            page.get_by_role("button", name="Register spreadsheet", exact=True).click()
                            expect(page.get_by_role("heading", name="Spreadsheet: UI Budget", exact=True)).to_be_visible()
                            page.get_by_role("button", name="Read spreadsheet range", exact=True).click()
                            expect(page.get_by_text('"Synthetic cell"', exact=False)).to_be_visible()
                            page.get_by_label("Proposed cell values (JSON rows)", exact=True).fill('[["=literal", 99]]')
                            page.get_by_role("button", name="Propose exact cell update", exact=True).click()
                            expect(page.get_by_role("button", name="Review spreadsheet update in Approvals", exact=True)).to_be_visible()
                            self.assertEqual(fixture.writes, [])
                            pending = api.get("/api/approvals").json()["approvals"][0]
                            self.assertEqual(pending["payload"]["values"], [["=literal", 99]])
                            page.get_by_role("button", name="Review spreadsheet update in Approvals", exact=True).click()
                            expect(page.get_by_role("heading", name="Approvals", exact=True)).to_be_visible()
                            page.get_by_role("button", name="Approve this exact action", exact=True).click()
                            expect(page.get_by_role("button", name="Approve this exact action", exact=True)).to_have_count(0)
                            self.assertEqual(api.get("/api/approvals").json()["approvals"][0]["status"], "completed")
                            self.assertEqual(len(fixture.writes), 1)
                            self.assertEqual(fixture.writes[0].url.params["valueInputOption"], "RAW")

                            page.get_by_role("navigation", name="Main navigation").get_by_role("button", name="Connections", exact=True).click()
                            page.get_by_role("button", name="Enable spreadsheet discovery", exact=True).click()
                            page.get_by_label("Spreadsheet name search", exact=True).fill("Budget")
                            page.get_by_role("button", name="Search spreadsheets", exact=True).click()
                            page.get_by_role("button", name="Inspect Synthetic Budget", exact=True).click()
                            page.get_by_role("button", name="List spreadsheet tabs", exact=True).click()
                            expect(page.get_by_role("button", name="Use tab Budget", exact=True)).to_be_visible()
                            page.get_by_role("button", name="Use tab Budget", exact=True).click()
                            expect(page.get_by_label("A1 range to read or update", exact=True)).to_have_value("'Budget'!A1:D25")
                            page.get_by_role("button", name="Disable spreadsheet discovery", exact=True).click()
                            expect(page.get_by_role("heading", name="Spreadsheet: Synthetic Budget", exact=True)).to_have_count(0)
                            self.assertFalse(api.get("/api/google/sheets").json()["discoveryEnabled"])
                            self.assertEqual(len(api.get("/api/google/sheets").json()["sheets"]), 1)
                    config.port = 0
                    with serve(config, fixture.runtime) as origin:
                        connect(origin)
                        expect(page.get_by_role("button", name="Open UI Budget", exact=True)).to_be_visible()
                        expect(page.get_by_role("button", name="Enable spreadsheet discovery", exact=True)).to_be_visible()
                    self.assertEqual(errors, [])
                    self.assertEqual(external, [])
                    self.assertEqual(failures, [])
                    print("Google UI registration, bounded read, RAW approval, discovery and restart passed")
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
