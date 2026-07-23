"""Strict read-only ManyChat customer-profile adapter for V2."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import re

from v2_contracts.profile import PrivateCustomerBinding


_SUBSCRIBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EXPECTED_FIELDS = frozenset(
    ("subscriber_id", "full_name", "email", "phone_e164", "country_code")
)


class ManyChatProfilePayloadError(ValueError):
    """The read transport returned a noncanonical private profile."""


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise TypeError("now must be an exact UTC datetime")
    return value


def _subscriber_from_lead(lead_id: object) -> str:
    if type(lead_id) is not str or not lead_id.startswith("manychat:"):
        raise ValueError("lead_id must use the manychat namespace")
    subscriber_id = lead_id.removeprefix("manychat:")
    if _SUBSCRIBER_RE.fullmatch(subscriber_id) is None:
        raise ValueError("lead_id contains an invalid subscriber id")
    return subscriber_id


def _private_value(value: object, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value or value != value.strip():
        raise ManyChatProfilePayloadError(f"{name} is not canonical")
    return value


class ManyChatProfileAdapter:
    adapter_id = "manychat-profile:v2"

    def __init__(self, *, transport: object, ttl: timedelta) -> None:
        if not callable(getattr(transport, "fetch_profile", None)):
            raise TypeError("transport must expose fetch_profile")
        if type(ttl) is not timedelta or ttl <= timedelta(0) or ttl > timedelta(days=1):
            raise ValueError("ttl must be an exact positive timedelta no greater than one day")
        self._transport = transport
        self._ttl = ttl

    def read(self, lead_id: str, *, now: datetime) -> PrivateCustomerBinding:
        subscriber_id = _subscriber_from_lead(lead_id)
        observed_at = _utc(now)
        raw = self._transport.fetch_profile(subscriber_id)
        if type(raw) is not dict or set(raw) != _EXPECTED_FIELDS:
            raise ManyChatProfilePayloadError("profile payload fields are not exact")
        returned_subscriber = raw["subscriber_id"]
        if type(returned_subscriber) is not str or returned_subscriber != subscriber_id:
            raise ManyChatProfilePayloadError("profile subscriber identity conflicts")
        values = {
            "full_name": _private_value(raw["full_name"], "full_name"),
            "email": _private_value(raw["email"], "email"),
            "phone_e164": _private_value(raw["phone_e164"], "phone_e164"),
            "country_code": _private_value(raw["country_code"], "country_code"),
        }
        canonical = json.dumps(
            {"subscriber_id": subscriber_id, **values},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        content_hash = hashlib.sha256(
            b"v2-private-customer-profile-v1\0" + canonical
        ).hexdigest()
        binding_hash = hashlib.sha256(
            b"v2-private-customer-binding-v1\0"
            + subscriber_id.encode("utf-8")
            + b"\0"
            + content_hash.encode("ascii")
        ).hexdigest()
        complete = all(value is not None for value in values.values())
        try:
            return PrivateCustomerBinding(
                binding_id=f"profile-binding:{binding_hash}",
                content_hash=content_hash,
                full_name=values["full_name"],
                email=values["email"],
                phone_e164=values["phone_e164"],
                country_code=values["country_code"],
                observed_at=observed_at,
                expires_at=observed_at + self._ttl,
                complete=complete,
            )
        except (TypeError, ValueError) as exc:
            raise ManyChatProfilePayloadError("profile values failed validation") from exc


__all__ = ["ManyChatProfileAdapter", "ManyChatProfilePayloadError"]
