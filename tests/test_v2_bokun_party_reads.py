from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from v2_adapters.bokun import BokunReadAdapter
from v2_adapters.provider_http import BokunHTTPTransport, ProviderHTTPError
from v2_contracts.private_offers import PrivateOfferQuery
from v2_contracts.providers import ReadKind, ReadRequest


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def test_bokun_activity_description_marks_missing_material_guidance_explicitly() -> None:
    def transport(operation: str, payload: dict[str, object]) -> dict[str, object]:
        assert operation == "activity_description"
        assert payload == {"product_id": "product:buracao", "locale": "pt-BR"}
        return {
            "bokun_product_id": "buracao-provider-id",
            "product_public_name": "Buracão",
            "description": "Passeio por trilhas e cânions.",
        }

    observation = BokunReadAdapter(
        transport=transport,
        clock=SimpleNamespace(now=lambda: NOW),
        ttl=timedelta(minutes=5),
    ).read(
        ReadRequest(
            request_id="read:buracao-description",
            kind=ReadKind.ACTIVITY_DESCRIPTION,
            product_id="product:buracao",
            locale="pt-BR",
        )
    )

    assert observation.public_payload == {
        "product_id": "product:buracao",
        "product_public_name": "Buracão",
        "description": "Passeio por trilhas e cânions.",
        "age_guidance": None,
        "suitability_guidance": None,
    }


def test_activity_read_request_binds_adult_child_composition_and_keeps_legacy() -> None:
    mixed = ReadRequest(
        request_id="read:party-aware",
        kind=ReadKind.ACTIVITY,
        product_id="product:tour-4ps",
        activity_date=date(2026, 11, 18),
        adults=2,
        children=1,
    )
    legacy = ReadRequest(
        request_id="read:legacy-party",
        kind=ReadKind.ACTIVITY,
        product_id="product:tour-4ps",
        activity_date=date(2026, 11, 18),
        participants=3,
    )

    assert mixed.activity_party() == (2, 1)
    assert legacy.activity_party() == (3, 0)
    assert json.loads(mixed.to_canonical_bytes()) == {
        "activity_date": "2026-11-18",
        "adults": 2,
        "children": 1,
        "kind": "activity",
        "product_id": "product:tour-4ps",
        "request_id": "read:party-aware",
    }
    assert mixed.query_hash() != legacy.query_hash()
    with pytest.raises(ValueError, match="composition"):
        ReadRequest(
            request_id="read:ambiguous-party",
            kind=ReadKind.ACTIVITY,
            product_id="product:tour-4ps",
            activity_date=date(2026, 11, 18),
            adults=2,
            children=1,
            participants=3,
        )


def test_bokun_read_adapter_sends_composition_and_returns_public_counts() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def transport(operation: str, payload: dict[str, object]) -> dict[str, object]:
        seen.append((operation, payload))
        return {
            "product_id": "product:tour-4ps",
            "bokun_product_id": "912303",
            "start_time_id": "start-4ps",
            "start_time": "08:30",
            "rate_id": "rate-4ps",
            "adult_pricing_category_id": "adult-1",
            "child_pricing_category_id": "child-1",
            "product_public_name": "Roteiro dos 4Ps",
            "base_amount": "750.00",
            "booking_fee_amount": "5.00",
            "total_amount": "755.00",
            "currency": "BRL",
            "price_includes_booking_fee": True,
            "available": True,
        }

    adapter = BokunReadAdapter(
        transport=transport,
        clock=SimpleNamespace(now=lambda: NOW),
        ttl=timedelta(minutes=5),
    )
    request = ReadRequest(
        request_id="read:mixed-adapter",
        kind=ReadKind.ACTIVITY,
        product_id="product:tour-4ps",
        activity_date=date(2026, 11, 18),
        adults=2,
        children=1,
    )

    observation = adapter.read(request)

    assert seen == [
        (
            "activity",
            {
                "product_id": "product:tour-4ps",
                "activity_date": "2026-11-18",
                "adults": 2,
                "children": 1,
                "quote_scope": request.query_hash(),
            },
        )
    ]
    assert observation.public_payload["adults"] == 2
    assert observation.public_payload["children"] == 1
    assert observation.public_payload["participants"] == 3
    assert observation.public_payload["start_time"] == "08:30"
    assert observation.public_payload["availability_statement"] == (
        "AVAILABLE: this activity is available on 2026-11-18 "
        "for exactly 2 adults and 1 child."
    )


def test_bokun_read_returns_explicit_unavailable_statement_for_exact_scope() -> None:
    def transport(_operation: str, _payload: dict[str, object]) -> dict[str, object]:
        return {
            "product_id": "product:tour-4ps",
            "bokun_product_id": "912303",
            "start_time_id": "start-4ps",
            "rate_id": "rate-4ps",
            "adult_pricing_category_id": "adult-1",
            "child_pricing_category_id": "child-1",
            "product_public_name": "Roteiro dos 4Ps",
            "total_amount": "0.00",
            "currency": "BRL",
            "available": False,
        }

    observation = BokunReadAdapter(
        transport=transport,
        clock=SimpleNamespace(now=lambda: NOW),
        ttl=timedelta(minutes=5),
    ).read(
        ReadRequest(
            request_id="read:unavailable-statement",
            kind=ReadKind.ACTIVITY,
            product_id="product:tour-4ps",
            activity_date=date(2026, 11, 18),
            adults=1,
            children=0,
        )
    )

    assert observation.public_payload["availability_statement"] == (
        "UNAVAILABLE: this activity is unavailable on 2026-11-18 "
        "for exactly 1 adult and 0 children."
    )


def test_bokun_private_reread_preserves_selected_public_start_time() -> None:
    def transport(operation: str, payload: dict[str, object]) -> dict[str, object]:
        assert operation == "activity"
        return {
            "product_id": "product:tour-4ps",
            "bokun_product_id": "912303",
            "start_time_id": "start-4ps",
            "start_time": "08:30",
            "rate_id": "rate-4ps",
            "adult_pricing_category_id": "adult-1",
            "child_pricing_category_id": "child-1",
            "product_public_name": "Roteiro dos 4Ps",
            "base_amount": "960.00",
            "booking_fee_amount": "14.40",
            "total_amount": "974.40",
            "currency": "BRL",
            "price_includes_booking_fee": True,
            "available": True,
        }

    adapter = BokunReadAdapter(
        transport=transport,
        clock=SimpleNamespace(now=lambda: NOW),
        ttl=timedelta(minutes=5),
    )
    request = ReadRequest(
        request_id="read:mixed-private-roundtrip",
        kind=ReadKind.ACTIVITY,
        product_id="product:tour-4ps",
        activity_date=date(2026, 11, 18),
        adults=2,
        children=1,
    )
    observation = adapter.read(request)
    query = PrivateOfferQuery(
        service="activity",
        offer_id=observation.public_payload["offer_id"],
        request_hash=request.query_hash(),
        binding_hash=observation.private_binding_hash,
        canonical_product_id="product:tour-4ps",
        start_date=date(2026, 11, 18),
        end_date=None,
        start_time="08:30",
        adults=2,
        children=1,
        total_amount="974.40",
        currency="BRL",
        available=True,
    )

    binding = adapter.resolve(query)

    assert binding.query == query


def test_bokun_http_transport_selects_requested_start_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/activity.json/912303"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": 912303,
                    "title": "Roteiro dos 4Ps",
                    "pricingCategories": [
                        {"id": "adult-1", "ticketCategory": "ADULT"},
                        {"id": "child-1", "ticketCategory": "CHILD"},
                    ],
                },
            )
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "date": "2026-11-18",
                    "startTimeId": "start-0830",
                    "startTime": "08:30",
                    "available": True,
                    "availabilityCount": 5,
                    "pricesByRate": [
                        {
                            "activityRateId": "rate-0830",
                            "pricePerCategoryUnit": [
                                {"id": "adult-1", "amount": {"amount": 300, "currency": "BRL"}},
                                {"id": "child-1", "amount": {"amount": 150, "currency": "BRL"}},
                            ],
                        }
                    ],
                },
                {
                    "date": "2026-11-18",
                    "startTimeId": "start-0930",
                    "startTime": "09:30",
                    "available": True,
                    "availabilityCount": 5,
                    "pricesByRate": [
                        {
                            "activityRateId": "rate-0930",
                            "pricePerCategoryUnit": [
                                {"id": "adult-1", "amount": {"amount": 310, "currency": "BRL"}},
                                {"id": "child-1", "amount": {"amount": 155, "currency": "BRL"}},
                            ],
                        }
                    ],
                },
            ],
        )

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:tour-4ps": "912303"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-30 12:00:00",
        quote_checkout_enabled=False,
    )

    result = transport(
        "activity",
        {
            "product_id": "product:tour-4ps",
            "activity_date": "2026-11-18",
            "start_time": "09:30",
            "adults": 2,
            "children": 1,
        },
    )

    assert result["start_time"] == "09:30"
    assert result["start_time_id"] == "start-0930"
    assert result["rate_id"] == "rate-0930"
    assert result["total_amount"] == "775.00"


def test_bokun_http_transport_prices_and_quotes_exact_mixed_party() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        call = len(seen)
        if call == 1:
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": 912303,
                    "title": "Roteiro dos 4Ps",
                    "pricingCategories": [
                        {"id": "adult-1", "ticketCategory": "ADULT"},
                        {"id": "child-1", "ticketCategory": "CHILD"},
                    ],
                },
            )
        if call == 2:
            return httpx.Response(
                200,
                request=request,
                json=[
                    {
                        "date": "2026-11-18",
                        "startTimeId": "start-4ps",
                        "startTime": "08:30",
                        "available": True,
                        "availabilityCount": 5,
                        "pricesByRate": [
                            {
                                "activityRateId": "rate-4ps",
                                "pricePerCategoryUnit": [
                                    {
                                        "id": "adult-1",
                                        "amount": {"amount": 300, "currency": "BRL"},
                                    },
                                    {
                                        "id": "child-1",
                                        "amount": {"amount": 150, "currency": "BRL"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            )
        if call == 3:
            session_id = request.url.path.split("/session/", 1)[1]
            return httpx.Response(
                200,
                request=request,
                json={
                    "sessionId": session_id,
                    "size": 0,
                    "activityBookings": [],
                    "accommodationBookings": [],
                    "routeBookings": [],
                    "giftCardBookings": [],
                },
            )
        if call == 4:
            body = json.loads(request.content)
            assert body["pricingCategoryBookings"] == [
                {"pricingCategoryId": "adult-1"},
                {"pricingCategoryId": "adult-1"},
                {"pricingCategoryId": "child-1"},
            ]
            session_id = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            return httpx.Response(
                200,
                request=request,
                json={
                    "uuid": session_id,
                    "activityBookings": [
                        {
                            "bookingId": "quote-activity",
                            "activityId": "912303",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": "passenger-adult-1",
                                    "pricingCategoryId": "adult-1",
                                },
                                {
                                    "bookingId": "passenger-adult-2",
                                    "pricingCategoryId": "adult-1",
                                },
                                {
                                    "bookingId": "passenger-child-1",
                                    "pricingCategoryId": "child-1",
                                },
                            ],
                        }
                    ],
                },
            )
        assert call == 5
        return httpx.Response(
            200,
            request=request,
            json={
                "options": [
                    {"invoice": {"remainingAmountAsText": "R$ 755,00"}}
                ]
            },
        )

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:tour-4ps": "912303"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-30 12:00:00",
        quote_checkout_enabled=True,
    )

    result = transport(
        "activity",
        {
            "product_id": "product:tour-4ps",
            "activity_date": "2026-11-18",
            "adults": 2,
            "children": 1,
            "quote_scope": "a" * 64,
        },
    )

    assert result == {
        "product_id": "product:tour-4ps",
        "bokun_product_id": "912303",
        "product_public_name": "Roteiro dos 4Ps",
        "start_time_id": "start-4ps",
        "start_time": "08:30",
        "rate_id": "rate-4ps",
        "adult_pricing_category_id": "adult-1",
        "child_pricing_category_id": "child-1",
        "base_amount": "750.00",
        "booking_fee_amount": "5.00",
        "total_amount": "755.00",
        "currency": "BRL",
        "price_includes_booking_fee": True,
        "available": True,
    }
    assert len(seen) == 5


def test_bokun_http_transport_fails_closed_without_required_child_category() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": 912303,
                    "title": "Roteiro dos 4Ps",
                    "pricingCategories": [
                        {"id": "adult-1", "ticketCategory": "ADULT"}
                    ],
                },
            )
        assert calls == 2
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "date": "2026-11-18",
                    "startTimeId": "start-4ps",
                    "available": True,
                    "availabilityCount": 5,
                    "pricesByRate": [
                        {
                            "activityRateId": "rate-4ps",
                            "pricePerCategoryUnit": [
                                {
                                    "id": "adult-1",
                                    "amount": {"amount": 300, "currency": "BRL"},
                                }
                            ],
                        }
                    ],
                }
            ],
        )

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:tour-4ps": "912303"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-30 12:00:00",
        quote_checkout_enabled=True,
    )

    with pytest.raises(ProviderHTTPError, match="pricing category"):
        transport(
            "activity",
            {
                "product_id": "product:tour-4ps",
                "activity_date": "2026-11-18",
                "adults": 1,
                "children": 1,
                "quote_scope": "b" * 64,
            },
        )
    assert calls == 2


def test_bokun_http_transport_fails_closed_for_mixed_pricing_currencies() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": 912303,
                    "title": "Roteiro dos 4Ps",
                    "pricingCategories": [
                        {"id": "adult-1", "ticketCategory": "ADULT"},
                        {"id": "child-1", "ticketCategory": "CHILD"},
                    ],
                },
            )
        assert calls == 2
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "date": "2026-11-18",
                    "startTimeId": "start-4ps",
                    "available": True,
                    "availabilityCount": 5,
                    "pricesByRate": [
                        {
                            "activityRateId": "rate-4ps",
                            "pricePerCategoryUnit": [
                                {
                                    "id": "adult-1",
                                    "amount": {"amount": 300, "currency": "BRL"},
                                },
                                {
                                    "id": "child-1",
                                    "amount": {"amount": 150, "currency": "USD"},
                                },
                            ],
                        }
                    ],
                }
            ],
        )

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:tour-4ps": "912303"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-30 12:00:00",
    )

    with pytest.raises(ProviderHTTPError, match="currency"):
        transport(
            "activity",
            {
                "product_id": "product:tour-4ps",
                "activity_date": "2026-11-18",
                "adults": 1,
                "children": 1,
            },
        )
    assert calls == 2


def test_bokun_http_transport_rejects_later_non_brl_rate_after_mixed_rate() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": 912303,
                    "title": "Roteiro dos 4Ps",
                    "pricingCategories": [
                        {"id": "adult-1", "ticketCategory": "ADULT"},
                        {"id": "child-1", "ticketCategory": "CHILD"},
                    ],
                },
            )
        assert calls == 2
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "date": "2026-11-18",
                    "startTimeId": "start-4ps",
                    "available": True,
                    "availabilityCount": 5,
                    "pricesByRate": [
                        {
                            "activityRateId": "rate-mixed",
                            "pricePerCategoryUnit": [
                                {
                                    "id": "adult-1",
                                    "amount": {"amount": 300, "currency": "BRL"},
                                },
                                {
                                    "id": "child-1",
                                    "amount": {"amount": 150, "currency": "USD"},
                                },
                            ],
                        },
                        {
                            "activityRateId": "rate-eur",
                            "pricePerCategoryUnit": [
                                {
                                    "id": "adult-1",
                                    "amount": {"amount": 300, "currency": "EUR"},
                                },
                                {
                                    "id": "child-1",
                                    "amount": {"amount": 150, "currency": "EUR"},
                                },
                            ],
                        },
                    ],
                }
            ],
        )

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:tour-4ps": "912303"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-30 12:00:00",
    )

    with pytest.raises(ProviderHTTPError, match="currency"):
        transport(
            "activity",
            {
                "product_id": "product:tour-4ps",
                "activity_date": "2026-11-18",
                "adults": 1,
                "children": 1,
            },
        )
    assert calls == 2


def test_bokun_http_transport_selects_later_valid_brl_rate() -> None:
    metadata = {
        "id": 912303,
        "title": "Roteiro dos 4Ps",
        "pricingCategories": [
            {"id": "adult-1", "ticketCategory": "ADULT"},
            {"id": "child-1", "ticketCategory": "CHILD"},
        ],
    }
    availability = [
        {
            "date": "2026-11-18",
            "startTimeId": "start-4ps",
            "available": True,
            "availabilityCount": 5,
            "pricesByRate": [
                {
                    "activityRateId": "rate-mixed",
                    "pricePerCategoryUnit": [
                        {
                            "id": "adult-1",
                            "amount": {"amount": 300, "currency": "EUR"},
                        },
                        {
                            "id": "child-1",
                            "amount": {"amount": 150, "currency": "EUR"},
                        },
                    ],
                },
                {
                    "activityRateId": "rate-brl",
                    "pricePerCategoryUnit": [
                        {
                            "id": "adult-1",
                            "amount": {"amount": 310, "currency": "BRL"},
                        },
                        {
                            "id": "child-1",
                            "amount": {"amount": 155, "currency": "BRL"},
                        },
                    ],
                },
            ],
        }
    ]
    responses = iter((metadata, availability))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=next(responses))

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:tour-4ps": "912303"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-30 12:00:00",
    )

    result = transport(
        "activity",
        {
            "product_id": "product:tour-4ps",
            "activity_date": "2026-11-18",
            "adults": 2,
            "children": 1,
        },
    )

    assert result["rate_id"] == "rate-brl"
    assert result["total_amount"] == "775.00"
    assert result["currency"] == "BRL"


def test_bokun_http_transport_prefers_unique_public_age_qualified_adult_category() -> None:
    metadata = {
        "id": 913372,
        "title": "Buracão",
        "pricingCategories": [
            {
                "id": "adult-public",
                "ticketCategory": "ADULT",
                "ageQualified": True,
                "minAge": 18,
                "maxAge": 59,
            },
            {
                "id": "adult-internal-youth",
                "ticketCategory": "ADULT",
                "ageQualified": False,
            },
            {
                "id": "adult-internal-default",
                "ticketCategory": "ADULT",
                "ageQualified": False,
            },
            {
                "id": "adult-internal-senior",
                "ticketCategory": "ADULT",
                "ageQualified": False,
            },
        ],
    }
    availability = [
        {
            "date": "2026-11-18",
            "startTimeId": "start-buracao",
            "available": True,
            "availabilityCount": 8,
            "pricesByRate": [
                {
                    "activityRateId": "rate-public",
                    "pricePerCategoryUnit": [
                        {
                            "id": "adult-public",
                            "amount": {"amount": 300, "currency": "BRL"},
                        },
                        {
                            "id": "adult-internal-default",
                            "amount": {"amount": 999, "currency": "BRL"},
                        },
                    ],
                }
            ],
        }
    ]
    responses = iter((metadata, availability))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=next(responses))

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:buracao": "913372"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-31 01:30:00",
    )

    result = transport(
        "activity",
        {
            "product_id": "product:buracao",
            "activity_date": "2026-11-18",
            "adults": 2,
            "children": 0,
        },
    )

    assert result["adult_pricing_category_id"] == "adult-public"
    assert result["rate_id"] == "rate-public"
    assert result["total_amount"] == "600.00"
    assert result["currency"] == "BRL"


def _solo_transport(
    *,
    metadata_changes: dict[str, object] | None = None,
    availability_changes: dict[str, object] | None = None,
    availability_remove: tuple[str, ...] = (),
    quote_enabled: bool = False,
):
    metadata = {
        "id": 913372,
        "title": "Buracão",
        "pricingCategories": [
            {"id": "1160099", "ticketCategory": "ADULT", "ageQualified": False},
            {"id": "857489", "ticketCategory": "ADULT", "ageQualified": True},
        ],
    }
    if metadata_changes:
        metadata.update(metadata_changes)
    availability = {
        "date": "2026-11-18",
        "startTimeId": "start-buracao",
        "startTime": "07:30",
        "available": True,
        "soldOut": False,
        "unavailable": False,
        "availabilityCount": 4,
        "minParticipantsToBookNow": 2,
        "pricesByRate": [
            {
                "activityRateId": "2375672",
                "pricePerCategoryUnit": [
                    {
                        "id": "1160099",
                        "amount": {"amount": 300, "currency": "BRL"},
                    }
                ],
            }
        ],
    }
    if availability_changes:
        availability.update(availability_changes)
    for key in availability_remove:
        availability.pop(key, None)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/activity.json/913372"):
            return httpx.Response(200, request=request, json=metadata)
        if request.url.path.endswith("/availabilities"):
            return httpx.Response(200, request=request, json=[availability])
        raise AssertionError("solo selector must not reach quote endpoints in this fixture")

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:buracao": "913372"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-08-13 12:00:00",
        quote_checkout_enabled=quote_enabled,
    )
    return transport, seen


def test_bokun_http_transport_accepts_live_epoch_and_numeric_booking_fields() -> None:
    metadata = {
        "id": 913372,
        "title": "Buracão",
        "pricingCategories": [
            {"id": 1160099, "ticketCategory": "ADULT", "ageQualified": True}
        ],
    }
    availability = {
        "date": 1_794_960_000_000,
        "startTimeId": 445566,
        "startTime": "07:30",
        "soldOut": False,
        "unavailable": False,
        "availabilityCount": 4,
        "pricesByRate": [
            {
                "activityRateId": 2375672,
                "pricePerCategoryUnit": [
                    {
                        "id": 1160099,
                        "amount": {"amount": 300, "currency": "BRL"},
                    }
                ],
            }
        ],
    }
    responses = iter((metadata, [availability]))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=next(responses))

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:buracao": "913372"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-08-13 12:00:00",
    )

    result = transport(
        "activity",
        {
            "product_id": "product:buracao",
            "activity_date": "2026-11-18",
            "adults": 2,
            "children": 0,
        },
    )

    assert result["start_time_id"] == "445566"
    assert result["rate_id"] == "2375672"
    assert result["adult_pricing_category_id"] == "1160099"
    assert result["total_amount"] == "600.00"


def test_bokun_exact_solo_accepts_live_explicit_negative_flags_without_available() -> None:
    transport, seen = _solo_transport(
        metadata_changes={
            "pricingCategories": [
                {
                    "id": 1160099,
                    "ticketCategory": "ADULT",
                    "ageQualified": False,
                }
            ]
        },
        availability_changes={
            "date": 1_794_960_000_000,
            "startTimeId": 445566,
            "pricesByRate": [
                {
                    "activityRateId": 2375672,
                    "pricePerCategoryUnit": [
                        {
                            "id": 1160099,
                            "amount": {"amount": 300, "currency": "BRL"},
                        }
                    ],
                }
            ],
        },
        availability_remove=("available",),
    )

    result = transport(
        "activity",
        {
            "product_id": "product:buracao",
            "activity_date": "2026-11-18",
            "adults": 1,
            "children": 0,
            "expected_bokun_product_id": "913372",
            "expected_rate_id": "2375672",
            "expected_adult_category_id": "1160099",
            "ignore_minimum_participants": True,
        },
    )

    assert result["start_time_id"] == "445566"
    assert result["rate_id"] == "2375672"
    assert result["adult_pricing_category_id"] == "1160099"
    assert [request.method for request in seen] == ["GET", "GET"]


def test_bokun_http_transport_quotes_exact_solo_selection() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        call = len(seen)
        if call == 1:
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": 913372,
                    "title": "Buracão",
                    "pricingCategories": [
                        {
                            "id": "1160099",
                            "ticketCategory": "ADULT",
                            "ageQualified": False,
                        }
                    ],
                },
            )
        if call == 2:
            return httpx.Response(
                200,
                request=request,
                json=[
                    {
                        "date": "2026-11-18",
                        "startTimeId": "start-buracao",
                        "startTime": "07:30",
                        "available": True,
                        "availabilityCount": 4,
                        "minParticipantsToBookNow": 2,
                        "pricesByRate": [
                            {
                                "activityRateId": "2375672",
                                "pricePerCategoryUnit": [
                                    {
                                        "id": "1160099",
                                        "amount": {
                                            "amount": 300,
                                            "currency": "BRL",
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            )
        if call == 3:
            session_id = request.url.path.split("/session/", 1)[1]
            return httpx.Response(
                200,
                request=request,
                json={
                    "sessionId": session_id,
                    "size": 0,
                    "activityBookings": [],
                    "accommodationBookings": [],
                    "routeBookings": [],
                    "giftCardBookings": [],
                },
            )
        if call == 4:
            body = json.loads(request.content)
            assert body["activityId"] == "913372"
            assert body["rateId"] == "2375672"
            assert body["pricingCategoryBookings"] == [
                {"pricingCategoryId": "1160099"}
            ]
            session_id = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            return httpx.Response(
                200,
                request=request,
                json={
                    "uuid": session_id,
                    "activityBookings": [
                        {
                            "bookingId": "quote-solo",
                            "activityId": "913372",
                            "date": "2026-11-18",
                            "startTimeId": "start-buracao",
                            "rateId": "2375672",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": "passenger-solo",
                                    "pricingCategoryId": "1160099",
                                }
                            ],
                        }
                    ],
                },
            )
        assert call == 5
        return httpx.Response(
            200,
            request=request,
            json={
                "options": [
                    {"invoice": {"remainingAmountAsText": "R$ 304,50"}}
                ]
            },
        )

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:buracao": "913372"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-08-13 12:00:00",
        quote_checkout_enabled=True,
    )
    result = transport(
        "activity",
        {
            "product_id": "product:buracao",
            "activity_date": "2026-11-18",
            "adults": 1,
            "children": 0,
            "quote_scope": "f" * 64,
            "expected_bokun_product_id": "913372",
            "expected_rate_id": "2375672",
            "expected_adult_category_id": "1160099",
            "ignore_minimum_participants": True,
        },
    )

    assert result["available"] is True
    assert result["base_amount"] == "300.00"
    assert result["booking_fee_amount"] == "4.50"
    assert result["total_amount"] == "304.50"
    assert result["rate_id"] == "2375672"
    assert result["adult_pricing_category_id"] == "1160099"
    assert len(seen) == 5


def test_bokun_http_transport_availability_only_inspects_gets_without_quote() -> None:
    transport, seen = _solo_transport(quote_enabled=True)

    result = transport(
        "activity",
        {
            "product_id": "product:buracao",
            "activity_date": "2026-11-18",
            "adults": 1,
            "children": 0,
            "quote_scope": "a" * 64,
            "availability_only": True,
        },
    )

    assert result["available"] is False
    assert result["total_amount"] == "0.00"
    assert [request.method for request in seen] == ["GET", "GET"]
    assert not any("shopping-cart" in request.url.path for request in seen)


def test_bokun_http_transport_exact_solo_selection_bypasses_only_minimum() -> None:
    transport, seen = _solo_transport()

    result = transport(
        "activity",
        {
            "product_id": "product:buracao",
            "activity_date": "2026-11-18",
            "adults": 1,
            "children": 0,
            "expected_bokun_product_id": "913372",
            "expected_rate_id": "2375672",
            "expected_adult_category_id": "1160099",
            "ignore_minimum_participants": True,
        },
    )

    assert result["rate_id"] == "2375672"
    assert result["adult_pricing_category_id"] == "1160099"
    assert result["total_amount"] == "300.00"
    assert [request.method for request in seen] == ["GET", "GET"]


def test_bokun_http_transport_exact_agent_rate_accepts_provider_minimum_one() -> None:
    transport, seen = _solo_transport(
        availability_changes={"minParticipantsToBookNow": 1}
    )

    result = transport(
        "activity",
        {
            "product_id": "product:buracao",
            "activity_date": "2026-11-18",
            "adults": 1,
            "children": 0,
            "expected_bokun_product_id": "913372",
            "expected_rate_id": "2375672",
            "expected_adult_category_id": "1160099",
            "ignore_minimum_participants": True,
        },
    )

    assert result["rate_id"] == "2375672"
    assert result["adult_pricing_category_id"] == "1160099"
    assert result["total_amount"] == "300.00"
    assert [request.method for request in seen] == ["GET", "GET"]


@pytest.mark.parametrize(
    "payload_changes",
    (
        {"expected_bokun_product_id": "999999"},
        {"expected_rate_id": "999999"},
        {"expected_adult_category_id": "999999"},
    ),
)
def test_bokun_http_transport_exact_solo_selection_rejects_id_drift(
    payload_changes: dict[str, object],
) -> None:
    transport, _ = _solo_transport()
    payload = {
        "product_id": "product:buracao",
        "activity_date": "2026-11-18",
        "adults": 1,
        "children": 0,
        "expected_bokun_product_id": "913372",
        "expected_rate_id": "2375672",
        "expected_adult_category_id": "1160099",
        "ignore_minimum_participants": True,
    }
    payload.update(payload_changes)

    with pytest.raises(ProviderHTTPError, match="exact|selection|product"):
        transport("activity", payload)


@pytest.mark.parametrize(
    "availability_changes",
    (
        {"available": False},
        {"available": None},
        {"soldOut": True},
        {"availabilityCount": 0},
        {"availabilityCount": None},
        {"date": "2026-11-19"},
        {"date": None},
        {"minParticipantsToBookNow": 3},
        {
            "pricesByRate": [
                {
                    "activityRateId": "2375672",
                    "pricePerCategoryUnit": [
                        {
                            "id": "1160099",
                            "amount": {"amount": 300, "currency": "USD"},
                        }
                    ],
                }
            ]
        },
    ),
)
def test_bokun_http_transport_exact_solo_selection_keeps_other_gates_closed(
    availability_changes: dict[str, object],
) -> None:
    transport, _ = _solo_transport(availability_changes=availability_changes)

    with pytest.raises(
        ProviderHTTPError,
        match="exact|selection|unavailable|currency|date is invalid",
    ):
        transport(
            "activity",
            {
                "product_id": "product:buracao",
                "activity_date": "2026-11-18",
                "adults": 1,
                "children": 0,
                "expected_bokun_product_id": "913372",
                "expected_rate_id": "2375672",
                "expected_adult_category_id": "1160099",
                "ignore_minimum_participants": True,
            },
        )


@pytest.mark.parametrize("missing_flag", ("soldOut", "unavailable"))
def test_bokun_exact_solo_requires_both_live_negative_flags_when_available_is_absent(
    missing_flag: str,
) -> None:
    transport, _ = _solo_transport(
        availability_remove=("available", missing_flag),
    )

    with pytest.raises(ProviderHTTPError, match="exact|selection|unavailable"):
        transport(
            "activity",
            {
                "product_id": "product:buracao",
                "activity_date": "2026-11-18",
                "adults": 1,
                "children": 0,
                "expected_bokun_product_id": "913372",
                "expected_rate_id": "2375672",
                "expected_adult_category_id": "1160099",
                "ignore_minimum_participants": True,
            },
        )


def test_bokun_http_transport_rejects_minimum_bypass_without_exact_solo_shape() -> None:
    transport, _ = _solo_transport()
    with pytest.raises(ProviderHTTPError, match="selection"):
        transport(
            "activity",
            {
                "product_id": "product:buracao",
                "activity_date": "2026-11-18",
                "adults": 2,
                "children": 0,
                "expected_bokun_product_id": "913372",
                "expected_rate_id": "2375672",
                "expected_adult_category_id": "1160099",
                "ignore_minimum_participants": True,
            },
        )


def test_bokun_http_transport_rejects_multiple_public_adult_categories() -> None:
    with pytest.raises(ProviderHTTPError, match="adult pricing category is ambiguous"):
        BokunHTTPTransport._activity_booking_fields(
            {
                "startTimeId": "start",
                "pricesByRate": [],
            },
            meta={
                "pricingCategories": [
                    {
                        "id": "adult-public-a",
                        "ticketCategory": "ADULT",
                        "ageQualified": True,
                    },
                    {
                        "id": "adult-public-b",
                        "ticketCategory": "ADULT",
                        "ageQualified": True,
                    },
                ]
            },
            adults=1,
            children=0,
        )
