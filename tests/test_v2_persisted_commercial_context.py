"""Committed facts are validation context, never consent or effect evidence."""
from dataclasses import replace

import pytest

from tests.test_v2_component_renewal import renewal  # noqa: F401
from tests.test_v2_turn_executor import BATCH
from v2_application.active_execution import blocks_active_commercial_progression
from v2_application.turn_plan import derive_selection_reads, proposal_values
from v2_contracts.model import ModelFact, ModelProposal
from v2_contracts.critical_actions import ApprovalBasis, CriticalActionKind


def selection():
    return ModelProposal("event:known-scope", "select", ("Novo resumo.",), (), (), (),
                         target_offer_id="offer:new")


def context(lab):
    state = lab.store.load_state(BATCH.lead_id).state
    projection = lab.store.load_latest_conversation_projection(BATCH.lead_id)
    return state, projection, lab.executor._execution_status_resolver.context(state)


def test_scope_reuses_committed_service_without_rewriting_delta(renewal):
    state, projection, ctx = context(renewal)
    proposal = selection()
    args = dict(execution_context=ctx, execution_status=ctx.status,
                now=renewal.executor._clock.now())
    assert blocks_active_commercial_progression(state, proposal, **args)
    assert not blocks_active_commercial_progression(state, proposal, projection=projection, **args)
    assert proposal.facts == ()
    assert proposal_values(proposal, projection)["service"] == "hostel"
    assert not blocks_active_commercial_progression(
        state, replace(proposal, intent="inform", target_offer_id=None), **args)


def test_current_scope_and_party_override_committed_values(renewal):
    state, projection, ctx = context(renewal)
    proposal = replace(selection(), facts=(ModelFact("service", "agency"), ModelFact("adults", 3)))
    assert proposal_values(proposal, projection)["service"] == "agency"
    assert proposal_values(proposal, projection)["adults"] == 3
    assert blocks_active_commercial_progression(state, proposal, projection=projection,
        execution_context=ctx, execution_status=ctx.status, now=renewal.executor._clock.now())


@pytest.mark.parametrize("change", [{"status": "confirmed"}, {"paid": "450.00"}, {"source": "unavailable"}])
def test_retained_scope_never_overrides_nonrenewable_history(renewal, change):
    for name, value in change.items():
        setattr(renewal, name, value)
    state, projection, ctx = context(renewal)
    assert blocks_active_commercial_progression(state, selection(), projection=projection,
        execution_context=ctx, execution_status=ctx.status, now=renewal.executor._clock.now())


def test_retained_scope_never_confirms_the_old_command(renewal):
    state, projection, ctx = context(renewal)
    proposal = ModelProposal("event:old-confirm", "confirm", ("Confirmo.",), (), (), (), confirmed_summary_version=1,
                             confirmed_action_kinds=(CriticalActionKind.INITIATE_PAYMENT, CriticalActionKind.RESERVE_LODGING),
                             approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE)
    assert blocks_active_commercial_progression(state, proposal, projection=projection,
        execution_context=ctx, execution_status=ctx.status, now=renewal.executor._clock.now())


def test_selection_derives_fresh_read_from_existing_values_not_old_offer_evidence(renewal):
    _, projection, _ = context(renewal)
    proposal = selection()
    assert derive_selection_reads(proposal) == ()
    reads = derive_selection_reads(proposal, projection)
    assert len(reads) == 1
    values = proposal_values(proposal, projection)
    assert reads[0].check_in == values["start_date"]
    assert reads[0].check_out == values["end_date"]
    assert reads[0].adults == values["adults"]
    assert proposal.read_requests == ()
