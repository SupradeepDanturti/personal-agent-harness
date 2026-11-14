"""Google Gmail and Calendar connector using Google's raw HTTP APIs.

New credentials use dcrypt text envelopes. Legacy AES-GCM/TypeScript version-1
vaults remain readable and migrate on the next successful save.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timezone
from email.header import Header
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from harness.integrations.credentials import CredentialCipher

GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"
SCOPES = ["openid", "email", GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE, CALENDAR_SCOPE, SHEETS_SCOPE, DRIVE_SCOPE]
IDENTITY_URL = "https://openidconnect.googleapis.com/v1/userinfo"
MAX_RESPONSE_BYTES = 1024 * 1024
_STATE_TTL = 600
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_EMAIL_RE = re.compile(r"^[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+$")


class GoogleConnector:
    """Google OAuth/Gmail/Calendar client. ``begin_auth`` is synchronous;
    every operation that performs I/O is asynchronous.

    ``http_client`` can be supplied for tests/custom transports. The connector
    does not close an injected client; internally-created clients are closed by
    ``disconnect``.
    """

    def __init__(self, client_id: str | None, client_secret: str | None,
                 redirect_uri: str, vault_path: Path, encryption_secret: str,
                 http_client: httpx.AsyncClient | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        if len(encryption_secret) < 24:
            raise ValueError("Connector encryption secret must have at least 24 characters")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.vault_path = Path(vault_path)
        self._key = hashlib.sha256(encryption_secret.encode("utf-8")).digest()
        self._cipher = CredentialCipher(encryption_secret)
        self._client = http_client or httpx.AsyncClient(transport=transport, timeout=20.0,
                                                       follow_redirects=False, trust_env=False)
        self._owns_client = http_client is None
        self._pending: dict[str, tuple[str, float, int]] = {}
        self._refresh_lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()
        self._generation = 0

    def _configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _load(self) -> dict[str, Any] | None:
        try:
            envelope = json.loads(await asyncio.to_thread(self.vault_path.read_text, "utf-8"))
            if envelope.get("version") == 2:
                tokens = await asyncio.to_thread(self._cipher.open, envelope, "google")
            else:
                if envelope.get("version") != 1:
                    raise ValueError("Unsupported credential envelope")
                iv = base64.b64decode(envelope["iv"], validate=True)
                tag = base64.b64decode(envelope["tag"], validate=True)
                encrypted = base64.b64decode(envelope["encrypted"], validate=True)
                if len(iv) != 12 or len(tag) != 16:
                    raise ValueError("bad envelope")
                plaintext = AESGCM(self._key).decrypt(iv, encrypted + tag, None)
                tokens = json.loads(plaintext.decode("utf-8"))
            if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str):
                raise ValueError("bad credential payload")
            return tokens
        except FileNotFoundError:
            return None
        except Exception as exc:
            raise RuntimeError("Cannot unlock saved Google credentials. Restore the original credential key or reconnect Google.") from exc

    async def _save(self, tokens: dict[str, Any]) -> None:
        envelope = await asyncio.to_thread(self._cipher.seal, tokens, "google")
        body = json.dumps(envelope, separators=(",", ":"))
        await asyncio.to_thread(self._atomic_write, body)

    def _atomic_write(self, body: str) -> None:
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=self.vault_path.name + ".", suffix=".tmp", dir=self.vault_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.chmod(name, 0o600)
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.vault_path)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    async def status(self) -> dict[str, Any]:
        tokens = await self._load()
        scopes = self._scopes(tokens or {})
        identity = (tokens or {}).get("identity")
        return {"provider": "google", "configured": self._configured(),
                "connected": bool(tokens),
                "scopes": scopes, "identity": identity, "connectionId": (tokens or {}).get("connectionId"),
                "missingScopes": [scope for scope in SCOPES if scope not in scopes],
                "reconnectRequired": bool(tokens) and (not identity or not tokens.get("connectionId") or any(scope not in scopes for scope in SCOPES)),
                "capabilities": {"gmailRead": GMAIL_READ_SCOPE in scopes, "gmailSend": bool(identity) and GMAIL_SEND_SCOPE in scopes,
                    "calendarRead": CALENDAR_SCOPE in scopes, "calendarWrite": bool(identity) and CALENDAR_SCOPE in scopes,
                    "sheets": bool(identity) and SHEETS_SCOPE in scopes,
                    "driveDiscovery": bool(identity) and SHEETS_SCOPE in scopes and DRIVE_SCOPE in scopes},
                "redirectUri": self.redirect_uri}

    @staticmethod
    def _scopes(tokens):
        value = tokens.get("scope", "")
        return value.split() if isinstance(value, str) else []

    @staticmethod
    def _identity(value):
        if (not isinstance(value, dict) or not isinstance(value.get("sub"), str) or not 1 <= len(value["sub"]) <= 255
                or any(ord(c) < 33 or ord(c) == 127 for c in value["sub"])
                or value.get("email_verified") is not True or not isinstance(value.get("email"), str)
                or not _EMAIL_RE.fullmatch(value["email"]) or len(value["email"]) > 254):
            raise RuntimeError("Google did not verify this account; reconnect Google")
        return {"id": value["sub"], "email": value["email"]}

    @staticmethod
    def _expiry(data):
        try:
            seconds = float(data.get("expires_in", 3600))
            if not math.isfinite(seconds) or seconds <= 0: raise ValueError()
            return time.time() * 1000 + min(seconds, 86400) * 1000
        except (ValueError, TypeError, OverflowError):
            raise RuntimeError("Google returned an invalid token expiry") from None

    def _binding(self, tokens, required_scope=None):
        if not tokens: raise RuntimeError("Google is not connected")
        if not tokens.get("identity") or not tokens.get("connectionId"):
            raise RuntimeError("Reconnect Google to verify the account before using this operation")
        if required_scope and required_scope not in self._scopes(tokens):
            raise RuntimeError("Google permission is missing; reconnect Google to grant the required scope")
        return {"account": tokens["identity"], "connectionId": tokens["connectionId"]}

    async def binding(self, required_scope=None):
        async with self._connection_lock:
            return self._binding(await self._load(), required_scope)

    async def bind_write(self, kind, payload, expected_binding=None):
        fields = {"gmail.send": {"to", "subject", "body"}, "calendar.create": {"summary", "start", "end", "timeZone"},
                  "sheets.update": {"sheetId", "spreadsheetId", "range", "values"}}
        scopes = {"gmail.send": GMAIL_SEND_SCOPE, "calendar.create": CALENDAR_SCOPE, "sheets.update": SHEETS_SCOPE}
        if kind not in fields or not isinstance(payload, dict) or set(payload) - fields[kind]:
            raise ValueError("Unsupported Google action payload")
        async with self._connection_lock:
            binding = self._binding(await self._load(), scopes[kind])
            if expected_binding is not None and expected_binding != binding:
                raise ValueError("Google account changed; create a new approval")
            return {**payload, **binding}

    async def execute_write(self, kind, payload):
        binding = {key: payload.get(key) for key in ("account", "connectionId")}
        if kind == "gmail.send": return await self.send_mail(payload, binding=binding)
        if kind == "calendar.create": return await self.create_event(payload, binding=binding)
        if kind == "sheets.update": return await self.update_sheet(payload, binding=binding)
        raise ValueError("Unsupported Google action")

    def begin_auth(self) -> dict[str, str]:
        if not self._configured():
            raise RuntimeError("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET before connecting Google.")
        redirect = urlparse(self.redirect_uri)
        if redirect.scheme != "http" or redirect.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("Local Google OAuth requires a loopback HTTP redirect URI")
        now = time.time()
        self._pending = {s: p for s, p in self._pending.items() if p[1] >= now}
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        if len(self._pending) >= 10: raise ValueError("Too many pending Google authorization requests")
        self._pending[state] = (verifier, now + _STATE_TTL, self._generation)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {"client_id": self.client_id, "redirect_uri": self.redirect_uri,
                  "response_type": "code", "scope": " ".join(SCOPES), "state": state,
                  "access_type": "offline", "prompt": "consent select_account",
                  "code_challenge": challenge, "code_challenge_method": "S256"}
        return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), "state": state}

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        payload = await self._request_json("https://oauth2.googleapis.com/token", method="POST", data=data)
        if (not isinstance(payload.get("access_token"), str) or not 1 <= len(payload["access_token"]) <= 8192
                or any(ord(c) < 33 or ord(c) == 127 for c in payload["access_token"])):
            raise RuntimeError("Google returned no access token")
        return payload

    async def complete_auth(self, code: str, state: str) -> dict[str, bool]:
        record = self._pending.pop(state, None)
        if not record or record[1] < time.time() or not code or len(code) > 4096:
            raise ValueError("Invalid or expired OAuth state. Start Google connection again.")
        data = await self._token_request({"client_id": self.client_id or "", "client_secret": self.client_secret or "",
              "redirect_uri": self.redirect_uri, "grant_type": "authorization_code", "code": code,
              "code_verifier": record[0]})
        identity = self._identity(await self._request_json(IDENTITY_URL, headers={"Authorization": "Bearer " + data["access_token"]}))
        async with self._connection_lock:
            if record[2] != self._generation: raise ValueError("Google connection changed; start authorization again")
            prior = await self._load()
            refresh = data.get("refresh_token")
            if not refresh and (prior or {}).get("identity") == identity: refresh = prior.get("refresh_token")
            if not isinstance(refresh, str) or not 1 <= len(refresh) <= 8192:
                raise RuntimeError("Google did not grant offline access; reconnect Google")
            scopes = self._scopes(data)
            if len(scopes) > 30 or any(len(scope) > 200 for scope in scopes): raise RuntimeError("Invalid Google scopes")
            await self._save({"access_token": data["access_token"], "refresh_token": refresh,
                  "expires_at": self._expiry(data), "scope": " ".join(scopes),
                  "identity": identity, "connectionId": secrets.token_hex(16)})
            self._generation += 1
            self._pending.clear()
        return {"connected": True}

    async def _access(self, required_scope=None) -> str:
        tokens = await self._load()
        if not tokens:
            raise RuntimeError("Google is not connected")
        if required_scope and required_scope not in self._scopes(tokens):
            raise RuntimeError("Google permission is missing; reconnect Google to grant the required scope")
        if float(tokens.get("expires_at", 0)) > time.time() * 1000 + 60_000:
            return tokens["access_token"]
        if not tokens.get("refresh_token"):
            raise RuntimeError("Google authorization expired; reconnect the account")
        async with self._refresh_lock:
            # Another caller may have refreshed while this caller waited.
            current = await self._load() or tokens
            if float(current.get("expires_at", 0)) > time.time() * 1000 + 60_000:
                return current["access_token"]
            data = await self._token_request({"client_id": self.client_id or "", "client_secret": self.client_secret or "",
                    "grant_type": "refresh_token", "refresh_token": current["refresh_token"]})
            refreshed = {**current, "access_token": data["access_token"],
                         "refresh_token": data.get("refresh_token") or current["refresh_token"],
                         "scope": data.get("scope") if isinstance(data.get("scope"), str) else current.get("scope", ""),
                         "expires_at": self._expiry(data)}
            await self._save(refreshed)
            if required_scope and required_scope not in self._scopes(refreshed):
                raise RuntimeError("Google permission was revoked; reconnect Google")
            return refreshed["access_token"]

    async def _request_json(self, url, *, method="GET", headers=None, data=None, json_body=None):
        try:
            async with self._client.stream(method, url, headers=headers, data=data, json=json_body, follow_redirects=False) as response:
                if not response.is_success: raise RuntimeError(f"Google request failed ({response.status_code}); check authorization and scopes")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES: raise RuntimeError("Google response exceeds 1 MiB; narrow the request")
            result = json.loads(raw)
            if not isinstance(result, dict): raise ValueError()
            return result
        except (httpx.HTTPError, ValueError, TypeError):
            raise RuntimeError("Google returned invalid data or could not be reached") from None

    async def _api(self, url: str, *, method: str = "GET", json_body: Any = None,
                   required_scope=None, binding=None) -> dict[str, Any]:
        parsed = urlsplit(url)
        allowed = ((parsed.netloc == "gmail.googleapis.com" and re.fullmatch(r"/gmail/v1/users/me/messages(?:/[A-Za-z0-9_-]{1,100})?", parsed.path))
            or (parsed.netloc == "www.googleapis.com" and parsed.path in ("/calendar/v3/calendars/primary/events", "/drive/v3/files"))
            or (parsed.netloc == "sheets.googleapis.com" and re.fullmatch(r"/v4/spreadsheets/[A-Za-z0-9_-]{10,150}(?:/values/[^/?#]+)?", parsed.path)))
        if parsed.scheme != "https" or not allowed or parsed.fragment: raise ValueError("Unsupported Google endpoint")
        async with self._connection_lock:
            record = await self._load()
            if binding is not None and binding != self._binding(record, required_scope):
                raise ValueError("Google account changed; create a new approval")
            token = await self._access(required_scope)
            result = await self._request_json(url, method=method, json_body=json_body,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            return json.loads(json.dumps(result).replace(token, "[redacted]"))

    async def list_mail(self, query: str = "", max_results: int = 10) -> dict[str, Any]:
        if len(query) > 2000:
            raise ValueError("Mail query is too long")
        count = max(1, min(25, int(max_results)))
        return await self._api("https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urlencode({"q": query, "maxResults": count}), required_scope=GMAIL_READ_SCOPE)

    async def read_mail(self, id: str) -> dict[str, Any]:
        if not _ID_RE.fullmatch(id):
            raise ValueError("Invalid mail ID")
        message = await self._api(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{id}?format=full", required_scope=GMAIL_READ_SCOPE)
        payload = message.get("payload")
        wanted = {"from", "to", "subject", "date"}
        headers = [h for h in (payload or {}).get("headers", []) if str(h.get("name", "")).lower() in wanted]
        pieces: list[str] = []
        size = 0
        def walk(part: dict[str, Any], depth: int = 0) -> None:
            nonlocal size
            if depth > 10 or size >= 30000:
                return
            if part.get("mimeType") == "text/plain":
                data = (part.get("body") or {}).get("data")
                if data:
                    try:
                        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
                        piece = decoded[:30000-size]
                        pieces.append(piece); size += len(piece)
                    except (ValueError, binascii.Error):
                        pass
            for child in part.get("parts", []) or []:
                walk(child, depth + 1)
        if payload:
            walk(payload)
        return {"id": message.get("id"), "threadId": message.get("threadId"), "snippet": message.get("snippet"),
                "headers": headers, "body": "\n".join(pieces)[:30000]}

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        if not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", value):
            return None
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result if result.tzinfo else None
        except ValueError:
            return None

    async def list_events(self, start: str, end: str) -> dict[str, Any]:
        s, e = self._parse_time(start), self._parse_time(end)
        if not s or not e or s >= e:
            raise ValueError("Provide a valid calendar time range with UTC offsets")
        params = {"timeMin": s.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                  "timeMax": e.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                  "singleEvents": "true", "orderBy": "startTime", "maxResults": "50"}
        return await self._api("https://www.googleapis.com/calendar/v3/calendars/primary/events?" + urlencode(params), required_scope=CALENDAR_SCOPE)

    async def send_mail(self, input: dict[str, Any], *, binding=None) -> dict[str, Any]:
        to, subject, body = input.get("to", ""), input.get("subject", ""), input.get("body", "")
        if not isinstance(to, str) or not _EMAIL_RE.fullmatch(to) or not isinstance(subject, str) or not isinstance(body, str) or "\r" in to + subject or "\n" in to + subject or len(subject) > 500 or len(body) > 50000:
            raise ValueError("Invalid email recipient, subject, or body")
        # RFC 2047 encodes long/non-ASCII subjects without permitting header injection.
        encoded_subject = Header(subject, "utf-8", maxlinelen=42).encode(linesep="\r\n ")
        body64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
        wrapped = "\r\n".join(body64[i:i+76] for i in range(0, len(body64), 76))
        raw_message = (f"To: {to}\r\nSubject: {encoded_subject}\r\nMIME-Version: 1.0\r\n"
                       "Content-Type: text/plain; charset=UTF-8\r\nContent-Transfer-Encoding: base64\r\n\r\n" + wrapped)
        raw = base64.urlsafe_b64encode(raw_message.encode("utf-8")).rstrip(b"=").decode("ascii")
        return await self._api("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", method="POST", json_body={"raw": raw}, required_scope=GMAIL_SEND_SCOPE, binding=binding)

    async def create_event(self, input: dict[str, Any], *, binding=None) -> dict[str, Any]:
        summary, start, end, zone = (input.get(k, "") for k in ("summary", "start", "end", "timeZone"))
        s, e = self._parse_time(start) if isinstance(start, str) else None, self._parse_time(end) if isinstance(end, str) else None
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 500 or not s or not e or e <= s:
            raise ValueError("Invalid calendar event")
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, TypeError):
            raise ValueError("Invalid calendar timezone") from None
        to_rfc3339 = lambda dt: dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload = {"summary": summary, "start": {"dateTime": to_rfc3339(s), "timeZone": zone},
                   "end": {"dateTime": to_rfc3339(e), "timeZone": zone}}
        return await self._api("https://www.googleapis.com/calendar/v3/calendars/primary/events", method="POST", json_body=payload, required_scope=CALENDAR_SCOPE, binding=binding)

    async def search_sheets(self, query, page_token=None, *, binding):
        if not isinstance(query, str) or len(query) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in query):
            raise ValueError("Use a spreadsheet name of at most 100 characters")
        if page_token is not None and (not isinstance(page_token, str) or not 1 <= len(page_token) <= 2048 or any(ord(c) < 33 or ord(c) == 127 for c in page_token)):
            raise ValueError("Invalid spreadsheet search page token")
        filters = "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
        if query: filters += " and name contains '" + query.replace("\\", "\\\\").replace("'", "\\'") + "'"
        params = {"q": filters, "pageSize": 20, "spaces": "drive", "corpora": "user", "orderBy": "modifiedTime desc",
            "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,capabilities(canEdit))",
            "includeItemsFromAllDrives": "true", "supportsAllDrives": "true"}
        if page_token: params["pageToken"] = page_token
        return await self._api("https://www.googleapis.com/drive/v3/files?" + urlencode(params), required_scope=DRIVE_SCOPE, binding=binding)

    async def sheet_metadata(self, spreadsheet_id, *, binding):
        if not isinstance(spreadsheet_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,150}", spreadsheet_id):
            raise ValueError("Invalid spreadsheet ID")
        return await self._api("https://sheets.googleapis.com/v4/spreadsheets/" + spreadsheet_id + "?" + urlencode({
            "fields": "sheets(properties(sheetId,title,sheetType,gridProperties(rowCount,columnCount)))"}),
            required_scope=SHEETS_SCOPE, binding=binding)

    async def read_sheet(self, spreadsheet_id, area, *, binding):
        from harness.integrations.google_sheets import bounded_range
        bounded_range(area)
        if not isinstance(spreadsheet_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,150}", spreadsheet_id):
            raise ValueError("Invalid spreadsheet ID")
        endpoint = "https://sheets.googleapis.com/v4/spreadsheets/" + spreadsheet_id + "/values/" + quote(area, safe="")
        return await self._api(endpoint + "?" + urlencode({"majorDimension": "ROWS", "valueRenderOption": "UNFORMATTED_VALUE"}),
            required_scope=SHEETS_SCOPE, binding=binding)

    async def update_sheet(self, payload, *, binding):
        from harness.integrations.google_sheets import bounded_range, bounded_values
        spreadsheet_id, area = payload.get("spreadsheetId"), payload.get("range")
        bounded_range(area)
        values = bounded_values(payload.get("values"), area)
        if not isinstance(spreadsheet_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,150}", spreadsheet_id):
            raise ValueError("Invalid spreadsheet ID")
        endpoint = "https://sheets.googleapis.com/v4/spreadsheets/" + spreadsheet_id + "/values/" + quote(area, safe="")
        return await self._api(endpoint + "?valueInputOption=RAW", method="PUT", required_scope=SHEETS_SCOPE, binding=binding,
            json_body={"range": area, "majorDimension": "ROWS", "values": values})

    async def disconnect(self) -> dict[str, bool]:
        async with self._connection_lock:
            self._generation += 1
            return await self._disconnect()

    async def _disconnect(self) -> dict[str, bool]:
        tokens = await self._load()
        self._pending.clear()
        for attempt in range(5):
            try:
                await asyncio.to_thread(self.vault_path.unlink)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                # Windows commonly maps a sharing violation to winerror 32;
                # on POSIX the matching transient file-busy error is EBUSY.
                if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) not in (5, 32) and getattr(exc, "errno", None) not in (13, 16):
                    raise
                if attempt == 4:
                    raise RuntimeError("Saved Google credentials could not be removed. A file lock or permission issue must be resolved before disconnect can be confirmed.") from None
                await asyncio.sleep(.05 * (attempt + 1))
        revoked = False
        if tokens:
            try:
                response = await self._client.post("https://oauth2.googleapis.com/revoke",
                    data={"token": tokens.get("refresh_token") or tokens["access_token"]}, timeout=10.0)
                revoked = response.is_success
            except Exception:
                pass
        return {"connected": False, "revoked": revoked}

    async def close(self) -> None:
        """Release resources at runtime shutdown without changing credentials."""
        if self._owns_client:
            await self._client.aclose()
