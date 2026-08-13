from __future__ import annotations

import json
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_AAD_SCHEMA = "v2-ops-encrypted-json"
_AAD_VERSION = 1


class TraceCryptoError(RuntimeError):
    """Encrypted trace content could not be authenticated or decoded."""


def parse_trace_key_hex(value: str | None = None) -> bytes:
    """Parse the independent exact 32-byte trace key."""

    raw = os.environ.get("V2_OPS_TRACE_KEY_HEX", "") if value is None else value
    if type(raw) is not str:
        raise TypeError("trace key must be 64 hexadecimal characters for a 32-byte key")
    if len(raw) != 64:
        raise ValueError("trace key must be 64 hexadecimal characters for a 32-byte key")
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(
            "trace key must be 64 hexadecimal characters for a 32-byte key"
        ) from exc
    if len(key) != 32:
        raise ValueError("trace key must be an exact 32-byte key")
    return key


def _aad(*, execution_id: str, node_id: str, side: str) -> bytes:
    return json.dumps(
        {
            "execution_id": execution_id,
            "node_id": node_id,
            "schema": _AAD_SCHEMA,
            "side": side,
            "version": _AAD_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class EncryptedTraceValue:
    nonce: bytes
    ciphertext: bytes


class AES256GCMCipher:
    """Small exact AES-256-GCM envelope bound to trace row identity and side."""

    def __init__(self, key: bytes) -> None:
        if type(key) is not bytes:
            raise TypeError("AES-256-GCM key must be an exact 32-byte value")
        if len(key) != 32:
            raise ValueError("AES-256-GCM key must be an exact 32-byte value")
        self._cipher = AESGCM(key)

    def encrypt(
        self,
        plaintext: bytes,
        *,
        execution_id: str,
        node_id: str,
        side: str,
    ) -> EncryptedTraceValue:
        if type(plaintext) is not bytes:
            raise TypeError("plaintext must be exact bytes")
        if side not in {"input", "output"}:
            raise ValueError("encrypted trace side must be input or output")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext,
            _aad(execution_id=execution_id, node_id=node_id, side=side),
        )
        return EncryptedTraceValue(nonce=nonce, ciphertext=ciphertext)

    def decrypt(
        self,
        nonce: bytes,
        ciphertext: bytes,
        *,
        execution_id: str,
        node_id: str,
        side: str,
    ) -> bytes:
        if type(nonce) is not bytes or len(nonce) != 12:
            raise TraceCryptoError("encrypted trace content unavailable")
        if type(ciphertext) is not bytes or not ciphertext:
            raise TraceCryptoError("encrypted trace content unavailable")
        if side not in {"input", "output"}:
            raise ValueError("encrypted trace side must be input or output")
        try:
            return self._cipher.decrypt(
                nonce,
                ciphertext,
                _aad(execution_id=execution_id, node_id=node_id, side=side),
            )
        except (InvalidTag, ValueError, TypeError) as exc:
            raise TraceCryptoError("encrypted trace content unavailable") from exc
