"""Causal payment evidence tests; no live network or production databases."""
import hashlib
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from tests.test_v2_stripe_settlement import lab, worker  # noqa: F401
from v2_adapters.execution_context import component_wire
from v2_application.active_execution import ReservationExecutionStatusResolver


def test_maya_keeps_verified_capture_when_provider_settlement_is_unknown(lab):  # noqa: F811 - imported pytest fixture
    lab.behavior = "timeout"
    worker(lab).run_once(now=lab.now)
    resolver = ReservationExecutionStatusResolver(lab.execution, followup=lab.followup)
    command, ledger = lab.execution.list_outcome_projection_inputs()[0]
    wire = component_wire(resolver._component(command, ledger))
    settlement = wire["payment"]["settlements"][0]
    assert settlement["certainty"] == "dispatched_unknown"
    evidence = settlement["verified_payment"]
    assert evidence["source"] == "stripe"
    assert evidence["status"] == "captured"
    assert evidence["amount_minor"] == 42000
    assert evidence["currency"] == "BRL"
    assert evidence["observed_at"]
    assert settlement["status"] != "paid"


def test_ambiguous_cloudbeds_response_keeps_received_response_evidence(lab):  # noqa: F811 - imported pytest fixture
    handler = lab.provider_client._transport.handler
    body = b'{"success":true,"paymentID":"pay123"}'
    def respond(request):
        if request.method == "POST":
            lab.provider_requests.append(request)
            return httpx.Response(200, content=body)
        return handler(request)
    lab.provider_client._transport.handler = respond
    assert worker(lab).run_once(now=lab.now).disposition.value == "manual_review"
    state = lab.followup.load_payment(lab.receipt.payment_id)
    assert state.status.value != "paid"
    assert hashlib.sha256(body).hexdigest() in state.settlement_finish.outcome.claim_evidence
    assert len([r for r in lab.provider_requests if r.method == "POST"]) == 1
    assert worker(lab).run_once(now=lab.now + timedelta(seconds=60)).disposition.value == "idle"


def test_physical_payment_effect_deadline_stays_closed(lab):  # noqa: F811 - imported pytest fixture
    from reservation_followup.workers import PaymentOutboxWorker
    lab.status = "canceled"
    worker(lab).run_once(now=lab.now)
    def not_ready(claim):
        raise ValueError("fixture not ready")
    port = SimpleNamespace(delivery_id="fixture:physical", delivery_version=1, deliver=not_ready)
    outbox = PaymentOutboxWorker(store=lab.followup, delivery=port, worker_id="worker:physical", lease_ttl=timedelta(seconds=30))
    assert outbox.run_once(now=lab.now).disposition.value == "retryable_failure"
    with pytest.raises(ValueError, match="immutable dispatch deadline"):
        outbox.run_once(now=lab.now + timedelta(seconds=31))
