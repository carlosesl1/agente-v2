from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final

_SCRYPT_N: Final = 16_384
_SCRYPT_R: Final = 8
_SCRYPT_P: Final = 1
_DKLEN: Final = 32
_PREFIX: Final = "scrypt$n=16384$r=8$p=1"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    if not value or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in value):
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if type(password) is not str or not 12 <= len(password.encode("utf-8")) <= 1024:
        raise ValueError("password must contain between 12 and 1024 UTF-8 bytes")
    actual_salt = secrets.token_bytes(16) if salt is None else salt
    if type(actual_salt) is not bytes or len(actual_salt) != 16:
        raise ValueError("salt must be exactly 16 bytes")
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=actual_salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DKLEN,
    )
    return f"{_PREFIX}${_b64(actual_salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        if type(password) is not str or type(encoded) is not str:
            return False
        parts = encoded.split("$")
        if len(parts) != 6 or "$".join(parts[:4]) != _PREFIX:
            return False
        salt = _unb64(parts[4])
        expected = _unb64(parts[5])
        if len(salt) != 16 or len(expected) != _DKLEN:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=_DKLEN,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False


def valid_password_hash(encoded: str) -> bool:
    try:
        if type(encoded) is not str:
            return False
        parts = encoded.split("$")
        if len(parts) != 6 or "$".join(parts[:4]) != _PREFIX:
            return False
        return len(_unb64(parts[4])) == 16 and len(_unb64(parts[5])) == _DKLEN
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True, slots=True)
class SessionClaims:
    username: str
    csrf: str
    expires_at: datetime


class SessionCodec:
    def __init__(self, key: bytes, *, ttl: timedelta) -> None:
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("session key must be exactly 32 bytes")
        if type(ttl) is not timedelta or not timedelta(minutes=1) <= ttl <= timedelta(hours=24):
            raise ValueError("session TTL must be between one minute and 24 hours")
        self._key = key
        self._ttl = ttl

    def issue(self, *, username: str, csrf: str, now: datetime) -> str:
        instant = _utc(now)
        if type(username) is not str or not username or type(csrf) is not str or not csrf:
            raise ValueError("session identity must be non-empty text")
        payload = json.dumps(
            {
                "u": username,
                "c": csrf,
                "exp": int((instant + self._ttl).timestamp()),
                "nonce": secrets.token_hex(16),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self._key, b"v2-ops-session-v1\0" + payload, hashlib.sha256).digest()
        return f"{_b64(payload)}.{_b64(signature)}"

    def verify(self, token: str, *, now: datetime) -> SessionClaims | None:
        try:
            instant = _utc(now)
            if type(token) is not str or token.count(".") != 1:
                return None
            payload_text, signature_text = token.split(".")
            payload = _unb64(payload_text)
            signature = _unb64(signature_text)
            expected = hmac.new(
                self._key, b"v2-ops-session-v1\0" + payload, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(signature, expected):
                return None
            decoded = json.loads(payload.decode("utf-8"))
            if type(decoded) is not dict or set(decoded) != {"u", "c", "exp", "nonce"}:
                return None
            username = decoded["u"]
            csrf = decoded["c"]
            expires = decoded["exp"]
            nonce = decoded["nonce"]
            if (
                type(username) is not str
                or not username
                or type(csrf) is not str
                or not csrf
                or type(expires) is not int
                or type(nonce) is not str
                or len(nonce) != 32
            ):
                return None
            expires_at = datetime.fromtimestamp(expires, tz=timezone.utc)
            if instant >= expires_at:
                return None
            return SessionClaims(username, csrf, expires_at)
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, OverflowError):
            return None


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError("time must be an exact UTC datetime")
    return value


class LoginLimiter:
    def __init__(
        self,
        *,
        max_failures: int = 5,
        window: timedelta = timedelta(minutes=5),
        max_entries: int = 1024,
    ) -> None:
        if type(max_failures) is not int or not 1 <= max_failures <= 100:
            raise ValueError("max_failures is outside the closed range")
        if type(window) is not timedelta or not timedelta(seconds=1) <= window <= timedelta(hours=1):
            raise ValueError("window is outside the closed range")
        if type(max_entries) is not int or not 1 <= max_entries <= 10_000:
            raise ValueError("max_entries is outside the closed range")
        self._max_failures = max_failures
        self._window = window
        self._max_entries = max_entries
        self._failures: OrderedDict[str, deque[datetime]] = OrderedDict()

    @property
    def entry_count(self) -> int:
        return len(self._failures)

    def _active(self, key: str, *, now: datetime) -> deque[datetime]:
        instant = _utc(now)
        events = self._failures.pop(key, deque())
        cutoff = instant - self._window
        while events and events[0] <= cutoff:
            events.popleft()
        if events:
            self._failures[key] = events
        return events

    def allowed(self, key: str, *, now: datetime) -> bool:
        if type(key) is not str or not key:
            raise ValueError("limiter key must be non-empty text")
        return len(self._active(key, now=now)) < self._max_failures

    def failure(self, key: str, *, now: datetime) -> None:
        instant = _utc(now)
        events = self._active(key, now=instant)
        events.append(instant)
        self._failures[key] = events
        while len(self._failures) > self._max_entries:
            self._failures.popitem(last=False)

    def success(self, key: str) -> None:
        self._failures.pop(key, None)
