import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from harness.core.config import Config
from harness.integrations.google import GMAIL_READ_SCOPE, SHEETS_SCOPE, MAX_RESPONSE_BYTES
from harness.integrations.google_sheets import bounded_range, bounded_values, registration
from tests.google_fixture import ACCOUNT, SHEET_ID, GoogleFixture
from tests.offline_loop import OfflineAsyncCase


class SheetBoundsTests(unittest.TestCase):
    def test_rectangles_are_bounded_and_tab_qualified(self):
        self.assertEqual(bounded_range("'Owner''s Budget'!A1:J100"), (100, 10))
        for area in ("A1:B2", "Budget!A:A", "Budget!A1:U1", "Budget!A1:B101", "Budget!A1:T100", "Budget!B2:A1", "Budget!A0:B2", "Budget!A1:B2;Other!A1:B2"):
            with self.subTest(area=area), self.assertRaises(ValueError):
                bounded_range(area)

    def test_values_reject_nonfinite_nested_oversize_and_invalid_registration(self):
        self.assertEqual(bounded_values([["=literal", True], [None, 1]], "Budget!A1:B2"), [["=literal", True], [None, 1]])
        for rows in ([[float("nan")]], [[float("inf")]], [[{}]], [["x" * 501]], [["\x00"]], [[1, 2, 3]], []):
            with self.subTest(rows=repr(rows)[:40]), self.assertRaises(ValueError):
                bounded_values(rows, "Budget!A1:B2")
        with self.assertRaises(ValueError):
            registration({"name": "Budget", "spreadsheetId": SHEET_ID, "access": "spreadsheet", "range": "Budget!A1:B2"})


class GoogleWorkspaceTests(OfflineAsyncCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        root = Path(self.temp.name)
        self.config = Config(root=root, data_dir=root / ".assistant", token="synthetic-harness-token-123456789",
            encryption_secret="synthetic-credential-secret-123456", api_key="synthetic-model")
        self.fixture = GoogleFixture()
        self.runtime = self.fixture.runtime(self.config)
        self.google = self.runtime.google
        self.sheets = self.runtime.sheets

    async def asyncTearDown(self):
        await self.runtime.close()
        for attempt in range(8):
            try:
                self.temp.cleanup()
                break
            except PermissionError:
                if attempt == 7: raise
                await asyncio.sleep(.05 * (attempt + 1))

    async def register(self, access="range"):
        return await self.sheets.register({"name": "Budget", "spreadsheetId": SHEET_ID,
            "access": access, "range": "Budget!A1:B2" if access == "range" else None})

    async def approve(self, proposal):
        decided = self.runtime.store.decide_approval(proposal["id"], "approved", proposal["digest"])
        return await self.runtime.execute_action(decided)

    async def test_legacy_grants_keep_reads_but_require_identity_for_writes(self):
        self.fixture.seed(self.config, with_identity=False)
        status = await self.google.status()
        self.assertTrue(status["capabilities"]["gmailRead"])
        self.assertFalse(status["capabilities"]["gmailSend"])
        self.assertTrue(status["reconnectRequired"])
        with self.assertRaisesRegex(RuntimeError, "Reconnect Google"):
            await self.google.bind_write("gmail.send", {"to": "test@example.invalid", "subject": "Test", "body": "Test"})

    async def test_oauth_verifies_identity_and_does_not_infer_missing_scopes(self):
        self.fixture.scopes = GMAIL_READ_SCOPE
        auth = self.google.begin_auth()
        await self.google.complete_auth("synthetic-code", auth["state"])
        status = await self.google.status()
        self.assertEqual(status["identity"], ACCOUNT)
        self.assertTrue(status["capabilities"]["gmailRead"])
        self.assertFalse(status["capabilities"]["sheets"])
        self.assertIn(SHEETS_SCOPE, status["missingScopes"])
        with self.assertRaises(ValueError):
            await self.google.complete_auth("synthetic-code", auth["state"])

    async def test_failed_identity_and_other_account_without_refresh_do_not_replace_grant(self):
        old = await self.google.binding()
        self.fixture.account["email_verified"] = False
        auth = self.google.begin_auth()
        with self.assertRaisesRegex(RuntimeError, "verify"):
            await self.google.complete_auth("synthetic-code", auth["state"])
        self.assertEqual(await self.google.binding(), old)
        self.fixture.account.update(sub="other-account", email="other@example.invalid", email_verified=True)
        self.fixture.refresh_token = None
        auth = self.google.begin_auth()
        with self.assertRaisesRegex(RuntimeError, "offline access"):
            await self.google.complete_auth("synthetic-code", auth["state"])
        self.assertEqual(await self.google.binding(), old)

    async def test_range_registration_cannot_be_broadened_and_is_account_scoped(self):
        sheet = await self.register()
        result = await self.sheets.read(sheet["id"])
        self.assertEqual(result["values"], self.fixture.values)
        with self.assertRaisesRegex(ValueError, "saved range"):
            await self.sheets.read(sheet["id"], "Budget!A1:C3")
        with self.assertRaisesRegex(ValueError, "whole-spreadsheet"):
            await self.sheets.tabs(sheet["id"])
        self.fixture.seed(self.config, account={"id": "other", "email": "other@example.invalid"}, connection_id="other-grant")
        self.assertEqual((await self.sheets.list())["sheets"], [])
        with self.assertRaises(ValueError):
            await self.sheets.read(sheet["id"])
        with self.assertRaises(ValueError):
            await self.sheets.remove(sheet["id"])

    async def test_registration_limit_and_tab_pagination(self):
        for _ in range(20): await self.register()
        with self.assertRaisesRegex(ValueError, "20"):
            await self.register()
        sheet = (await self.sheets.list())["sheets"][0]
        await self.sheets.remove(sheet["id"])
        whole = await self.register("spreadsheet")
        self.fixture.tabs *= 61
        result = await self.sheets.tabs(whole["id"])
        self.assertEqual(len(result["tabs"]), 50)
        self.assertEqual(result["nextOffset"], 50)
        self.assertEqual(len((await self.sheets.tabs(whole["id"], 50))["tabs"]), 11)

    async def test_discovery_requires_opt_in_escapes_query_and_handles_expire(self):
        with self.assertRaisesRegex(ValueError, "Enable"):
            await self.sheets.search("Budget")
        self.assertEqual(self.fixture.requests, [])
        await self.sheets.discovery(True)
        result = await self.sheets.search("Owner's \\ Budget", "page")
        request = self.fixture.requests[-1]
        self.assertIn("application/vnd.google-apps.spreadsheet", request.url.params["q"])
        self.assertIn("Owner\\'s \\\\ Budget", request.url.params["q"])
        self.assertEqual(request.url.params["pageSize"], "20")
        self.assertEqual(request.url.params["pageToken"], "page")
        handle = result["sheets"][0]["id"]
        self.assertEqual((await self.sheets.read(handle, "Budget!A1:B2"))["values"], self.fixture.values)
        self.sheets.discovered[handle]["expiresAt"] = time.time() - 1
        with self.assertRaisesRegex(ValueError, "expired"):
            await self.sheets.read(handle, "Budget!A1:B2")
        handle = (await self.sheets.search("Budget"))["sheets"][0]["id"]
        await self.sheets.discovery(False)
        with self.assertRaises(ValueError):
            await self.sheets.read(handle, "Budget!A1:B2")

    async def test_raw_update_requires_approval_and_replay_is_blocked(self):
        sheet = await self.register()
        proposal = await self.runtime.propose_action("sheets.update", {"sheetId": sheet["id"], "values": [["=literal", 42]]}, "Exact test update")
        self.assertEqual(self.fixture.writes, [])
        self.assertEqual(proposal["payload"]["account"], ACCOUNT)
        completed = await self.approve(proposal)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(self.fixture.writes), 1)
        write = self.fixture.writes[0]
        self.assertEqual(write.url.params["valueInputOption"], "RAW")
        self.assertEqual(json.loads(write.content)["values"], [["=literal", 42]])
        with self.assertRaises(ValueError):
            await self.approve(proposal)
        self.assertEqual(len(self.fixture.writes), 1)

    async def test_proposal_route_cannot_override_its_registered_handle(self):
        sheet = await self.register()
        response = await self.runtime.dispatch("POST", f"/api/google/sheets/{sheet['id']}/propose",
            {"sheetId": "different-handle", "range": "Budget!A1:B2", "values": [["Test"]]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.runtime.store.list_approvals(), [])

    async def test_google_reconnect_blocks_old_gmail_calendar_and_sheet_actions(self):
        sheet = await self.register()
        actions = [("gmail.send", {"to": "test@example.invalid", "subject": "Test", "body": "Test"}),
            ("calendar.create", {"summary": "Test", "start": "2026-11-13T12:00:00Z", "end": "2026-11-13T13:00:00Z"}),
            ("sheets.update", {"sheetId": sheet["id"], "values": [["Test"]]})]
        proposals = [await self.runtime.propose_action(kind, payload, "Bound test action") for kind, payload in actions]
        auth = self.google.begin_auth()
        await self.google.complete_auth("synthetic-code", auth["state"])
        for proposal in proposals:
            self.assertEqual((await self.approve(proposal))["status"], "uncertain")
        self.assertEqual(self.fixture.writes, [])

    async def test_registration_removal_and_discovery_revocation_block_pending_updates(self):
        sheet = await self.register()
        registered = await self.runtime.propose_action("sheets.update", {"sheetId": sheet["id"], "values": [["Test"]]}, "Registered update")
        await self.sheets.remove(sheet["id"])
        self.assertEqual((await self.approve(registered))["status"], "uncertain")
        await self.sheets.discovery(True)
        found = (await self.sheets.search("Budget"))["sheets"][0]
        discovered = await self.runtime.propose_action("sheets.update", {"sheetId": found["id"], "range": "Budget!A1:B2", "values": [["Test"]]}, "Discovered update")
        await self.sheets.discovery(False)
        self.assertEqual((await self.approve(discovered))["status"], "uncertain")
        self.assertEqual(self.fixture.writes, [])

    async def test_refresh_preserves_binding_and_scope_revocation_blocks_write(self):
        self.fixture.seed(self.config, expires_at=0)
        binding = await self.google.binding(SHEETS_SCOPE)
        sheet = await self.register()
        proposal = await self.runtime.propose_action("sheets.update", {"sheetId": sheet["id"], "values": [["Refreshed"]]}, "Refresh test")
        self.assertEqual((await self.approve(proposal))["status"], "completed")
        self.assertEqual(await self.google.binding(SHEETS_SCOPE), binding)
        self.fixture.seed(self.config, expires_at=0)
        self.fixture.scopes = GMAIL_READ_SCOPE
        proposal = await self.runtime.propose_action("sheets.update", {"sheetId": sheet["id"], "values": [["Blocked"]]}, "Revoked test")
        self.assertEqual((await self.approve(proposal))["status"], "uncertain")
        self.assertEqual(len(self.fixture.writes), 1)

    async def test_read_only_agents_and_arbitrary_endpoints_cannot_write(self):
        sheet = await self.register()
        result = await self.runtime.operate("sheets_read", {"action": "read", "id": sheet["id"]}, read_only=True)
        self.assertEqual(result["values"], self.fixture.values)
        with self.assertRaisesRegex(ValueError, "read-only"):
            await self.runtime.operate("propose_action", {"kind": "sheets.update"}, read_only=True)
        for endpoint in ("https://evil.example/", "https://www.googleapis.com/drive/v3/files/arbitrary/download", "http://sheets.googleapis.com/v4/spreadsheets/" + SHEET_ID):
            with self.assertRaises(ValueError): await self.google._api(endpoint)

    async def test_reconnect_waits_for_an_already_claimed_write(self):
        sheet = await self.register()
        proposal = await self.runtime.propose_action("sheets.update", {"sheetId": sheet["id"], "values": [["Old account"]]}, "Claimed write")
        old_binding = await self.google.binding()
        self.fixture.write_started, self.fixture.write_release = asyncio.Event(), asyncio.Event()
        writing = asyncio.create_task(self.approve(proposal))
        await self.fixture.write_started.wait()
        self.fixture.account.update(sub="other-account", email="other@example.invalid")
        auth = self.google.begin_auth()
        reconnecting = asyncio.create_task(self.google.complete_auth("synthetic-code", auth["state"]))
        try:
            for _ in range(10): await asyncio.sleep(0)
            self.assertFalse(reconnecting.done())
        finally:
            self.fixture.write_release.set()
            result, _ = await asyncio.gather(writing, reconnecting)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.fixture.writes), 1)
        self.assertEqual(self.fixture.writes[0].headers["Authorization"], "Bearer synthetic-access")
        self.assertNotEqual(await self.google.binding(), old_binding)

    async def test_response_cap_and_redirects_fail_without_following(self):
        requests = []
        async def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(302, headers={"Location": "https://evil.example/"})
            return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            old = self.google._client
            self.google._client = client
            try:
                with self.assertRaises(RuntimeError): await self.google.list_mail()
                with self.assertRaisesRegex(RuntimeError, "1 MiB"): await self.google.list_mail()
            finally: self.google._client = old
        self.assertEqual(len(requests), 2)
