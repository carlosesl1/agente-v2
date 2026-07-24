from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from v2_adapters.pix import PixInstructionAdapter
from v2_adapters.stripe import StripeLinkAdapter, StripeTestHTTPTransport
from v2_adapters.wise import WiseInstructionAdapter
from v2_application.payments import (
    PaymentInitiationDisposition,
    PaymentInitiationWorker,
    PaymentService,
    SQLitePaymentInitiationStore,
)
from v2_contracts.payments import (
    BusinessUnit,
    DueKind,
    PaymentMethod,
    PaymentObligation,
    PaymentSelection,
    StripeLinkRequest,
)


TEST_KEY = "rk_" + "test_v2_scoped_key"
LIVE_KEY = "rk_" + "live_v2_forbidden_key"
SUBSCRIBER_FINGERPRINT = "a" * 64
NOW = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)
RESULT_KEY = b"stripe-result-test-key-000000001"


def _request() -> StripeLinkRequest:
    return StripeLinkRequest(
        payment_id="payment:hostel:stripe:001",
        reservation_anchor_id="anchor:cloudbeds:001",
        account_profile_id="stripe-account:hostel:test",
        amount_minor=15300,
        currency="BRL",
        economic_version=2,
        idempotency_key="stripe-link:payment:hostel:stripe:001:v2",
        subscriber_fingerprint=SUBSCRIBER_FINGERPRINT,
        payment_percentage=100,
    )


def _transport(handler, *, key: str = TEST_KEY) -> StripeTestHTTPTransport:
    return StripeTestHTTPTransport(
        secret_keys={"stripe-account:hostel:test": key},
        base_url="https://api.stripe.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_product_price_and_link_use_closed_forms_and_deterministic_keys() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        form = parse_qs(request.content.decode(), keep_blank_values=True)
        if request.url.path == "/v1/products":
            assert form == {
                "name": ["V2 hostel reservation payment"],
                "metadata[payment_id_sha256]": [
                    "753be5cfd85fdf54b74f42ed6a28eea417418711099ccd7e1f2109fab627635a"
                ],
                "metadata[economic_version]": ["2"],
            }
            return httpx.Response(
                200,
                request=request,
                json={"id": "prod_test_001", "livemode": False},
            )
        if request.url.path == "/v1/prices":
            assert form == {
                "product": ["prod_test_001"],
                "currency": ["brl"],
                "unit_amount": ["15300"],
            }
            return httpx.Response(
                200,
                request=request,
                json={"id": "price_test_001", "livemode": False},
            )
        if request.url.path == "/v1/payment_links":
            assert form == {
                "line_items[0][price]": ["price_test_001"],
                "line_items[0][quantity]": ["1"],
                "metadata[reservation_anchor_sha256]": [
                    "761bedf72cf75935ffe67cf434d904ace0cb355ce6af5f00748c87ce07462531"
                ],
                "metadata[subscriber_sha256]": [SUBSCRIBER_FINGERPRINT],
                "metadata[business_unit]": ["hostel"],
                "metadata[economic_version]": ["2"],
                "metadata[payment_percentage]": ["100"],
            }
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "plink_test_001",
                    "url": "https://buy.stripe.com/test_link_001",
                    "active": True,
                    "livemode": False,
                },
            )
        assert request.method == "GET"
        assert request.url.path == "/v1/payment_links/plink_test_001"
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "plink_test_001",
                "url": "https://buy.stripe.com/test_link_001",
                "active": True,
                "livemode": False,
                "metadata": {
                    "reservation_anchor_sha256": (
                        "761bedf72cf75935ffe67cf434d904ace0cb355ce6af5f00748c87ce07462531"
                    ),
                    "subscriber_sha256": SUBSCRIBER_FINGERPRINT,
                    "business_unit": "hostel",
                    "economic_version": "2",
                    "payment_percentage": "100",
                },
            },
        )

    result = _transport(handler)(_request())

    assert result == {
        "link_id": "plink_test_001",
        "url": "https://buy.stripe.com/test_link_001",
    }
    assert [request.headers["Idempotency-Key"] for request in seen[:3]] == [
        _request().idempotency_key + ":product",
        _request().idempotency_key + ":price",
        _request().idempotency_key + ":payment_link",
    ]
    assert "Idempotency-Key" not in seen[3].headers
    assert all(request.headers["Authorization"] == f"Bearer {TEST_KEY}" for request in seen)
    wire = b"&".join(request.content for request in seen).decode()
    assert _request().payment_id not in wire
    assert _request().reservation_anchor_id not in wire


def test_livemode_response_is_never_accepted_as_a_test_effect() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"id": "prod_live_forbidden", "livemode": True},
        )

    with pytest.raises(RuntimeError, match="test mode"):
        _transport(handler)(_request())
    assert [request.url.path for request in seen] == ["/v1/products"]


def test_payment_link_readback_mismatch_is_unknown() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/products":
            return httpx.Response(
                200,
                request=request,
                json={"id": "prod_test_001", "livemode": False},
            )
        if request.url.path == "/v1/prices":
            return httpx.Response(
                200,
                request=request,
                json={"id": "price_test_001", "livemode": False},
            )
        if request.method == "POST":
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "plink_test_001",
                    "url": "https://buy.stripe.com/test_link_001",
                    "active": True,
                    "livemode": False,
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "different",
                "url": "https://buy.stripe.com/different",
                "active": True,
                "livemode": False,
                "metadata": {},
            },
        )

    with pytest.raises(RuntimeError, match="read-back"):
        _transport(handler)(_request())
    assert [request.method for request in seen] == ["POST", "POST", "POST", "GET"]


def test_live_key_and_noncanonical_api_base_fail_before_http() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    with pytest.raises(ValueError, match="test key"):
        _transport(handler, key=LIVE_KEY)(_request())
    assert calls == 0

    with pytest.raises(ValueError, match="canonical Stripe API"):
        StripeTestHTTPTransport(
            secret_keys={"stripe-account:hostel:test": TEST_KEY},
            base_url="https://api.stripe.com/v1/live",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
    assert calls == 0


def test_partial_creation_is_manual_review_and_never_recreates_product(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/v1/products":
            return httpx.Response(
                200,
                request=request,
                json={"id": "prod_test_partial", "livemode": False},
            )
        raise httpx.ReadTimeout("after Product creation", request=request)

    stripe = StripeLinkAdapter(
        transport=_transport(handler),
        account_profiles={
            BusinessUnit.HOSTEL: "stripe-account:hostel:test",
            BusinessUnit.AGENCY: "stripe-account:agency:test",
        },
        enabled=True,
        subscriber_id="1873018537",
        payment_percentages={
            BusinessUnit.HOSTEL: 100,
            BusinessUnit.AGENCY: 100,
        },
    )

    class Knowledge:
        def pix_instruction(self, profile: str) -> str:
            return "Pix fechado neste teste."

    payments = PaymentService(
        stripe=stripe,
        wise=WiseInstructionAdapter(
            instructions={"receiver:hostel": "Wise fechado neste teste."}
        ),
        pix=PixInstructionAdapter(knowledge=Knowledge()),
    )
    store = SQLitePaymentInitiationStore(
        tmp_path / "stripe-partial.sqlite3",
        result_encryption_key=RESULT_KEY,
    )
    selection = PaymentSelection(
        PaymentObligation(
            payment_id="payment:hostel:stripe:partial",
            reservation_anchor_id="anchor:cloudbeds:partial",
            business_unit=BusinessUnit.HOSTEL,
            amount_minor=15300,
            currency="BRL",
            due_kind=DueKind.PREPAYMENT,
            economic_version=1,
            receiver_profile_id="receiver:hostel",
        ),
        PaymentMethod.STRIPE,
    )
    store.enqueue(selection, now=NOW)
    worker = PaymentInitiationWorker(
        store=store,
        payments=payments,
        worker_id="worker:stripe-test-link",
        lease_ttl=timedelta(seconds=30),
    )

    first = worker.run_once(now=NOW + timedelta(seconds=1))
    second = worker.run_once(now=NOW + timedelta(seconds=2))

    assert first.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert second.disposition is PaymentInitiationDisposition.IDLE
    assert seen == ["/v1/products", "/v1/prices"]
    assert store.dispatch_slots(selection) == 1
