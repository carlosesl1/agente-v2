from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from v2_adapters.cloudbeds import CloudbedsReadAdapter
from v2_adapters.manychat import ManyChatDeliveryAdapter, ManyChatTransportResponse
from v2_adapters.provider_http import (
    BokunHTTPTransport,
    CloudbedsHTTPTransport,
    ManyChatHTTPTransport,
    ProviderHTTPError,
)
from v2_contracts.providers import ReadKind, ReadRequest


def test_cloudbeds_transport_calls_native_read_endpoints_and_normalizes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("getAvailableRoomTypes"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "data": [{
                        "propertyCurrency": {"currencyCode": "BRL"},
                        "propertyRooms": [
                        {
                            "roomTypeID": "rt-1",
                            "roomRateID": "rr-1",
                            "roomTypeName": "Suíte Serra",
                            "roomsAvailable": 2,
                            "totalRate": "450.00",
                        }
                        ],
                    }]
                },
            )
        return httpx.Response(200, json={"data": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = CloudbedsHTTPTransport(
        api_key="cloudbeds-secret",
        property_id="property-1",
        base_url="https://api.cloudbeds.invalid",
        client=client,
    )

    result = transport(
        "lodging",
        {
            "check_in": "2026-08-10",
            "check_out": "2026-08-12",
            "adults": 2,
            "children": 0,
        },
    )

    assert result == {
        "options": [
            {
                "check_in": "2026-08-10",
                "check_out": "2026-08-12",
                "adults": 2,
                "children": 0,
                "room_type_id": "rt-1",
                "room_rate_id": "rr-1",
                "room_public_name": "Suíte Serra",
                "total_amount": "450.00",
                "currency": "BRL",
                "available_units": 2,
            }
        ]
    }
    assert [request.url.path for request in seen] == [
        "/api/v1.3/getAvailableRoomTypes",
        "/api/v1.2/getRatePlans",
    ]
    assert all(request.headers["Authorization"] == "Bearer cloudbeds-secret" for request in seen)
    query = parse_qs(seen[0].url.query.decode())
    assert query["propertyID"] == ["property-1"]
    assert query["detailedRates"] == ["true"]


def test_cloudbeds_room_description_exposes_selected_public_room_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("getAvailableRoomTypes"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "data": [
                        {
                            "propertyCurrency": {"currencyCode": "BRL"},
                            "propertyRooms": [
                                {
                                    "roomTypeID": "rt-suite",
                                    "roomRateID": "rr-suite",
                                    "roomTypeName": "Suite Casal",
                                    "roomsAvailable": 1,
                                    "totalRate": "450.00",
                                }
                            ],
                        }
                    ],
                },
            )
        if request.url.path.endswith("getRoomTypes"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "data": [
                        {
                            "roomTypeID": "rt-suite",
                            "roomTypeName": "Suite Casal",
                            "roomTypeDescription": "Quarto com cama de casal e banheiro privativo.",
                            "roomTypeFeatures": ["Wi-Fi", "Banheiro privativo"],
                        }
                    ],
                },
            )
        return httpx.Response(200, request=request, json={"data": []})

    transport = CloudbedsHTTPTransport(
        api_key="cloudbeds-secret",
        property_id="property-1",
        base_url="https://api.cloudbeds.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter = CloudbedsReadAdapter(
        transport=transport,
        clock=SimpleNamespace(
            now=lambda: datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        ),
        ttl=timedelta(minutes=5),
    )
    lodging = adapter.read(
        ReadRequest(
            request_id="read:lodging:room-description",
            kind=ReadKind.LODGING,
            check_in=date(2026, 8, 20),
            check_out=date(2026, 8, 22),
            adults=2,
            children=0,
        )
    )
    description = adapter.read(
        ReadRequest(
            request_id="read:room-description",
            kind=ReadKind.ROOM_DESCRIPTION,
            offer_id=lodging.public_payload["options"][0]["offer_id"],
        )
    )

    assert description.public_payload == {
        "offer_id": lodging.public_payload["options"][0]["offer_id"],
        "room_public_name": "Suite Casal",
        "description": "Quarto com cama de casal e banheiro privativo.",
        "amenities": ["Wi-Fi", "Banheiro privativo"],
    }


@pytest.mark.parametrize("invalid_name", (123, " Suite Casal "))
def test_cloudbeds_room_description_rejects_noncanonical_public_name(
    invalid_name: object,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "data": [
                    {
                        "roomTypeID": "rt-suite",
                        "roomTypeName": invalid_name,
                        "roomTypeDescription": "Descrição válida.",
                    }
                ],
            },
        )

    transport = CloudbedsHTTPTransport(
        api_key="cloudbeds-secret",
        property_id="property-1",
        base_url="https://api.cloudbeds.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    transport._offer_room_types["offer:test"] = "rt-suite"

    with pytest.raises(ProviderHTTPError, match="public name"):
        transport("room_description", {"offer_id": "offer:test"})


def test_bokun_transport_signs_exact_native_paths_and_uses_canonical_product_map() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/availabilities"):
            return httpx.Response(
                200,
                json=[
                    {
                        "date": "2026-08-10",
                        "startTimeId": "start-1",
                        "available": True,
                        "availabilityCount": 6,
                        "pricesByRate": [
                            {
                                "activityRateId": "rate-1",
                                "pricePerCategoryUnit": [
                                    {"id": "857489", "amount": {"amount": 300, "currency": "BRL"}}
                                ],
                            }
                        ],
                    }
                ],
            )
        return httpx.Response(
            200,
            json={
                "id": 913372,
                "title": "Roteiro do Buracão",
                "description": "Dia inteiro",
                "pricingCategories": [{"id": 857489, "ticketCategory": "ADULT"}],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"tour:buracao": "913372"},
        base_url="https://api.bokun.invalid",
        client=client,
        timestamp=lambda: "2026-07-23 12:00:00",
    )

    result = transport(
        "activity",
        {
            "product_id": "tour:buracao",
            "activity_date": "2026-08-10",
            "participants": 2,
            "locale": "en",
        },
    )

    assert result == {
        "product_id": "tour:buracao",
        "bokun_product_id": "913372",
        "start_time_id": "start-1",
        "rate_id": "rate-1",
        "adult_pricing_category_id": "857489",
        "product_public_name": "Roteiro do Buracão",
        "total_amount": "600.00",
        "currency": "BRL",
        "available": False,
    }
    assert len(seen) == 2
    assert all(request.headers["X-Bokun-AccessKey"] == "access" for request in seen)
    assert all(request.headers["X-Bokun-Date"] == "2026-07-23 12:00:00" for request in seen)
    assert all(request.headers["X-Bokun-Signature"] for request in seen)
    assert seen[0].url.path == "/activity.json/913372"
    assert seen[1].url.path == "/activity.json/913372/availabilities"
    assert parse_qs(seen[0].url.query.decode())["lang"] == ["en"]


def test_bokun_transport_quotes_fee_inclusive_checkout_before_offering() -> None:
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
                        {"id": 857489, "ticketCategory": "ADULT"}
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
                                        "id": "857489",
                                        "amount": {
                                            "amount": 330,
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
            assert request.method == "GET"
            assert request.url.path.startswith("/shopping-cart.json/session/v2-quote-")
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
            assert request.method == "POST"
            assert request.url.path.endswith("/activity")
            session_id = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            assert json.loads(request.content) == {
                "activityId": "912303",
                "date": "2026-11-18",
                "startTimeId": "start-4ps",
                "rateId": "rate-4ps",
                "pricingCategoryBookings": [
                    {"pricingCategoryId": "857489"}
                ],
            }
            return httpx.Response(
                200,
                request=request,
                json={
                    "uuid": session_id,
                    "activityBookings": [
                        {
                            "bookingId": "quote-activity-1",
                            "activityId": "912303",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": "quote-passenger-1",
                                    "pricingCategoryId": "857489",
                                }
                            ],
                        }
                    ],
                },
            )
        assert call == 5
        assert request.method == "GET"
        assert request.url.path.startswith(
            "/checkout.json/options/shopping-cart/v2-quote-"
        )
        return httpx.Response(
            200,
            request=request,
            json={
                "options": [
                    {
                        "formattedAmount": "R$ 330,00",
                        "invoice": {
                            "remainingAmountAsText": "R$ 334,95"
                        },
                    }
                ]
            },
        )

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"tour:4ps": "912303"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-27 18:00:00",
        quote_checkout_enabled=True,
    )

    result = transport(
        "activity",
        {
            "product_id": "tour:4ps",
            "activity_date": "2026-11-18",
            "participants": 1,
            "quote_scope": "a" * 64,
        },
    )

    assert result["available"] is True
    assert result["base_amount"] == "330.00"
    assert result["booking_fee_amount"] == "4.95"
    assert result["total_amount"] == "334.95"
    assert result["price_includes_booking_fee"] is True
    assert [request.method for request in seen] == ["GET", "GET", "GET", "POST", "GET"]
    assert seen[3].headers["X-Idempotency-Key"].endswith(":cart")


def test_bokun_fee_quote_reuses_existing_deterministic_cart_without_post() -> None:
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
                        {"id": "857489", "ticketCategory": "ADULT"}
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
                        "pricesByRate": [
                            {
                                "activityRateId": "rate-4ps",
                                "pricePerCategoryUnit": [
                                    {
                                        "id": "857489",
                                        "amount": {
                                            "amount": 330,
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
                    "activityBookings": [
                        {
                            "id": "quote-activity-existing",
                            "activity": {"id": 912303},
                            "pricingCategoryBookings": [
                                {
                                    "id": "quote-passenger-existing",
                                    "pricingCategoryId": 857489,
                                    "pricingCategory": {"id": 857489},
                                }
                            ],
                        }
                    ],
                },
            )
        assert call == 4
        return httpx.Response(
            200,
            request=request,
            json={
                "options": [
                    {
                        "invoice": {
                            "remainingAmountAsText": "R$ 334,95"
                        }
                    }
                ]
            },
        )

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"tour:4ps": "912303"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-27 18:00:00",
        quote_checkout_enabled=True,
    )

    result = transport(
        "activity",
        {
            "product_id": "tour:4ps",
            "activity_date": "2026-11-18",
            "participants": 1,
            "quote_scope": "b" * 64,
        },
    )

    assert result["total_amount"] == "334.95"
    assert [request.method for request in seen] == ["GET", "GET", "GET", "GET"]


def test_bokun_transport_requires_capacity_for_all_participants() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/availabilities"):
            return httpx.Response(
                200,
                request=request,
                json=[
                    {
                        "capacityCount": 1,
                        "available": True,
                        "date": "2026-08-10",
                        "pricesByRate": [
                            {
                                "pricePerCategoryUnit": [
                                    {"amount": {"amount": 300, "currency": "BRL"}}
                                ]
                            }
                        ],
                    }
                ],
            )
        return httpx.Response(
            200,
            request=request,
            json={"id": 913372, "title": "Roteiro do Buracão"},
        )

    transport = BokunHTTPTransport(
        access_key="access",
        secret_key="secret",
        product_map={"product:buracao": "913372"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-23 12:00:00",
    )

    result = transport(
        "activity",
        {
            "activity_date": "2026-08-10",
            "participants": 2,
            "product_id": "product:buracao",
        },
    )

    assert result["available"] is False
    assert result["product_id"] == "product:buracao"


def test_manychat_transport_normalizes_profile_and_confirms_native_send() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": 1873018537,
                        "first_name": "Carlos",
                        "last_name": "Eduardo",
                        "email": "carlos@example.invalid",
                        "phone": "+1" + "202" + "555" + "0123",
                        "whatsapp_phone": "+55" + "75" + "9" * 9,
                        "country": None,
                        "gender": None,
                    }
                },
            )
        body = json.loads(request.content)
        assert body["subscriber_id"] == "1873018537"
        assert body["data"]["content"]["messages"] == [{"type": "text", "text": "Olá"}]
        return httpx.Response(200, json={"status": "success", "request_id": "mc-123"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = ManyChatHTTPTransport(
        api_key="manychat-secret",
        base_url="https://api.manychat.invalid",
        client=client,
    )

    profile = transport.fetch_profile("1873018537")
    receipt = transport.send_text(
        subscriber_id="1873018537",
        text="Olá",
        idempotency_key="public-idempotency:abc",
    )

    assert profile == {
        "subscriber_id": "1873018537",
        "full_name": "Carlos Eduardo",
        "email": "carlos@example.invalid",
        "phone_e164": "+55" + "75" + "9" * 9,
        "country_code": "BR",
        "gender": "m",
    }

    assert receipt.provider_request_id == "mc-123"
    assert receipt.dispatch_correlation_id.startswith("manychat-correlation:")
    assert all(request.headers["Authorization"] == "Bearer manychat-secret" for request in seen)
    assert seen[0].url.path.endswith("/fb/subscriber/getInfo")
    assert seen[1].url.path.endswith("/fb/sending/sendContent")


def test_manychat_delivery_adapter_accepts_boundary_dispatch_claim_shape() -> None:
    class RecordingTransport:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def send_text(self, **values: str) -> ManyChatTransportResponse:
            self.calls.append(values)
            return ManyChatTransportResponse("provider-receipt-1")

    transport = RecordingTransport()
    adapter = ManyChatDeliveryAdapter(transport)
    claim = SimpleNamespace(
        message_id="public:message-1",
        subscriber_id="1873018537",
        chunk=SimpleNamespace(text="Olá do boundary"),
    )

    receipt = adapter.send(claim)

    assert receipt.state.value == "accepted_by_manychat"
    assert receipt.operations == ("send_content",)
    assert receipt.provider_request_ids == ("provider-receipt-1",)
    assert len(receipt.dispatch_correlation_ids) == 1
    assert transport.calls == [
        {
            "subscriber_id": "1873018537",
            "text": "Olá do boundary",
            "idempotency_key": "public:message-1",
        }
    ]


def test_manychat_success_without_external_id_keeps_only_local_correlation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"status": "success"},
        )

    transport = ManyChatHTTPTransport(
        api_key="manychat-secret",
        base_url="https://api.manychat.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    response = transport.set_custom_field(
        subscriber_id="1873018537",
        field_id=101,
        field_value="Resposta da Maya.",
        idempotency_key="public:message-1:field",
    )

    assert response.provider_request_id is None
    assert response.dispatch_correlation_id.startswith("manychat-correlation:")
    assert not hasattr(response, "provider_message_id")
