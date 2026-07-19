from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from reservation_domain import LookupEvidence, LookupStatus, OfferSnapshot, SearchQuery

from .identity import lookup_id_for, request_fingerprint, response_hash
from .types import (
    LookupFailure,
    LookupProvenance,
    LookupResult,
    ProviderKind,
    ReadRequest,
    ReadResponse,
    ReadTransport,
)

_MAX_TTL = timedelta(minutes=15)


class ProviderSchemaError(ValueError):
    pass


def validate_clock(observed_at: datetime, ttl: timedelta) -> None:
    if (
        type(observed_at) is not datetime
        or observed_at.tzinfo is None
        or observed_at.utcoffset() != timezone.utc.utcoffset(observed_at)
    ):
        raise ValueError("observed_at must be UTC")
    if type(ttl) is not timedelta or ttl <= timedelta(0) or ttl > _MAX_TTL:
        raise ValueError("ttl must be greater than zero and at most 15 minutes")


def execute_requests(
    transport: ReadTransport,
    requests: tuple[ReadRequest, ...],
) -> tuple[tuple[ReadResponse, ...], tuple[LookupFailure, ...]]:
    responses: list[ReadResponse] = []
    failures: list[LookupFailure] = []
    for index, request in enumerate(requests):
        try:
            response = transport.send(request)
        except Exception:
            responses.append(
                ReadResponse(
                    status_code=599,
                    body={"failure": "transport_error", "request_index": index},
                )
            )
            failures.append(
                LookupFailure(code="transport_error", detail=f"request_{index}_failed")
            )
            _append_not_attempted(responses, start=index + 1, total=len(requests))
            break
        if type(response) is not ReadResponse:
            responses.append(
                ReadResponse(
                    status_code=599,
                    body={"failure": "invalid_transport_response", "request_index": index},
                )
            )
            failures.append(
                LookupFailure(
                    code="transport_error",
                    detail=f"request_{index}_returned_invalid_type",
                )
            )
            _append_not_attempted(responses, start=index + 1, total=len(requests))
            break
        responses.append(response)
        if not 200 <= response.status_code <= 299:
            failures.append(
                LookupFailure(code="http_error", detail=f"request_{index}_non_2xx")
            )
            _append_not_attempted(responses, start=index + 1, total=len(requests))
            break
    return tuple(responses), tuple(failures)


def _append_not_attempted(
    responses: list[ReadResponse], *, start: int, total: int
) -> None:
    for index in range(start, total):
        responses.append(
            ReadResponse(
                status_code=599,
                body={"failure": "not_attempted", "request_index": index},
            )
        )


def provenance_for(
    provider: ProviderKind,
    requests: tuple[ReadRequest, ...],
    responses: tuple[ReadResponse, ...],
) -> LookupProvenance:
    return LookupProvenance(
        provider=provider,
        request_fingerprints=tuple(request_fingerprint(item) for item in requests),
        response_hashes=tuple(response_hash(item) for item in responses),
    )


def lookup_id_from(
    *,
    provider: ProviderKind,
    query: SearchQuery,
    observed_at: datetime,
    provenance: LookupProvenance,
) -> str:
    return lookup_id_for(
        provider=provider,
        query=query,
        observed_at=observed_at,
        request_fingerprints=provenance.request_fingerprints,
        response_hashes=provenance.response_hashes,
    )


def result_for(
    *,
    provider: ProviderKind,
    query: SearchQuery,
    observed_at: datetime,
    ttl: timedelta,
    provenance: LookupProvenance,
    status: LookupStatus,
    offers: Iterable[OfferSnapshot] = (),
    failures: Iterable[LookupFailure] = (),
) -> LookupResult:
    lookup_id = lookup_id_from(
        provider=provider,
        query=query,
        observed_at=observed_at,
        provenance=provenance,
    )
    evidence = LookupEvidence(
        lookup_id=lookup_id,
        service=query.service,
        query_signature=query.signature,
        observed_at=observed_at,
        expires_at=observed_at + ttl,
        snapshot_hash=provenance.snapshot_hash,
        status=status,
    )
    return LookupResult(
        query=query,
        evidence=evidence,
        provenance=provenance,
        offers=tuple(offers),
        failures=tuple(failures),
    )
