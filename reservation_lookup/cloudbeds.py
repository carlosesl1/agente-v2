from __future__ import annotations

from collections.abc import Mapping
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
    CloudbedsLookupRequest,
    LookupFailure,
    ProviderKind,
    ReadRequest,
    ReadTransport,
)

_TECHNICAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class CloudbedsReadAdapter:
    def __init__(self, transport: ReadTransport):
        if transport is None or not callable(getattr(transport, "send", None)):
            raise TypeError("transport must implement send")
        self._transport = transport

    def lookup(
        self,
        request: CloudbedsLookupRequest,
        *,
        observed_at: datetime,
        ttl: timedelta,
    ):
        if type(request) is not CloudbedsLookupRequest:
            raise TypeError("request must be CloudbedsLookupRequest")
        validate_clock(observed_at, ttl)
        requests = _build_requests(request)
        responses, transport_failures = execute_requests(self._transport, requests)
        provenance = provenance_for(ProviderKind.CLOUDBEDS, requests, responses)
        if transport_failures:
            return result_for(
                provider=ProviderKind.CLOUDBEDS,
                query=request.query,
                observed_at=observed_at,
                ttl=ttl,
                provenance=provenance,
                status=LookupStatus.UNCERTAIN,
                failures=transport_failures,
            )

        lookup_id = lookup_id_from(
            provider=ProviderKind.CLOUDBEDS,
            query=request.query,
            observed_at=observed_at,
            provenance=provenance,
        )
        try:
            offers = _normalize_offers(
                available_body=responses[0].body,
                rates_body=responses[1].body,
                request=request,
                lookup_id=lookup_id,
            )
        except ProviderSchemaError as exc:
            return result_for(
                provider=ProviderKind.CLOUDBEDS,
                query=request.query,
                observed_at=observed_at,
                ttl=ttl,
                provenance=provenance,
                status=LookupStatus.UNCERTAIN,
                failures=(LookupFailure(code="schema_error", detail=str(exc)),),
            )
        status = LookupStatus.POSITIVE if offers else LookupStatus.NEGATIVE
        return result_for(
            provider=ProviderKind.CLOUDBEDS,
            query=request.query,
            observed_at=observed_at,
            ttl=ttl,
            provenance=provenance,
            status=status,
            offers=offers,
        )


def _build_requests(request: CloudbedsLookupRequest) -> tuple[ReadRequest, ...]:
    query = request.query
    if query.end_date is None:
        raise ValueError("Cloudbeds query requires end_date")
    params = (
        ("propertyID", request.property_id),
        ("startDate", query.start_date.isoformat()),
        ("endDate", query.end_date.isoformat()),
        ("adults", str(query.party.adults)),
        ("children", str(query.party.children)),
        ("detailedRates", "true"),
    )
    return (
        ReadRequest(
            method="GET",
            path="/api/v1.3/getAvailableRoomTypes",
            query=params,
        ),
        ReadRequest(
            method="GET",
            path="/api/v1.2/getRatePlans",
            query=params,
        ),
    )


def _normalize_offers(
    *,
    available_body,
    rates_body,
    request: CloudbedsLookupRequest,
    lookup_id: str,
) -> tuple[OfferSnapshot, ...]:
    room_items = _provider_data(available_body, "available_rooms")
    rate_items = _provider_data(rates_body, "rate_plans")
    rate_plan_ids = _rate_plan_ids(rate_items)
    expected_dates = _stay_dates(request.query.start_date, request.query.end_date)
    offers: list[OfferSnapshot] = []
    seen_offer_ids: set[str] = set()
    for index, item in enumerate(room_items):
        if not isinstance(item, Mapping):
            raise ProviderSchemaError(f"room_{index}_not_object")
        available_units = _strict_int(item.get("roomsAvailable"), f"room_{index}_units")
        if available_units <= 0:
            continue
        room_id = _technical_id(item.get("roomTypeID"), f"room_{index}_id")
        rate_plan_id = _technical_id(item.get("ratePlanID"), f"room_{index}_rate_plan")
        if rate_plan_id not in rate_plan_ids:
            raise ProviderSchemaError(f"room_{index}_rate_plan_not_found")
        label = _label(item.get("roomTypeName"), f"room_{index}_label")
        currency = _currency(item.get("currency"), f"room_{index}_currency")
        total = _daily_total(
            item.get("roomRateDetailed"),
            expected_dates=expected_dates,
            currency=currency,
            room_index=index,
        )
        provider_ref = (
            f"cloudbeds.property.{request.property_id}."
            f"room.{room_id}.rate.{rate_plan_id}"
        )
        base = OfferSnapshot(
            offer_id="offer:pending",
            lookup_id=lookup_id,
            service=ServiceKind.LODGING,
            provider_ref=provider_ref,
            public_label=label,
            start_date=request.query.start_date,
            end_date=request.query.end_date,
            start_time=request.query.start_time,
            party=request.query.party,
            total=Money(amount=total, currency=currency),
            available=True,
        )
        offer = replace(
            base,
            offer_id=offer_id_for(provider=ProviderKind.CLOUDBEDS, offer=base),
        )
        if offer.offer_id in seen_offer_ids:
            raise ProviderSchemaError("duplicate_canonical_offer")
        seen_offer_ids.add(offer.offer_id)
        offers.append(offer)
    return tuple(sorted(offers, key=lambda item: item.offer_id))


def _provider_data(body, source: str) -> tuple:
    if not isinstance(body, Mapping):
        raise ProviderSchemaError(f"{source}_envelope_not_object")
    if body.get("success") is not True:
        raise ProviderSchemaError(f"{source}_success_not_true")
    data = body.get("data")
    if type(data) is not tuple:
        raise ProviderSchemaError(f"{source}_data_not_array")
    return data


def _rate_plan_ids(items: tuple) -> set[str]:
    values: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ProviderSchemaError(f"rate_plan_{index}_not_object")
        rate_id = _technical_id(item.get("ratePlanID"), f"rate_plan_{index}_id")
        if rate_id in values:
            raise ProviderSchemaError("duplicate_rate_plan_id")
        values.add(rate_id)
    return values


def _stay_dates(start: date, end: date | None) -> tuple[str, ...]:
    if end is None or end <= start:
        raise ValueError("invalid stay dates")
    return tuple(
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days)
    )


def _daily_total(
    value,
    *,
    expected_dates: tuple[str, ...],
    currency: str,
    room_index: int,
) -> Decimal:
    if type(value) is not tuple:
        raise ProviderSchemaError(f"room_{room_index}_daily_rates_not_array")
    if len(value) != len(expected_dates):
        raise ProviderSchemaError(f"room_{room_index}_partial_stay")
    amounts: dict[str, Decimal] = {}
    for row_index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise ProviderSchemaError(f"room_{room_index}_rate_{row_index}_not_object")
        day = row.get("date")
        if type(day) is not str or day not in expected_dates or day in amounts:
            raise ProviderSchemaError(f"room_{room_index}_rate_{row_index}_date_invalid")
        units = _strict_int(
            row.get("roomsAvailable"),
            f"room_{room_index}_rate_{row_index}_units",
        )
        if units <= 0:
            raise ProviderSchemaError(f"room_{room_index}_partial_stay")
        row_currency = _currency(
            row.get("currency"),
            f"room_{room_index}_rate_{row_index}_currency",
        )
        if row_currency != currency:
            raise ProviderSchemaError(f"room_{room_index}_currency_mismatch")
        amounts[day] = _positive_decimal(
            row.get("rate"),
            f"room_{room_index}_rate_{row_index}_amount",
        )
    if set(amounts) != set(expected_dates):
        raise ProviderSchemaError(f"room_{room_index}_partial_stay")
    return sum((amounts[day] for day in expected_dates), Decimal("0"))


def _technical_id(value, field: str) -> str:
    if type(value) is not str or not _TECHNICAL_ID_RE.fullmatch(value):
        raise ProviderSchemaError(f"{field}_invalid")
    return value


def _label(value, field: str) -> str:
    if type(value) is not str:
        raise ProviderSchemaError(f"{field}_invalid")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > 160:
        raise ProviderSchemaError(f"{field}_invalid")
    return cleaned


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
