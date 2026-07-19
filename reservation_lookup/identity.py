from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable

from reservation_domain import OfferSnapshot, SearchQuery

from .types import ProviderKind, ReadRequest, ReadResponse

_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, (list, tuple)):
        items = [_canonical_value(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
        )
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
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


def canonical_exchanges(
    *,
    request_fingerprints: Iterable[str],
    response_hashes: Iterable[str],
) -> tuple[tuple[str, str], ...]:
    requests = tuple(request_fingerprints)
    responses = tuple(response_hashes)
    if not requests or len(requests) != len(responses):
        raise ValueError("request/response provenance must be non-empty and paired")
    if any(
        type(value) is not str or not _HASH_RE.fullmatch(value)
        for value in requests + responses
    ):
        raise ValueError("provenance values must be sha256 hex digests")
    return tuple(sorted(zip(requests, responses, strict=True)))


def snapshot_hash_from_exchanges(
    *,
    request_fingerprints: Iterable[str],
    response_hashes: Iterable[str],
) -> str:
    pairs = canonical_exchanges(
        request_fingerprints=request_fingerprints,
        response_hashes=response_hashes,
    )
    return _sha256(
        {
            "exchanges": [
                {"request_fingerprint": request, "response_hash": response}
                for request, response in pairs
            ]
        }
    )


def snapshot_hash_for(
    requests: tuple[ReadRequest, ...],
    responses: tuple[ReadResponse, ...],
) -> str:
    if type(requests) is not tuple or type(responses) is not tuple:
        raise TypeError("requests and responses must be tuples")
    return snapshot_hash_from_exchanges(
        request_fingerprints=(request_fingerprint(item) for item in requests),
        response_hashes=(response_hash(item) for item in responses),
    )


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
    request_fingerprints: tuple[str, ...],
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
    if type(request_fingerprints) is not tuple or type(response_hashes) is not tuple:
        raise TypeError("request_fingerprints and response_hashes must be tuples")
    exchanges = canonical_exchanges(
        request_fingerprints=request_fingerprints,
        response_hashes=response_hashes,
    )
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
        "exchanges": [
            {"request_fingerprint": request, "response_hash": response}
            for request, response in exchanges
        ],
    }
    return f"lookup:{_sha256(payload)}"
