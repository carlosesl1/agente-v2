from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from v2_adapters.bokun import BokunReadAdapter
from v2_adapters.provider_http import BokunHTTPTransport, ProviderHTTPError
from v2_contracts.providers import ReadKind, ReadRequest


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


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
                            "amount": {"amount": 300, "currency": "BRL"},
                        },
                        {
                            "id": "child-1",
                            "amount": {"amount": 150, "currency": "USD"},
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
