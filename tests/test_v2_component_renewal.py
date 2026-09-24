"""Controlled renewal tests. Real turns/SQLite; no live provider or channel calls."""
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from reservation_domain import AwaitingConfirmationState, ExecutionCertainty
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from tests.test_v2_outcome_projector import _finish_next, _persist
from tests.test_v2_turn_executor import (
    AUTHORITY, BATCH, EVENT, NOW, FakeLodgingReadPort, FixedClock,
    MappingAuthority, SequenceClock, _approval_expiry_fixture,
    _install_public_authority,
)
from v2_application.active_execution import ReservationExecutionStatusResolver
from v2_application.reservations import ReservationAllocator
from v2_application.reads import V2ReadService
from v2_contracts.execution_context import ProviderReservationStatus
from v2_contracts.model import ModelFact, ModelProposal
from v2_contracts.providers import ReadKind, ReadRequest


@pytest.fixture
def renewal(tmp_path):
    store, model, read, confirmation, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=30), confirmation_clock=SequenceClock(),
    )
    executor.execute(confirmation)
    executor._clock = SimpleNamespace(now=lambda: NOW + timedelta(seconds=20))
    parent = store.load_state(BATCH.lead_id).state.workflow.command
    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
    followup = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
    commands = ReservationAllocator().allocate(parent).commands
    _persist(execution, commands)
    _finish_next(execution, now=NOW + timedelta(seconds=15), certainty=ExecutionCertainty.EFFECT_CONFIRMED)
    lab = SimpleNamespace(
        store=store, model=model, executor=executor, execution=execution,
        followup=followup, parent=parent, read=read, confirmation=confirmation,
        status="canceled", paid="0.00", source="observed", status_age=timedelta(0),
    )
    def status_read(**kwargs):
        return ProviderReservationStatus(
            lab.source, lab.executor._clock.now() - lab.status_age,
            lab.status if lab.source == "observed" else None,
            "NOT_PAID" if lab.source == "observed" else None,
            lab.paid if lab.source == "observed" else None,
            "450.00" if lab.source == "observed" else None,
            "BRL" if lab.source == "observed" else None,
        )
    executor._execution_status_resolver = ReservationExecutionStatusResolver(
        execution, followup=followup,
        payment_store=SimpleNamespace(context_for_payment=lambda _: ()),
        lead_resolver=SimpleNamespace(lead_id_for_command=lambda _: BATCH.lead_id),
        reservation_status_reader=SimpleNamespace(read=status_read),
    )
    try:
        yield lab
    finally:
        store.close()
        execution.close()
        followup.close()


def next_batch(lab, name, text):
    instant = lab.executor._clock.now() + timedelta(seconds=1)
    lab.executor._clock = SimpleNamespace(now=lambda: instant)
    event = replace(EVENT, event_id=f"event:{name}", text=text, payload_hash="e" * 64, occurred_at=lab.executor._clock.now())
    batch = replace(BATCH, batch_id=f"batch:{name}", events=(event,), combined_text=text)
    authority = replace(
        AUTHORITY, authorization_id=f"auth:{name}",
        allocation_ids=(f"allocation:{name}",), allocation_manifest_hash="d" * 64,
    )
    _install_public_authority(lab.store, authority)
    lab.executor._public_authority = MappingAuthority({batch.batch_id: authority})
    return batch


def selection(lab, name="renewal"):
    batch = next_batch(lab, name, "Quero fazer uma nova reserva no lugar da cancelada.")
    facts = (
        ModelFact("service", "hostel"),
        ModelFact("start_date", date(2026, 8, 10)),
        ModelFact("end_date", date(2026, 8, 12)),
        ModelFact("adults", 2), ModelFact("children", 0),
        ModelFact("payment_method", "stripe"),
    )
    read = ReadRequest(
        f"read:{name}", ReadKind.LODGING, check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12), adults=2, children=0,
    )
    proposals = [
        ModelProposal(batch.batch_id, "inform", (), facts, (read,), ()),
        ModelProposal(batch.batch_id, "select", ("Segue um novo resumo.",), facts, (), (),
                      target_offer_id="offer:" + "7" * 64),
        ModelProposal(batch.batch_id, "inform", ("A operação foi bloqueada.",), (), (), ()),
    ]
    lab.model.proposals[:] = proposals
    lab.executor._reads = V2ReadService({ReadKind.LODGING: FakeLodgingReadPort(lab.store)})
    return batch


def test_cancelled_unpaid_component_can_reach_new_summary_without_replay(renewal):
    lab = renewal
    old = lab.execution.list_outcome_projection_inputs()
    batch = selection(lab)
    result = lab.executor.execute(batch)
    state = lab.store.load_state(BATCH.lead_id).state
    assert isinstance(state.workflow, AwaitingConfirmationState)
    assert result.receipt.command_rows == result.receipt.relay_rows == ()
    assert state.workflow.meta.workflow_id != lab.parent.workflow_id
    assert lab.execution.list_outcome_projection_inputs() == old
    assert lab.executor.execute(batch).replayed
    assert lab.store._connection.execute("SELECT count(*) FROM boundary_commands").fetchone()[0] == 1


@pytest.mark.parametrize("change", [
    {"status": "confirmed"}, {"source": "unavailable"}, {"paid": "450.00"},
    {"status_age": timedelta(minutes=2)}, {"status_age": -timedelta(seconds=1)},
])
def test_unsafe_or_unavailable_target_cannot_replace_old_command(renewal, change):
    for name, value in change.items():
        setattr(renewal, name, value)
    before = renewal.store.load_state(BATCH.lead_id).state.workflow
    result = renewal.executor.execute(selection(renewal))
    assert renewal.store.load_state(BATCH.lead_id).state.workflow == before
    assert result.receipt.command_rows == result.receipt.relay_rows == ()
