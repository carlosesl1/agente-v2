from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from reservation_domain import OfferSnapshot, SearchQuery

from .types import ProviderKind, ReadRequest, ReadResponse


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def request_fingerprint(request: ReadRequest) -> str:
    if type(request) is not ReadRequest:
        raise TypeError("request must be ReadRequest")
    return _sha256(
        {
            "method": request.method,
            "path": request.path,
            "query": [[key, value] for key, value in request.query],
        }
    )


def response_hash(response: ReadResponse) -> str:
    if type(response) is not ReadResponse:
        raise TypeError("response must be ReadResponse")
    return _sha256({"status_code": response.status_code, "body": response.body})


def snapshot_hash_from_hashes(response_hashes: Iterable[str]) -> str:
    values = tuple(sorted(response_hashes))
    if not values:
        raise ValueError("at least one response hash is required")
    return _sha256({"response_hashes": list(values)})


def snapshot_hash_for(responses: tuple[ReadResponse, ...]) -> str:
    if type(responses) is not tuple or not responses:
        raise ValueError("responses must be a non-empty tuple")
    return snapshot_hash_from_hashes(response_hash(item) for item in responses)


def offer_id_for(*, provider: ProviderKind, offer: OfferSnapshot) -> str:
    if type(provider) is not ProviderKind:
        raise TypeError("provider must be ProviderKind")
    if type(offer) is not OfferSnapshot:
        raise TypeError("offer must be OfferSnapshot")
    payload = {
        "provider": provider.value,
        "provider_ref": offer.provider_ref,
        "service": offer.service.value,
        "start_date": offer.start_date.isoformat(),
        "end_date": offer.end_date.isoformat() if offer.end_date else None,
        "start_time": offer.start_time,
        "party": {
            "adults": offer.party.adults,
            "children": offer.party.children,
        },
        "total": {
            "amount": format(offer.total.amount, "f"),
            "currency": offer.total.currency,
        },
        "available": offer.available,
    }
    return f"offer:{_sha256(payload)}"


def lookup_id_for(
    *,
    provider: ProviderKind,
    query: SearchQuery,
    observed_at: datetime,
    response_hashes: tuple[str, ...],
) -> str:
    if type(provider) is not ProviderKind:
        raise TypeError("provider must be ProviderKind")
    if type(query) is not SearchQuery:
        raise TypeError("query must be SearchQuery")
    if (
        type(observed_at) is not datetime
        or observed_at.tzinfo is None
        or observed_at.utcoffset() != timezone.utc.utcoffset(observed_at)
    ):
        raise ValueError("observed_at must be UTC")
    if type(response_hashes) is not tuple or not response_hashes:
        raise ValueError("response_hashes must be a non-empty tuple")
    payload = {
        "provider": provider.value,
        "query": {
            "service": query.service.value,
            "start_date": query.start_date.isoformat(),
            "end_date": query.end_date.isoformat() if query.end_date else None,
            "start_time": query.start_time,
            "party": {
                "adults": query.party.adults,
                "children": query.party.children,
            },
        },
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "response_hashes": sorted(response_hashes),
    }
    return f"lookup:{_sha256(payload)}"
