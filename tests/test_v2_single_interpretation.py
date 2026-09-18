"""Single interpretation: real adapter/executor, simulated model/provider transport."""

import json
from dataclasses import fields, replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests import test_v2_turn_executor as fx
from v2_adapters.hermes_model import HermesModelAdapter, _request_wire
from v2_contracts.critical_actions import (
    CriticalActionKind,
    PendingCriticalActionContext,
)
from v2_contracts.model import ConversationExchange, ModelFact, ModelRequest

REMOVED = (
    "confirmation_review_required",
    "selection_review_required",
    "progress_review_required",
    "recap_reuse_required",
    "public_reply_correction_reasons",
)


def request():
    return ModelRequest(
        request_id="request:single",
        lead_id="lead:single",
        source_event_id="turn:single",
        message="Obrigado, volto depois.",
        locale="pt-BR",
        state_version=0,
        state_facts=(ModelFact("full_name", "Pessoa Sintética"),),
        recent_dialogue=(ConversationExchange("Olá", ("Como posso ajudar?",)),),
    )


def payload(text="Até mais!", intent="inform"):
    return json.dumps(
        {
            "intent": intent,
            "reply_chunks": [{"text": text, "expects_reply": False}],
            "facts": [],
            "read_requests": [],
            "selected_choice_refs": [],
            "selection_requested": False,
            "pending_action_disposition": None,
            "passengers": [],
        }
    ).encode()


@pytest.mark.parametrize("flag", REMOVED)
def test_redundant_protocol_is_absent_from_contract_and_wire(flag):
    assert flag not in {f.name for f in fields(ModelRequest)}
    wire = json.loads(_request_wire(request(), "Maya"))
    assert flag not in json.loads(wire["messages"][-1][1])
    assert flag not in wire["system_prompt"]


@pytest.mark.parametrize(
    "text",
    [
        "Até mais!",
        "Posso ajudar.",
        "reserve preço disponibilidade",
        "Хорошо. Entendido.",
    ],
)
def test_valid_inform_is_not_reinterpreted_by_adapter(text):
    calls = []

    def run(command, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(
            returncode=0, stdout=b"PHASE8_RESULT\0" + payload(text), stderr=b""
        )

    adapter = HermesModelAdapter(
        command=("offline",),
        system_prompt="Maya original",
        timeout=10,
        transcript_key=b"k" * 32,
        run=run,
        environ={},
    )
    turn = adapter.complete_audited(request())
    assert len(calls) == 1
    assert len(turn.frames) == 1
    assert turn.proposal.reply_chunks == (text,)
    assert calls[0]["messages"][0] == ["user", "Olá"]
    facts = json.loads(calls[0]["messages"][-1][1])["state_facts"]
    assert {f["name"]: f["value"] for f in facts}["full_name"] == "Pessoa Sintética"


def test_pending_summary_inform_commits_without_confirmation_review():
    store, model, port, batch, executor = fx._approval_expiry_fixture(
        approval_ttl=timedelta(minutes=5), confirmation_clock=fx.FixedClock()
    )
    proposal = replace(
        fx._proposal("Pode perguntar, sem executar nada."),
        source_event_id=batch.batch_id,
    )
    model.proposals[:] = [
        proposal,
        replace(proposal, reply_chunks=("Não deve ser chamada.",)),
    ]
    calls = len(model.calls)
    try:
        result = executor.execute(batch)
        assert len(model.calls) == calls + 1
        assert model.calls[-1].pending_action is not None
        assert result.reply_chunks == proposal.reply_chunks
        assert result.receipt.command_rows == result.receipt.relay_rows == ()
        assert len(port.calls) == 1
        assert executor.execute(batch).replayed
        assert len(model.calls) == calls + 1
    finally:
        store.close()


def test_pending_confirmation_is_bound_in_normal_adapter_frame():
    pending = PendingCriticalActionContext(
        summary_version=3,
        action_kinds=(CriticalActionKind.BOOK_ACTIVITY,),
        public_summary="Resumo sintético completo",
        expires_at=fx.NOW + timedelta(minutes=5),
    )
    req = replace(request(), pending_action=pending)
    calls = []

    def run(command, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\0" + payload("Vou encaminhar.", "confirm"),
            stderr=b"",
        )

    turn = HermesModelAdapter(
        command=("offline",),
        system_prompt="Maya original",
        timeout=10,
        transcript_key=b"k" * 32,
        run=run,
        environ={},
    ).complete_audited(req)
    assert len(calls) == 1
    assert calls[0]["system_prompt"].startswith("Maya original")
    assert turn.proposal.confirmed_summary_version == pending.summary_version
    assert turn.proposal.confirmed_action_kinds == pending.action_kinds
    wire = json.loads(calls[0]["messages"][-1][1])
    assert not set(REMOVED) & wire.keys()
    assert wire["pending_action"]["public_summary"] == pending.public_summary


@pytest.mark.parametrize("initial_intent", ["inform", "adjust"])
def test_post_tool_cannot_invent_initial_confirmation(initial_intent):
    store, model, port, batch, executor = fx._approval_expiry_fixture(
        approval_ttl=timedelta(minutes=5), confirmation_clock=fx.SequenceClock()
    )
    confirmation = model.proposals[0]
    read = replace(port.calls[0], request_id="read:confirmation-escalation")
    initial = replace(
        confirmation,
        intent=initial_intent,
        confirmed_summary_version=None,
        confirmed_action_kinds=(),
        approval_basis=None,
        read_requests=(read,),
        pending_disposition="revoke" if initial_intent == "adjust" else None,
    )
    recovery = replace(fx._proposal("Essa ação não foi autorizada."), source_event_id=batch.batch_id)
    model.proposals[:] = [initial, confirmation, recovery]
    try:
        result = executor.execute(batch)
        assert result.reply_chunks == recovery.reply_chunks
        assert model.calls[-1].action_rejection
        if initial_intent == "adjust":
            assert not isinstance(store.load_state(batch.lead_id).state.workflow, fx.AwaitingConfirmationState)
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone() == (0,)
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_command_relays"
        ).fetchone() == (0,)
    finally:
        store.close()


@pytest.mark.parametrize("service", ["hostel", "agency"])
def test_real_read_continuation_sees_initial_facts_without_inferred_selection(service):
    from datetime import date

    from reservation_boundary.sqlite_store import SQLiteBoundaryStore
    from v2_application.reads import V2ReadService
    from v2_contracts.model import ModelProposal
    from v2_contracts.providers import ReadKind, ReadRequest

    day = date(2026, 8, 12)
    facts = (
        ModelFact("service", service),
        ModelFact("adults", 1),
        ModelFact("children", 0),
        ModelFact("payment_method", "stripe"),
    )
    if service == "agency":
        facts += (
            ModelFact("product_id", "product:buracao"),
            ModelFact("activity_date", day),
            ModelFact("birth_date", date(1990, 1, 1)),
            ModelFact("gender", "m"),
        )
        read = ReadRequest(
            request_id="read:explicit-intent",
            kind=ReadKind.ACTIVITY,
            product_id="product:buracao",
            activity_date=day,
            adults=1,
            children=0,
        )
    else:
        facts += (
            ModelFact("start_date", day),
            ModelFact("end_date", date(2026, 8, 14)),
        )
        read = ReadRequest(
            request_id="read:explicit-intent",
            kind=ReadKind.LODGING,
            check_in=day,
            check_out=date(2026, 8, 14),
            adults=1,
            children=0,
        )
    first = ModelProposal(
        source_event_id=fx.BATCH.batch_id,
        intent="inform",
        reply_chunks=(),
        facts=facts,
        read_requests=(read,),
        effect_proposals=(),
        selection_requested=True,
    )
    final = replace(
        fx._proposal("A consulta foi concluída; vamos conversar sobre as opções."),
        selection_requested=False,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    fx._install_public_authority(store, fx.AUTHORITY)
    model = fx.FakeAuditedModel(store, [first, final])
    port = (
        fx.FakeActivityReadPort(store)
        if service == "agency"
        else fx.FakeLodgingReadPort(store)
    )
    executor = fx._executor(
        store=store,
        model=model,
        profile=fx.FakeProfile(store),
        reads=V2ReadService({read.kind: port}),
    )
    try:
        result = executor.execute(fx.BATCH)
        assert len(model.calls) == 2
        assert len(port.calls) == 1
        context = {f.name: f.value for f in model.calls[-1].state_facts}
        for fact in facts:
            assert context[fact.name] == fact.value
        assert result.reply_chunks == final.reply_chunks
        assert result.receipt.command_rows == result.receipt.relay_rows == ()
        assert not isinstance(
            store.load_state(fx.BATCH.lead_id).state.workflow,
            fx.AwaitingConfirmationState,
        )
        assert len(model.proposals) == 0
    finally:
        store.close()
