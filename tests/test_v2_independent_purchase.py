"""Separate new consent from retained captured funds; controlled I/O only."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from reservation_domain import AwaitingConfirmationState
from tests.test_v2_component_renewal import renewal, next_batch, selection  # noqa: F401
from tests.test_v2_component_renewal_guards import confirmation_proposals
from tests.test_v2_stripe_settlement import lab, worker  # noqa: F401
from tests.test_v2_turn_executor import BATCH, NOW
from v2_adapters.execution_context import component_wire
from v2_application.active_execution import (
    ReservationExecutionStatusResolver, _terminal_unpaid_component,
    blocks_active_commercial_progression,
)
from v2_contracts.execution_context import PaymentSettlementContext, ProviderReservationStatus
from v2_contracts.model import ModelFact, ModelProposal


def test_model_protocol_separates_new_consent_from_old_capture():
    from v2_adapters.hermes_model import _ACTIVE_EXECUTION_SYSTEM_SUFFIX
    assert "terminal not_dispatched" in _ACTIVE_EXECUTION_SYSTEM_SUFFIX
    assert "separately confirmed new purchase" in _ACTIVE_EXECUTION_SYSTEM_SUFFIX
    assert "transfer an old checkout or capture" in _ACTIVE_EXECUTION_SYSTEM_SUFFIX
    assert "pending settlement or captured money" not in _ACTIVE_EXECUTION_SYSTEM_SUFFIX


def captured_history(renewal):
    """Guard/turn-level controlled context; producer-level test below is separate."""
    resolver = renewal.executor._execution_status_resolver
    renewal.financial_status = "retryable"
    renewal.financial_certainty = "not_dispatched"

    def context(state):
        value = resolver.context(state)
        components = tuple(replace(c, settlement_status="recorded", settlements=(
            PaymentSettlementContext(c.payment_id, 2, 14616, "BRL", "stripe",
                                     renewal.financial_status, renewal.financial_certainty, NOW),
        )) for c in value.components)
        return replace(value, components=components)

    renewal.executor._execution_status_resolver = SimpleNamespace(context=context)
    return context


def test_captured_not_dispatched_has_new_summary_then_distinct_confirmation(renewal):
    value = renewal
    context = captured_history(value)
    before = value.execution.list_outcome_projection_inputs()
    proposal = selection(value, "independent")
    summary = value.executor.execute(proposal)
    state = value.store.load_state(BATCH.lead_id).state
    assert type(state.workflow) is AwaitingConfirmationState
    assert summary.receipt.command_rows == summary.receipt.relay_rows == ()
    assert state.workflow.meta.workflow_id != value.parent.workflow_id
    old_settlement = context(state).components[0].settlements[0]
    assert old_settlement.stripe_capture_observed_at == NOW
    assert old_settlement.certainty == "not_dispatched"
    # The actual model request still sees the old capture, not a sanitized history.
    assert any(c.settlements == (old_settlement,)
               for request in value.model.calls for c in request.execution_components)
    batch = next_batch(value, "independent-confirm", "Confirmo a nova compra separada.")
    confirmation_proposals(value, batch)
    confirmed = value.executor.execute(batch)
    assert len(confirmed.receipt.command_rows) == 1
    assert confirmed.receipt.command_rows[0][0] != value.parent.command_id
    new_command = value.store.load_state(BATCH.lead_id).state.workflow.command
    assert new_command.idempotency_key != value.parent.idempotency_key
    assert new_command.draft_id != value.parent.draft_id
    assert value.execution.list_outcome_projection_inputs() == before
    assert value.executor.execute(batch).replayed
    assert value.executor.execute(value.confirmation).replayed
    assert value.store._connection.execute("SELECT count(*) FROM boundary_commands").fetchone()[0] == 2


@pytest.mark.parametrize("status,certainty", [
    ("awaiting_financial_confirmation", None), ("settlement_queued", None),
    ("settling", None), ("manual_review", "dispatched_unknown"),
    ("manual_review", "partially_settled"), ("paid", "settled"),
    ("settlement_queued", "not_dispatched"),
])
def test_capture_pending_unknown_or_settled_never_becomes_new_purchase(renewal, status, certainty):
    captured_history(renewal)
    renewal.financial_status, renewal.financial_certainty = status, certainty
    before = renewal.store.load_state(BATCH.lead_id).state.workflow
    result = renewal.executor.execute(selection(renewal))
    assert renewal.store.load_state(BATCH.lead_id).state.workflow == before
    assert result.receipt.command_rows == result.receipt.relay_rows == ()


@pytest.mark.parametrize("race", ["provider_reactivated", "unknown", "queued"])
def test_confirmation_rechecks_old_payment_and_provider(renewal, race):
    captured_history(renewal)
    renewal.executor.execute(selection(renewal))
    assert type(renewal.store.load_state(BATCH.lead_id).state.workflow) is AwaitingConfirmationState
    batch = next_batch(renewal, "independent-race", "Confirmo a nova compra.")
    confirmation_proposals(renewal, batch)
    def change():
        if race == "provider_reactivated":
            renewal.status = "confirmed"
        elif race == "unknown":
            renewal.financial_status, renewal.financial_certainty = "manual_review", "dispatched_unknown"
        else:
            renewal.financial_status, renewal.financial_certainty = "settlement_queued", None
    renewal.model.on_call = change
    result = renewal.executor.execute(batch)
    assert result.receipt.command_rows == result.receipt.relay_rows == ()
    assert renewal.store._connection.execute("SELECT count(*) FROM boundary_commands").fetchone()[0] == 1


def test_old_confirm_or_adjust_never_replays_captured_payment(renewal):
    from v2_contracts.critical_actions import ApprovalBasis, CriticalActionKind
    context = captured_history(renewal)
    state = renewal.store.load_state(BATCH.lead_id).state
    for proposal in [
        ModelProposal("batch:old", "confirm", ("Confirmo.",), (), (), (), confirmed_summary_version=1,
                      confirmed_action_kinds=(CriticalActionKind.RESERVE_LODGING,),
                      approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE),
        ModelProposal("batch:old", "adjust", ("Ajuste.",), (ModelFact("service", "hostel"),), (), (), pending_disposition="revoke"),
    ]:
        assert blocks_active_commercial_progression(state, proposal, execution_context=context(state),
                                                    execution_status="confirmed", now=renewal.executor._clock.now())


def test_native_capture_and_terminal_worker_result_are_retained_during_eligibility(lab):
    # This fixture creates issuance, signed native receipt and queue through owners.
    # Provider is synthetic; the productive adapter is real and no POST is allowed.
    lab.status = "canceled"
    assert worker(lab).run_once(now=lab.now).disposition.value == "preparation_terminal"
    before = lab.followup.load_payment(lab.receipt.payment_id)
    assert before.settlement_finish.outcome.certainty.value == "not_dispatched"
    assert before.status.value == "retryable"
    resolver = ReservationExecutionStatusResolver(
        lab.execution, followup=lab.followup, payment_store=lab.payments,
        reservation_status_reader=SimpleNamespace(read=lambda **_: ProviderReservationStatus(
            "observed", lab.now, "canceled", "NOT_PAID", "0.00", "420.00", "BRL")),
    )
    command, ledger = lab.execution.list_outcome_projection_inputs()[0]
    component = resolver._component(command, ledger)
    assert _terminal_unpaid_component(component, lab.now)
    evidence = component_wire(component)["payment"]["settlements"][0]["verified_payment"]
    assert evidence["status"] == "captured" and evidence["amount_minor"] == 42000
    assert lab.followup.load_payment(lab.receipt.payment_id) == before
    from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
    lab.followup.close()
    lab.followup = SQLiteFollowupUnitOfWork.open_v2(lab.paths["followup"])
    resolver._followup = lab.followup
    assert resolver._component(command, ledger) == component
    assert lab.followup.load_payment(lab.receipt.payment_id) == before
    assert worker(lab).run_once(now=lab.now + timedelta(seconds=1)).disposition.value == "idle"
    assert not [request for request in lab.provider_requests if request.method == "POST"]
