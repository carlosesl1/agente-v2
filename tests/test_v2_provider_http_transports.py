from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx

from v2_adapters.manychat import ManyChatDeliveryAdapter, ManyChatTransportResponse
from v2_adapters.provider_http import (
    BokunHTTPTransport,
    CloudbedsHTTPTransport,
    ManyChatHTTPTransport,
)


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
        {"product_id": "tour:buracao", "activity_date": "2026-08-10", "participants": 2},
    )

    assert result == {
        "product_id": "tour:buracao",
        "bokun_product_id": "913372",
        "start_time_id": "start-1",
        "rate_id": "rate-1",
        "pricing_category_id": "857489",
        "product_public_name": "Roteiro do Buracão",
        "total_amount": "600.00",
        "currency": "BRL",
        "available": True,
    }
    assert len(seen) == 2
    assert all(request.headers["X-Bokun-AccessKey"] == "access" for request in seen)
    assert all(request.headers["X-Bokun-Date"] == "2026-07-23 12:00:00" for request in seen)
    assert all(request.headers["X-Bokun-Signature"] for request in seen)
    assert seen[0].url.path == "/activity.json/913372"
    assert seen[1].url.path == "/activity.json/913372/availabilities"


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
                        "phone": "+5575999999999",
                        "country": "BR",
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
        "phone_e164": "+5575999999999",
        "country_code": "BR",
    }
    assert receipt.provider_message_id == "mc-123"
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

    assert receipt == "provider-receipt-1"
    assert transport.calls == [
        {
            "subscriber_id": "1873018537",
            "text": "Olá do boundary",
            "idempotency_key": "public:message-1",
        }
    ]
