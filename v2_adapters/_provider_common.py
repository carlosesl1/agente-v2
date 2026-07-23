"""Shared validation helpers for direct provider read adapters."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from typing import Protocol

from v2_contracts.providers import (
    ProviderCertainty,
    ProviderDispatchPermit,
    ProviderExecutionResult,
)


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


def _canonical_bytes(value: object, name: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProviderReadError(f"{name} is not closed JSON") from exc


def binding_hash(payload: object) -> str:
    encoded = _canonical_bytes(payload, "private binding")
    return hashlib.sha256(b"v2-private-provider-binding-v1\0" + encoded).hexdigest()


def reservation_result(
    *,
    permit: ProviderDispatchPermit,
    provider: str,
    operation: str,
    reference_field: str,
    transport,
) -> ProviderExecutionResult:
    if type(permit) is not ProviderDispatchPermit:
        raise TypeError("permit must be an exact ProviderDispatchPermit")
    if permit.provider != provider or permit.operation != operation:
        raise ProviderReadError("provider reservation permit mismatch")
    payload = json.loads(permit.canonical_payload)
    response = transport(
        operation,
        payload,
        idempotency_key=permit.idempotency_key,
    )
    if type(response) is not dict:
        raise ProviderReadError("provider reservation response must be an exact object")
    encoded = _canonical_bytes(response, "provider reservation response")
    evidence = hashlib.sha256(b"v2-provider-write-response-v1\0" + encoded).hexdigest()
    status = response.get("status")
    if status == "confirmed":
        reference = response.get(reference_field)
        if type(reference) is not str or not reference.strip():
            raise ProviderReadError("confirmed provider response lacks its reference")
        return ProviderExecutionResult(
            ProviderCertainty.EFFECT_CONFIRMED,
            "confirmed",
            hashlib.sha256(reference.strip().encode("utf-8")).hexdigest(),
            (evidence,),
        )
    if status in ("rejected", "no_effect"):
        return ProviderExecutionResult(
            ProviderCertainty.CALLED_NO_EFFECT,
            "rejected",
            None,
            (evidence,),
        )
    return ProviderExecutionResult(
        ProviderCertainty.CALLED_UNKNOWN,
        "unknown",
        None,
        (evidence,),
    )


__all__ = [
    "ProviderReadError",
    "ProviderTransport",
    "binding_hash",
    "exact_dict",
    "observed_window",
    "reservation_result",
    "text",
    "validated_adapter",
]
