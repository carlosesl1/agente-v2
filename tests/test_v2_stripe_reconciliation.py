from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import inspect
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs

import httpx
import pytest

import v2_adapters.stripe as stripe_module
from v2_adapters.pix import PixInstructionAdapter
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
    StripeCreationStep,
    StripeLinkRequest,
    StripePaymentLink,
    StripeReconciliationResult,
    StripeStepReceipt,
    StripeStepStatus,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
RESULT_KEY = b"stripe-reconcile-test-key-000001"
TEST_KEY = "rk_" + "test_reconciliation_key"
PROFILE = "stripe-account:hostel:test"
SUBSCRIBER_ID = "subscriber-test-only-001"
SUBSCRIBER_HASH = hashlib.sha256(SUBSCRIBER_ID.encode()).hexdigest()


DETAILS = PaymentDisplayDetails(
    service=CheckoutService.LODGING,
    public_label="Suíte Teste",
    start_date=date(2026, 12, 20),
    end_date=date(2026, 12, 22),
    start_time=None,
    adults=1,
    children=0,
    reservation_total_minor=15300,
    package_component=False,
    customer_language=CustomerLanguage.PT_BR,
)
OBLIGATION = PaymentObligation(
    payment_id="payment:stripe:reconciliation:001",
    reservation_anchor_id="anchor:stripe:reconciliation:001",
    business_unit=BusinessUnit.HOSTEL,
    amount_minor=15300,
    currency="BRL",
    due_kind=DueKind.PREPAYMENT,
    economic_version=1,
    receiver_profile_id="receiver:hostel",
    display_details=DETAILS,
)
SELECTION = PaymentSelection(OBLIGATION, PaymentMethod.STRIPE)


class Knowledge:
    def pix_instruction(self, profile: str) -> str:
        return "Pix fechado neste teste."


def _request() -> StripeLinkRequest:
    return StripeLinkRequest(
        payment_id=OBLIGATION.payment_id,
        reservation_anchor_id=OBLIGATION.reservation_anchor_id,
        account_profile_id=PROFILE,
        amount_minor=OBLIGATION.amount_minor,
        currency=OBLIGATION.currency,
        economic_version=OBLIGATION.economic_version,
        idempotency_key=f"stripe-link:{OBLIGATION.payment_id}:v1",
        subscriber_fingerprint=SUBSCRIBER_HASH,
        payment_percentage=100,
        business_unit=BusinessUnit.HOSTEL,
        display_details=DETAILS,
    )


def _product_payload(*, product_id: str = "prod_test_reconcile") -> dict[str, object]:
    request = _request()
    presentation = stripe_product_presentation(request)
    return {
        "id": product_id,
        "object": "product",
        "livemode": False,
        "active": True,
        "name": presentation.name,
        "description": presentation.description,
        "metadata": {
            "payment_id_sha256": hashlib.sha256(request.payment_id.encode()).hexdigest(),
            "economic_version": "1",
            "display_details_sha256": presentation.details_sha256,
        },
    }


def _build_worker(
    tmp_path: Path,
    *,
    write_handler,
    read_handler,
    clock=None,
    effect_guard=None,
) -> tuple[PaymentInitiationWorker, SQLitePaymentInitiationStore, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def capture_write(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return write_handler(request)

    def capture_read(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return read_handler(request)

    store = SQLitePaymentInitiationStore(
        tmp_path / "stripe-reconciliation.sqlite3",
        result_encryption_key=RESULT_KEY,
    )
    write_transport = stripe_module.StripeTestHTTPTransport(
        secret_keys={PROFILE: TEST_KEY},
        base_url="https://api.stripe.invalid",
        client=httpx.Client(transport=httpx.MockTransport(capture_write)),
        journal=store,
        clock=clock or (lambda: NOW + timedelta(seconds=1)),
        effect_guard=effect_guard,
    )
    read_transport = stripe_module.StripeTestReconciliationTransport(
        secret_keys={PROFILE: TEST_KEY},
        base_url="https://api.stripe.invalid",
        client=httpx.Client(transport=httpx.MockTransport(capture_read)),
    )
    profiles = {
        BusinessUnit.HOSTEL: PROFILE,
        BusinessUnit.AGENCY: "stripe-account:agency:test",
    }
    percentages = {
        BusinessUnit.HOSTEL: 100,
        BusinessUnit.AGENCY: 100,
    }
    stripe = stripe_module.StripeLinkAdapter(
        transport=write_transport,
        account_profiles=profiles,
        enabled=True,
        subscriber_id=SUBSCRIBER_ID,
        payment_percentages=percentages,
    )
    reconciler = stripe_module.StripeLinkReconciliationAdapter(
        transport=read_transport,
        account_profiles=profiles,
        subscriber_id=SUBSCRIBER_ID,
        payment_percentages=percentages,
    )
    payments = PaymentService(
        stripe=stripe,
        wise=WiseInstructionAdapter(
            instructions={"receiver:hostel": "Wise fechado neste teste."}
        ),
        pix=PixInstructionAdapter(knowledge=Knowledge()),
    )
    store.enqueue(SELECTION, now=NOW)
    worker = PaymentInitiationWorker(
        store=store,
        payments=payments,
        worker_id="worker:stripe-reconciliation",
        lease_ttl=timedelta(seconds=30),
        effect_guard=effect_guard,
        stripe_reconciler=reconciler,
    )
    return worker, store, seen


def test_write_authority_is_rechecked_immediately_before_every_stripe_post(
    tmp_path: Path,
) -> None:
    class MutableClock:
        current = NOW + timedelta(seconds=1)

        def now(self) -> datetime:
            return self.current

    class DeadlineGuard:
        def __init__(self, clock: MutableClock) -> None:
            self.clock = clock
            self.calls: list[datetime] = []

        def allows_workflow(self, workflow_id: str) -> bool:
            assert workflow_id == "stripe-payment-initiation"
            self.calls.append(self.clock.now())
            return self.clock.now() < NOW + timedelta(seconds=2)

    clock = MutableClock()
    guard = DeadlineGuard(clock)

    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/products"
        response = httpx.Response(200, request=request, json=_product_payload())
        clock.current = NOW + timedelta(seconds=3)
        return response

    def read_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected reconciliation read: {request.url.path}")

    worker, store, seen = _build_worker(
        tmp_path,
        write_handler=write_handler,
        read_handler=read_handler,
        clock=clock.now,
        effect_guard=guard,
    )

    result = worker.run_once(now=NOW + timedelta(seconds=1))

    assert result.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert [request.url.path for request in seen] == ["/v1/products"]
    assert guard.calls == [
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=3),
    ]
    assert [
        (receipt.step, receipt.status)
        for receipt in store.stripe_step_receipts(SELECTION)
    ] == [
        (StripeCreationStep.PRODUCT, StripeStepStatus.ACCEPTED),
        (StripeCreationStep.PRICE, StripeStepStatus.INTENT),
    ]


def test_timeout_after_accepted_product_persists_receipt_and_reconciles_get_only(
    tmp_path: Path,
) -> None:
    def write_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products":
            return httpx.Response(200, request=request, json=_product_payload())
        raise httpx.ReadTimeout("ambiguous after Price dispatch", request=request)

    def read_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products/prod_test_reconcile":
            return httpx.Response(200, request=request, json=_product_payload())
        assert request.url.path == "/v1/prices"
        return httpx.Response(
            200,
            request=request,
            json={"object": "list", "data": [], "has_more": False},
        )

    worker, store, seen = _build_worker(
        tmp_path,
        write_handler=write_handler,
        read_handler=read_handler,
    )

    first = worker.run_once(now=NOW + timedelta(seconds=1))
    receipts_after_write = store.stripe_step_receipts(SELECTION)
    second = worker.run_once(now=NOW + timedelta(seconds=2))
    third = worker.run_once(now=NOW + timedelta(seconds=3))

    assert first.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert second.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert third.disposition is PaymentInitiationDisposition.IDLE
    assert [(receipt.step.value, receipt.status.value) for receipt in receipts_after_write] == [
        ("product", "accepted"),
        ("price", "intent"),
    ]
    assert receipts_after_write[0].provider_object_id == "prod_test_reconcile"
    assert receipts_after_write[0].canonical_url is None
    assert [request.method for request in seen] == ["POST", "POST", "GET", "GET"]
    assert [request.url.path for request in seen] == [
        "/v1/products",
        "/v1/prices",
        "/v1/products/prod_test_reconcile",
        "/v1/prices",
    ]
    assert store.dispatch_slots(SELECTION) == 1


def test_timeout_after_product_post_is_reconciled_once_without_replay(
    tmp_path: Path,
) -> None:
    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/products"
        raise httpx.ReadTimeout("ambiguous", request=request)

    def read_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/products/search"
        return httpx.Response(
            200,
            request=request,
            json={"data": [], "has_more": False},
        )

    worker, store, seen = _build_worker(
        tmp_path,
        write_handler=write_handler,
        read_handler=read_handler,
    )
    assert worker.run_once(now=NOW + timedelta(seconds=1)).disposition is (
        PaymentInitiationDisposition.MANUAL_REVIEW
    )
    assert worker.run_once(now=NOW + timedelta(seconds=2)).disposition is (
        PaymentInitiationDisposition.MANUAL_REVIEW
    )
    assert worker.run_once(now=NOW + timedelta(seconds=3)).disposition is (
        PaymentInitiationDisposition.IDLE
    )
    assert [request.method for request in seen] == ["POST", "GET"]
    assert store.completed_offers() == ()
    assert store.dispatch_slots(SELECTION) == 1


def test_live_clock_expiry_after_product_blocks_price_before_post(
    tmp_path: Path,
) -> None:
    current = [NOW + timedelta(seconds=1)]

    def clock() -> datetime:
        return current[0]

    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/products"
        current[0] = NOW + timedelta(seconds=32)
        return httpx.Response(200, request=request, json=_product_payload())

    def read_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/products/prod_test_reconcile"
        return httpx.Response(200, request=request, json=_product_payload())

    worker, store, seen = _build_worker(
        tmp_path,
        write_handler=write_handler,
        read_handler=read_handler,
        clock=clock,
    )

    first = worker.run_once(now=NOW + timedelta(seconds=1))
    second = worker.run_once(now=NOW + timedelta(seconds=32))

    assert first.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert second.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert worker.run_once(now=NOW + timedelta(seconds=33)).disposition is (
        PaymentInitiationDisposition.IDLE
    )
    assert [request.method for request in seen] == ["POST", "GET"]
    assert [request.url.path for request in seen] == [
        "/v1/products",
        "/v1/products/prod_test_reconcile",
    ]
    receipts = store.stripe_step_receipts(SELECTION)
    assert [(item.step, item.status) for item in receipts] == [
        (StripeCreationStep.PRODUCT, StripeStepStatus.ACCEPTED)
    ]


def test_sqlite_reopen_reconciles_ambiguous_product_without_post_replay(
    tmp_path: Path,
) -> None:
    def ambiguous_product(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/products"
        raise httpx.ReadTimeout("ambiguous", request=request)

    def no_product_match(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/products/search"
        return httpx.Response(
            200,
            request=request,
            json={"data": [], "has_more": False},
        )

    first_worker, first_store, first_seen = _build_worker(
        tmp_path,
        write_handler=ambiguous_product,
        read_handler=no_product_match,
    )
    assert first_worker.run_once(now=NOW + timedelta(seconds=1)).disposition is (
        PaymentInitiationDisposition.MANUAL_REVIEW
    )
    first_store._connection.close()

    def forbidden_write(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected write after reopen: {request.method}")

    second_worker, second_store, second_seen = _build_worker(
        tmp_path,
        write_handler=forbidden_write,
        read_handler=no_product_match,
    )
    assert second_worker.run_once(now=NOW + timedelta(seconds=2)).disposition is (
        PaymentInitiationDisposition.MANUAL_REVIEW
    )
    assert second_worker.run_once(now=NOW + timedelta(seconds=3)).disposition is (
        PaymentInitiationDisposition.IDLE
    )
    assert [request.method for request in first_seen] == ["POST"]
    assert [request.method for request in second_seen] == ["GET"]
    second_store._connection.close()


def test_sqlite_reopen_recovers_expired_fence_before_first_stripe_intent(
    tmp_path: Path,
) -> None:
    def forbidden_http(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected HTTP during pre-intent recovery: {request.method}")

    _, first_store, _ = _build_worker(
        tmp_path,
        write_handler=forbidden_http,
        read_handler=forbidden_http,
    )
    claim = first_store.claim(
        worker_id="crash-before-first-intent",
        now=NOW + timedelta(seconds=1),
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    first_store.fence(claim, now=NOW + timedelta(seconds=1))
    first_store.close()

    restarted_worker, restarted_store, seen = _build_worker(
        tmp_path,
        write_handler=forbidden_http,
        read_handler=forbidden_http,
    )
    recovered = restarted_worker.run_once(now=NOW + timedelta(seconds=32))

    assert recovered.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert restarted_worker.run_once(
        now=NOW + timedelta(seconds=33)
    ).disposition is PaymentInitiationDisposition.IDLE
    assert seen == []
    state = restarted_store._connection.execute(
        "SELECT status,dispatch_slots FROM payment_initiations"
    ).fetchone()
    assert state == ("manual_review", 1)


def test_stripe_step_journal_fences_intents_but_preserves_late_acceptance(
    tmp_path: Path,
) -> None:
    store = SQLitePaymentInitiationStore(
        tmp_path / "stripe-journal-fence.sqlite3",
        result_encryption_key=RESULT_KEY,
    )
    store.enqueue(SELECTION, now=NOW)
    claim = store.claim(
        worker_id="journal-owner",
        now=NOW + timedelta(seconds=1),
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    store.fence(claim, now=NOW + timedelta(seconds=1))
    intent = StripeStepReceipt(
        step=StripeCreationStep.PRODUCT,
        status=StripeStepStatus.INTENT,
        account_profile_id=PROFILE,
        expected_metadata_hash="a" * 64,
        idempotency_key="stripe-link:opaque:v1:product",
    )
    accepted = replace(
        intent,
        status=StripeStepStatus.ACCEPTED,
        provider_object_id="prod_test_late_acceptance",
    )

    with pytest.raises(RuntimeError, match="stale"):
        store.record_stripe_step_intent(
            claim.initiation_id,
            intent,
            worker_id="wrong-owner",
            fencing_token=claim.fencing_token,
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(RuntimeError, match="expired"):
        store.record_stripe_step_intent(
            claim.initiation_id,
            intent,
            worker_id=claim.worker_id,
            fencing_token=claim.fencing_token,
            now=NOW + timedelta(seconds=32),
        )
    store.record_stripe_step_intent(
        claim.initiation_id,
        intent,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        now=NOW + timedelta(seconds=2),
    )
    store.record_stripe_step_accepted(
        claim.initiation_id,
        accepted,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        now=NOW + timedelta(seconds=32),
    )

    assert store.stripe_step_receipts(SELECTION) == (accepted,)


def test_stripe_writer_has_no_read_surface_and_reconciler_has_no_post_surface() -> None:
    writer_source = inspect.getsource(stripe_module.StripeTestHTTPTransport)
    reader_source = inspect.getsource(stripe_module.StripeTestReconciliationTransport)

    assert "self._client.get(" not in writer_source
    assert "self._client.post(" not in reader_source
    assert not hasattr(stripe_module.StripeTestReconciliationTransport, "post")


def test_reconciliation_receipts_remain_bound_to_original_stripe_account(
    tmp_path: Path,
) -> None:
    def ambiguous_product(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("ambiguous product", request=request)

    worker, store, _ = _build_worker(
        tmp_path,
        write_handler=ambiguous_product,
        read_handler=lambda request: httpx.Response(
            500,
            request=request,
        ),
    )
    assert worker.run_once(now=NOW + timedelta(seconds=1)).disposition is (
        PaymentInitiationDisposition.MANUAL_REVIEW
    )
    receipts = store.stripe_step_receipts(SELECTION)
    initiation_id = store._connection.execute(
        "SELECT initiation_id FROM payment_initiations"
    ).fetchone()[0]
    drift_reads: list[httpx.Request] = []

    def drift_handler(request: httpx.Request) -> httpx.Response:
        drift_reads.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"data": [], "has_more": False},
        )

    drift_profile = "stripe-account:hostel:test:rotated"
    drift_reader = stripe_module.StripeTestReconciliationTransport(
        secret_keys={drift_profile: TEST_KEY},
        base_url="https://api.stripe.invalid",
        client=httpx.Client(transport=httpx.MockTransport(drift_handler)),
    )
    drift_reconciler = stripe_module.StripeLinkReconciliationAdapter(
        transport=drift_reader,
        account_profiles={
            BusinessUnit.HOSTEL: drift_profile,
            BusinessUnit.AGENCY: "stripe-account:agency:test:rotated",
        },
        subscriber_id=SUBSCRIBER_ID,
        payment_percentages={
            BusinessUnit.HOSTEL: 30,
            BusinessUnit.AGENCY: 30,
        },
    )

    result = drift_reconciler.reconcile(
        SELECTION,
        receipts,
        initiation_id=initiation_id,
    )

    assert result == StripeReconciliationResult(offer=None, manual_review=True)
    assert drift_reads == []


@pytest.mark.parametrize(
    ("candidate_count", "private_query", "invalid_principal", "expected_disposition"),
    [
        (1, False, None, PaymentInitiationDisposition.COMPLETED),
        (2, False, None, PaymentInitiationDisposition.MANUAL_REVIEW),
        (1, True, None, PaymentInitiationDisposition.MANUAL_REVIEW),
        (1, False, "empty-id", PaymentInitiationDisposition.MANUAL_REVIEW),
        (1, False, "root-url", PaymentInitiationDisposition.MANUAL_REVIEW),
    ],
)
def test_unknown_payment_link_requires_exactly_one_test_mode_match(
    tmp_path: Path,
    candidate_count: int,
    private_query: bool,
    invalid_principal: str | None,
    expected_disposition: PaymentInitiationDisposition,
) -> None:
    link_form: dict[str, str] = {}

    def write_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices":
            return httpx.Response(
                200,
                request=request,
                json={"id": "price_test_reconcile", "livemode": False},
            )
        assert request.url.path == "/v1/payment_links"
        link_form.update(
            {
                key: values[0]
                for key, values in parse_qs(request.content.decode()).items()
            }
        )
        raise httpx.ReadTimeout("ambiguous after Payment Link dispatch", request=request)

    def read_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products/prod_test_reconcile":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices/price_test_reconcile":
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "price_test_reconcile",
                    "livemode": False,
                    "active": True,
                    "product": "prod_test_reconcile",
                    "currency": "brl",
                    "unit_amount": 15300,
                },
            )
        assert request.url.path == "/v1/payment_links"
        metadata = {
            key.removeprefix("metadata[").removesuffix("]"): value
            for key, value in link_form.items()
            if key.startswith("metadata[")
        }
        return httpx.Response(
            200,
            request=request,
            json={
                "object": "list",
                "has_more": False,
                "data": [
                    {
                        "id": (
                            ""
                            if invalid_principal == "empty-id"
                            else f"plink_test_reconciled_{index}"
                        ),
                        "livemode": False,
                        "active": True,
                        "url": (
                            (
                                "https://buy.stripe.com/"
                                if invalid_principal == "root-url"
                                else f"https://buy.stripe.com/test_reconciled_{index}"
                            )
                            + ("?secret=private-query" if private_query else "")
                        ),
                        "metadata": metadata,
                        "line_items": {
                            "data": [{"price": "price_test_reconcile"}]
                        },
                    }
                    for index in range(candidate_count)
                ],
            },
        )

    worker, store, seen = _build_worker(
        tmp_path,
        write_handler=write_handler,
        read_handler=read_handler,
    )
    assert worker.run_once(now=NOW + timedelta(seconds=1)).disposition is (
        PaymentInitiationDisposition.MANUAL_REVIEW
    )
    recovered = worker.run_once(now=NOW + timedelta(seconds=2))
    assert recovered.disposition is expected_disposition
    assert worker.run_once(now=NOW + timedelta(seconds=3)).disposition is (
        PaymentInitiationDisposition.IDLE
    )
    if expected_disposition is PaymentInitiationDisposition.COMPLETED:
        assert recovered.offer is not None
        assert recovered.offer.public_url == (
            "https://buy.stripe.com/test_reconciled_0"
        )
        assert store.completed_offers() == (recovered.offer,)
    else:
        assert recovered.offer is None
        assert store.completed_offers() == ()
    assert [request.method for request in seen].count("POST") == 3
    assert [request.method for request in seen].count("GET") == 3


def test_accepted_final_link_is_immediate_and_later_get_mismatch_never_downgrades(
    tmp_path: Path,
) -> None:
    link_form: dict[str, str] = {}

    def write_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices":
            return httpx.Response(
                200,
                request=request,
                json={"id": "price_test_reconcile", "livemode": False},
            )
        assert request.url.path == "/v1/payment_links"
        link_form.update(
            {
                key: values[0]
                for key, values in parse_qs(request.content.decode()).items()
            }
        )
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "plink_test_accepted",
                "livemode": False,
                "active": True,
                "url": "https://buy.stripe.com/test_accepted",
            },
        )

    def read_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products/prod_test_reconcile":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices/price_test_reconcile":
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "price_test_reconcile",
                    "livemode": False,
                    "active": True,
                    "product": "prod_test_reconcile",
                    "currency": "brl",
                    "unit_amount": 15300,
                },
            )
        assert request.url.path == "/v1/payment_links/plink_test_accepted"
        metadata = {
            key.removeprefix("metadata[").removesuffix("]"): value
            for key, value in link_form.items()
            if key.startswith("metadata[")
        }
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "plink_test_accepted",
                "livemode": False,
                "active": True,
                "url": "https://buy.stripe.com/test_different",
                "metadata": metadata,
                "line_items": {"data": [{"price": "price_test_reconcile"}]},
            },
        )

    worker, store, seen = _build_worker(
        tmp_path,
        write_handler=write_handler,
        read_handler=read_handler,
    )
    accepted = worker.run_once(now=NOW + timedelta(seconds=1))
    assert accepted.disposition is PaymentInitiationDisposition.COMPLETED
    assert accepted.offer is not None
    assert accepted.offer.public_url == "https://buy.stripe.com/test_accepted"
    assert store.completed_offers() == (accepted.offer,)

    audit = worker.run_once(now=NOW + timedelta(seconds=2))
    assert audit.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert store.completed_offers() == (accepted.offer,)
    assert worker.run_once(now=NOW + timedelta(seconds=3)).disposition is (
        PaymentInitiationDisposition.IDLE
    )
    assert [request.method for request in seen].count("POST") == 3


def test_crash_after_final_acceptance_recovers_offer_without_post_replay(
    tmp_path: Path,
) -> None:
    link_form: dict[str, str] = {}

    def write_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices":
            return httpx.Response(
                200,
                request=request,
                json={"id": "price_test_reconcile", "livemode": False},
            )
        assert request.url.path == "/v1/payment_links"
        link_form.update(
            {
                key: values[0]
                for key, values in parse_qs(request.content.decode()).items()
            }
        )
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "plink_test_crash_accepted",
                "livemode": False,
                "active": True,
                "url": "https://buy.stripe.com/test_crash_accepted",
            },
        )

    def read_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products/prod_test_reconcile":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices/price_test_reconcile":
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "price_test_reconcile",
                    "livemode": False,
                    "active": True,
                    "product": "prod_test_reconcile",
                    "currency": "brl",
                    "unit_amount": 15300,
                },
            )
        assert request.url.path == "/v1/payment_links/plink_test_crash_accepted"
        metadata = {
            key.removeprefix("metadata[").removesuffix("]"): value
            for key, value in link_form.items()
            if key.startswith("metadata[")
        }
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "plink_test_crash_accepted",
                "livemode": False,
                "active": True,
                "url": "https://buy.stripe.com/test_crash_divergent",
                "metadata": metadata,
                "line_items": {"data": [{"price": "price_test_reconcile"}]},
            },
        )

    worker, store, seen = _build_worker(
        tmp_path,
        write_handler=write_handler,
        read_handler=read_handler,
    )
    claim = store.claim(
        worker_id="crash-before-outer-complete",
        now=NOW + timedelta(seconds=1),
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    store.fence(claim, now=NOW + timedelta(seconds=1))
    accepted = worker._payments.initiate(
        claim.selection.obligation,
        claim.selection.method,
        initiation_id=claim.initiation_id,
        journal_worker_id=claim.worker_id,
        journal_fencing_token=claim.fencing_token,
    )
    assert accepted.public_url == "https://buy.stripe.com/test_crash_accepted"
    assert store.completed_offers() == ()

    class ClosedWriteGuard:
        def allows_workflow(self, workflow_id: str) -> bool:
            assert workflow_id == "stripe-payment-initiation"
            return False

    worker._effect_guard = ClosedWriteGuard()

    recovered = worker.run_once(now=NOW + timedelta(seconds=32))
    assert recovered.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert store.completed_offers() == (accepted,)
    assert worker.run_once(now=NOW + timedelta(seconds=33)).disposition is (
        PaymentInitiationDisposition.IDLE
    )
    assert [request.method for request in seen].count("POST") == 3


@pytest.mark.parametrize(
    "reaper_outcome",
    (
        "finished-before-accept",
        "finished-after-accept",
        "crashed-after-accept",
        "matched-different-before-recovery",
        "matched-different-before-accept",
    ),
)
def test_late_accepted_link_survives_reconciliation_takeover(
    tmp_path: Path,
    reaper_outcome: str,
) -> None:
    link_form: dict[str, str] = {}

    def write_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices":
            return httpx.Response(
                200,
                request=request,
                json={"id": "price_test_reconcile", "livemode": False},
            )
        assert request.url.path == "/v1/payment_links"
        link_form.update(
            {
                key: values[0]
                for key, values in parse_qs(request.content.decode()).items()
            }
        )
        raise httpx.ReadTimeout("ambiguous", request=request)

    def read_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products/prod_test_reconcile":
            return httpx.Response(200, request=request, json=_product_payload())
        if request.url.path == "/v1/prices/price_test_reconcile":
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "price_test_reconcile",
                    "livemode": False,
                    "active": True,
                    "product": "prod_test_reconcile",
                    "currency": "brl",
                    "unit_amount": 15300,
                },
            )
        if request.url.path == "/v1/payment_links":
            return httpx.Response(
                200,
                request=request,
                json={"data": [], "has_more": False},
            )
        assert request.url.path == "/v1/payment_links/plink_test_late_accepted"
        metadata = {
            key.removeprefix("metadata[").removesuffix("]"): value
            for key, value in link_form.items()
            if key.startswith("metadata[")
        }
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "plink_test_late_accepted",
                "livemode": False,
                "active": True,
                "url": "https://buy.stripe.com/test_late_accepted",
                "metadata": metadata,
                "line_items": {"data": [{"price": "price_test_reconcile"}]},
            },
        )

    worker, store, seen = _build_worker(
        tmp_path,
        write_handler=write_handler,
        read_handler=read_handler,
    )
    claim = store.claim(
        worker_id="late-provider-response",
        now=NOW + timedelta(seconds=1),
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    store.fence(claim, now=NOW + timedelta(seconds=1))

    with pytest.raises(RuntimeError, match="ambiguous"):
        worker._payments.initiate(
            claim.selection.obligation,
            claim.selection.method,
            initiation_id=claim.initiation_id,
            journal_worker_id=claim.worker_id,
            journal_fencing_token=claim.fencing_token,
        )

    takeover = store.claim_stripe_reconciliation(
        worker_id="stripe-reaper-overlap",
        now=NOW + timedelta(seconds=32),
        lease_ttl=timedelta(seconds=30),
    )
    assert takeover is not None
    assert takeover.receipts[-1].status is StripeStepStatus.INTENT
    link_intent = store.stripe_step_receipts(SELECTION)[-1]
    assert link_intent.step is StripeCreationStep.PAYMENT_LINK
    assert link_intent.status is StripeStepStatus.INTENT
    late_accepted = replace(
        link_intent,
        status=StripeStepStatus.ACCEPTED,
        provider_object_id="plink_test_late_accepted",
        canonical_url="https://buy.stripe.com/test_late_accepted",
    )
    with pytest.raises(RuntimeError, match="stale Stripe accepted receipt authority"):
        store.record_stripe_step_accepted(
            claim.initiation_id,
            late_accepted,
            worker_id="different-provider-worker",
            fencing_token=claim.fencing_token,
            now=NOW + timedelta(seconds=33),
        )
    if reaper_outcome == "finished-before-accept":
        store.finish_stripe_reconciliation(
            takeover,
            StripeReconciliationResult(offer=None, manual_review=True),
            now=NOW + timedelta(seconds=33),
        )
    elif reaper_outcome == "matched-different-before-accept":
        store.finish_stripe_reconciliation(
            takeover,
            StripeReconciliationResult(
                offer=StripePaymentLink(
                    payment_id=OBLIGATION.payment_id,
                    reservation_anchor_id=OBLIGATION.reservation_anchor_id,
                    account_profile_id=PROFILE,
                    economic_version=OBLIGATION.economic_version,
                    public_url="https://buy.stripe.com/test_discovered_different",
                    provider_reference_fingerprint="d" * 64,
                    receipt_hash="e" * 64,
                    customer_language=CustomerLanguage.PT_BR,
                ),
                manual_review=False,
            ),
            now=NOW + timedelta(seconds=33),
        )
    store.record_stripe_step_accepted(
        claim.initiation_id,
        late_accepted,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        now=NOW + timedelta(seconds=33),
    )
    if reaper_outcome == "finished-after-accept":
        store.finish_stripe_reconciliation(
            takeover,
            StripeReconciliationResult(offer=None, manual_review=True),
            now=NOW + timedelta(seconds=33),
        )
    elif reaper_outcome == "matched-different-before-recovery":
        store.finish_stripe_reconciliation(
            takeover,
            StripeReconciliationResult(
                offer=StripePaymentLink(
                    payment_id=OBLIGATION.payment_id,
                    reservation_anchor_id=OBLIGATION.reservation_anchor_id,
                    account_profile_id=PROFILE,
                    economic_version=OBLIGATION.economic_version,
                    public_url="https://buy.stripe.com/test_discovered_different",
                    provider_reference_fingerprint="d" * 64,
                    receipt_hash="e" * 64,
                    customer_language=CustomerLanguage.PT_BR,
                ),
                manual_review=False,
            ),
            now=NOW + timedelta(seconds=33),
        )

    recovery_at = NOW + timedelta(
        seconds=63 if reaper_outcome == "crashed-after-accept" else 34
    )

    recovery = store.claim_stripe_reconciliation(
        worker_id="stripe-late-acceptance-recovery",
        now=recovery_at,
        lease_ttl=timedelta(seconds=30),
    )
    assert recovery is not None
    assert recovery.receipts[-1].status is StripeStepStatus.ACCEPTED
    assert worker._stripe_reconciler is not None
    reconciled = worker._stripe_reconciler.reconcile(
        recovery.selection,
        recovery.receipts,
        initiation_id=recovery.initiation_id,
    )
    store.finish_stripe_reconciliation(
        recovery,
        reconciled,
        now=recovery_at,
    )

    assert reconciled.manual_review is False
    assert reconciled.offer is not None
    assert reconciled.offer.public_url == "https://buy.stripe.com/test_late_accepted"
    completed = store.completed_offers()[0]
    if reaper_outcome in {
        "matched-different-before-recovery",
        "matched-different-before-accept",
    }:
        assert completed.public_url == "https://buy.stripe.com/test_discovered_different"
        expected_reconciliation_status = "manual_review"
    else:
        assert completed == reconciled.offer
        expected_reconciliation_status = "matched"
    assert store._connection.execute(
        "SELECT status FROM stripe_reconciliations"
    ).fetchone() == (expected_reconciliation_status,)
    store.record_stripe_step_accepted(
        claim.initiation_id,
        late_accepted,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        now=recovery_at + timedelta(seconds=1),
    )
    assert worker.run_once(now=recovery_at + timedelta(seconds=1)).disposition is (
        PaymentInitiationDisposition.IDLE
    )
    assert [request.method for request in seen].count("POST") == 3


def test_private_stripe_receipts_and_reconciliation_repr_do_not_expose_customer_data(
    tmp_path: Path,
) -> None:
    sentinel = "CUSTOMER-PRIVATE-SENTINEL"
    private_details = replace(DETAILS, public_label=sentinel)
    private_obligation = replace(
        OBLIGATION,
        payment_id="payment:stripe:privacy:001",
        reservation_anchor_id="anchor:stripe:privacy:001",
        display_details=private_details,
    )
    selection = PaymentSelection(private_obligation, PaymentMethod.STRIPE)
    db_path = tmp_path / "stripe-private.sqlite3"
    store = SQLitePaymentInitiationStore(
        db_path,
        result_encryption_key=RESULT_KEY,
    )
    store.enqueue(selection, now=NOW)
    claim = store.claim(
        worker_id="privacy-worker",
        now=NOW + timedelta(seconds=1),
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    store.fence(claim, now=NOW + timedelta(seconds=1))
    product_intent = StripeStepReceipt(
        step=StripeCreationStep.PRODUCT,
        status=StripeStepStatus.INTENT,
        account_profile_id=PROFILE,
        expected_metadata_hash="b" * 64,
        idempotency_key="stripe-link:opaque:v1:product",
    )
    product_accepted = StripeStepReceipt(
        step=StripeCreationStep.PRODUCT,
        status=StripeStepStatus.ACCEPTED,
        account_profile_id=PROFILE,
        expected_metadata_hash="b" * 64,
        idempotency_key="stripe-link:opaque:v1:product",
        provider_object_id="prod_private_provider_id",
    )
    price_intent = StripeStepReceipt(
        step=StripeCreationStep.PRICE,
        status=StripeStepStatus.INTENT,
        account_profile_id=PROFILE,
        expected_metadata_hash="c" * 64,
        idempotency_key="stripe-link:opaque:v1:price",
    )
    price_accepted = StripeStepReceipt(
        step=StripeCreationStep.PRICE,
        status=StripeStepStatus.ACCEPTED,
        account_profile_id=PROFILE,
        expected_metadata_hash="c" * 64,
        idempotency_key="stripe-link:opaque:v1:price",
        provider_object_id="price_private_provider_id",
    )
    intent = StripeStepReceipt(
        step=StripeCreationStep.PAYMENT_LINK,
        status=StripeStepStatus.INTENT,
        account_profile_id=PROFILE,
        expected_metadata_hash="a" * 64,
        idempotency_key="stripe-link:opaque:v1:payment_link",
    )
    accepted_receipt = StripeStepReceipt(
        step=StripeCreationStep.PAYMENT_LINK,
        status=StripeStepStatus.ACCEPTED,
        account_profile_id=PROFILE,
        expected_metadata_hash="a" * 64,
        idempotency_key="stripe-link:opaque:v1:payment_link",
        provider_object_id="plink_private_provider_id",
        canonical_url="https://buy.stripe.com/private_authenticated_url",
    )
    journal_authority = {
        "worker_id": claim.worker_id,
        "fencing_token": claim.fencing_token,
        "now": NOW + timedelta(seconds=1),
    }
    store.record_stripe_step_intent(
        claim.initiation_id, product_intent, **journal_authority
    )
    store.record_stripe_step_accepted(
        claim.initiation_id, product_accepted, **journal_authority
    )
    store.record_stripe_step_intent(
        claim.initiation_id, price_intent, **journal_authority
    )
    store.record_stripe_step_accepted(
        claim.initiation_id, price_accepted, **journal_authority
    )
    store.record_stripe_step_intent(
        claim.initiation_id, intent, **journal_authority
    )
    store.record_stripe_step_accepted(
        claim.initiation_id, accepted_receipt, **journal_authority
    )
    store.mark_unknown(claim, now=NOW + timedelta(seconds=1))
    reconciliation = store.claim_stripe_reconciliation(
        worker_id="privacy-worker",
        now=NOW + timedelta(seconds=2),
        lease_ttl=timedelta(seconds=30),
    )
    assert reconciliation is not None
    rendered = repr(reconciliation)
    assert sentinel not in rendered
    assert "plink_private_provider_id" not in rendered
    assert "private_authenticated_url" not in rendered

    connection = sqlite3.connect(db_path)
    receipt_rows = connection.execute(
        "SELECT receipt_json FROM stripe_step_receipts"
    ).fetchall()
    connection.close()
    raw = b"".join(bytes(row[0]) for row in receipt_rows)
    assert sentinel.encode() not in raw
    assert b"plink_private_provider_id" not in raw
    assert b"private_authenticated_url" not in raw

    assert store.claim_stripe_reconciliation(
        worker_id="privacy-worker-2",
        now=NOW + timedelta(seconds=33),
        lease_ttl=timedelta(seconds=30),
    ) is None
    connection = sqlite3.connect(db_path)
    state = connection.execute(
        "SELECT status,attempts FROM stripe_reconciliations"
    ).fetchone()
    connection.close()
    assert state == ("manual_review", 1)
