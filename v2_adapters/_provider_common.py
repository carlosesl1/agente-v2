"""Shared validation helpers for direct provider read adapters."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from typing import Protocol


class ProviderReadError(RuntimeError):
    pass


class ProviderClock(Protocol):
    def now(self) -> datetime: ...


class ProviderTransport(Protocol):
    def __call__(self, operation: str, payload: dict[str, object]) -> object: ...


def validated_adapter(
    transport: object,
    clock: object,
    ttl: object,
) -> tuple[ProviderTransport, ProviderClock, timedelta]:
    if not callable(transport):
        raise TypeError("transport must be callable")
    if not hasattr(clock, "now"):
        raise TypeError("clock must implement now")
    if type(ttl) is not timedelta or ttl <= timedelta(0):
        raise ValueError("ttl must be a positive exact timedelta")
    return transport, clock, ttl  # type: ignore[return-value]


def observed_window(clock: ProviderClock, ttl: timedelta) -> tuple[datetime, datetime]:
    now = clock.now()
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ProviderReadError("provider clock must return an exact UTC datetime")
    return now, now + ttl


def exact_dict(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ProviderReadError(f"{name} must be an exact object")
    return value


def text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ProviderReadError(f"{name} must be a non-empty exact string")
    return value


def binding_hash(payload: object) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProviderReadError("private binding is not closed JSON") from exc
    return hashlib.sha256(b"v2-private-provider-binding-v1\0" + encoded).hexdigest()


__all__ = [
    "ProviderReadError",
    "ProviderTransport",
    "binding_hash",
    "exact_dict",
    "observed_window",
    "text",
    "validated_adapter",
]
