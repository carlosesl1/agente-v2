"""Renewal race/history and component-scope regressions on controlled evidence."""
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.test_v2_component_renewal import renewal, next_batch, selection  # noqa: F401
from tests.test_v2_turn_executor import BATCH, NOW
from v2_application.active_execution import blocks_active_commercial_progression
from v2_contracts.execution_context import ExecutionContext, PaymentSettlementContext
from v2_contracts.model import ModelFact, ModelProposal


def confirmation_proposals(lab, batch):
    from v2_contracts.critical_actions import ApprovalBasis, CriticalActionKind
    proposal = ModelProposal(
        batch.batch_id, "confirm", ("Vou executar a nova contratação.",), (), (), (),
        confirmed_summary_version=1,
        confirmed_action_kinds=(CriticalActionKind.RESERVE_LODGING,),
        approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
    )
    lab.model.proposals[:] = [proposal, proposal,
        ModelProposal(batch.batch_id, "inform", ("O estado mudou; não fiz nova reserva.",), (), (), ())]


def test_new_confirmation_creates_distinct_command_and_preserves_history(renewal):
    lab = renewal
    old = lab.execution.list_outcome_projection_inputs()
    lab.executor.execute(selection(lab))
    batch = next_batch(lab, "renewal-confirm", "Confirmo a nova contratação.")
    confirmation_proposals(lab, batch)
    result = lab.executor.execute(batch)
    assert len(result.receipt.command_rows) == 1
    assert result.receipt.command_rows[0][0] != lab.parent.command_id
    assert lab.store._connection.execute("SELECT count(*) FROM boundary_commands").fetchone()[0] == 2
    assert lab.execution.list_outcome_projection_inputs() == old
    assert lab.executor.execute(batch).replayed
    assert lab.executor.execute(lab.confirmation).replayed
    assert lab.store._connection.execute("SELECT count(*) FROM boundary_commands").fetchone()[0] == 2


def test_provider_reactivation_during_confirmation_stops_new_command(renewal):
    lab = renewal
    lab.executor.execute(selection(lab))
    batch = next_batch(lab, "renewal-race", "Confirmo a nova contratação.")
    confirmation_proposals(lab, batch)
    lab.model.on_call = lambda: setattr(lab, "status", "confirmed")
    result = lab.executor.execute(batch)
    assert result.receipt.command_rows == result.receipt.relay_rows == ()
    assert lab.store._connection.execute("SELECT count(*) FROM boundary_commands").fetchone()[0] == 1


def test_expired_activity_scope_preserves_active_lodging(renewal):
    lab = renewal
    state = lab.store.load_state(BATCH.lead_id).state
    lodging = lab.executor._execution_status_resolver.context(state).components[0]
    lodging = replace(lodging, reservation_status=replace(lodging.reservation_status, reservation_status="confirmed"))
    activity = replace(
        lodging, command_id="command:activity",
        offer=replace(lodging.offer, service="activity"),
        outcome=replace(lodging.outcome, command_id="command:activity", provider_reference="provider:bokun:id:123"),
        reservation_status=replace(lodging.reservation_status, reservation_status="TIMEOUT"),
    )
    context = ExecutionContext("confirmed", (lodging, activity))
    request = ModelProposal("batch:activity-renewal", "select", ("Novo resumo.",),
                            (ModelFact("service", "agency"),), (), (), target_offer_id="offer:new-activity")
    args = dict(execution_context=context, execution_status=context.status, now=NOW + timedelta(seconds=20))
    assert not blocks_active_commercial_progression(state, request, **args)
    assert blocks_active_commercial_progression(state, replace(request, facts=(ModelFact("service", "package"), ModelFact("activity_date", NOW.date())), target_offer_id=None, target_offer_ids=("offer:new-activity", "offer:new-lodging")), **args)
    assert blocks_active_commercial_progression(state, replace(request, facts=()), **args)
    assert context.components == (lodging, activity)
    # A terminal state permits a NEW selection, never editing an old command.
    adjustment = ModelProposal("event:adjust-old", "adjust", ("Ajustar.",),
        (ModelFact("service", "hostel"),), (), (), pending_disposition="revoke")
    assert blocks_active_commercial_progression(state, adjustment,
        execution_status=context.status,
        execution_context=lab.executor._execution_status_resolver.context(state),
        now=NOW + timedelta(seconds=20))


@pytest.mark.parametrize("change", ["capture", "unknown", "missing_financial_source", "queued"])
def test_terminal_status_never_overrides_unresolved_local_evidence(renewal, change):
    lab = renewal
    state = lab.store.load_state(BATCH.lead_id).state
    component = lab.executor._execution_status_resolver.context(state).components[0]
    if change == "capture":
        component = replace(component, settlement_status="recorded", settlements=(
            PaymentSettlementContext(component.payment_id, 1, 45000, "BRL", "stripe", "manual_review", "dispatched_unknown", NOW),
        ))
    elif change == "unknown":
        component = replace(component, outcome=replace(component.outcome, certainty="called_unknown"))
    elif change == "missing_financial_source":
        component = replace(component, settlement_status="unavailable")
    else:
        component = replace(component, outcome=None, ledger_status="queued")
    request = ModelProposal("batch:unsafe-renewal", "select", ("Novo resumo.",),
                            (ModelFact("service", "hostel"),), (), (), target_offer_id="offer:new-lodging")
    assert blocks_active_commercial_progression(
        state, request, execution_status="confirmed",
        execution_context=ExecutionContext("confirmed", (component,)), now=NOW + timedelta(seconds=20),
    )
