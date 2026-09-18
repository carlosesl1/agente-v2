"""Integration of persisted operation context into the executor (offline)."""

from dataclasses import replace
from datetime import timedelta

from reservation_domain import ExecutionCertainty, loads_outcome
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from tests.phase6_helpers import confirmed_anchor, payment_effect_policy
from tests.test_v2_execution_context import _queued_state
from tests.test_v2_outcome_projector import (
    NOW,
    _finish_next,
    _package_command,
    _persist,
    _stores,
)
from v2_application.active_execution import ReservationExecutionStatusResolver
from v2_application.completion import PublicOutboxStore, PublicReply
from v2_application.lead_identity import DurableLeadResolver
from v2_application.reservations import ReservationAllocator
from v2_contracts.channel import PublicMessageAuthor
from v2_contracts.model import ModelProposal
from v2_contracts.providers import ReadKind, ReadRequest


def test_settlement_uses_reservation_binding_not_the_initiation_id(tmp_path):
    execution, payments, _ = _stores(tmp_path)
    followup = SQLiteFollowupUnitOfWork.open(tmp_path / "followup.sqlite3")
    parent = _package_command()
    _persist(execution, ReservationAllocator().allocate(parent).commands)
    confirmed = _finish_next(
        execution, now=NOW, certainty=ExecutionCertainty.EFFECT_CONFIRMED
    )
    ledger = next(
        ledger
        for command, ledger in execution.list_outcome_projection_inputs()
        if command.command_id == confirmed.command_id
    )
    opened = followup.open_payment(
        confirmed_anchor(outcome=loads_outcome(ledger.outcome_json)),
        payment_effect_policy(),
    )
    try:
        context = ReservationExecutionStatusResolver(
            execution, payment_store=payments, followup=followup
        ).context(_queued_state(parent))
        component = next(
            c for c in context.components if c.command_id == confirmed.command_id
        )
        assert component.payment_id != opened.state.subject.payment_id
        assert component.settlement_status == "recorded"
        assert component.settlements[0].payment_id == opened.state.subject.payment_id
        assert component.settlements[0].status == opened.state.status.value
        assert component.settlements[0].certainty is None
        assert component.payment_initiation_status == "not_recorded"
    finally:
        followup.close()
        payments.close()
        execution.close()


def test_executor_receives_persisted_results_and_outbox_in_all_read_frames(
    tmp_path, monkeypatch
):
    from tests import test_v2_turn_executor as fixtures

    event = replace(fixtures.EVENT, lead_id="manychat:12345", subscriber_id="12345")
    batch = replace(
        fixtures.BATCH,
        lead_id=event.lead_id,
        subscriber_id=event.subscriber_id,
        events=(event,),
    )
    monkeypatch.setattr(fixtures, "EVENT", event)
    monkeypatch.setattr(fixtures, "BATCH", batch)
    monkeypatch.setattr(
        fixtures,
        "AUTHORITY",
        replace(fixtures.AUTHORITY, scope_subject_id=event.subscriber_id),
    )
    from tests.test_v2_turn_executor import (
        _approval_expiry_fixture,
        SequenceClock,
        BATCH,
        EVENT,
        AUTHORITY,
        MappingAuthority,
        _install_public_authority,
    )

    store, model, read_port, confirmation, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=30),
        confirmation_clock=SequenceClock(),
    )
    execution, payments, _ = _stores(tmp_path)
    followup = SQLiteFollowupUnitOfWork.open(tmp_path / "followup.sqlite3")
    public = PublicOutboxStore((tmp_path / "public.sqlite3").resolve())
    try:
        executor.execute(confirmation)
        state = store.load_state(BATCH.lead_id).state
        parent = state.workflow.command
        _persist(execution, ReservationAllocator().allocate(parent).commands)
        _finish_next(execution, now=NOW, certainty=ExecutionCertainty.EFFECT_CONFIRMED)
        public.enqueue(
            PublicReply(
                "release:result",
                BATCH.lead_id,
                "result:async",
                "manychat",
                ("Resultado persistido da reserva.",),
                PublicMessageAuthor.AUTHENTICATED_SYSTEM,
            ),
            now=NOW,
        )
        resolver = ReservationExecutionStatusResolver(
            execution,
            payment_store=payments,
            public_store=public,
            followup=followup,
            lead_resolver=DurableLeadResolver(
                boundary=store, execution=execution, followup=followup
            ),
        )
        executor._execution_status_resolver = resolver
        expected = resolver.context(state)
        assert expected.status == "confirmed"
        assert len(expected.components) == 1
        assert (
            resolver.context(replace(state, workflow=None)).components
            == expected.components
        )
        foreign = resolver.context(
            replace(state, workflow=None, lead_key="manychat:999999")
        )
        assert foreign.components == foreign.messages == ()
        event = replace(
            EVENT,
            event_id="evt:" + "c" * 64,
            text="Consulte mais duas noites.",
            payload_hash="c" * 64,
        )
        batch = replace(
            BATCH, batch_id="agg:" + "c" * 64, events=(event,), combined_text=event.text
        )
        authority = replace(
            AUTHORITY,
            authorization_id="auth:context",
            allocation_ids=("allocation:context",),
            allocation_manifest_hash="c" * 64,
        )
        _install_public_authority(store, authority)
        executor._public_authority = MappingAuthority({batch.batch_id: authority})
        model.proposals.extend(
            (
                ModelProposal(
                    batch.batch_id,
                    "inform",
                    ("Vou consultar.",),
                    (),
                    (
                        ReadRequest(
                            "read:other",
                            ReadKind.LODGING,
                            check_in=NOW.date(),
                            check_out=(NOW + timedelta(days=2)).date(),
                            adults=2,
                            children=0,
                        ),
                    ),
                    (),
                ),
                ModelProposal(
                    batch.batch_id,
                    "inform",
                    ("Sua reserva anterior permanece confirmada.",),
                    (),
                    (),
                    (),
                ),
            )
        )
        calls = len(model.calls)
        ledger_before = execution.list_outcome_projection_inputs()
        result = executor.execute(batch)
        frames = model.calls[calls:]
        assert len(frames) == 2
        assert frames[1].observations
        assert all(f.execution_components == expected.components for f in frames)
        assert all(f.operational_messages == expected.messages for f in frames)
        assert result.receipt.command_rows == result.receipt.relay_rows == ()
        assert store.load_state(BATCH.lead_id).state.workflow == state.workflow
        assert execution.list_outcome_projection_inputs() == ledger_before
        assert public.pending_count() == 1
    finally:
        public.close()
        followup.close()
        execution.close()
        payments.close()
        store.close()
