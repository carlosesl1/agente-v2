from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
from typing import Any, Protocol

from reservation_domain import LookupEvidence, LookupStatus, OfferSnapshot, SearchQuery, ServiceKind

_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_INTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_QUERY_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
_FAILURE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ProviderKind(str, Enum):
    CLOUDBEDS = "cloudbeds"
    BOKUN = "bokun"


@dataclass(frozen=True, slots=True)
class ReadRequest:
    method: str
    path: str
    query: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.method) is not str or self.method != "GET":
            raise ValueError("read requests must use GET")
        if (
            type(self.path) is not str
            or not self.path.startswith("/")
            or self.path.startswith("//")
            or "://" in self.path
            or "?" in self.path
            or "#" in self.path
        ):
            raise ValueError("path must be an absolute relative path without query")
        if type(self.query) is not tuple:
            raise TypeError("query must be a tuple")
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in self.query:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("query entries must be key/value tuples")
            key, value = item
            if type(key) is not str or not _QUERY_KEY_RE.fullmatch(key):
                raise ValueError("invalid query key")
            if type(value) is not str or not value or len(value) > 256:
                raise ValueError("invalid query value")
            if key in seen:
                raise ValueError("duplicate query key")
            seen.add(key)
            normalized.append((key, value))
        object.__setattr__(self, "query", tuple(sorted(normalized)))


@dataclass(frozen=True, slots=True)
class ReadResponse:
    status_code: int
    body: Any

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be an HTTP integer")
        try:
            json.dumps(
                self.body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("body must be finite JSON") from exc


class ReadTransport(Protocol):
    def send(self, request: ReadRequest) -> ReadResponse: ...


@dataclass(frozen=True, slots=True)
class LookupFailure:
    code: str
    detail: str

    def __post_init__(self) -> None:
        if type(self.code) is not str or not _FAILURE_CODE_RE.fullmatch(self.code):
            raise ValueError("invalid failure code")
        if type(self.detail) is not str or not self.detail or len(self.detail) > 256:
            raise ValueError("invalid failure detail")


@dataclass(frozen=True, slots=True)
class LookupProvenance:
    provider: ProviderKind
    request_fingerprints: tuple[str, ...]
    response_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.provider) is not ProviderKind:
            raise TypeError("provider must be ProviderKind")
        for name, values in (
            ("request_fingerprints", self.request_fingerprints),
            ("response_hashes", self.response_hashes),
        ):
            if type(values) is not tuple or not values:
                raise ValueError(f"{name} must be a non-empty tuple")
            if any(type(value) is not str or not _HASH_RE.fullmatch(value) for value in values):
                raise ValueError(f"{name} must contain sha256 hex digests")
        if len(self.request_fingerprints) != len(self.response_hashes):
            raise ValueError("request/response provenance lengths must match")

    @property
    def snapshot_hash(self) -> str:
        from .identity import snapshot_hash_from_hashes

        return snapshot_hash_from_hashes(self.response_hashes)


@dataclass(frozen=True, slots=True)
class CloudbedsLookupRequest:
    property_id: str
    query: SearchQuery

    def __post_init__(self) -> None:
        _validate_internal_id(self.property_id, "property_id")
        if type(self.query) is not SearchQuery or self.query.service is not ServiceKind.LODGING:
            raise ValueError("Cloudbeds lookup requires a lodging SearchQuery")
        if self.query.end_date is None:
            raise ValueError("Cloudbeds lookup requires end_date")


@dataclass(frozen=True, slots=True)
class BokunLookupRequest:
    product_id: str
    query: SearchQuery

    def __post_init__(self) -> None:
        _validate_internal_id(self.product_id, "product_id")
        if type(self.query) is not SearchQuery or self.query.service is not ServiceKind.ACTIVITY:
            raise ValueError("Bokun lookup requires an activity SearchQuery")


@dataclass(frozen=True, slots=True)
class LookupResult:
    query: SearchQuery
    evidence: LookupEvidence
    provenance: LookupProvenance
    offers: tuple[OfferSnapshot, ...]
    failures: tuple[LookupFailure, ...] = ()

    def __post_init__(self) -> None:
        if type(self.query) is not SearchQuery:
            raise TypeError("query must be SearchQuery")
        if type(self.evidence) is not LookupEvidence:
            raise TypeError("evidence must be LookupEvidence")
        if type(self.provenance) is not LookupProvenance:
            raise TypeError("provenance must be LookupProvenance")
        if type(self.offers) is not tuple or any(type(item) is not OfferSnapshot for item in self.offers):
            raise TypeError("offers must be a tuple of OfferSnapshot")
        if type(self.failures) is not tuple or any(type(item) is not LookupFailure for item in self.failures):
            raise TypeError("failures must be a tuple of LookupFailure")
        if self.evidence.service is not self.query.service:
            raise ValueError("evidence service does not match query")
        if self.evidence.query_signature != self.query.signature:
            raise ValueError("evidence query signature does not match query")
        if self.evidence.snapshot_hash != self.provenance.snapshot_hash:
            raise ValueError("evidence snapshot hash does not match provenance")

        status = self.evidence.status
        if status is LookupStatus.POSITIVE:
            if not self.offers or self.failures:
                raise ValueError("positive lookup requires offers and zero failures")
        elif status is LookupStatus.NEGATIVE:
            if self.offers or self.failures:
                raise ValueError("negative lookup requires zero offers and failures")
        elif status is LookupStatus.UNCERTAIN:
            if self.offers or not self.failures:
                raise ValueError("uncertain lookup requires failures and zero offers")
        else:
            raise ValueError("unsupported lookup status")

        from .identity import offer_id_for

        for offer in self.offers:
            if offer.lookup_id != self.evidence.lookup_id:
                raise ValueError("offer lookup_id does not match evidence")
            if offer.service is not self.query.service:
                raise ValueError("offer service does not match query")
            if self.query.service is ServiceKind.LODGING:
                if offer.start_date != self.query.start_date:
                    raise ValueError("offer start_date does not match lodging query")
                if offer.end_date != self.query.end_date:
                    raise ValueError("offer end_date does not match lodging query")
                if offer.start_time != self.query.start_time:
                    raise ValueError("offer start_time does not match lodging query")
            else:
                end_bound = self.query.end_date or self.query.start_date
                if not self.query.start_date <= offer.start_date <= end_bound:
                    raise ValueError("offer start_date is outside activity query")
                if offer.end_date is not None:
                    raise ValueError("activity offer end_date must be absent")
                if (
                    self.query.start_time is not None
                    and offer.start_time != self.query.start_time
                ):
                    raise ValueError("offer start_time does not match activity query")
            if offer.party != self.query.party:
                raise ValueError("offer party does not match query")
            if not offer.available:
                raise ValueError("positive offers must be available")
            expected = offer_id_for(provider=self.provenance.provider, offer=offer)
            if offer.offer_id != expected:
                raise ValueError("offer_id is not canonical")

        object.__setattr__(self, "offers", tuple(sorted(self.offers, key=lambda item: item.offer_id)))


def _validate_internal_id(value: str, field: str) -> None:
    if type(value) is not str or not _INTERNAL_ID_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
