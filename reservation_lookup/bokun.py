from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from reservation_domain import LookupStatus, Money, OfferSnapshot, ServiceKind

from ._common import (
    ProviderSchemaError,
    execute_requests,
    lookup_id_from,
    provenance_for,
    result_for,
    validate_clock,
)
from .identity import offer_id_for
from .types import (
    BokunLookupRequest,
    LookupFailure,
    ProviderKind,
    ReadRequest,
    ReadTransport,
)

_TECHNICAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class BokunReadAdapter:
    def __init__(self, transport: ReadTransport):
        if transport is None or not callable(getattr(transport, "send", None)):
            raise TypeError("transport must implement send")
        self._transport = transport

    def lookup(
        self,
        request: BokunLookupRequest,
        *,
        observed_at: datetime,
        ttl: timedelta,
    ):
        if type(request) is not BokunLookupRequest:
            raise TypeError("request must be BokunLookupRequest")
        validate_clock(observed_at, ttl)
        requests = _build_requests(request)
        responses, transport_failures = execute_requests(self._transport, requests)
        provenance = provenance_for(ProviderKind.BOKUN, requests, responses)
        if transport_failures:
            return result_for(
                provider=ProviderKind.BOKUN,
                query=request.query,
                observed_at=observed_at,
                ttl=ttl,
                provenance=provenance,
                status=LookupStatus.UNCERTAIN,
                failures=transport_failures,
            )
        lookup_id = lookup_id_from(
            provider=ProviderKind.BOKUN,
            query=request.query,
            observed_at=observed_at,
            provenance=provenance,
        )
        try:
            offers = _normalize_offers(
                activity_body=responses[0].body,
                availability_body=responses[1].body,
                request=request,
                lookup_id=lookup_id,
            )
        except ProviderSchemaError as exc:
            return result_for(
                provider=ProviderKind.BOKUN,
                query=request.query,
                observed_at=observed_at,
                ttl=ttl,
                provenance=provenance,
                status=LookupStatus.UNCERTAIN,
                failures=(LookupFailure(code="schema_error", detail=str(exc)),),
            )
        status = LookupStatus.POSITIVE if offers else LookupStatus.NEGATIVE
        return result_for(
            provider=ProviderKind.BOKUN,
            query=request.query,
            observed_at=observed_at,
            ttl=ttl,
            provenance=provenance,
            status=status,
            offers=offers,
        )


def _build_requests(request: BokunLookupRequest) -> tuple[ReadRequest, ...]:
    start = request.query.start_date.isoformat()
    end = (request.query.end_date or request.query.start_date).isoformat()
    root = f"/activity.json/{request.product_id}"
    return (
        ReadRequest(
            method="GET",
            path=root,
            query=(("lang", "pt_BR"), ("currency", "BRL")),
        ),
        ReadRequest(
            method="GET",
            path=f"{root}/availabilities",
            query=(("start", start), ("end", end), ("currency", "BRL")),
        ),
    )


def _normalize_offers(
    *,
    activity_body,
    availability_body,
    request: BokunLookupRequest,
    lookup_id: str,
) -> tuple[OfferSnapshot, ...]:
    title = _activity_title(activity_body, request.product_id)
    items = _availability_data(availability_body)
    end_bound = request.query.end_date or request.query.start_date
    offers: list[OfferSnapshot] = []
    seen_offer_ids: set[str] = set()
    for index, item in enumerate(items):
        if type(item) is not dict:
            raise ProviderSchemaError(f"availability_{index}_not_object")
        option_date = _iso_date(item.get("date"), f"availability_{index}_date")
        if not request.query.start_date <= option_date <= end_bound:
            continue
        available = _strict_bool(item.get("available"), f"availability_{index}_available")
        sold_out = _strict_bool(item.get("soldOut"), f"availability_{index}_sold_out")
        unavailable = _strict_bool(
            item.get("unavailable"), f"availability_{index}_unavailable"
        )
        available_units = _strict_int(
            item.get("availabilityCount"), f"availability_{index}_units"
        )
        if not available or sold_out or unavailable or available_units <= 0:
            continue
        start_time = _time(item.get("startTime"), f"availability_{index}_time")
        if request.query.start_time is not None and start_time != request.query.start_time:
            continue
        start_time_id = _technical_id(
            item.get("startTimeId"), f"availability_{index}_start_time_id"
        )
        rate_id = _technical_id(
            item.get("defaultRateId"), f"availability_{index}_rate_id"
        )
        currency = _currency(item.get("currency"), f"availability_{index}_currency")
        if currency != "BRL":
            raise ProviderSchemaError(f"availability_{index}_currency_mismatch")
        total = _positive_decimal(
            item.get("totalAmount"), f"availability_{index}_total"
        )
        provider_ref = (
            f"bokun.product.{request.product_id}.start.{start_time_id}.rate.{rate_id}"
        )
        public_label = f"{title} — {option_date.isoformat()} {start_time}"
        base = OfferSnapshot(
            offer_id="offer:pending",
            lookup_id=lookup_id,
            service=ServiceKind.ACTIVITY,
            provider_ref=provider_ref,
            public_label=public_label,
            start_date=option_date,
            end_date=None,
            start_time=start_time,
            party=request.query.party,
            total=Money(amount=total, currency=currency),
            available=True,
        )
        offer = replace(
            base,
            offer_id=offer_id_for(provider=ProviderKind.BOKUN, offer=base),
        )
        if offer.offer_id in seen_offer_ids:
            raise ProviderSchemaError("duplicate_canonical_offer")
        seen_offer_ids.add(offer.offer_id)
        offers.append(offer)
    return tuple(sorted(offers, key=lambda item: item.offer_id))


def _activity_title(body, expected_product_id: str) -> str:
    if type(body) is not dict:
        raise ProviderSchemaError("activity_envelope_not_object")
    if body.get("success") is False:
        raise ProviderSchemaError("activity_success_false")
    product_id = _technical_id(body.get("id"), "activity_id")
    if product_id != expected_product_id:
        raise ProviderSchemaError("activity_id_mismatch")
    title = body.get("title")
    if type(title) is not str:
        raise ProviderSchemaError("activity_title_invalid")
    cleaned = " ".join(title.split())
    if not cleaned or len(cleaned) > 120:
        raise ProviderSchemaError("activity_title_invalid")
    return cleaned


def _availability_data(body) -> list:
    if type(body) is not dict:
        raise ProviderSchemaError("availability_envelope_not_object")
    if body.get("success") is not True:
        raise ProviderSchemaError("availability_success_not_true")
    data = body.get("data")
    if type(data) is not list:
        raise ProviderSchemaError("availability_data_not_array")
    return data


def _technical_id(value, field: str) -> str:
    if type(value) is not str or not _TECHNICAL_ID_RE.fullmatch(value):
        raise ProviderSchemaError(f"{field}_invalid")
    return value


def _iso_date(value, field: str) -> date:
    if type(value) is not str:
        raise ProviderSchemaError(f"{field}_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProviderSchemaError(f"{field}_invalid") from exc
    if parsed.isoformat() != value:
        raise ProviderSchemaError(f"{field}_non_canonical")
    return parsed


def _time(value, field: str) -> str:
    if type(value) is not str or not _TIME_RE.fullmatch(value):
        raise ProviderSchemaError(f"{field}_invalid")
    return value


def _strict_bool(value, field: str) -> bool:
    if type(value) is not bool:
        raise ProviderSchemaError(f"{field}_invalid")
    return value


def _strict_int(value, field: str) -> int:
    if type(value) is not int:
        raise ProviderSchemaError(f"{field}_invalid")
    return value


def _currency(value, field: str) -> str:
    if type(value) is not str or not _CURRENCY_RE.fullmatch(value):
        raise ProviderSchemaError(f"{field}_invalid")
    return value


def _positive_decimal(value, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ProviderSchemaError(f"{field}_invalid")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ProviderSchemaError(f"{field}_invalid") from exc
    if not amount.is_finite() or amount <= 0:
        raise ProviderSchemaError(f"{field}_invalid")
    return amount
