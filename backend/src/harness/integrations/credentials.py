"""Versioned dcrypt text envelopes; credential data is JSON, never pickle.

dcrypt's signature includes its RSA private key. Protect the complete signature
with the existing credential secret instead of writing that key bundle to disk.
Both encrypted layers bind their contents to the intended credential store.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from typing import Any

from cryptography.fernet import Fernet
from dcrypt import CommonSignature, CryptKey, Signature, TextCrypt


class CredentialCipher:
    def __init__(self, secret: str):
        if len(secret) < 24:
            raise ValueError("Credential key must have at least 24 characters")
        self._wrapper = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))
        self._crypt: TextCrypt | None = None
        self._lock = threading.Lock()

    def seal(self, value: dict[str, Any], context: str) -> dict[str, Any]:
        with self._lock:
            if self._crypt is None:
                # dcrypt defaults to 1024-bit RSA; explicitly select 2048 bits.
                self._crypt = TextCrypt(CryptKey(signature_strength=2))
            crypt = self._crypt
        protected_key = self._wrapper.encrypt(json.dumps({
            "context": context, "signature": crypt.key.signature.common().json(),
        }, separators=(",", ":")).encode()).decode()
        ciphertext = crypt.encrypt(json.dumps({
            "context": context, "value": value,
        }, separators=(",", ":")))
        return {"version": 2, "cipher": "dcrypt-fernet", "wrappedKey": protected_key,
                "ciphertext": ciphertext}

    def open(self, envelope: dict[str, Any], context: str) -> dict[str, Any]:
        try:
            if envelope.get("version") != 2 or envelope.get("cipher") != "dcrypt-fernet":
                raise ValueError("Unsupported credential envelope")
            protected_key = json.loads(self._wrapper.decrypt(envelope["wrappedKey"].encode()))
            if protected_key["context"] != context:
                raise ValueError("Credential context mismatch")
            signature = Signature.from_common(CommonSignature(**protected_key["signature"]))
            crypt = TextCrypt(CryptKey(signature=signature))
            payload = json.loads(crypt.decrypt(envelope["ciphertext"]))
            if payload["context"] != context or not isinstance(payload["value"], dict):
                raise ValueError("Invalid credential payload")
            return payload["value"]
        except Exception:
            raise ValueError("Cannot unlock credential envelope") from None
