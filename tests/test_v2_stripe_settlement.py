"""Real SQLite fence + actual HTTP adapter, with synthetic provider endpoints."""

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from reservation_followup.types import PaymentStatus
from reservation_followup.workers import PaymentSettlementWorker
from tests.native_stripe_helpers import close_lab, make_lab
from tests.test_v2_native_stripe import NOW, accept


@pytest.fixture
def lab(tmp_path):
    value = make_lab(tmp_path)
    value.receipt = accept(value)
    value.followup = SQLiteFollowupUnitOfWork.open_v2(value.paths["followup"])
    value.provider_requests = []
    value.status = "confirmed"
    value.behavior = "accepted"
    value.paid = "0.00"
    value.guard = True
    value.now = NOW + timedelta(seconds=1)

    def respond(req):
        value.provider_requests.append(req)
        if req.url.path.endswith("getReservation"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "reservationID": "123",
                        "propertyID": "789",
                        "status": value.status,
                        "currency": "BRL",
                        "balance": "420.00",
                        "balanceDetailed": {"paid": value.paid},
                    },
                },
            )
        if req.url.path.endswith("getPaymentMethods"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "propertyID": "789",
                        "methods": [
                            {
                                "method": "Cartãodecrédito",
                                "code": "Cartãodecrédito",
                                "name": "Cartão de crédito",
                            }
                        ],
                    },
                },
            )
        assert req.url.path.endswith("postPayment") and req.method == "POST"
        if value.behavior == "timeout":
            raise httpx.ReadTimeout(
                "fixture: accepted remotely then response lost", request=req
            )
        if value.behavior == "redirect":
            return httpx.Response(
                307, headers={"Location": "https://cloudbeds.invalid/redirect"}
            )
        if value.behavior == "denied":
            return httpx.Response(
                200, json={"success": False, "message": "fixture: rejected"}
            )
        value.paid = "420.00"
        return httpx.Response(
            200,
            json={"success": True, "paymentID": "pay123", "transactionID": "txn123"},
        )

    value.provider_client = httpx.Client(
        transport=httpx.MockTransport(respond), follow_redirects=True
    )
    try:
        yield value
    finally:
        value.followup.close()
        value.provider_client.close()
        close_lab(value)


def worker(lab):
    from v2_host.stripe_settlement import StripeSettlementAdapter

    adapter = StripeSettlementAdapter(
        store=lab.followup,
        execution=lab.execution,
        cloudbeds_api_key="fixture",
        cloudbeds_property_id="789",
        bokun_access_key="fixture",
        bokun_secret_key="fixture",
        client=lab.provider_client,
        clock=lambda: lab.now,
        effect_guard=SimpleNamespace(allows_workflow=lambda _: lab.guard),
        lead_resolver=SimpleNamespace(lead_id_for_command=lambda _: "manychat:12345"),
        allowed_subscribers=("12345",),
        cloudbeds_base_url=getattr(
            lab, "cloudbeds_base_url", "https://api.cloudbeds.com"
        ),
    )
    return PaymentSettlementWorker(
        store=lab.followup,
        settlement=adapter,
        worker_id="worker:settlement",
        lease_ttl=timedelta(seconds=90),
    )


def test_native_paid_checkout_posts_one_cloudbeds_form_and_survives_reopen(lab):
    assert worker(lab).run_once(now=lab.now).disposition.value == "settled"
    assert (
        lab.followup.load_payment(lab.receipt.payment_id).status is PaymentStatus.PAID
    )
    posts = [r for r in lab.provider_requests if r.method == "POST"]
    assert len(posts) == 1
    form = parse_qs(posts[0].content.decode())
    assert form["type"] == ["Cartãodecrédito"]
    assert form["reservationID"] == ["123"] and form["propertyID"] == ["789"]
    assert form["amount"] == ["420.00"]
    assert (
        posts[0].headers["content-type"].startswith("application/x-www-form-urlencoded")
    )
    assert posts[0].headers["Authorization"] == "Bearer fixture"
    lab.followup.close()
    lab.followup = SQLiteFollowupUnitOfWork.open_v2(lab.paths["followup"])
    assert (
        worker(lab).run_once(now=lab.now + timedelta(seconds=1)).disposition.value
        == "idle"
    )
    assert len([r for r in lab.provider_requests if r.method == "POST"]) == 1


@pytest.mark.parametrize("status", ["canceled", "cancelled", "no_show", "checked_out"])
def test_terminal_reservation_never_receives_payment_or_reactivation(lab, status):
    lab.status = status
    assert worker(lab).run_once(now=lab.now).disposition.value == "preparation_terminal"
    state = lab.followup.load_payment(lab.receipt.payment_id)
    assert state.settlement_finish.outcome.certainty.value == "not_dispatched"
    assert (
        worker(lab).run_once(now=lab.now + timedelta(seconds=1)).disposition.value
        == "idle"
    )
    assert not [r for r in lab.provider_requests if r.method == "POST"]


@pytest.mark.parametrize("behavior", ["timeout", "redirect", "denied"])
def test_ambiguous_or_rejected_post_is_fenced_forever(lab, behavior):
    lab.behavior = behavior
    assert worker(lab).run_once(now=lab.now).disposition.value == "manual_review"
    assert len([r for r in lab.provider_requests if r.method == "POST"]) == 1
    lab.followup.close()
    lab.followup = SQLiteFollowupUnitOfWork.open_v2(lab.paths["followup"])
    assert (
        worker(lab).run_once(now=lab.now + timedelta(seconds=100)).disposition.value
        == "idle"
    )
    assert len([r for r in lab.provider_requests if r.method == "POST"]) == 1


def test_gate_closes_during_preflight_before_physical_post(lab):
    original = lab.provider_client._transport.handler

    def close(req):
        result = original(req)
        if req.url.path.endswith("getPaymentMethods"):
            lab.guard = False
        return result

    lab.provider_client._transport.handler = close
    worker(lab).run_once(now=lab.now)
    assert not [r for r in lab.provider_requests if r.method == "POST"]
    assert (
        lab.followup.load_payment(lab.receipt.payment_id).status
        is not PaymentStatus.PAID
    )


def test_settlement_fact_wakes_maya_once_and_terminal_failure_opens_handoff(
    lab, tmp_path
):
    from reservation_boundary.sqlite_store import SQLiteBoundaryStore
    from v2_application.completion import PublicOutboxStore
    from v2_application.completion_projector import CompletionProjector
    from v2_application.recovery import HandoffCoordinator, ManualReviewHandoffProjector

    boundary = SQLiteBoundaryStore.open_path_v8(
        tmp_path / "completion-boundary.sqlite3"
    )
    public = PublicOutboxStore(tmp_path / "public.sqlite3")
    resolver = SimpleNamespace(
        lead_id_for_command=lambda _: "manychat:12345",
        lead_id_for_payment=lambda _: "manychat:12345",
    )
    completion = CompletionProjector(
        execution=lab.execution,
        payment_store=lab.payments,
        public_store=public,
        boundary=boundary,
        lead_resolver=resolver,
        followup=lab.followup,
    )
    try:
        assert not [e for e in completion.events() if e.kind == "payment_settlement"]
        lab.status = "canceled"
        worker(lab).run_once(now=lab.now)
        events = [e for e in completion.events() if e.kind == "payment_settlement"]
        assert len(events) == 1 and events[0].command_ids == (lab.command.command_id,)
        projector = ManualReviewHandoffProjector(
            execution=lab.execution,
            followup=lab.followup,
            coordinator=HandoffCoordinator(store=lab.followup),
            lead_resolver=resolver,
        )
        assert projector.run_once(now=lab.now).created == 1
        assert projector.run_once(now=lab.now + timedelta(seconds=1)).created == 0
        from reservation_followup.workers import PaymentOutboxWorker
        from v2_application.stripe_payment_effects import StripePaymentEffectObserver

        observer = StripePaymentEffectObserver(
            followup=lab.followup,
            boundary=boundary,
            lead_resolver=resolver,
            coordinator=HandoffCoordinator(store=lab.followup),
            clock=lambda: lab.now,
        )
        job = PaymentOutboxWorker(
            store=lab.followup,
            delivery=observer,
            worker_id="worker:handoff-receipt",
            lease_ttl=timedelta(seconds=30),
        )
        assert job.run_once(now=lab.now).disposition.value == "delivered"
        assert job.run_once(now=lab.now).disposition.value == "idle"

    finally:
        boundary.close()
        public.close()


@pytest.mark.parametrize("status", ["RESERVED", "CANCELLED"])
def test_agency_deposit_uses_bound_bokun_payment_contract(tmp_path, status):
    import base64
    import hashlib
    import hmac
    import json

    lab = make_lab(tmp_path, unit="agency")
    lab.receipt = accept(lab)
    lab.followup = SQLiteFollowupUnitOfWork.open_v2(lab.paths["followup"])
    lab.now = NOW + timedelta(seconds=1)
    lab.guard = True
    requests = []
    total = float(lab.command.payload.components[0].total.amount)
    paid = lab.objects["price_Native"]["unit_amount"] / 100
    assert paid == total * 0.2

    def transport(req):
        requests.append(req)
        date = req.headers["X-Bokun-Date"]
        signed = date + "fixture" + req.method + req.url.raw_path.decode()
        assert (
            req.headers["X-Bokun-Signature"]
            == base64.b64encode(
                hmac.new(b"fixture", signed.encode(), hashlib.sha1).digest()
            ).decode()
        )
        row = {
            "bookingId": 456,
            "status": status,
            "currency": "BRL",
            "totalPaid": 0,
            "totalDue": total,
        }
        if req.method == "POST":
            assert req.url.path == "/booking.json/456/confirm"
            body = json.loads(req.content)
            assert (
                body["payment"]["amount"] == paid
                and body["payment"]["currency"] == "BRL"
            )
            assert body["payment"]["paymentReferenceId"] == "pi_Native"
            assert req.url.params["sendCustomerNotification"] == "false"
            row.update(
                status="CONFIRMED",
                totalPaid=paid,
                totalDue=total - paid,
                customerPayments=[{"id": 789, **body["payment"]}],
            )
        return httpx.Response(200, json=row)

    lab.provider_client = httpx.Client(transport=httpx.MockTransport(transport))
    try:
        result = worker(lab).run_once(now=lab.now)
        assert result.disposition.value == (
            "settled" if status == "RESERVED" else "preparation_terminal"
        )
        assert len([r for r in requests if r.method == "POST"]) == (
            1 if status == "RESERVED" else 0
        )
        assert (
            worker(lab).run_once(now=lab.now + timedelta(seconds=1)).disposition.value
            == "idle"
        )
    finally:
        lab.followup.close()
        lab.provider_client.close()
        close_lab(lab)


def test_physical_dispatch_rejects_expired_permit(lab):
    from reservation_followup.sqlite_store import StaleLease

    port = worker(lab)._settlement
    claim = lab.followup.claim_settlement(
        worker_id="worker:expire", now=lab.now, lease_ttl=timedelta(seconds=30)
    )
    payload = port.prepare(claim.command)
    permit = lab.followup.fence_settlement(claim, payload, now=lab.now)
    lab.now += timedelta(seconds=31)
    with pytest.raises(StaleLease):
        port.dispatch(permit)
    assert not [r for r in lab.provider_requests if r.method == "POST"]


def test_effect_observer_does_not_invent_customer_delivery(lab, tmp_path):
    from reservation_boundary.sqlite_store import SQLiteBoundaryStore
    from reservation_followup.workers import PaymentOutboxWorker
    from v2_application.recovery import HandoffCoordinator
    from v2_application.stripe_payment_effects import StripePaymentEffectObserver

    boundary = SQLiteBoundaryStore.open_path_v8(tmp_path / "effect-boundary.sqlite3")
    worker(lab).run_once(now=lab.now)
    observer = StripePaymentEffectObserver(
        followup=lab.followup,
        boundary=boundary,
        lead_resolver=SimpleNamespace(lead_id_for_command=lambda _: "manychat:12345"),
        coordinator=HandoffCoordinator(store=lab.followup),
        clock=lambda: lab.now,
    )
    outbox = PaymentOutboxWorker(
        store=lab.followup,
        delivery=observer,
        worker_id="worker:effect",
        lease_ttl=timedelta(seconds=30),
    )
    try:
        results = []
        for _ in range(3):
            results.append(outbox.run_once(now=lab.now).disposition.value)
            lab.now += timedelta(seconds=1)
        # Current owner chooses jobs by stable identity. Public confirmation may
        # be first; it must remain pending rather than assert synthetic delivery.
        assert "retryable_failure" in results
        assert all(x in {"delivered", "retryable_failure"} for x in results)
    finally:
        boundary.close()


def test_effect_observer_requires_bound_channel_acceptance_bytes(lab, monkeypatch):
    import v2_application.stripe_payment_effects as effects
    from reservation_boundary.public_dispatch import PublicAcceptanceReceipt
    from reservation_followup.projection import PaymentEffectKind
    from v2_application.recovery import HandoffCoordinator
    from v2_contracts.channel import (
        PublicAcceptanceOperation,
        PublicAcceptanceState,
        PublicChannelAcceptance,
    )

    worker(lab).run_once(now=lab.now)
    ack = PublicChannelAcceptance(
        PublicAcceptanceState.ACCEPTED_BY_MANYCHAT,
        (PublicAcceptanceOperation.TRIGGER_FLOW,),
        ("fixture-provider-request",),
        ("fixture-correlation",),
    )
    receipt = PublicAcceptanceReceipt(
        "public:fixture", "idempotency:fixture", ack, lab.now
    )
    row = [
        "public:fixture",
        "idempotency:fixture",
        "pending",
        receipt.to_canonical_bytes().decode(),
        receipt.canonical_hash(),
    ]
    boundary = SimpleNamespace(
        _connection=SimpleNamespace(
            execute=lambda *_: SimpleNamespace(fetchall=lambda: [row])
        )
    )
    monkeypatch.setattr(
        effects, "source_coverage", lambda *_: ("turn:fixture", "a" * 64)
    )
    observer = effects.StripePaymentEffectObserver(
        followup=lab.followup,
        boundary=boundary,
        lead_resolver=SimpleNamespace(lead_id_for_command=lambda _: "manychat:12345"),
        coordinator=HandoffCoordinator(store=lab.followup),
        clock=lambda: lab.now,
    )
    claim = lab.followup.claim_payment_outbox(
        worker_id="worker:receipt",
        delivery_id=observer.delivery_id,
        delivery_version=1,
        now=lab.now,
        lease_ttl=timedelta(seconds=30),
    )
    if claim.message.kind is PaymentEffectKind.PAID_STATE_TRANSITION:
        lab.followup.complete_payment_outbox(
            claim, observer.deliver(claim), now=lab.now
        )
        claim = lab.followup.claim_payment_outbox(
            worker_id="worker:receipt",
            delivery_id=observer.delivery_id,
            delivery_version=1,
            now=lab.now,
            lease_ttl=timedelta(seconds=30),
        )
    assert claim.message.kind is PaymentEffectKind.CUSTOMER_PAYMENT_CONFIRMATION
    with pytest.raises(ValueError, match="not accepted"):
        observer.deliver(claim)
    row[2] = "delivered"
    row[4] = "b" * 64
    with pytest.raises(ValueError, match="diverged"):
        observer.deliver(claim)
    row[4] = receipt.canonical_hash()
    assert observer.deliver(claim).delivery_reference.startswith("manychat-accepted:")


def test_cloudbeds_configured_version_prefix_is_not_duplicated(lab):
    lab.cloudbeds_base_url = "https://api.cloudbeds.com/api/v1.1"
    assert worker(lab).run_once(now=lab.now).disposition.value == "settled"
    assert all(
        r.url.path.startswith("/api/v1.3/") and r.url.path.count("/api/") == 1
        for r in lab.provider_requests
    )


@pytest.mark.parametrize("currency", ["BRL", "USD"])
def test_cloudbeds_missing_reservation_currency_uses_property_fact(lab, currency):
    handler = lab.provider_client._transport.handler

    def respond(req):
        if req.url.path.endswith("getCurrencySettings"):
            lab.provider_requests.append(req)
            return httpx.Response(
                200, json={"success": True, "data": {"default": currency}}
            )
        response = handler(req)
        if req.url.path.endswith("getReservation"):
            row = response.json()
            row["data"].pop("currency")
            return httpx.Response(200, json=row)
        return response

    lab.provider_client._transport.handler = respond
    result = worker(lab).run_once(now=lab.now)
    assert any(
        r.url.path.endswith("getCurrencySettings") for r in lab.provider_requests
    )
    assert result.disposition.value == (
        "settled" if currency == "BRL" else "preparation_terminal"
    )
    assert len([r for r in lab.provider_requests if r.method == "POST"]) == (
        1 if currency == "BRL" else 0
    )


def test_existing_provider_payment_is_not_blindly_credited_again(lab):
    lab.paid = "420.00"
    assert worker(lab).run_once(now=lab.now).disposition.value == "preparation_terminal"
    assert not [r for r in lab.provider_requests if r.method == "POST"]


@pytest.mark.parametrize("delay", [1, 31, 3600])
def test_read_only_effect_observation_can_wait_for_durable_handoff(lab, tmp_path, delay):
    from reservation_boundary.sqlite_store import SQLiteBoundaryStore
    from reservation_followup.workers import PaymentOutboxWorker
    from v2_application.recovery import HandoffCoordinator, ManualReviewHandoffProjector
    from v2_application.stripe_payment_effects import StripePaymentEffectObserver

    lab.status = "canceled"
    worker(lab).run_once(now=lab.now)
    boundary = SQLiteBoundaryStore.open_path_v8(tmp_path / "delayed-effect.sqlite3")
    resolver = SimpleNamespace(lead_id_for_command=lambda _: "manychat:12345")
    coordinator = HandoffCoordinator(store=lab.followup)
    observer = StripePaymentEffectObserver(
        followup=lab.followup, boundary=boundary, lead_resolver=resolver,
        coordinator=coordinator, clock=lambda: lab.now,
    )
    outbox = PaymentOutboxWorker(
        store=lab.followup, delivery=observer, worker_id="worker:delayed-observation",
        lease_ttl=timedelta(seconds=30),
    )
    try:
        assert outbox.run_once(now=lab.now).disposition.value == "retryable_failure"
        lab.now += timedelta(seconds=delay)
        ManualReviewHandoffProjector(
            execution=lab.execution, followup=lab.followup,
            coordinator=coordinator, lead_resolver=resolver,
        ).run_once(now=lab.now)
        assert outbox.run_once(now=lab.now).disposition.value == "delivered"
        assert outbox.run_once(now=lab.now).disposition.value == "idle"
        assert not [r for r in lab.provider_requests if r.method == "POST"]
    finally:
        boundary.close()


def test_payment_receipt_completion_uses_time_after_delivery(lab, monkeypatch):
    from reservation_followup.types import PaymentReceipt
    from reservation_followup.workers import PaymentOutboxWorker

    lab.status = "canceled"
    worker(lab).run_once(now=lab.now)
    ticks = iter([10.0, 11.0])
    monkeypatch.setattr("reservation_followup.workers.time.monotonic", lambda: next(ticks))
    class ReceiptPort:
        delivery_id = "fixture:receipt-time"
        delivery_version = 1
        def deliver(self, claim):
            return PaymentReceipt.for_claim(
                claim, receipt_id="receipt:after-delivery", delivery_reference="fixture:accepted",
                delivered_at=lab.now + timedelta(milliseconds=500),
            )
    outbox = PaymentOutboxWorker(
        store=lab.followup, delivery=ReceiptPort(), worker_id="worker:receipt-time",
        lease_ttl=timedelta(seconds=30),
    )
    assert outbox.run_once(now=lab.now).disposition.value == "delivered"
