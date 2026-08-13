from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Mapping

from v2_ops.auth import valid_password_hash
from v2_ops.crypto import parse_trace_key_hex

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def _hex_key(value: str, *, name: str) -> bytes:
    if type(value) is not str:
        raise TypeError(f"{name} must be text")
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be exactly 32 bytes of hex") from exc
    if len(key) != 32 or value != value.lower() or len(value) != 64:
        raise ValueError(f"{name} must be exactly 32 bytes of lowercase hex")
    return key


@dataclass(frozen=True, slots=True)
class OpsWebSettings:
    username: str
    password_hash: str
    session_key: bytes
    trace_path: Path
    trace_key: bytes
    session_ttl: timedelta = timedelta(hours=8)
    secure_cookie: bool = True
    stale_after: timedelta = timedelta(minutes=5)
    release_sha: str = "unknown"
    image_digest: str = "unknown"
    config_fingerprint: str = "unknown"

    def __post_init__(self) -> None:
        if type(self.username) is not str or _USERNAME_RE.fullmatch(self.username) is None:
            raise ValueError("username is outside the closed grammar")
        if not valid_password_hash(self.password_hash):
            raise ValueError("password hash is outside the scrypt grammar")
        if type(self.session_key) is not bytes or len(self.session_key) != 32:
            raise ValueError("session key must be exactly 32 bytes")
        if not isinstance(self.trace_path, Path) or not self.trace_path.is_absolute():
            raise ValueError("trace path must be an absolute pathlib.Path")
        if type(self.trace_key) is not bytes or len(self.trace_key) != 32:
            raise ValueError("trace key must be exactly 32 bytes")
        if type(self.session_ttl) is not timedelta or not timedelta(minutes=1) <= self.session_ttl <= timedelta(hours=24):
            raise ValueError("session TTL is outside the closed range")
        if type(self.secure_cookie) is not bool:
            raise TypeError("secure_cookie must be an exact bool")
        if type(self.stale_after) is not timedelta or not timedelta(seconds=10) <= self.stale_after <= timedelta(hours=1):
            raise ValueError("stale_after is outside the closed range")
        if self.release_sha != "unknown" and _SHA_RE.fullmatch(self.release_sha) is None:
            raise ValueError("release SHA is outside the closed grammar")
        if self.image_digest != "unknown" and _DIGEST_RE.fullmatch(self.image_digest) is None:
            raise ValueError("image digest is outside the closed grammar")
        if self.config_fingerprint != "unknown" and _FINGERPRINT_RE.fullmatch(self.config_fingerprint) is None:
            raise ValueError("config fingerprint is outside the closed grammar")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OpsWebSettings":
        source = os.environ if env is None else env
        trace_path = Path(source.get("V2_OPS_TRACE_PATH", ""))
        ttl = int(source.get("V2_OPS_SESSION_TTL_SECONDS", "28800"))
        secure_raw = source.get("V2_OPS_SECURE_COOKIE", "true")
        if secure_raw not in {"true", "false"}:
            raise ValueError("V2_OPS_SECURE_COOKIE must be true or false")
        return cls(
            username=source.get("V2_OPS_USERNAME", ""),
            password_hash=source.get("V2_OPS_PASSWORD_HASH", ""),
            session_key=_hex_key(
                source.get("V2_OPS_SESSION_KEY_HEX", ""),
                name="V2_OPS_SESSION_KEY_HEX",
            ),
            trace_path=trace_path,
            trace_key=parse_trace_key_hex(source.get("V2_OPS_TRACE_KEY_HEX")),
            session_ttl=timedelta(seconds=ttl),
            secure_cookie=secure_raw == "true",
            release_sha=source.get("V2_OPS_RELEASE_SHA", "unknown"),
            image_digest=source.get("V2_OPS_IMAGE_DIGEST", "unknown"),
            config_fingerprint=source.get("V2_OPS_CONFIG_FINGERPRINT", "unknown"),
        )
