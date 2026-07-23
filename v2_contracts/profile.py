"""Private customer-profile contracts that never enter public model context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Protocol


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical identifier")
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be canonical non-empty text or None")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} contains a forbidden control character")
    return value


def _utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class PrivateCustomerBinding:
    binding_id: str
    content_hash: str
    full_name: str | None
    email: str | None
    phone_e164: str | None
    country_code: str | None
    observed_at: datetime
    expires_at: datetime
    complete: bool

    def __post_init__(self) -> None:
        _identifier(self.binding_id, "binding_id")
        if type(self.content_hash) is not str or _SHA256_RE.fullmatch(self.content_hash) is None:
            raise ValueError("content_hash must be a lowercase SHA-256")
        full_name = _optional_text(self.full_name, "full_name")
        email = _optional_text(self.email, "email")
        phone = _optional_text(self.phone_e164, "phone_e164")
        country = _optional_text(self.country_code, "country_code")
        if email is not None and (
            email.count("@") != 1
            or any(char.isspace() for char in email)
            or len(email) > 254
        ):
            raise ValueError("email must be canonical")
        if phone is not None and _E164_RE.fullmatch(phone) is None:
            raise ValueError("phone_e164 must be canonical E.164")
        if country is not None and _COUNTRY_RE.fullmatch(country) is None:
            raise ValueError("country_code must be ISO alpha-2 uppercase")
        observed = _utc(self.observed_at, "observed_at")
        expires = _utc(self.expires_at, "expires_at")
        if expires <= observed:
            raise ValueError("expires_at must be after observed_at")
        if type(self.complete) is not bool:
            raise TypeError("complete must be an exact bool")
        expected_complete = all(
            value is not None for value in (full_name, email, phone, country)
        )
        if self.complete is not expected_complete:
            raise ValueError("complete disagrees with required profile fields")


class CustomerProfileReadPort(Protocol):
    def read(self, lead_id: str, *, now: datetime) -> PrivateCustomerBinding:
        raise NotImplementedError


__all__ = ["CustomerProfileReadPort", "PrivateCustomerBinding"]
