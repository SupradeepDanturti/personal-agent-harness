"""Synthetic Google transport shared by unit and real-local-UI acceptance tests."""
import base64
import hashlib
import json
import os
import tempfile
import time

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from harness.application.runtime import Runtime
from harness.integrations.google import GoogleConnector, SCOPES

SHEET_ID = "synthetic_spreadsheet_12345"
ACCOUNT = {"id": "synthetic-owner", "email": "owner@example.invalid"}


class GoogleFixture:
    def __init__(self):
        self.requests = []
        self.writes = []
        self.values = [["Synthetic cell", 123]]
        self.account = {"sub": ACCOUNT["id"], "email": ACCOUNT["email"], "email_verified": True}
        self.scopes = " ".join(SCOPES)
        self.refresh_token = "synthetic-refresh"
        self.tabs = [{"properties": {"sheetId": 0, "title": "Budget", "sheetType": "GRID",
                      "gridProperties": {"rowCount": 100, "columnCount": 20}}}]
        self.write_started = None
        self.write_release = None

    async def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/token"):
            data = {"access_token": "synthetic-new-access", "expires_in": 3600, "scope": self.scopes}
            if self.refresh_token is not None:
                data["refresh_token"] = self.refresh_token
            return httpx.Response(200, json=data)
        if path == "/v1/userinfo":
            return httpx.Response(200, json=self.account)
        if path.endswith("/revoke"):
            return httpx.Response(200, json={})
        if path == "/drive/v3/files":
            return httpx.Response(200, json={"files": [{"id": SHEET_ID, "name": "Synthetic Budget",
                "mimeType": "application/vnd.google-apps.spreadsheet", "capabilities": {"canEdit": True}}],
                "nextPageToken": "synthetic-page"})
        if path.startswith("/v4/spreadsheets/"):
            if "/values/" in path:
                if request.method == "PUT":
                    if self.write_started is not None:
                        self.write_started.set()
                        await self.write_release.wait()
                    self.writes.append(request)
                    self.values = json.loads(request.content)["values"]
                    return httpx.Response(200, json={"updatedRange": "Budget!A1:B2", "updatedCells": 2})
                return httpx.Response(200, json={"values": self.values})
            return httpx.Response(200, json={"sheets": self.tabs})
        if path.endswith("/messages/send"):
            self.writes.append(request)
            return httpx.Response(200, json={"id": "synthetic-mail-receipt"})
        if path.endswith("/events"):
            if request.method == "POST":
                self.writes.append(request)
                return httpx.Response(200, json={"id": "synthetic-event-receipt"})
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404, json={"error": "Unexpected synthetic endpoint"})

    def seed(self, config, *, account=ACCOUNT, connection_id="synthetic-grant", scopes=None,
             expires_at=None, with_identity=True):
        path = config.data_dir / "credentials" / "google.enc"
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
                 "expires_at": expires_at if expires_at is not None else time.time() * 1000 + 3600000,
                 "scope": scopes if scopes is not None else self.scopes}
        if with_identity:
            value.update(identity=account, connectionId=connection_id)
        iv = bytes(range(12))
        sealed = AESGCM(hashlib.sha256(config.encryption_secret.encode()).digest()).encrypt(
            iv, json.dumps(value).encode(), None)
        # Replace like the real vault: Windows can deny truncating a live vault
        # file even when a sibling file can safely be replaced atomically.
        body = json.dumps({"version": 1, "iv": base64.b64encode(iv).decode(),
            "encrypted": base64.b64encode(sealed[:-16]).decode(), "tag": base64.b64encode(sealed[-16:]).decode()})
        fd, name = tempfile.mkstemp(dir=path.parent, suffix='.fixture')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(body)
            for attempt in range(8):
                try:
                    os.replace(name, path)
                    break
                except PermissionError:
                    if attempt == 7: raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            for attempt in range(8):
                try:
                    os.unlink(name)
                    break
                except FileNotFoundError:
                    break
                except PermissionError:
                    if attempt == 7: raise
                    time.sleep(0.05 * (attempt + 1))

    def runtime(self, config, store=None):
        if not (config.data_dir / "credentials/google.enc").is_file():
            self.seed(config)
        connector = GoogleConnector("synthetic-client", "synthetic-client-secret",
            f"http://127.0.0.1:{config.port}/oauth/google/callback",
            config.data_dir / "credentials/google.enc", config.encryption_secret,
            transport=httpx.MockTransport(self.handle))
        runtime = Runtime(config, store=store, google=connector, runner=synthetic_runner)
        runtime.store.set_setting("nightlyDreamEnabled", False)
        return runtime


async def synthetic_runner(config, prompt, context, caps):
    caps.on_usage(3, 3)
    return "Synthetic Google acceptance result"
