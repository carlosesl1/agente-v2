"""Offline causal tests: real stores/ledgers, no provider or channel calls."""

from dataclasses import replace
from datetime import timedelta

import pytest

from reservation_domain import ExecutionCertainty, loads_state
from v2_application.active_execution import (
    ReservationExecutionStatusResolver,
    blocks_active_commercial_progression,
)
from v2_application.completion import PublicOutboxStore, PublicReply
from v2_application.relay_worker import build_reservation_relay_bundle
from v2_application.reservations import ReservationAllocator
from v2_contracts.channel import (
    PublicAcceptanceOperation,
    PublicAcceptanceState,
    PublicChannelAcceptance,
    PublicMessageAuthor,
)
from v2_contracts.model import ModelFact, ModelProposal, ModelRequest
from v2_contracts.critical_actions import ApprovalBasis, CriticalActionKind
from v2_contracts.providers import ReadKind, ReadRequest
from tests.test_v2_active_execution import _state
from tests.test_v2_outcome_projector import (
    NOW,
    _finish_next,
    _package_command,
    _persist,
    _stores,
)


def _queued_state(parent):
    return replace(
        _state(),
        workflow=loads_state(
            build_reservation_relay_bundle(parent).expected_final_state.decode()
        ),
    )


def test_partial_result_survives_reopen_as_components_and_reaches_wire(tmp_path):
    import json
    from v2_adapters.hermes_model import _request_wire

    execution, payments, projector = _stores(tmp_path)
    parent = _package_command()
    _persist(execution, ReservationAllocator().allocate(parent).commands)
    confirmed = _finish_next(
        execution, now=NOW, certainty=ExecutionCertainty.EFFECT_CONFIRMED
    )
    _finish_next(
        execution,
        now=NOW + timedelta(seconds=1),
        certainty=ExecutionCertainty.CALLED_NO_EFFECT,
    )
    assert projector.run_once(now=NOW + timedelta(seconds=2)).inserted == 0
    execution.close()
    from reservation_execution.sqlite_store import SQLiteUnitOfWork

    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
    try:
        context = ReservationExecutionStatusResolver(
            execution, payment_store=payments
        ).context(_queued_state(parent))
        assert context.status == "partial_failure"
        assert len(context.components) == 2
        success = next(
            c for c in context.components if c.command_id == confirmed.command_id
        )
        assert success.outcome.certainty == ExecutionCertainty.EFFECT_CONFIRMED.value
        assert success.outcome.provider_reference
        assert success.offer.offer_id == confirmed.payload.components[0].offer_id
        assert all(
            c.payment_initiation_status == "not_recorded" for c in context.components
        )
        assert all(c.settlement_status == "unavailable" for c in context.components)
        request = ModelRequest(
            "request:context",
            "manychat:1",
            "event:context",
            "Deu certo?",
            "pt-BR",
            0,
            execution_components=context.components,
        )
        wire = json.loads(json.loads(_request_wire(request, "Maya"))["messages"][-1][1])
        rows = wire["execution_components"]
        assert len(rows) == 2
        row = next(c for c in rows if c["command_id"] == confirmed.command_id)
        assert row["provider_reference"] == success.outcome.provider_reference
        assert row["certainty"] == "effect_confirmed"
        assert row["total"] == success.offer.amount
        assert row["payment"]["initiation_status"] == "not_recorded"
    finally:
        execution.close()
        payments.close()


def test_missing_and_fenced_outcomes_do_not_claim_no_effect(tmp_path):
    execution, payments, _ = _stores(tmp_path)
    parent = _package_command()
    state = _queued_state(parent)
    resolver = ReservationExecutionStatusResolver(execution)
    try:
        before_relay = resolver.context(state)
        assert len(before_relay.components) == 2
        assert all(
            c.command_id is None and c.outcome is None for c in before_relay.components
        )
        _persist(execution, ReservationAllocator().allocate(parent).commands)
        from reservation_domain import dumps_command
        from reservation_execution import DispatchRequest

        claim = execution.claim_command(
            worker_id="worker:test", now=NOW, lease_ttl=timedelta(seconds=30)
        )
        execution.fence_dispatch(
            claim,
            DispatchRequest.from_command(claim.command, dumps_command(claim.command)),
            now=NOW,
        )
        context = resolver.context(state)
        assert context.status == "executing"
        assert all(c.outcome is None for c in context.components)
        assert any(c.ledger_status == "dispatch_fenced" for c in context.components)
    finally:
        execution.close()
        payments.close()


def test_outbox_context_is_read_only_deduplicated_and_not_a_delivery_receipt(tmp_path):
    path = (tmp_path / "public.sqlite3").resolve()
    public = PublicOutboxStore(path)
    reply = PublicReply(
        "release:completion",
        "manychat:1",
        "message:completion",
        "manychat",
        ("Hospedagem confirmada; passeio não concluído.", "Referência: TEST-123"),
        PublicMessageAuthor.AUTHENTICATED_SYSTEM,
    )
    public.enqueue(reply, now=NOW)
    public.enqueue(reply, now=NOW)
    public.enqueue(
        replace(reply, release_id="release:other", lead_id="manychat:2"), now=NOW
    )
    try:
        pending = public.conversation_messages("manychat:1")
        assert len(pending) == 2
        assert [m.text for m in pending] == list(reply.chunks)
        assert all(m.status == "pending" for m in pending)
        claim = public.claim(
            worker_id="worker:public", now=NOW, lease_ttl=timedelta(seconds=30)
        )
        public.complete(
            claim,
            PublicChannelAcceptance(
                PublicAcceptanceState.ACCEPTED_BY_MANYCHAT,
                (PublicAcceptanceOperation.SEND_CONTENT,),
                (None,),
                ("dispatch:test",),
            ),
            now=NOW + timedelta(seconds=1),
        )
        public.close()
        public = PublicOutboxStore(path)
        rows = public.conversation_messages("manychat:1")
        assert rows[0].outbox_id == pending[0].outbox_id
        assert rows[0].status == "accepted_by_manychat"
        assert rows[1].status == "pending"
        assert public.pending_count() == 3
    finally:
        public.close()


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "executing",
        "confirmed",
        "partial_failure",
        "uncertain",
        "failed_no_effect",
    ],
)
def test_information_and_reads_are_not_business_replay(status):
    request = ReadRequest(
        "read:independent",
        ReadKind.LODGING,
        check_in=NOW.date(),
        check_out=(NOW + timedelta(days=2)).date(),
        adults=2,
        children=0,
    )
    proposal = ModelProposal(
        "event:info",
        "inform",
        ("Vou consultar outra data.",),
        (ModelFact("adults", 2),),
        (request,),
        (),
    )
    state = _queued_state(_package_command())
    assert not blocks_active_commercial_progression(
        state, proposal, execution_status=status
    )
    assert blocks_active_commercial_progression(
        state,
        replace(
            proposal,
            intent="confirm",
            facts=(),
            read_requests=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(CriticalActionKind.RESERVE_LODGING,),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        ),
        execution_status=status,
    )
    assert blocks_active_commercial_progression(
        state,
        replace(
            proposal,
            intent="select",
            facts=(),
            read_requests=(),
            target_offer_id="offer:test",
        ),
        execution_status=status,
    )


def test_payment_creation_is_not_settlement_and_pending_is_not_absent(tmp_path):
    from tests.test_v2_completion_projector import _payment_worker, _StripeTransport

    execution, payments, projector = _stores(tmp_path)
    parent = _package_command()
    _persist(execution, ReservationAllocator().allocate(parent).commands)
    _finish_next(execution, now=NOW, certainty=ExecutionCertainty.EFFECT_CONFIRMED)
    _finish_next(
        execution,
        now=NOW + timedelta(seconds=1),
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
    )
    projector.run_once(now=NOW + timedelta(seconds=2))
    try:
        resolver = ReservationExecutionStatusResolver(execution, payment_store=payments)
        queued = resolver.context(_queued_state(parent))
        assert all(c.payment_initiation_status == "recorded" for c in queued.components)
        assert all(
            c.payments[0].status == "queued" and c.payments[0].offer is None
            for c in queued.components
        )
        worker = _payment_worker(payments, _StripeTransport())
        worker.run_once(now=NOW + timedelta(seconds=3))
        worker.run_once(now=NOW + timedelta(seconds=4))
        done = resolver.context(_queued_state(parent))
        assert all(c.payments[0].status == "completed" for c in done.components)
        assert all(c.payments[0].offer.public_url for c in done.components)
        assert all(c.settlement_status == "unavailable" for c in done.components)
        assert all(
            c.payments[0].selection.obligation.amount_minor > 0 for c in done.components
        )
    finally:
        execution.close()
        payments.close()


@pytest.mark.parametrize("certainty", list(ExecutionCertainty))
def test_all_terminal_certainties_remain_distinct_without_replay(tmp_path, certainty):
    execution, payments, _ = _stores(tmp_path)
    parent = _package_command()
    _persist(execution, ReservationAllocator().allocate(parent).commands)
    if certainty is ExecutionCertainty.NOT_CALLED:
        from reservation_execution.adapter import PreparationFailure

        claim = execution.claim_command(
            worker_id="worker:not-called", now=NOW, lease_ttl=timedelta(seconds=30)
        )
        command = claim.command
        execution.release_preparation_failure(
            claim,
            PreparationFailure("synthetic_preparation_failure", False, ()),
            now=NOW,
        )
    else:
        command = _finish_next(execution, now=NOW, certainty=certainty)
    try:
        before = execution.list_outcome_projection_inputs()
        resolver = ReservationExecutionStatusResolver(execution)
        first = resolver.context(_queued_state(parent))
        second = resolver.context(_queued_state(parent))
        assert first == second
        component = next(
            c for c in first.components if c.command_id == command.command_id
        )
        assert component.outcome.certainty == certainty.value
        assert execution.list_outcome_projection_inputs() == before
    finally:
        execution.close()
        payments.close()


def test_unknown_payment_is_visible_and_reading_never_requeues(tmp_path):
    execution, payments, projector = _stores(tmp_path)
    parent = _package_command()
    _persist(execution, ReservationAllocator().allocate(parent).commands)
    _finish_next(execution, now=NOW, certainty=ExecutionCertainty.EFFECT_CONFIRMED)
    _finish_next(
        execution,
        now=NOW + timedelta(seconds=1),
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
    )
    projector.run_once(now=NOW + timedelta(seconds=2))
    claim = payments.claim(
        worker_id="worker:unknown",
        now=NOW + timedelta(seconds=3),
        lease_ttl=timedelta(seconds=30),
    )
    payments.fence(claim, now=NOW + timedelta(seconds=3))
    payments.mark_unknown(claim, now=NOW + timedelta(seconds=4))
    try:
        resolver = ReservationExecutionStatusResolver(execution, payment_store=payments)
        before = tuple(
            payments._connection.execute(
                "SELECT * FROM payment_initiations ORDER BY initiation_id"
            )
        )
        for _ in range(2):
            context = resolver.context(_queued_state(parent))
            payment = next(
                p
                for c in context.components
                for p in c.payments
                if p.initiation_id == claim.initiation_id
            )
            assert payment.status == "manual_review"
            assert payment.dispatch_started and payment.offer is None
        assert (
            tuple(
                payments._connection.execute(
                    "SELECT * FROM payment_initiations ORDER BY initiation_id"
                )
            )
            == before
        )
    finally:
        payments.close()
        execution.close()


def test_unknown_communication_remains_visible_but_not_claimable(tmp_path):
    public = PublicOutboxStore((tmp_path / "public.sqlite3").resolve())
    reply = PublicReply(
        "release:unknown",
        "manychat:1",
        "message:unknown",
        "manychat",
        ("Conclusão.",),
        PublicMessageAuthor.AUTHENTICATED_SYSTEM,
    )
    try:
        public.enqueue(reply, now=NOW)
        claim = public.claim(
            worker_id="worker:test", now=NOW, lease_ttl=timedelta(seconds=30)
        )
        public.mark_manual_review(claim, now=NOW)
        rows = public.conversation_messages(reply.lead_id)
        assert rows[0].status == "manual_review"
        assert rows == public.conversation_messages(reply.lead_id)
        assert (
            public.claim(
                worker_id="worker:test",
                now=NOW + timedelta(minutes=1),
                lease_ttl=timedelta(seconds=30),
            )
            is None
        )
    finally:
        public.close()
