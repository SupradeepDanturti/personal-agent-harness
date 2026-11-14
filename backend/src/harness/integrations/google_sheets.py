"""Account-scoped sheet registrations, temporary discovery handles and RAW edits.

Follows the reference Jarvis connector's bounded-access structure, using this
harness's durable approvals rather than a separate in-memory apply mechanism.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import time

from harness.integrations.google import DRIVE_SCOPE, SHEETS_SCOPE


def bounded_range(area):
    if not isinstance(area, str) or len(area) > 220:
        raise ValueError("Choose a tab-qualified bounded range such as Sheet1!A1:D20")
    match = re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_ ]{0,99}|'(?:[^'\x00-\x1f\x7f]|''){1,100}')!([A-Z]{1,3})([1-9][0-9]{0,6}):([A-Z]{1,3})([1-9][0-9]{0,6})", area)
    if not match:
        raise ValueError("Choose a tab-qualified bounded range such as Sheet1!A1:D20")
    def column(name):
        number = 0
        for char in name: number = number * 26 + ord(char) - 64
        return number
    first_col, first_row, last_col, last_row = match.groups()
    height, width = int(last_row) - int(first_row) + 1, column(last_col) - column(first_col) + 1
    if height < 1 or width < 1 or height > 100 or width > 20 or height * width > 1000:
        raise ValueError("Choose at most 100 rows, 20 columns and 1000 cells")
    return height, width


def bounded_values(rows, area, *, allow_empty=False):
    height, width = bounded_range(area)
    if not isinstance(rows, list) or not (0 if allow_empty else 1) <= len(rows) <= height:
        raise ValueError("Provide rows within the chosen range")
    for row in rows:
        if not isinstance(row, list) or len(row) > width: raise ValueError("Cells exceed the chosen range")
        for cell in row:
            if cell is not None and type(cell) not in (str, int, float, bool): raise ValueError("Cells must be JSON scalar values")
            if isinstance(cell, float) and not math.isfinite(cell): raise ValueError("Cell numbers must be finite")
            if isinstance(cell, str) and (len(cell) > 500 or any(ord(c) < 32 and c not in "\n\t" for c in cell)):
                raise ValueError("Cell text must have at most 500 characters without control characters")
    if len(json.dumps(rows, allow_nan=False).encode()) > 20000: raise ValueError("Cell data exceeds the bounded update limit")
    return rows


def registration(body):
    if not isinstance(body, dict) or set(body) - {"name", "spreadsheetId", "access", "range"}:
        raise ValueError("Invalid spreadsheet registration")
    name, identifier = body.get("name"), body.get("spreadsheetId")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Spreadsheet name must have 1-60 characters")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,150}", identifier):
        raise ValueError("Invalid spreadsheet ID")
    access, area = body.get("access", "range"), body.get("range")
    if access == "range": bounded_range(area)
    elif access != "spreadsheet" or area is not None: raise ValueError("Choose a saved range or an entire spreadsheet")
    return {"name": name.strip(), "spreadsheetId": identifier, "access": access, "range": area}


class GoogleSheets:
    def __init__(self, store, google):
        self.store, self.google = store, google
        self.discovered = {}
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS google_sheets(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_google_sheets_account ON google_sheets(account_id);
        """)

    def _enabled(self, account):
        return self.store.get_setting("googleSheetDiscovery:" + account) is True

    def _registered(self, account):
        return [{"id": row[0], **json.loads(row[1])} for row in self.store.db.execute(
            "SELECT id,data FROM google_sheets WHERE account_id=? ORDER BY rowid", (account,))]

    async def _current(self, expected=None):
        binding = await self.google.binding(SHEETS_SCOPE)
        if expected is not None and expected != binding: raise ValueError("Google account changed; repeat the operation")
        return binding

    async def list(self):
        binding = await self._current()
        account = binding["account"]["id"]
        return {"sheets": self._registered(account), "discoveryEnabled": self._enabled(account)}

    async def register(self, body):
        sheet = registration(body)
        binding = await self._current()
        account = binding["account"]["id"]
        if len(self._registered(account)) >= 20: raise ValueError("At most 20 spreadsheets or ranges can be registered")
        identifier = secrets.token_hex(16)
        self.store.db.execute("INSERT INTO google_sheets VALUES(?,?,?)", (identifier, account, json.dumps(sheet)))
        return {"id": identifier, **sheet}

    async def remove(self, identifier):
        binding = await self._current()
        changed = self.store.db.execute("DELETE FROM google_sheets WHERE id=? AND account_id=?", (identifier, binding["account"]["id"])).rowcount
        if not changed: raise ValueError("Spreadsheet registration not found")
        return {"deleted": True}

    async def discovery(self, enabled):
        if type(enabled) is not bool: raise ValueError("Discovery enabled must be a boolean")
        binding = await self._current()
        if enabled:
            if await self.google.binding(DRIVE_SCOPE) != binding: raise ValueError("Google account changed")
        self.store.set_setting("googleSheetDiscovery:" + binding["account"]["id"], enabled)
        self.discovered.clear()
        return {"discoveryEnabled": enabled}

    def _resolve(self, identifier, binding):
        account = binding["account"]["id"]
        sheet = next((sheet for sheet in self._registered(account) if sheet["id"] == identifier), None)
        if sheet: return sheet
        found = self.discovered.get(identifier)
        if (found and self._enabled(account) and found["binding"] == binding and found["expiresAt"] > time.time()):
            return found["sheet"]
        raise ValueError("Spreadsheet access not found or expired; register it or search again")

    @staticmethod
    def _target(sheet, area):
        if sheet["access"] == "range":
            if area is not None and area != sheet["range"]: raise ValueError("This registration only allows its saved range")
            area = sheet["range"]
        bounded_range(area)
        return {**sheet, "range": area}

    async def search(self, query, page_token=None):
        binding = await self._current()
        account = binding["account"]["id"]
        if not self._enabled(account): raise ValueError("Enable spreadsheet discovery for this account first")
        result = await self.google.search_sheets(query, page_token, binding=binding)
        await self._current(binding)
        if not self._enabled(account): raise ValueError("Spreadsheet discovery was disabled")
        files, next_page = result.get("files", []), result.get("nextPageToken")
        if not isinstance(files, list) or len(files) > 20: raise ValueError("Invalid spreadsheet discovery response")
        if next_page is not None and (not isinstance(next_page, str) or not 1 <= len(next_page) <= 2048 or any(ord(c) < 33 or ord(c) == 127 for c in next_page)):
            raise ValueError("Invalid spreadsheet search page token")
        parsed = []
        for file in files:
            if not isinstance(file, dict) or file.get("mimeType") != "application/vnd.google-apps.spreadsheet":
                raise ValueError("Google returned an unexpected file type")
            name, identifier, capabilities = file.get("name"), file.get("id"), file.get("capabilities", {})
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 255 or any(ord(c) < 32 or ord(c) == 127 for c in name) or not isinstance(capabilities, dict):
                raise ValueError("Invalid spreadsheet metadata")
            parsed.append({**registration({"name": name[:60], "spreadsheetId": identifier, "access": "spreadsheet"}),
                           "displayName": name, "canEdit": capabilities.get("canEdit") is True})
        self.discovered = {key: value for key, value in self.discovered.items() if value["expiresAt"] > time.time() and value["binding"] == binding}
        sheets = []
        for file in parsed:
            while len(self.discovered) >= 100: self.discovered.pop(next(iter(self.discovered)))
            identifier = secrets.token_hex(16)
            sheet = {"id": identifier, **file}
            self.discovered[identifier] = {"binding": binding, "expiresAt": time.time() + 300, "sheet": sheet}
            sheets.append(sheet)
        return {"sheets": sheets, "nextPageToken": next_page, "partial": result.get("incompleteSearch") is True,
                "expiresInSeconds": 300}

    async def tabs(self, identifier, offset=0):
        if type(offset) is not int or not 0 <= offset <= 100000: raise ValueError("Invalid tab page")
        binding = await self._current()
        sheet = self._resolve(identifier, binding)
        if sheet["access"] != "spreadsheet": raise ValueError("Tab discovery requires whole-spreadsheet access")
        result = await self.google.sheet_metadata(sheet["spreadsheetId"], binding=binding)
        await self._current(binding)
        self._resolve(identifier, binding)
        items = result.get("sheets")
        if not isinstance(items, list): raise ValueError("Invalid spreadsheet tab metadata")
        tabs = []
        for item in items[offset:offset + 50]:
            props = item.get("properties", {}) if isinstance(item, dict) else {}
            title, grid = props.get("title"), props.get("gridProperties", {})
            if not isinstance(title, str) or not 1 <= len(title) <= 100 or any(ord(c) < 32 or ord(c) == 127 for c in title) or not isinstance(grid, dict):
                raise ValueError("Invalid spreadsheet tab metadata")
            supported = props.get("sheetType") == "GRID"
            rows, columns = grid.get("rowCount"), grid.get("columnCount")
            if supported and (type(rows) is not int or type(columns) is not int or not 1 <= rows <= 10000000 or not 1 <= columns <= 18278):
                raise ValueError("Invalid spreadsheet grid size")
            tabs.append({"title": title, "rows": rows if supported else None, "columns": columns if supported else None, "supported": supported})
        return {"sheet": sheet, "tabs": tabs, "nextOffset": offset + 50 if offset + 50 < len(items) else None}

    async def read(self, identifier, area=None):
        binding = await self._current()
        sheet = self._target(self._resolve(identifier, binding), area)
        result = await self.google.read_sheet(sheet["spreadsheetId"], sheet["range"], binding=binding)
        await self._current(binding)
        self._resolve(identifier, binding)
        rows = bounded_values(result.get("values", []), sheet["range"], allow_empty=True)
        return {"sheet": sheet, "values": rows, "entireSpreadsheet": False}

    async def prepare_update(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"sheetId", "range", "values"}:
            raise ValueError("Invalid sheet update proposal")
        binding = await self._current()
        sheet = self._target(self._resolve(payload.get("sheetId"), binding), payload.get("range"))
        if sheet.get("canEdit") is False: raise ValueError("This spreadsheet is read-only for the connected account")
        rows = bounded_values(payload.get("values"), sheet["range"])
        return await self.google.bind_write("sheets.update", {"sheetId": sheet["id"], "spreadsheetId": sheet["spreadsheetId"],
            "range": sheet["range"], "values": rows}, expected_binding=binding)

    async def validate_update(self, payload):
        binding = await self._current()
        if binding != {key: payload.get(key) for key in ("account", "connectionId")}:
            raise ValueError("Google account changed; create a new approval")
        sheet = self._target(self._resolve(payload.get("sheetId"), binding), payload.get("range"))
        if sheet["spreadsheetId"] != payload.get("spreadsheetId") or sheet.get("canEdit") is False:
            raise ValueError("Spreadsheet access changed; create a new approval")
        bounded_values(payload.get("values"), sheet["range"])
