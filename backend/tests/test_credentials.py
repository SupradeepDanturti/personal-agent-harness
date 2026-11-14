"""Real encryption, persistence compatibility and tamper rejection."""
import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from harness.integrations.credentials import CredentialCipher
from harness.integrations.google import GoogleConnector
from harness.integrations.providers import CredentialVault
from tests.offline_loop import OfflineAsyncCase


SECRET = "synthetic-credential-secret-at-least-24"


class CredentialCipherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cipher = CredentialCipher(SECRET)

    def test_json_round_trip_survives_new_instance_without_plaintext_keys(self):
        value = {"access_token": "synthetic-access", "label": "café 日本", "scopes": ["read"]}
        envelope = self.cipher.seal(value, "google")
        self.assertEqual(CredentialCipher(SECRET).open(envelope, "google"), value)
        encoded = json.dumps(envelope)
        for secret_part in ("synthetic-access", "PRIVATE KEY", "enc_master_key", "priv_key"):
            self.assertNotIn(secret_part, encoded)
        # The library default is weaker; verify the actual protected key bundle.
        wrapper = Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest()))
        key_record = json.loads(wrapper.decrypt(envelope["wrappedKey"].encode()))
        from dcrypt import CommonSignature, Signature
        signature = Signature.from_common(CommonSignature(**key_record["signature"]))
        self.assertGreaterEqual(signature.pub_key.n.bit_length(), 2048)

    def test_wrong_secret_tampering_and_unknown_version_fail_closed(self):
        envelope = self.cipher.seal({"access_token": "secret"}, "google")
        with self.assertRaisesRegex(ValueError, "Cannot unlock"):
            CredentialCipher("different-synthetic-secret-long-enough").open(envelope, "google")
        for field in ("wrappedKey", "ciphertext"):
            corrupted = {**envelope, field: "invalid"}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Cannot unlock"):
                self.cipher.open(corrupted, "google")
        with self.assertRaises(ValueError):
            self.cipher.open({**envelope, "version": 99}, "google")

    def test_provider_and_google_contexts_cannot_be_swapped(self):
        envelope = self.cipher.seal({"access_token": "secret"}, "provider:slack")
        for context in ("provider:mastodon", "google"):
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.cipher.open(envelope, context)

    def test_wrapped_key_and_data_from_different_envelopes_cannot_be_mixed(self):
        first = self.cipher.seal({"access_token": "first"}, "google")
        second = CredentialCipher(SECRET).seal({"access_token": "second"}, "google")
        with self.assertRaises(ValueError):
            self.cipher.open({**first, "ciphertext": second["ciphertext"]}, "google")


class CredentialMigrationTests(OfflineAsyncCase):
    async def test_legacy_provider_is_read_without_rewrite_then_migrates_on_save(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            vault = CredentialVault(Path(folder), SECRET)
            value = {"access_token": "legacy-provider-token", "connectionId": "same-grant"}
            iv = bytes(range(12))
            sealed = AESGCM(hashlib.sha256(SECRET.encode()).digest()).encrypt(
                iv, json.dumps(value).encode(), b"slack")
            original = json.dumps({"iv": base64.b64encode(iv).decode(), "sealed": base64.b64encode(sealed).decode()})
            vault.path("slack").write_text(original, encoding="utf-8")
            self.assertEqual(vault.load("slack"), value)
            self.assertEqual(vault.path("slack").read_text(encoding="utf-8"), original)
            vault.save("slack", value)
            self.assertEqual(json.loads(vault.path("slack").read_text())["version"], 2)
            self.assertEqual(CredentialVault(Path(folder), SECRET).load("slack"), value)
            vault.path("mastodon").write_bytes(vault.path("slack").read_bytes())
            with self.assertRaisesRegex(ValueError, "Cannot unlock provider"):
                vault.load("mastodon")

    async def test_legacy_google_migrates_and_wrong_key_does_not_overwrite_vault(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            path = Path(folder) / "google.enc"
            value = {"access_token": "legacy-google-token", "refresh_token": "keep-refresh", "scope": "read"}
            iv = bytes(range(12))
            sealed = AESGCM(hashlib.sha256(SECRET.encode()).digest()).encrypt(iv, json.dumps(value).encode(), None)
            path.write_text(json.dumps({"version": 1, "iv": base64.b64encode(iv).decode(),
                "encrypted": base64.b64encode(sealed[:-16]).decode(), "tag": base64.b64encode(sealed[-16:]).decode()}))
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
                connector = GoogleConnector("id", "secret", "http://127.0.0.1/callback", path, SECRET, http_client=client)
                original = path.read_bytes()
                self.assertEqual(await connector._load(), value)
                self.assertEqual(path.read_bytes(), original)
                await connector._save(value)
                self.assertEqual(json.loads(path.read_text())["version"], 2)
                reopened = GoogleConnector("id", "secret", "http://127.0.0.1/callback", path, SECRET, http_client=client)
                self.assertEqual(await reopened._load(), value)
                wrong_key = GoogleConnector("id", "secret", "http://127.0.0.1/callback", path,
                    "different-secret-that-is-long-enough", http_client=client)
                saved = path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, "Cannot unlock"):
                    await wrong_key._load()
                self.assertEqual(path.read_bytes(), saved)

    async def test_atomic_google_write_failure_preserves_old_vault(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            path = Path(folder) / "google.enc"
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
                connector = GoogleConnector("id", "secret", "http://127.0.0.1/callback", path, SECRET, http_client=client)
                await connector._save({"access_token": "original"})
                original = path.read_bytes()
                with patch("harness.integrations.google.os.replace", side_effect=OSError("simulated disk failure")):
                    with self.assertRaises(OSError):
                        await connector._save({"access_token": "replacement"})
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(await connector._load(), {"access_token": "original"})
                self.assertEqual(list(Path(folder).glob("*.tmp")), [])
