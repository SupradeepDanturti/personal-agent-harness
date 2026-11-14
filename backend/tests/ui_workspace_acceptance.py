"""Real Chromium workspace settings, keyboard navigation and mobile layout.

Build frontend/dist first; run from backend: python -m tests.ui_workspace_acceptance.
No real account credentials, external requests or model calls are needed.
"""
from pathlib import Path
import os
import time
import tempfile
import unittest

from playwright.sync_api import expect, sync_playwright

from harness.core.config import Config
from harness.application.runtime import Runtime
from tests.ui_acceptance import ROOT, TOKEN, serve


class WorkspaceUIAcceptance(unittest.TestCase):
    def test_settings_theme_connectors_permissions_and_mobile(self):
        self.assertTrue((ROOT / "frontend/dist/index.html").is_file(), "Build frontend/dist first")
        with tempfile.TemporaryDirectory(prefix="harness-workspace-ui-") as folder:
            root = Path(folder)
            config = Config(
                root=root, data_dir=root / ".assistant", port=0, token=TOKEN,
                api_key="synthetic-model-no-network", encryption_secret=TOKEN,
                frontend_dir=ROOT / "frontend/dist", browser_channel="chromium",
                run_token_reservation=100, daily_token_limit=100000,
            )
            def factory(config,store):
                store.set_setting('nightlyDreamEnabled',False)
                runtime=Runtime(config,store=store)
                store.add_message('user','Help me organize the project for this week.')
                store.add_message('assistant','We can keep the next steps clear: finish the workspace, verify the workflows, and review the results together.')
                for title in ('Review the launch checklist','Plan the week ahead'):
                    session=runtime.chats.create({'title':title})
                    store.add_message('user','Keep these project notes together.',session_id=session['id'])
                store.save_schedule({'prompt':'Review the project priorities','runAt':int(time.time()*1000)+86400000})
                store.append_event('action.completed',{'result':'Saved the reviewed checklist'})
                store.create_approval({'kind':'skill.save','payload':{'name':'review','content':'# Review'},'summary':'Save the reviewed procedure'})
                return runtime
            artifact_dir=Path(os.environ['HARNESS_UI_ARTIFACT_DIR']).resolve() if os.environ.get('HARNESS_UI_ARTIFACT_DIR') else None
            if artifact_dir: artifact_dir.mkdir(parents=True,exist_ok=True)
            with serve(config,factory) as origin, sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    page.set_default_timeout(15000)
                    errors, external = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def local_only(route):
                        if route.request.url.startswith(origin):
                            route.continue_()
                        else:
                            external.append(route.request.url)
                            route.abort()

                    page.route("**/*", local_only)
                    page.goto(origin)
                    page.get_by_label("Workspace access token", exact=True).fill(TOKEN)
                    page.get_by_role("button", name="Connect workspace", exact=True).click()
                    expect(page.get_by_label("Choose chat")).to_be_visible()
                    companion=page.get_by_role('complementary',name='Your assistant')
                    expect(companion.get_by_text('Review the project priorities',exact=True)).to_be_visible()
                    expect(companion.get_by_text('Approved action completed',exact=True)).to_be_visible()
                    companion.get_by_role('button',name='Approvals',exact=True).click()
                    expect(companion.get_by_text('Save the reviewed procedure',exact=True)).to_be_visible()
                    companion.get_by_role('button',name='Identity',exact=True).click()
                    expect(companion.get_by_text('gpt-6-luna',exact=True)).to_be_visible()
                    companion.get_by_role('button',name='Activity',exact=True).click()
                    if artifact_dir: page.screenshot(path=str(artifact_dir/'workspace-desktop.png'),full_page=True)
                    settings = page.get_by_role("button", name="Settings", exact=True)
                    settings.click()
                    dialog = page.get_by_role("dialog", name="Settings", exact=True)
                    expect(dialog).to_be_visible()
                    expect(dialog.get_by_role("tab", name="General", exact=True)).to_have_attribute("aria-selected", "true")
                    expect(dialog.get_by_role("heading", name="Daily budget", exact=True)).to_be_visible()
                    expect(dialog.get_by_text("Local workspace", exact=True)).to_be_visible()
                    for theme in ("Light", "Dark"):
                        appearance = dialog.get_by_role("button", name=theme, exact=True)
                        appearance.click()
                        expect(appearance).to_have_attribute("aria-pressed", "true")
                        self.assertEqual(page.evaluate("localStorage.getItem('harness-color-mode')"), theme.lower())
                    if artifact_dir: page.screenshot(path=str(artifact_dir/'settings-desktop.png'),full_page=True)
                    dialog.get_by_role("tab", name="Connectors", exact=True).click()
                    expect(dialog.get_by_role("heading", name="Google Workspace", exact=True)).to_be_visible()
                    expect(dialog.get_by_role("heading", name="MCP servers", exact=True)).to_be_visible()
                    dialog.get_by_role("tab", name="Permissions", exact=True).click()
                    expect(dialog.get_by_text("Review actions before they happen", exact=True)).to_be_visible()
                    dialog.get_by_role("button", name="Review approvals", exact=False).click()
                    expect(dialog).not_to_be_visible()
                    expect(page.get_by_role("heading", name="Approvals", exact=True)).to_be_visible()

                    settings.click()
                    expect(dialog).to_be_visible()
                    dialog.get_by_role("tab", name="General", exact=True).focus()
                    page.keyboard.press("ArrowDown")
                    expect(dialog.get_by_role("tab", name="Connectors", exact=True)).to_be_focused()
                    page.keyboard.press("Escape")
                    expect(dialog).not_to_be_visible()
                    expect(settings).to_be_focused()

                    settings.click()
                    expect(dialog).to_be_visible()
                    bounds = dialog.bounding_box()
                    self.assertIsNotNone(bounds)
                    page.mouse.click(max(1, bounds["x"] - 10), max(1, bounds["y"] - 10))
                    expect(dialog).not_to_be_visible()
                    expect(settings).to_be_focused()

                    page.set_viewport_size({"width": 390, "height": 844})
                    settings.click()
                    expect(dialog).to_be_visible()
                    expect(dialog.get_by_role("button", name="Close settings", exact=True)).to_be_visible()
                    for section in ("General", "Connectors", "Permissions", "Data controls", "Help & support"):
                        dialog.get_by_role("tab", name=section, exact=True).click()
                        expect(dialog.get_by_role("tabpanel")).to_be_visible()
                        self.assertTrue(dialog.evaluate("element => element.scrollWidth <= element.clientWidth + 1"), section)
                        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), section)
                    page.keyboard.press("Escape")
                    expect(dialog).not_to_be_visible()
                    expect(settings).to_be_focused()
                    self.assertEqual(errors, [])
                    self.assertEqual(external, [], "Settings must not fetch remote documentation or account content")
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
