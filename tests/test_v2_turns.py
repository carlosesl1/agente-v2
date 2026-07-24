from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_boundary.types import KernelDecision
from v2_adapters.hermes_model import HermesModelAdapter
from v2_application.reads import V2ReadService
from v2_application.turns import V2TurnService
from v2_contracts.channel import InboundBatch, InboundEvent
from v2_contracts.model import (
    EffectProposal,
    InvalidModelProposal,
    ModelProposal,
    ModelRequest,
)
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
LODGING_REQUEST = ReadRequest(
    request_id="read-lodging-001",
    kind=ReadKind.LODGING,
    check_in=date(2026, 8, 10),
    check_out=date(2026, 8, 12),
    adults=2,
    children=0,
)
EVENT = InboundEvent(
    event_id="event-001",
    lead_id="manychat:lead-001",
    subscriber_id="lead-001",
    conversation_id="conversation-001",
    text="Quero hospedagem para duas pessoas.",
    media_url=None,
    media_type=None,
    occurred_at=NOW - timedelta(seconds=1),
    payload_hash="1" * 64,
)
LODGING_BATCH = InboundBatch(
    batch_id="batch-001",
    lead_id=EVENT.lead_id,
    subscriber_id=EVENT.subscriber_id,
    events=(EVENT,),
    combined_text=EVENT.text,
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class NoopLock:
    @contextmanager
    def claim(self, **kwargs):
        yield


class NoopKernel:
    def reduce(self, state, intent):
        return KernelDecision(state, (), (), (), ())


class FakeReadPort:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store
        self.calls: list[ReadRequest] = []
        self.on_call = None

    def read(self, request: ReadRequest) -> ReadObservation:
        if self.on_call is not None:
            self.on_call()
        self.calls.append(request)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="cloudbeds",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            public_payload={
                "room_public_name": "Suíte Casal",
                "total_amount": "480.00",
                "currency": "BRL",
            },
            private_binding_hash="2" * 64,
        )


class FakeModel:
    def __init__(
        self, store: SQLiteBoundaryStore, proposals: list[ModelProposal]
    ) -> None:
        self.store = store
        self.proposals = proposals
        self.requests = []
        self.on_call = None

    def complete(self, request):
        if self.on_call is not None:
            self.on_call()
        self.requests.append(request)
        return self.proposals.pop(0)


def _proposal_with_read() -> ModelProposal:
    return ModelProposal(
        source_event_id=LODGING_BATCH.batch_id,
        intent="inform",
        reply_chunks=(),
        facts=(),
        read_requests=(LODGING_REQUEST,),
        effect_proposals=(),
    )


def _final_proposal() -> ModelProposal:
    return ModelProposal(
        source_event_id=LODGING_BATCH.batch_id,
        intent="inform",
        reply_chunks=("Encontrei a Suíte Casal por R$ 480,00 no total.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )


def test_model_and_read_run_without_open_sqlite_transaction() -> None:
    store = SQLiteBoundaryStore.open_memory()
    read_port = FakeReadPort(store)
    model = FakeModel(store, [_proposal_with_read(), _final_proposal()])
    model.on_call = lambda: assert_not_in_transaction(store)
    read_port.on_call = lambda: assert_not_in_transaction(store)
    service = V2TurnService(
        store=store,
        lock=NoopLock(),
        kernel=NoopKernel(),
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        clock=FixedClock(),
        turn_timeout=timedelta(seconds=30),
    )
    try:
        result = service.handle(LODGING_BATCH)

        assert result.reply_chunks == (
            "Encontrei a Suíte Casal por R$ 480,00 no total.",
        )
        assert store.turn_receipt_count(LODGING_BATCH.batch_id) == 1
        assert len(model.requests) == 2
        assert len(model.requests[1].observations) == 1
        assert len(read_port.calls) == 1
        assert (
            store._connection.execute(
                "SELECT count(*) FROM legacy_import_claims"
            ).fetchone()[0]
            == 0
        )
    finally:
        store.close()


def assert_not_in_transaction(store: SQLiteBoundaryStore) -> None:
    assert store._connection.in_transaction is False


def test_model_cannot_mix_read_and_effect_proposal() -> None:
    store = SQLiteBoundaryStore.open_memory()
    read_port = FakeReadPort(store)
    mixed = ModelProposal(
        source_event_id=LODGING_BATCH.batch_id,
        intent="inform",
        reply_chunks=(),
        facts=(),
        read_requests=(LODGING_REQUEST,),
        effect_proposals=(EffectProposal("reserve_lodging", {}),),
    )
    model = FakeModel(store, [mixed])
    service = V2TurnService(
        store=store,
        lock=NoopLock(),
        kernel=NoopKernel(),
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        clock=FixedClock(),
        turn_timeout=timedelta(seconds=30),
    )
    try:
        with pytest.raises(InvalidModelProposal, match="mix read and effect"):
            service.handle(LODGING_BATCH)
        assert read_port.calls == []
        assert store.turn_receipt_count(LODGING_BATCH.batch_id) == 0
    finally:
        store.close()


class Completed:
    def __init__(self, stdout: bytes) -> None:
        self.returncode = 0
        self.stdout = stdout
        self.stderr = b""


def test_hermes_adapter_exposes_only_public_observation_to_tool_free_child() -> None:
    captured = {}
    response = json.dumps(
        {
            "schema": "v2-model-proposal-v1",
            "source_event_id": "batch-001",
            "intent": "inform",
            "reply_chunks": ["A suíte está disponível."],
            "facts": [],
            "read_requests": [],
            "effect_proposals": [],
            "target_offer_id": None,
            "confirmed_summary_version": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    def run(command, *, input, capture_output, timeout, check, env):
        captured.update(command=command, input=input, check=check, env=env)
        return Completed(b"PHASE8_RESULT\x00" + response)

    observation = ReadObservation(
        request_hash=LODGING_REQUEST.canonical_hash(),
        provider="cloudbeds",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        public_payload={"total_amount": "480.00", "currency": "BRL"},
        private_binding_hash="f" * 64,
    )
    adapter = HermesModelAdapter(
        command=("hermes-model-child",),
        system_prompt="Você é Maya. Retorne somente o contrato fechado.",
        timeout=30,
        transcript_key=b"t" * 32,
        run=run,
        environ={
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "model-only",
            "V2_CLOUDBEDS_API_KEY": "must-not-leak",
            "V2_MANYCHAT_API_KEY": "must-not-leak",
        },
    )

    audited = adapter.complete_audited(
        ModelRequest(
            request_id="batch-001:model:1",
            lead_id="manychat:lead-001",
            source_event_id="batch-001",
            message="Tem disponibilidade?",
            locale="pt-BR",
            state_version=0,
            observations=(observation,),
        )
    )
    proposal = audited.proposal

    sent = captured["input"].decode()
    assert proposal.reply_chunks == ("A suíte está disponível.",)
    assert captured["command"] == ("hermes-model-child",)
    assert captured["check"] is False
    assert captured["env"] == {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "model-only",
    }
    assert "total_amount" in sent
    assert observation.private_binding_hash not in sent
    assert "token" not in sent.lower()
    assert "api_key" not in sent.lower()
    assert (
        audited.frames[0].request_hash == hashlib.sha256(captured["input"]).hexdigest()
    )
    assert (
        audited.frames[0].stdout_hash
        == hashlib.sha256(b"PHASE8_RESULT\x00" + response).hexdigest()
    )
    assert audited.frames[0].response_bytes == response
    assert audited.closure.final_seq == 1
    assert audited.closure.zero_requests_in_flight is True
    with pytest.raises(ValueError, match="byte hash mismatch"):
        replace(audited.frames[0], stdout_bytes=audited.frames[0].stdout_bytes + b"x")
