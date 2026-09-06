"""Task B causal witnesses: scripted Maya, in-memory authority, no external IO."""
import json
from dataclasses import replace
from datetime import date, timedelta

import pytest
import test_v2_turn_executor as te
import test_v2_conversation_reducer as cr
from reservation_followup.handoff import (
    HandoffRequested, HandoffReasonCode, HandoffEffectPolicy, HandoffCancelled,
    HandoffCancellationCode, new_handoff, reduce_handoff,
)
from v2_adapters.hermes_model import _v8_proposal, _request_wire, _confirmation_proposal
from v2_contracts.model import ModelProposal, ModelRequest, ModelFact, InvalidModelProposal
from v2_contracts.providers import ReadKind, ReadRequest
from v2_contracts.critical_actions import PendingCriticalActionContext, CriticalActionKind


def frame(source, text="Resposta da Maya.", reads=()):
    return ModelProposal(source_event_id=source, intent="inform", reply_chunks=(text,),
                         facts=(), read_requests=reads, effect_proposals=())


@pytest.mark.parametrize("kind", ["audio", "image", "application/custom", None])
def test_media_only_reaches_maya_and_commits_replayable_limitation(kind):
    event = replace(te.EVENT, text="", media_url="https://example.invalid/media", media_type=kind)
    batch = replace(te.BATCH, events=(event,), combined_text="")
    store = te.SQLiteBoundaryStore.open_memory_v8()
    private = te.SQLitePrivateCustomerFactStore.open_memory()
    reply = "Não tenho o conteúdo desse anexo. Pode me contar por texto?"
    model = te.FakeAuditedModel(store, [frame(batch.batch_id, reply)])
    te._install_public_authority(store)
    executor = te._executor(store=store, model=model, profile=te.FakeProfile(store), private_customer_facts=private,
        public_authority=te.MappingAuthority({batch.batch_id: te.AUTHORITY}))
    try:
        result = executor.execute(batch)
        assert result.reply_chunks == (reply,)
        assert result.receipt.command_rows == result.receipt.relay_rows == ()
        assert result.receipt.read_observations == ()
        request = model.calls[0]
        assert request.message == ""
        wire = json.loads(_request_wire(request, "Prompt"))
        payload = json.loads(wire["messages"][-1][1])
        assert payload["attachments"] == [{"media_type": kind, "content_status": "not_extracted"}]
        assert executor.execute(batch).replayed
        assert len(model.calls) == 1
        assert private.load_recent_dialogue(batch.lead_id)[0].customer_message == ""
    finally:
        private.close()
        store.close()


@pytest.mark.parametrize("message", ["Consulte novamente.", "λ", "Just the same dates please."])
def test_explicit_same_scope_read_is_not_replaced_by_recap(message):
    store = te.SQLiteBoundaryStore.open_memory_v8()
    read = ReadRequest(request_id="read:first", kind=ReadKind.LODGING,
                       check_in=date(2026, 8, 10), check_out=date(2026, 8, 12), adults=2, children=0)
    event = replace(te.EVENT, event_id="event:refresh", text=message, payload_hash="c" * 64)
    batch = replace(te.BATCH, batch_id="batch:refresh", events=(event,), combined_text=message)
    authority = replace(te.AUTHORITY, authorization_id="auth:refresh", allocation_ids=("allocation:refresh",), allocation_manifest_hash="c" * 64)
    model = te.FakeAuditedModel(store, [frame(te.BATCH.batch_id, reads=(read,)), frame(te.BATCH.batch_id),
        frame(batch.batch_id, reads=(replace(read, request_id="read:refresh"),)), frame(batch.batch_id)])
    port = te.FakeLodgingReadPort(store)
    te._install_public_authority(store)
    te._install_public_authority(store, authority)
    executor = te._executor(store=store, model=model, profile=te.FakeProfile(store), reads=te.V2ReadService({ReadKind.LODGING: port}),
        public_authority=te.MappingAuthority({te.BATCH.batch_id: te.AUTHORITY, batch.batch_id: authority}))
    try:
        executor.execute(te.BATCH)
        result = executor.execute(batch)
        assert len(port.calls) == 2
        assert model.calls[-1].recap_reuse_required is False
        assert len(result.receipt.read_observations) == 1
        assert result.receipt.command_rows == result.receipt.relay_rows == ()
    finally:
        store.close()


def handoff(cancelled):
    opened = new_handoff(HandoffRequested(handoff_id="handoff:test", lead_key_hash="a" * 64,
        incident_key="incident:test", reason_code=HandoffReasonCode.CUSTOMER_REQUESTED,
        source_event_id="event:open", reservation_anchor=None, requested_at=cr.NOW), HandoffEffectPolicy.default_email_disabled()).state
    if not cancelled:
        return opened
    return reduce_handoff(opened, HandoffCancelled(handoff_id=opened.request.handoff_id,
        incident_key=opened.request.incident_key, cancellation_code=HandoffCancellationCode.OPERATOR_CANCELLED, cancelled_at=cr.NOW)).state


@pytest.mark.parametrize("cancelled", [True, False])
def test_handoff_selection_guard_tracks_lifecycle(cancelled):
    decision = cr._reducer().reduce(state=replace(cr._boundary(), handoff=handoff(cancelled)), projection=cr._projection(),
        proposal=cr._proposal(source="event:resume", intent="select", target_offer_id=cr.LODGING_OFFER_ID),
        profile=cr._profile(), reads=(cr._lodging_read(),), fact_commitment_hash=cr.FRAME_HASH, now=cr.NOW)
    assert ("handoff_effect_guard" in decision.receipt_requirements) is not cancelled
    if cancelled:
        assert decision.public_reply.kind == "summary"
    assert decision.commands == ()


@pytest.mark.parametrize("cancelled", [True, False])
def test_handoff_reopens_only_when_inactive(cancelled):
    old = handoff(cancelled)
    proposal = replace(frame("event:reopen"), intent="request_handoff")
    decision = cr._reducer().reduce(state=replace(cr._boundary(), handoff=old), projection=cr._projection(),
        proposal=proposal, profile=cr._profile(), reads=(), fact_commitment_hash=cr.FRAME_HASH, now=cr.NOW)
    assert (decision.handoff_request is not None) is cancelled
    if cancelled:
        assert decision.next_state.handoff.queue_active
        assert decision.next_state.handoff.request.handoff_id != old.request.handoff_id
    else:
        assert decision.next_state.handoff == old


def confirmation_request(review=False):
    return ModelRequest(request_id="request:confirm", lead_id="lead:test", source_event_id="event:confirm",
        message="Proceed.", locale="pt-BR", state_version=1, confirmation_review_required=review,
        pending_action=PendingCriticalActionContext(summary_version=1, action_kinds=(CriticalActionKind.RESERVE_LODGING,),
            public_summary="Resumo pendente.", expires_at=cr.NOW + timedelta(minutes=5)))


def confirmation_wire(facts):
    return dict(intent="confirm", reply_chunks=[dict(text="I will proceed.", expects_reply=False)], facts=facts,
        read_requests=[], selected_choice_refs=[], selection_requested=False, pending_action_disposition=None, passengers=[])


@pytest.mark.parametrize("review", [False, True])
def test_confirmation_accepts_locale_only_fact(review):
    request = confirmation_request(review)
    wire = confirmation_wire([dict(name="language", value="en")])
    if review:
        proposal = _confirmation_proposal(json.dumps(wire).encode(), request)
    else:
        proposal = _v8_proposal(wire, request)
    assert proposal.facts == (ModelFact("language", "en"),)


def test_locale_confirmation_keeps_commercial_signature_and_command():
    awaiting = cr._awaiting_from_ready(cr._ready_state(service=cr.ServiceKind.LODGING, workflow_id="workflow:locale"))
    proposal = replace(cr._proposal(source="event:locale", intent="confirm", confirmed_summary_version=awaiting.draft.version),
                       facts=(ModelFact("language", "en"),))
    decision = cr._reducer().reduce(state=cr._boundary(awaiting), projection=cr._projection(), proposal=proposal,
        profile=cr._profile(), reads=tuple(cr._read_for_component(item) for item in awaiting.draft.components),
        fact_commitment_hash=cr.FRAME_HASH, now=cr.NOW + timedelta(seconds=1))
    assert decision.projection.locale == "en"
    assert len(decision.commands) == 1
    assert decision.commands[0].subject_signature == awaiting.draft.subject_signature


def test_confirmation_still_rejects_commercial_fact():
    with pytest.raises(InvalidModelProposal):
        _v8_proposal(confirmation_wire([dict(name="adults", value=3)]), confirmation_request())
