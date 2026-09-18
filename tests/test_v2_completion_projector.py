from __future__ import annotations

from datetime import timedelta

import pytest

from reservation_domain import ExecutionCertainty
from reservation_execution import PreparationFailure
from tests.test_v2_outcome_projector import (
    NOW,
    US_PHONE,
    _finish_next,
    _package_command,
    _persist,
    _stores,
)
from tests.v2_completion_helpers import CompletionMaya, authored_chunks, make_completion
from v2_adapters.stripe import StripeLinkAdapter
from v2_adapters.wise import WiseInstructionAdapter
from v2_application.completion import PublicOutboxStore, PublicReply
from v2_application.completion_projector import _opaque
from v2_application.payments import PaymentInitiationWorker, PaymentService
from v2_application.reservations import ReservationAllocator
from v2_contracts.channel import PublicMessageAuthor
from v2_contracts.payments import BusinessUnit, PaymentMethod


class _ClosedInstruction:
    def instruction(self, obligation):
        raise AssertionError(
            f"unexpected non-Stripe obligation: {obligation.payment_id}"
        )


class _ClosedStripe:
    def create_link(self, obligation):
        raise AssertionError(f"unexpected Stripe obligation: {obligation.payment_id}")


class _StripeTransport:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if self.fail:
            raise TimeoutError("provider response lost")
        return {
            "link_id": f"plink-test-{request.business_unit.value}",
            "url": (
                f"https://buy.stripe.com/test_{request.business_unit.value}_completion"
            ),
        }


def _payment_worker(payments, transport: _StripeTransport) -> PaymentInitiationWorker:
    stripe = StripeLinkAdapter(
        transport=transport,
        account_profiles={
            BusinessUnit.HOSTEL: "stripe-account:hostel:test",
            BusinessUnit.AGENCY: "stripe-account:agency:test",
        },
        enabled=True,
        subscriber_id="1873018537",
    )
    closed = _ClosedInstruction()
    return PaymentInitiationWorker(
        store=payments,
        payments=PaymentService(stripe=stripe, wise=closed, pix=closed),
        worker_id="worker:completion-test",
        lease_ttl=timedelta(seconds=30),
    )


def _fail_next_before_provider(execution, *, now) -> None:
    claim = execution.claim_command(
        worker_id="worker:completion-preflight-fixture",
        now=now,
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    disposition = execution.release_preparation_failure(
        claim,
        PreparationFailure(
            reason="booking_profile_incomplete",
            retryable=False,
            evidence=("f" * 64,),
        ),
        now=now,
    )
    assert disposition.value == "terminal_not_called"


@pytest.mark.parametrize("service", ["package", "reserve_lodging", "book_activity"])
@pytest.mark.parametrize("certainty", list(ExecutionCertainty))
def test_terminal_result_is_an_event_not_a_controller_template(
    tmp_path, service, certainty
):
    execution, payments, _ = _stores(tmp_path)
    public = PublicOutboxStore(tmp_path / "public.sqlite3")
    commands = (
        ReservationAllocator().allocate(_package_command(phone_e164=US_PHONE)).commands
    )
    if service != "package":
        commands = tuple(c for c in commands if c.operation.value == service)
    _persist(execution, commands)
    projector = make_completion(tmp_path, execution, payments, public)
    try:
        assert projector.events() == ()
        for i in range(len(commands)):
            if certainty is ExecutionCertainty.NOT_CALLED:
                _fail_next_before_provider(execution, now=NOW + timedelta(seconds=i))
            else:
                _finish_next(
                    execution, now=NOW + timedelta(seconds=i), certainty=certainty
                )
        (event,) = projector.events()
        assert event.command_ids == tuple(sorted(c.command_id for c in commands))
        assert event.kind == "reservation_result"
        assert public.pending_count() == 0
        assert projector.run_once(now=NOW).inserted == 1
        (request,) = projector.executor._model.calls
        assert {c.outcome.certainty for c in request.execution_components} == {
            certainty.value
        }
        assert len(request.execution_components) == len(commands)
        assert authored_chunks(projector)[0].text == CompletionMaya.text
        assert authored_chunks(projector)[0].author == "maya"
        assert projector.run_once(now=NOW).inserted == 0
        assert len(projector.executor._model.calls) == 1
    finally:
        projector._boundary.close()
        public.close()
        payments.close()
        execution.close()


@pytest.mark.parametrize("method", [PaymentMethod.STRIPE, PaymentMethod.WISE])
def test_package_results_and_two_payment_components_reach_one_maya_turn(
    tmp_path, method
):
    execution, payments, outcome = _stores(tmp_path, enabled_methods=(method,))
    public = PublicOutboxStore(tmp_path / "public.sqlite3")
    commands = (
        ReservationAllocator()
        .allocate(_package_command(payment_method=method.value))
        .commands
    )
    _persist(execution, commands)
    for i in range(2):
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=i),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
    assert outcome.run_once(now=NOW + timedelta(seconds=2)).inserted == 2
    worker = (
        _payment_worker(payments, _StripeTransport())
        if method is PaymentMethod.STRIPE
        else PaymentInitiationWorker(
            store=payments,
            payments=PaymentService(
                stripe=_ClosedStripe(),
                wise=WiseInstructionAdapter(
                    instructions={
                        "stripe-account:hostel:test": "Dados Wise hostel",
                        "stripe-account:agency:test": "Dados Wise agência",
                    }
                ),
                pix=_ClosedInstruction(),
            ),
            worker_id="worker:wise-completion",
            lease_ttl=timedelta(seconds=30),
        )
    )
    for i in range(2):
        worker.run_once(now=NOW + timedelta(seconds=3 + i))
    projector = make_completion(tmp_path, execution, payments, public)
    try:
        assert len(projector.events()) == 3
        assert projector.run_once(now=NOW).inserted == 1
        (request,) = projector.executor._model.calls
        assert len(request.completion_events) == 3
        assert len(request.execution_components) == 2
        offers = [p.offer for c in request.execution_components for p in c.payments]
        assert len(offers) == 2
        assert all(not p.settled for p in offers)
        if method is PaymentMethod.STRIPE:
            assert all(
                p.public_url.startswith("https://buy.stripe.com/") for p in offers
            )
        else:
            assert all("Dados Wise" in p.public_text for p in offers)
            assert {p.public_text for p in offers} == {
                p.public_text for p in payments.completed_offers()
            }
        assert len(authored_chunks(projector)) == 1
        assert public.pending_count() == 0
        assert projector.run_once(now=NOW).inserted == 0
    finally:
        projector._boundary.close()
        public.close()
        payments.close()
        execution.close()


def test_unknown_payment_has_no_success_event_but_exposes_uncertainty_in_context(
    tmp_path,
):
    execution, payments, outcome = _stores(tmp_path)
    public = PublicOutboxStore(tmp_path / "public.sqlite3")
    command = ReservationAllocator().allocate(_package_command()).commands[0]
    _persist(execution, (command,))
    _finish_next(execution, now=NOW, certainty=ExecutionCertainty.EFFECT_CONFIRMED)
    outcome.run_once(now=NOW + timedelta(seconds=1))
    assert (
        _payment_worker(payments, _StripeTransport(fail=True))
        .run_once(now=NOW + timedelta(seconds=2))
        .disposition.value
        == "manual_review"
    )
    projector = make_completion(tmp_path, execution, payments, public)
    try:
        assert len(projector.events()) == 1
        projector.run_once(now=NOW)
        (request,) = projector.executor._model.calls
        initiation = request.execution_components[0].payments[0]
        assert initiation.status == "manual_review"
        assert initiation.offer is None
    finally:
        projector._boundary.close()
        public.close()
        payments.close()
        execution.close()


def test_existing_legacy_release_suppresses_regeneration_without_rewriting_history(
    tmp_path,
):
    execution, payments, _ = _stores(tmp_path)
    public = PublicOutboxStore(tmp_path / "public.sqlite3")
    command = ReservationAllocator().allocate(_package_command()).commands[0]
    _persist(execution, (command,))
    _finish_next(execution, now=NOW, certainty=ExecutionCertainty.EFFECT_CONFIRMED)
    release = _opaque(
        "release:00-reservation",
        command.payload.customer.customer_ref,
        command.draft_id,
        str(command.draft_version),
    )
    public.enqueue(
        PublicReply(
            release_id=release,
            lead_id="manychat:1873018537",
            message_id="message:legacy",
            channel="manychat",
            chunks=("Texto histórico preservado.",),
            author=PublicMessageAuthor.AUTHENTICATED_SYSTEM,
        ),
        now=NOW,
    )
    projector = make_completion(tmp_path, execution, payments, public)
    try:
        assert projector.run_once(now=NOW).inserted == 0
        assert not projector.executor._model.calls
        assert (
            public.conversation_messages("manychat:1873018537")[0].text
            == "Texto histórico preservado."
        )
    finally:
        projector._boundary.close()
        public.close()
        payments.close()
        execution.close()
