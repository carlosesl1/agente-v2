"""Private provider re-read contracts that never enter model context."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol, runtime_checkable

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_AMOUNT_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_PROVIDER_FIELDS = {
    "cloudbeds": frozenset(("room_rate_id", "room_type_id")),
    "bokun": frozenset(("bokun_product_id",)),
}


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be exact non-empty text")
    return value


def _utc(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value


@dataclass(frozen=True, slots=True)
class PrivateOfferQuery:
    service: str
    offer_id: str
    request_hash: str
    binding_hash: str
    canonical_product_id: str | None
    start_date: date
    end_date: date | None
    start_time: str | None
    adults: int
    children: int
    total_amount: str
    currency: str
    available: bool

    def __post_init__(self) -> None:
        if self.service not in ("lodging", "activity"):
            raise ValueError("service is outside the private offer catalog")
        _text(self.offer_id, "offer_id")
        for name, value in (
            ("request_hash", self.request_hash),
            ("binding_hash", self.binding_hash),
        ):
            if type(value) is not str or _HASH_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be a lowercase SHA-256")
        if self.service == "activity":
            _text(self.canonical_product_id, "canonical_product_id")
        elif self.canonical_product_id is not None:
            raise ValueError("lodging query cannot carry a canonical product id")
        if type(self.start_date) is not date or (
            self.end_date is not None and type(self.end_date) is not date
        ):
            raise TypeError("query dates must be exact date values")
        if self.service == "lodging" and self.end_date is None:
            raise ValueError("lodging query requires end_date")
        if self.service == "activity" and self.end_date is not None:
            raise ValueError("activity query cannot carry end_date")
        if self.start_time is not None and type(self.start_time) is not str:
            raise TypeError("start_time must be exact text or None")
        if type(self.adults) is not int or self.adults < 1:
            raise ValueError("adults must be an exact positive integer")
        if type(self.children) is not int or self.children < 0:
            raise ValueError("children must be an exact non-negative integer")
        if (
            type(self.total_amount) is not str
            or _AMOUNT_RE.fullmatch(self.total_amount) is None
        ):
            raise ValueError("total_amount must be canonical")
        if (
            type(self.currency) is not str
            or _CURRENCY_RE.fullmatch(self.currency) is None
        ):
            raise ValueError("currency must be canonical")
        if type(self.available) is not bool:
            raise TypeError("available must be an exact bool")


@dataclass(frozen=True, slots=True, repr=False)
class PrivateOfferBinding:
    provider: str
    query: PrivateOfferQuery
    observed_at: datetime
    expires_at: datetime
    provider_fields: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        expected_fields = _PROVIDER_FIELDS.get(self.provider)
        if expected_fields is None:
            raise ValueError("provider is outside the private binding catalog")
        if type(self.query) is not PrivateOfferQuery:
            raise TypeError("query must be an exact PrivateOfferQuery")
        if (self.provider == "cloudbeds") != (self.query.service == "lodging"):
            raise ValueError("provider and service disagree")
        observed = _utc(self.observed_at, "observed_at")
        expires = _utc(self.expires_at, "expires_at")
        if expires <= observed:
            raise ValueError("expires_at must be after observed_at")
        if type(self.provider_fields) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or any(type(value) is not str or not value for value in item)
            for item in self.provider_fields
        ):
            raise TypeError("provider_fields must be exact non-empty text pairs")
        names = tuple(name for name, _ in self.provider_fields)
        if frozenset(names) != expected_fields or names != tuple(sorted(names)):
            raise ValueError(
                "provider_fields disagree with the closed provider catalog"
            )

    def private_payload(self) -> dict[str, str]:
        return dict(self.provider_fields)


@runtime_checkable
class PrivateOfferReadPort(Protocol):
    def resolve(self, query: PrivateOfferQuery) -> PrivateOfferBinding: ...


__all__ = ["PrivateOfferBinding", "PrivateOfferQuery", "PrivateOfferReadPort"]
