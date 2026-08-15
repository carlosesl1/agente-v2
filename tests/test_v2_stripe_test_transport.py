from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from v2_adapters.pix import PixInstructionAdapter
from v2_adapters.stripe import (
    StripeLinkAdapter,
    StripeTestHTTPTransport,
    StripeTestReconciliationTransport,
    WiseBRLRates,
    WiseExchangeRateReader,
)
from v2_adapters.stripe_checkout import stripe_product_presentation
from v2_adapters.wise import WiseInstructionAdapter
from v2_application.payments import (
    PaymentInitiationDisposition,
    PaymentInitiationWorker,
    PaymentService,
    SQLitePaymentInitiationStore,
)
from v2_contracts.localization import CustomerLanguage
from v2_contracts.payments import (
    BusinessUnit,
    CheckoutService,
    DueKind,
    PaymentDisplayDetails,
    PaymentMethod,
    PaymentObligation,
    PaymentSelection,
    StripeLinkRequest,
    StripePaymentLink,
    StripeReconciliationResult,
    StripeCreationStep,
    StripeStepReceipt,
    StripeStepStatus,
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
        business_unit=BusinessUnit.HOSTEL,
        display_details=PaymentDisplayDetails(
            service=CheckoutService.LODGING,
            public_label="Suíte Compartilhada",
            start_date=date(2026, 12, 20),
            end_date=date(2026, 12, 22),
            start_time=None,
            adults=1,
            children=0,
            reservation_total_minor=15300,
            package_component=False,
            customer_language=CustomerLanguage.PT_BR,
        ),
    )


def _transport(handler, *, key: str = TEST_KEY) -> StripeTestHTTPTransport:
    return StripeTestHTTPTransport(
        secret_keys={"stripe-account:hostel:test": key},
        base_url="https://api.stripe.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        wise_rates=lambda: WiseBRLRates(
            usd_brl=Decimal("5.0000"),
            eur_brl=Decimal("6.0000"),
        ),
    )


def test_wise_reader_fetches_exact_routes_and_applies_v1_adjustment() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        target = request.url.params["target"]
        rate = {"USD": "0.200000", "EUR": "0.160000"}[target]
        return httpx.Response(
            200,
            request=request,
            json=[{"source": "BRL", "target": target, "rate": rate}],
        )

    reader = WiseExchangeRateReader(
        api_token="wise-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert reader() == WiseBRLRates(
        usd_brl=Decimal("4.8000"),
        eur_brl=Decimal("6.0500"),
    )
    assert [request.url.path for request in seen] == ["/v1/rates", "/v1/rates"]
    assert [request.url.params["source"] for request in seen] == ["BRL", "BRL"]
    assert [request.url.params["target"] for request in seen] == ["USD", "EUR"]
    assert all(
        request.headers["Authorization"] == "Bearer wise-secret"
        for request in seen
    )


def test_wise_failure_happens_before_any_stripe_create() -> None:
    seen: list[httpx.Request] = []

    def failed_rates() -> WiseBRLRates:
        raise RuntimeError("Wise exchange-rate read failed")

    transport = StripeTestHTTPTransport(
        secret_keys={"stripe-account:hostel:test": TEST_KEY},
        base_url="https://api.stripe.invalid",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: seen.append(request) or httpx.Response(500)
            )
        ),
        wise_rates=failed_rates,
    )

    with pytest.raises(RuntimeError, match="Wise exchange-rate"):
        transport(_request())
    assert seen == []


def _product_payload(*, product_id: str = "prod_test_001") -> dict[str, object]:
    presentation = stripe_product_presentation(_request())
    return {
        "id": product_id,
        "livemode": False,
        "name": presentation.name,
        "description": presentation.description,
        "metadata": {
            "payment_id_sha256": (
                "753be5cfd85fdf54b74f42ed6a28eea417418711099ccd7e1f2109fab627635a"
            ),
            "economic_version": "2",
            "display_details_sha256": presentation.details_sha256,
        },
    }


def test_product_price_and_link_use_closed_forms_and_deterministic_keys() -> None:
    seen: list[httpx.Request] = []
    presentation = stripe_product_presentation(_request())

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        form = parse_qs(request.content.decode(), keep_blank_values=True)
        if request.url.path == "/v1/products":
            assert form == {
                "name": [presentation.name],
                "description": [presentation.description],
                "metadata[payment_id_sha256]": [
                    "753be5cfd85fdf54b74f42ed6a28eea417418711099ccd7e1f2109fab627635a"
                ],
                "metadata[economic_version]": ["2"],
                "metadata[display_details_sha256]": [
                    presentation.details_sha256
                ],
            }
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "prod_test_001",
                    "livemode": False,
                    "name": presentation.name,
                    "description": presentation.description,
                    "metadata": {
                        "payment_id_sha256": (
                            "753be5cfd85fdf54b74f42ed6a28eea417418711099ccd7e1f2109fab627635a"
                        ),
                        "economic_version": "2",
                        "display_details_sha256": presentation.details_sha256,
                    },
                    "created": 1_722_000_000,
                },
            )
        if request.url.path == "/v1/prices":
            assert form == {
                "product": ["prod_test_001"],
                "currency": ["brl"],
                "unit_amount": ["15300"],
                "currency_options[usd][unit_amount]": ["3060"],
                "currency_options[eur][unit_amount]": ["2550"],
            }
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "price_test_001",
                    "livemode": False,
                    "active": True,
                },
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
                "metadata[display_details_sha256]": [
                    presentation.details_sha256
                ],
            }
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
                        "display_details_sha256": presentation.details_sha256,
                    },
                },
            )
        pytest.fail("accepted Payment Link must not trigger synchronous read-back")

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
    assert len(seen) == 3
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


@pytest.mark.parametrize("mismatch", ["name", "description", "display_hash"])
def test_product_display_mismatch_stops_before_price(mismatch: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = _product_payload()
        if mismatch == "name":
            payload["name"] = "generic payment"
        elif mismatch == "description":
            payload["description"] = "missing commercial facts"
        else:
            metadata = payload["metadata"]
            assert isinstance(metadata, dict)
            metadata["display_details_sha256"] = "0" * 64
        return httpx.Response(200, request=request, json=payload)

    with pytest.raises(RuntimeError, match="product.*match"):
        _transport(handler)(_request())
    assert [request.url.path for request in seen] == ["/v1/products"]


def test_accepted_payment_link_is_monotonic_despite_later_readback_mismatch() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/products":
            return httpx.Response(
                200,
                request=request,
                json=_product_payload(),
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

    result = _transport(handler)(_request())

    assert result == {
        "link_id": "plink_test_001",
        "url": "https://buy.stripe.com/test_link_001",
    }
    assert [request.method for request in seen] == ["POST", "POST", "POST"]


def test_authenticated_or_query_payment_link_url_fails_closed_without_echo() -> None:
    private_query = "private-client-secret-sentinel"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices":
            return httpx.Response(
                200,
                request=request,
                json={"id": "price_test_001", "livemode": False},
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "plink_test_private_query",
                "url": f"https://buy.stripe.com/test?secret={private_query}",
                "active": True,
                "livemode": False,
            },
        )

    with pytest.raises(RuntimeError, match="canonical") as captured:
        _transport(handler)(_request())
    assert private_query not in str(captured.value)


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

    private_origin = "private-origin-sentinel"
    for transport_type in (
        StripeTestHTTPTransport,
        StripeTestReconciliationTransport,
    ):
        for base_url in (
            f"https://user:{private_origin}@api.stripe.com",
            f"https://api.stripe.com:{private_origin}",
            "https://api.stripe.com:444",
        ):
            with pytest.raises(ValueError, match="canonical Stripe API") as captured:
                transport_type(
                    secret_keys={"stripe-account:hostel:test": TEST_KEY},
                    base_url=base_url,
                    client=httpx.Client(transport=httpx.MockTransport(handler)),
                )
            assert private_origin not in str(captured.value)
    assert calls == 0


def test_malformed_or_root_payment_link_url_fails_closed_without_echo() -> None:
    private_port = "private-port-sentinel"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices":
            return httpx.Response(
                200,
                request=request,
                json={"id": "price_test_001", "livemode": False},
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "plink_test_private_port",
                "url": f"https://buy.stripe.com:{private_port}/x",
                "active": True,
                "livemode": False,
            },
        )

    with pytest.raises(RuntimeError, match="canonical") as captured:
        _transport(handler)(_request())
    assert private_port not in str(captured.value)

    with pytest.raises(ValueError, match="canonical") as receipt_error:
        StripeStepReceipt(
            step=StripeCreationStep.PAYMENT_LINK,
            status=StripeStepStatus.ACCEPTED,
            account_profile_id="stripe-account:hostel:test",
            expected_metadata_hash="a" * 64,
            idempotency_key="stripe-link:test:payment_link",
            provider_object_id="plink_test_private_port",
            canonical_url=f"https://buy.stripe.com:{private_port}/x",
        )
    assert private_port not in str(receipt_error.value)

    offer = StripePaymentLink(
        payment_id="payment:stripe:url-contract:001",
        reservation_anchor_id="anchor:stripe:url-contract:001",
        account_profile_id="stripe-account:hostel:test",
        economic_version=1,
        public_url="https://buy.stripe.com/test_valid",
        provider_reference_fingerprint="b" * 64,
        receipt_hash="c" * 64,
    )
    with pytest.raises(ValueError, match="canonical"):
        replace(offer, public_url="https://buy.stripe.com/")


def test_stripe_request_offer_and_reconciliation_repr_redact_private_values() -> None:
    request = replace(
        _request(),
        subscriber_fingerprint="a" * 64,
        initiation_id="stripe-init:opaque:001",
        journal_worker_id="journal-worker-private-sentinel",
        journal_fencing_token=73,
    )
    offer = StripePaymentLink(
        payment_id=request.payment_id,
        reservation_anchor_id=request.reservation_anchor_id,
        account_profile_id=request.account_profile_id,
        economic_version=request.economic_version,
        public_url="https://buy.stripe.com/private-link-sentinel",
        provider_reference_fingerprint="b" * 64,
        receipt_hash="c" * 64,
        customer_language=CustomerLanguage.PT_BR,
    )
    result = StripeReconciliationResult(offer=offer, manual_review=False)
    receipt = StripeStepReceipt(
        step=StripeCreationStep.PRODUCT,
        status=StripeStepStatus.INTENT,
        account_profile_id=request.account_profile_id,
        expected_metadata_hash="d" * 64,
        idempotency_key="stripe-link:private-repr:product",
    )
    rendered = repr((request, offer, result, receipt))
    assert "offer=" not in repr(result)
    assert "account_profile_id=" not in repr(receipt)
    assert "journal_fencing_token=" not in repr(request)

    private_values = (
        request.payment_id,
        request.reservation_anchor_id,
        request.account_profile_id,
        request.idempotency_key,
        request.subscriber_fingerprint,
        request.initiation_id,
        request.journal_worker_id,
        request.display_details.public_label,
        offer.public_url,
        offer.provider_reference_fingerprint,
        offer.receipt_hash,
    )
    assert len(private_values) == len(set(private_values))
    for private_value in private_values:
        assert private_value not in rendered


def test_partial_creation_is_manual_review_and_never_recreates_product(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/v1/products":
            form = parse_qs(request.content.decode(), keep_blank_values=True)
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "prod_test_partial",
                    "livemode": False,
                    "name": form["name"][0],
                    "description": form["description"][0],
                    "metadata": {
                        "payment_id_sha256": form[
                            "metadata[payment_id_sha256]"
                        ][0],
                        "economic_version": form[
                            "metadata[economic_version]"
                        ][0],
                        "display_details_sha256": form[
                            "metadata[display_details_sha256]"
                        ][0],
                    },
                },
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
            display_details=_request().display_details,
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
