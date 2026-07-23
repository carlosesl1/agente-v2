"""Neutral, typed read requests and observations for V2 providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
import hashlib
import json
import re
from typing import Any, Final


_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_LOCALE_RE: Final = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_PRODUCT_ID_RE: Final = re.compile(r"^product:[a-z0-9][a-z0-9._-]{0,127}$")


class InvalidReadRequest(ValueError):
    """Raised when a read is not closed, canonical, or provider-safe."""


class ReadKind(str, Enum):
    KNOWLEDGE = "knowledge"
    LODGING = "lodging"
    ACTIVITY = "activity"
    ROOM_DESCRIPTION = "room_description"
    ACTIVITY_DESCRIPTION = "activity_description"


def _text(value: object, name: str, *, identifier: bool = False) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise InvalidReadRequest(f"{name} must be a non-empty exact string")
    if identifier and _ID_RE.fullmatch(value) is None:
        raise InvalidReadRequest(f"{name} is not a canonical identifier")
    return value


def _utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value


def _closed_json(value: object, name: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        decoded = json.loads(encoded)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError(f"{name} must be closed JSON") from exc
    return decoded


@dataclass(frozen=True, slots=True)
class ReadRequest:
    request_id: str
    kind: ReadKind
    query: str | None = None
    locale: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = None
    children: int | None = None
    product_id: str | None = None
    activity_date: date | None = None
    participants: int | None = None
    offer_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.request_id, "request_id", identifier=True)
        if type(self.kind) is not ReadKind:
            raise InvalidReadRequest("kind must be an exact ReadKind")
        if self.kind is ReadKind.KNOWLEDGE:
            _text(self.query, "query")
            locale = _text(self.locale, "locale")
            if _LOCALE_RE.fullmatch(locale) is None:
                raise InvalidReadRequest("locale must be canonical")
            self._require_none(
                "check_in", "check_out", "adults", "children", "product_id",
                "activity_date", "participants", "offer_id",
            )
        elif self.kind is ReadKind.LODGING:
            if type(self.check_in) is not date or type(self.check_out) is not date:
                raise InvalidReadRequest("lodging dates must be exact dates")
            if self.check_out <= self.check_in:
                raise InvalidReadRequest("check_out must be after check_in")
            if type(self.adults) is not int or self.adults < 1:
                raise InvalidReadRequest("adults must be a positive exact integer")
            if type(self.children) is not int or self.children < 0:
                raise InvalidReadRequest("children must be a non-negative exact integer")
            self._require_none(
                "query", "locale", "product_id", "activity_date", "participants", "offer_id",
            )
        elif self.kind is ReadKind.ACTIVITY:
            self._require_product()
            if type(self.activity_date) is not date:
                raise InvalidReadRequest("activity_date must be an exact date")
            if type(self.participants) is not int or self.participants < 1:
                raise InvalidReadRequest("participants must be a positive exact integer")
            self._require_none(
                "query", "locale", "check_in", "check_out", "adults", "children", "offer_id",
            )
        elif self.kind is ReadKind.ROOM_DESCRIPTION:
            _text(self.offer_id, "offer_id", identifier=True)
            self._require_none(
                "query", "locale", "check_in", "check_out", "adults", "children",
                "product_id", "activity_date", "participants",
            )
        else:
            self._require_product()
            self._require_none(
                "query", "locale", "check_in", "check_out", "adults", "children",
                "activity_date", "participants", "offer_id",
            )

    def _require_product(self) -> None:
        product_id = _text(self.product_id, "product_id")
        if _PRODUCT_ID_RE.fullmatch(product_id) is None:
            raise InvalidReadRequest("product_id must be a canonical product ID")

    def _require_none(self, *names: str) -> None:
        populated = [name for name in names if getattr(self, name) is not None]
        if populated:
            raise InvalidReadRequest(
                f"{self.kind.value} request has forbidden fields: {','.join(populated)}"
            )

    def to_canonical_bytes(self) -> bytes:
        values: dict[str, object] = {
            "request_id": self.request_id,
            "kind": self.kind.value,
        }
        for name in (
            "query", "locale", "check_in", "check_out", "adults", "children",
            "product_id", "activity_date", "participants", "offer_id",
        ):
            value = getattr(self, name)
            if value is not None:
                values[name] = value.isoformat() if type(value) is date else value
        return json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def canonical_hash(self) -> str:
        return hashlib.sha256(b"v2-read-request-v1\0" + self.to_canonical_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReadObservation:
    request_hash: str
    provider: str
    observed_at: datetime
    expires_at: datetime
    public_payload: dict[str, object]
    private_binding_hash: str

    def __post_init__(self) -> None:
        if type(self.request_hash) is not str or _SHA256_RE.fullmatch(self.request_hash) is None:
            raise ValueError("request_hash must be a lowercase SHA-256")
        _text(self.provider, "provider", identifier=True)
        observed = _utc(self.observed_at, "observed_at")
        expires = _utc(self.expires_at, "expires_at")
        if type(self.public_payload) is not dict:
            raise TypeError("public_payload must be an exact dict")
        object.__setattr__(self, "public_payload", _closed_json(self.public_payload, "public_payload"))
        if type(self.private_binding_hash) is not str or _SHA256_RE.fullmatch(
            self.private_binding_hash
        ) is None:
            raise ValueError("private_binding_hash must be a lowercase SHA-256")
        if expires <= observed:
            raise ValueError("expires_at must be after observed_at")


__all__ = [
    "InvalidReadRequest",
    "ReadKind",
    "ReadObservation",
    "ReadRequest",
]
