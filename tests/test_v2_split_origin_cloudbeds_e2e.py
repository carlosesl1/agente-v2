from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from reservation_boundary.effects import ReservationRelayBundle
from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.test_v2_cloudbeds_monotonic_e2e import (
    NOW as PROVIDER_NOW,
    PROPERTY_ID,
    _reservation_worker,
    _submit_handler,
)
from tests.test_v2_turn_executor import (
    AUTHORITY,
    BATCH,
    EVENT,
    NOW,
    FakeAuditedModel,
    FakeLodgingReadPort,
    MappingAuthority,
    PhoneOnlyManyChatContact,
    SequenceClock,
    _enabled_reducer,
    _install_public_authority,
)
from v2_adapters.provider_http import CloudbedsHTTPTransport
from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
from v2_application.reads import V2ReadService
from v2_application.turn_executor import TurnExecutionError, V2TurnExecutor
from v2_application.workers import V2WorkerDisposition
from v2_contracts.channel import InboundBatch
from v2_contracts.critical_actions import (
    ApprovalBasis,
    CriticalActionKind,
)
from v2_contracts.model import ModelFact, ModelProposal
from v2_contracts.profile import PrivateCustomerBinding
from v2_contracts.providers import ReadKind, ReadRequest


def _batch(*, suffix: str, text: str) -> InboundBatch:
    event = replace(
        EVENT,
        event_id=f"event:split-origin-{suffix}",
        text=text,
        payload_hash=(
            {
                "collect": "3",
                "summary": "4",
                "confirm": "5",
                "correct": "6",
            }[suffix]
            * 64
        ),
    )
    return InboundBatch(
        batch_id=f"batch:split-origin-{suffix}",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(event,),
        combined_text=text,
    )


def _authority(batch: InboundBatch, marker: str):
    return replace(
        AUTHORITY,
        authorization_id=f"auth:split-origin-{marker}",
        allocation_ids=(f"allocation:split-origin-{marker}",),
        allocation_manifest_hash=marker * 64,
        deadline_at=NOW + timedelta(minutes=10),
    )


def test_split_origin_profile_reaches_one_monotonic_cloudbeds_post_and_replays_once(
    tmp_path: Path,
) -> None:
    private_name = "Pessoa E2E Silva"
    private_email = "split.origin@example.invalid"
    private_country_name = "Brasil"
    authenticated_phone = "".join(("+1", "202", "555", "0199"))

    collect_batch = _batch(
        suffix="collect",
        text=(
            f"Meu nome completo é {private_name}, meu e-mail é {private_email} "
            f"e sou do {private_country_name}."
        ),
    )
    summary_batch = _batch(
        suffix="summary",
        text="Quero a suíte de 10 a 12 de agosto para dois adultos.",
    )
    confirm_batch = _batch(
        suffix="confirm",
        text="Sim, pode reservar exatamente esse resumo.",
    )
    authorities = {
        collect_batch.batch_id: _authority(collect_batch, "3"),
        summary_batch.batch_id: _authority(summary_batch, "4"),
        confirm_batch.batch_id: _authority(confirm_batch, "5"),
    }

    read_request = ReadRequest(
        request_id="read:split-origin-summary-lodging",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    summary_facts = (
        ModelFact("language", "pt-BR"),
        ModelFact("service", "hostel"),
        ModelFact("start_date", date(2026, 8, 10)),
        ModelFact("end_date", date(2026, 8, 12)),
        ModelFact("adults", 2),
        ModelFact("children", 0),
        ModelFact("payment_method", "stripe"),
    )
    proposals = (
        ModelProposal(
            source_event_id=collect_batch.batch_id,
            intent="inform",
            reply_chunks=("Dados recebidos.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
        ),
        ModelProposal(
            source_event_id=summary_batch.batch_id,
            intent="inform",
            reply_chunks=("Vou consultar.",),
            facts=(),
            read_requests=(read_request,),
            effect_proposals=(),
        ),
        ModelProposal(
            source_event_id=summary_batch.batch_id,
            intent="select",
            reply_chunks=("Vou preparar o resumo.",),
            facts=summary_facts,
            read_requests=(),
            effect_proposals=(),
            target_offer_id="offer:" + "7" * 64,
        ),
        ModelProposal(
            source_event_id=confirm_batch.batch_id,
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(CriticalActionKind.RESERVE_LODGING,),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        ),
        ModelProposal(
            source_event_id=confirm_batch.batch_id,
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(CriticalActionKind.RESERVE_LODGING,),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        ),
    )

    boundary = SQLiteBoundaryStore.open_memory_v8()
    queues = SQLiteBoundaryWorkerStore(boundary)
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "private-customer-e2e.sqlite3"
    )
    model = FakeAuditedModel(boundary, list(proposals))
    read_port = FakeLodgingReadPort(boundary)
    for authority in authorities.values():
        _install_public_authority(boundary, authority)
    executor = V2TurnExecutor(
        store=boundary,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        profile=PhoneOnlyManyChatContact(boundary),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority(authorities),
        clock=SequenceClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )

    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution-e2e.sqlite3")
    submit_client: httpx.Client | None = None
    try:
        collected = executor.execute(collect_batch)
        snapshot = private_store.load(collect_batch.lead_id)
        assert collected.receipt.command_rows == ()
        assert snapshot.full_name == private_name
        assert snapshot.email == private_email
        assert snapshot.country_code == "BR"
        for private_value in (private_name, private_email, private_country_name):
            assert private_value not in model.calls[0].message

        summary = executor.execute(summary_batch)
        assert summary.receipt.command_rows == ()
        assert "Só para confirmar" in summary.reply_chunks[0]

        confirmed = executor.execute(confirm_batch)
        pending = model.calls[3].pending_action
        assert pending is not None
        assert pending.public_summary == summary.reply_chunks[0]
        replayed_confirmation = executor.execute(confirm_batch)
        assert len(confirmed.receipt.command_rows) == 1
        assert len(confirmed.receipt.relay_rows) == 1
        assert replayed_confirmation.replayed is True
        assert replayed_confirmation.receipt == confirmed.receipt
        assert boundary._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone()[0] == 1

        relay_json = boundary._connection.execute(
            "SELECT bundle_json FROM boundary_command_relays"
        ).fetchone()[0]
        bundle = ReservationRelayBundle.from_canonical_bytes(relay_json.encode())
        claim = queues.claim_command_relay(
            worker_id="worker:split-origin-relay",
            now=NOW + timedelta(seconds=1),
            lease_ttl=timedelta(seconds=30),
        )
        assert claim is not None
        first_dispatch = execution.accept_boundary_reservation(
            operation_id=claim.target_operation_id,
            source_turn_receipt_hash=claim.source_turn_receipt_hash,
            bundle=bundle,
        )
        replay_dispatch = execution.accept_boundary_reservation(
            operation_id=claim.target_operation_id,
            source_turn_receipt_hash=claim.source_turn_receipt_hash,
            bundle=bundle,
        )
        assert replay_dispatch == first_dispatch
        command = execution.load_command(claim.command_id)
        assert command is not None
        assert command.payload.customer.full_name == private_name
        assert command.payload.customer.email == private_email
        assert command.payload.customer.phone_e164 == authenticated_phone
        assert command.payload.customer.country_code == "BR"

        seen: list[httpx.Request] = []
        submit_client = httpx.Client(
            transport=httpx.MockTransport(_submit_handler(command, seen))
        )
        transport = CloudbedsHTTPTransport(
            api_key="cloudbeds-synthetic-secret",
            property_id=PROPERTY_ID,
            source_id="source-split-origin-e2e",
            base_url="https://api.cloudbeds.invalid",
            client=submit_client,
        )
        worker = _reservation_worker(execution, command, transport)
        result = worker.run_once(now=PROVIDER_NOW)
        replay_result = worker.run_once(now=PROVIDER_NOW)

        assert result.disposition is V2WorkerDisposition.EFFECT_CONFIRMED
        assert replay_result.disposition is V2WorkerDisposition.IDLE
        posts = [request for request in seen if request.method == "POST"]
        assert len(posts) == 1
        form = parse_qs(posts[0].content.decode())
        assert form["guestFirstName"] == ["Pessoa"]
        assert form["guestLastName"] == ["E2E Silva"]
        assert form["guestEmail"] == [private_email]
        assert form["guestPhone"] == [authenticated_phone]
        assert form["guestCountry"] == ["BR"]
        ledger = execution._connection.execute(
            "SELECT dispatch_slots_consumed FROM execution_ledger"
        ).fetchone()
        assert ledger == (1,)
    finally:
        if submit_client is not None:
            submit_client.close()
        execution.close()
        private_store.close()
        boundary.close()


def test_invalid_private_correction_after_summary_revokes_pending_confirmation(
    tmp_path: Path,
) -> None:
    collect_batch = _batch(
        suffix="collect",
        text=(
            "Meu nome completo é Pessoa Original Silva, "
            "meu e-mail é original.person@example.invalid e sou do Brasil."
        ),
    )
    summary_batch = _batch(
        suffix="summary",
        text="Quero a suíte de 10 a 12 de agosto para dois adultos.",
    )
    correction_batch = _batch(
        suffix="correct",
        text="Nome completo: Mononym",
    )
    authorities = {
        collect_batch.batch_id: _authority(collect_batch, "3"),
        summary_batch.batch_id: _authority(summary_batch, "4"),
        correction_batch.batch_id: _authority(correction_batch, "6"),
    }
    read_request = ReadRequest(
        request_id="read:split-origin-correction-summary",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    summary_facts = (
        ModelFact("language", "pt-BR"),
        ModelFact("service", "hostel"),
        ModelFact("start_date", date(2026, 8, 10)),
        ModelFact("end_date", date(2026, 8, 12)),
        ModelFact("adults", 2),
        ModelFact("children", 0),
        ModelFact("payment_method", "stripe"),
    )
    proposals = [
        ModelProposal(
            source_event_id=collect_batch.batch_id,
            intent="inform",
            reply_chunks=("Dados recebidos.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
        ),
        ModelProposal(
            source_event_id=summary_batch.batch_id,
            intent="inform",
            reply_chunks=("Vou consultar.",),
            facts=(),
            read_requests=(read_request,),
            effect_proposals=(),
        ),
        ModelProposal(
            source_event_id=summary_batch.batch_id,
            intent="select",
            reply_chunks=("Vou preparar o resumo.",),
            facts=summary_facts,
            read_requests=(),
            effect_proposals=(),
            target_offer_id="offer:" + "7" * 64,
        ),
        ModelProposal(
            source_event_id=correction_batch.batch_id,
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(CriticalActionKind.RESERVE_LODGING,),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        ),
    ]

    boundary = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "private-customer-correction.sqlite3"
    )
    model = FakeAuditedModel(boundary, proposals)
    read_port = FakeLodgingReadPort(boundary)
    for authority in authorities.values():
        _install_public_authority(boundary, authority)
    executor = V2TurnExecutor(
        store=boundary,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        profile=PhoneOnlyManyChatContact(boundary),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority(authorities),
        clock=SequenceClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        executor.execute(collect_batch)
        summary = executor.execute(summary_batch)
        assert "Só para confirmar" in summary.reply_chunks[0]

        correction = executor.execute(correction_batch)
        current = boundary.load_state(correction_batch.lead_id)

        assert correction.receipt.command_rows == ()
        assert correction.receipt.relay_rows == ()
        assert type(current.state.workflow).__name__ == "AwaitingAdjustmentState"
        assert (
            executor._reducer.pending_action(
                current.state.workflow,
                locale="pt-BR",
            )
            is None
        )
        assert "Mononym" not in model.calls[-1].message
        assert "nome completo" in " ".join(correction.reply_chunks).casefold()
        assert private_store.load(correction_batch.lead_id).full_name == (
            "Pessoa Original Silva"
        )
    finally:
        private_store.close()
        boundary.close()


@pytest.mark.parametrize(
    ("mutation_kind", "mutation_timing", "should_block"),
    (
        pytest.param("phone", "precommit", True, id="authenticated_phone"),
        pytest.param(
            "selected_email",
            "precommit",
            True,
            id="selected_manychat_email",
        ),
        pytest.param(
            "unused_identity",
            "precommit",
            False,
            id="unused_manychat_identity_precommit",
        ),
        pytest.param(
            "unused_identity",
            "decision",
            False,
            id="unused_manychat_identity_decision",
        ),
    ),
)
def test_manychat_change_after_confirmation_decision_is_source_aware(
    tmp_path: Path,
    mutation_kind: str,
    mutation_timing: str,
    should_block: bool,
) -> None:
    summary_batch = _batch(
        suffix="summary",
        text="Quero a suíte de 10 a 12 de agosto para dois adultos.",
    )
    confirm_batch = _batch(
        suffix="confirm",
        text="Sim, pode reservar exatamente esse resumo.",
    )
    authorities = {
        summary_batch.batch_id: _authority(summary_batch, "4"),
        confirm_batch.batch_id: _authority(confirm_batch, "5"),
    }
    read_request = ReadRequest(
        request_id="read:split-origin-binding-change",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    summary_facts = (
        ModelFact("language", "pt-BR"),
        ModelFact("service", "hostel"),
        ModelFact("start_date", date(2026, 8, 10)),
        ModelFact("end_date", date(2026, 8, 12)),
        ModelFact("adults", 2),
        ModelFact("children", 0),
        ModelFact("payment_method", "stripe"),
    )
    proposals = [
        ModelProposal(
            source_event_id=summary_batch.batch_id,
            intent="inform",
            reply_chunks=("Vou consultar.",),
            facts=(),
            read_requests=(read_request,),
            effect_proposals=(),
        ),
        ModelProposal(
            source_event_id=summary_batch.batch_id,
            intent="select",
            reply_chunks=("Vou preparar o resumo.",),
            facts=summary_facts,
            read_requests=(),
            effect_proposals=(),
            target_offer_id="offer:" + "7" * 64,
        ),
        ModelProposal(
            source_event_id=confirm_batch.batch_id,
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(CriticalActionKind.RESERVE_LODGING,),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        ),
        ModelProposal(
            source_event_id=confirm_batch.batch_id,
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(CriticalActionKind.RESERVE_LODGING,),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        ),
    ]

    boundary = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "private-customer-binding-change.sqlite3"
    )
    private_facts = [
        ModelFact("full_name", "Pessoa Binding Silva"),
        ModelFact("country_code", "BR"),
    ]
    if mutation_kind != "selected_email":
        private_facts.append(
            ModelFact("email", "binding.person@example.invalid")
        )
    private_store.persist_turn(
        lead_id=summary_batch.lead_id,
        source_turn_id="batch:split-origin-binding-profile",
        source_event_hash="8" * 64,
        facts=tuple(private_facts),
        persisted_at=NOW - timedelta(minutes=1),
    )

    class MutableManyChatProfile:
        def __init__(self) -> None:
            self.revision = 0
            self.read_count = 0
            self.mutate_on_read: int | None = None

        def mutate(self) -> None:
            self.revision = 1

        def mutate_before_second_next_read(self) -> None:
            self.mutate_on_read = self.read_count + 2

        def read(self, lead_id: str, *, now) -> PrivateCustomerBinding:
            del lead_id
            self.read_count += 1
            if self.read_count == self.mutate_on_read:
                self.mutate()
            phone = (
                "".join(("+1", "202", "555", "0199"))
                if self.revision == 0 or mutation_kind != "phone"
                else "".join(("+1", "202", "555", "0200"))
            )
            if mutation_kind == "unused_identity" and self.revision == 1:
                full_name = "Pessoa ManyChat Alterada"
                email = "manychat.changed@example.invalid"
                country = "US"
            else:
                full_name = None
                email = (
                    "manychat.original@example.invalid"
                    if mutation_kind == "selected_email" and self.revision == 0
                    else "manychat.changed@example.invalid"
                    if mutation_kind == "selected_email"
                    else None
                )
                country = None
            return PrivateCustomerBinding(
                binding_id="profile-binding:" + "9" * 64,
                content_hash=("8" if self.revision == 0 else "7") * 64,
                full_name=full_name,
                email=email,
                phone_e164=phone,
                country_code=country,
                observed_at=now,
                expires_at=now + timedelta(minutes=5),
                complete=all(
                    value is not None for value in (full_name, email, phone, country)
                ),
            )

    profile = MutableManyChatProfile()
    delegate = MappingAuthority(authorities)

    class MutatingAuthority:
        def resolve(self, batch, *, chunk_count, now):
            if (
                batch.batch_id == confirm_batch.batch_id
                and mutation_timing == "precommit"
            ):
                profile.mutate()
            return delegate.resolve(batch, chunk_count=chunk_count, now=now)

    model = FakeAuditedModel(boundary, proposals)
    read_port = FakeLodgingReadPort(boundary)
    for authority in authorities.values():
        _install_public_authority(boundary, authority)
    executor = V2TurnExecutor(
        store=boundary,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        profile=profile,
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MutatingAuthority(),
        clock=SequenceClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        summary = executor.execute(summary_batch)
        assert "Só para confirmar" in summary.reply_chunks[0]
        if mutation_timing == "decision":
            profile.mutate_before_second_next_read()

        if should_block:
            with pytest.raises(
                TurnExecutionError,
                match="profile changed before commit",
            ):
                executor.execute(confirm_batch)
        else:
            confirmation = executor.execute(confirm_batch)
            assert len(confirmation.receipt.command_rows) == 1
            assert len(confirmation.receipt.relay_rows) == 1

        assert boundary._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone()[0] == (0 if should_block else 1)
        assert boundary._connection.execute(
            "SELECT count(*) FROM boundary_command_relays"
        ).fetchone()[0] == (0 if should_block else 1)
    finally:
        private_store.close()
        boundary.close()
