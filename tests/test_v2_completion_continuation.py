"""3B causal integration: real SQLite owners, simulated Maya/provider transport."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from reservation_boundary.sqlite_store import ConcurrencyConflict, SQLiteBoundaryStore
from reservation_domain import ExecutionCertainty
from tests.test_v2_outcome_projector import (
    NOW,
    _finish_next,
    _package_command,
    _persist,
    _stores,
)
from tests.test_v2_turn_executor import (
    BATCH,
    EVENT,
    FakeAuditedModel,
    FakeProfile,
    _executor,
    _proposal,
)
from v2_application.active_execution import ReservationExecutionStatusResolver
from v2_application.completion import PublicOutboxStore
from v2_application.completion_projector import CompletionProjector
from v2_application.reservations import ReservationAllocator
from v2_host.public_authority import GeneralAvailabilityPublicAuthorityResolver

LEAD = "manychat:12345"
TEXT = "Sua hospedagem ficou confirmada. O passeio não foi reservado; não vou repetir a hospedagem."


class Maya(FakeAuditedModel):
    def __init__(self, store):
        super().__init__(store, [])
        self.failure = False
        self.text = TEXT
        self.override = None

    def complete_audited(self, request):
        if self.failure:
            raise TimeoutError("simulated generation failure")
        proposal = replace(
            _proposal(self.text), source_event_id=request.source_event_id
        )
        self.proposals = [self.override(proposal) if self.override else proposal]
        return super().complete_audited(request)


@pytest.fixture
def lab(tmp_path):
    boundary = SQLiteBoundaryStore.open_path_v8(tmp_path / "boundary.sqlite3")
    execution, payments, _outcome = _stores(tmp_path)
    public = PublicOutboxStore(tmp_path / "public.sqlite3")
    owner = SimpleNamespace(
        lead_id_for_command=lambda _: LEAD, lead_id_for_payment=lambda _: LEAD
    )
    model = Maya(boundary)
    executor = _executor(
        store=boundary,
        model=model,
        profile=FakeProfile(boundary),
        public_authority=GeneralAvailabilityPublicAuthorityResolver(
            store=boundary, hmac_key=b"c" * 32
        ),
    )
    _persist(execution, ReservationAllocator().allocate(_package_command()).commands)
    _finish_next(execution, now=NOW, certainty=ExecutionCertainty.EFFECT_CONFIRMED)
    _finish_next(
        execution,
        now=NOW + timedelta(seconds=1),
        certainty=ExecutionCertainty.CALLED_NO_EFFECT,
    )
    executor._execution_status_resolver = ReservationExecutionStatusResolver(
        execution, payment_store=payments, public_store=public, lead_resolver=owner
    )
    try:
        yield SimpleNamespace(
            boundary=boundary,
            execution=execution,
            payments=payments,
            public=public,
            owner=owner,
            executor=executor,
            model=model,
            path=tmp_path,
        )
    finally:
        public.close()
        payments.close()
        execution.close()
        boundary.close()


def start(lab):
    lab.projector = CompletionProjector(
        execution=lab.execution,
        payment_store=lab.payments,
        public_store=lab.public,
        boundary=lab.boundary,
        lead_resolver=lab.owner,
    )
    lab.executor._completion_projector = lab.projector
    lab.projector.executor = lab.executor


def inbound(lab):
    event = replace(
        EVENT, lead_id=LEAD, subscriber_id="12345", text="E a reserva, deu certo?"
    )
    return replace(
        BATCH,
        lead_id=LEAD,
        subscriber_id="12345",
        events=(event,),
        combined_text=event.text,
    )


def count(lab, table):
    return lab.boundary._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[
        0
    ]


def test_async_authorship_has_full_context_no_effect_and_replays_after_reopen(lab):
    start(lab)
    before = lab.execution.list_outcome_projection_inputs()
    result = lab.projector.run_once(now=NOW + timedelta(seconds=2))
    assert result.inserted == 1
    assert len(lab.model.calls) == 1
    request = lab.model.calls[0]
    assert request.trigger == "operation_result"
    assert request.message == ""
    assert len(request.completion_events) == 1
    assert len(request.execution_components) == 2
    assert {c.outcome.certainty for c in request.execution_components} == {
        "effect_confirmed",
        "called_no_effect",
    }
    assert next(
        c
        for c in request.execution_components
        if c.outcome.certainty == "effect_confirmed"
    ).outcome.provider_reference
    rows = lab.boundary._connection.execute(
        "SELECT chunk_json,status FROM boundary_public_outbox"
    ).fetchall()
    assert len(rows) == 1 and rows[0][1] == "pending"
    assert TEXT in rows[0][0]
    from reservation_boundary.conversation import PublicReplyChunk

    assert PublicReplyChunk.from_canonical_bytes(rows[0][0].encode()).author == "maya"
    assert count(lab, "boundary_commands") == count(lab, "boundary_command_relays") == 0
    assert lab.execution.list_outcome_projection_inputs() == before
    assert lab.public.pending_count() == 0
    assert lab.projector.run_once(now=NOW + timedelta(seconds=3)).inserted == 0
    assert len(lab.model.calls) == 1
    reopened = SQLiteBoundaryStore.open_path_v8(lab.path / "boundary.sqlite3")
    try:
        lab.projector._boundary = reopened
        assert lab.projector.run_once(now=NOW + timedelta(seconds=4)).inserted == 0
    finally:
        lab.projector._boundary = lab.boundary
        reopened.close()


def test_failed_generation_keeps_event_pending_and_never_repeats_business_effect(lab):
    start(lab)
    before = lab.execution.list_outcome_projection_inputs()
    lab.model.failure = True
    with pytest.raises(TimeoutError):
        lab.projector.run_once(now=NOW + timedelta(seconds=2))
    assert count(lab, "boundary_events") == 0
    assert count(lab, "boundary_public_outbox") == 0
    lab.model.failure = False
    assert lab.projector.run_once(now=NOW + timedelta(seconds=3)).inserted == 1
    assert lab.execution.list_outcome_projection_inputs() == before


def test_inbound_consumes_not_yet_authored_completion_atomically(lab):
    start(lab)
    result = lab.executor.execute(inbound(lab))
    assert not result.replayed
    assert len(lab.model.calls[0].completion_events) == 1
    assert lab.model.calls[0].trigger == "customer_message"
    assert lab.projector.run_once(now=NOW + timedelta(seconds=4)).inserted == 0
    assert len(lab.model.calls) == 1
    assert lab.executor.execute(inbound(lab)).replayed
    assert len(lab.model.calls) == 1
    assert count(lab, "boundary_commands") == 0


def test_inbound_consolidates_authored_but_unsent_completion(lab):
    start(lab)
    lab.projector.run_once(now=NOW + timedelta(seconds=2))
    assert lab.executor.execute(inbound(lab)).reply_chunks == (TEXT,)
    statuses = lab.boundary._connection.execute(
        "SELECT status FROM boundary_public_outbox ORDER BY rowid"
    ).fetchall()
    assert statuses == [("cancelled",), ("pending",)]
    assert len(lab.model.calls[-1].completion_events) == 1
    assert lab.projector.run_once(now=NOW + timedelta(seconds=4)).inserted == 0
    assert len(lab.model.calls) == 2


def test_async_rejects_business_actions_without_review_or_rewrite(lab):
    start(lab)
    lab.model.override = lambda p: replace(p, intent="request_handoff")
    with pytest.raises(ValueError, match="communication-only"):
        lab.projector.run_once(now=NOW + timedelta(seconds=2))
    assert len(lab.model.calls) == 1
    assert count(lab, "boundary_events") == count(lab, "boundary_public_outbox") == 0


def test_model_wire_marks_internal_event_not_customer_speech(lab):
    start(lab)
    from v2_adapters.hermes_model import _request_wire

    lab.projector.run_once(now=NOW + timedelta(seconds=2))
    payload = json.loads(
        json.loads(_request_wire(lab.model.calls[0], "Maya"))["messages"][-1][1]
    )
    assert payload["trigger"] == "operation_result"
    assert payload["message"] == ""
    assert payload["completion_events"][0]["kind"] == "reservation_result"


def claim(lab):
    from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore

    queue = SQLiteBoundaryWorkerStore(lab.boundary)
    row = queue.claim_public_delivery(
        worker_id="worker:completion-test", now=NOW, lease_ttl=timedelta(seconds=30)
    )
    assert row is not None
    return queue, row


def test_claimed_but_not_fenced_message_can_be_consolidated(lab):
    start(lab)
    lab.projector.run_once(now=NOW)
    queue, row = claim(lab)
    lab.executor.execute(inbound(lab))
    with pytest.raises(ConcurrencyConflict):
        queue.fence_public_delivery(row, now=NOW)
    assert lab.boundary._connection.execute(
        "SELECT status FROM boundary_public_outbox WHERE public_row_id=?",
        (row.public_row_id,),
    ).fetchone() == ("cancelled",)
    # A failed stale fence cannot advance the allocation either.
    assert lab.boundary._connection.execute(
        "SELECT state FROM boundary_dispatch_authority WHERE public_row_id=?",
        (row.public_row_id,),
    ).fetchone() == ("bound",)


@pytest.mark.parametrize("state", ["dispatch_fenced", "manual_review", "delivered"])
def test_started_or_uncertain_send_is_never_consolidated_or_retried(lab, state):
    from reservation_boundary.public_dispatch import PublicAcceptanceReceipt

    start(lab)
    lab.projector.run_once(now=NOW)
    queue, row = claim(lab)
    queue.fence_public_delivery(row, now=NOW)
    if state == "manual_review":
        queue.mark_public_delivery_manual_review(row, now=NOW)
    elif state == "delivered":
        from tests.test_v2_turn_executor import RecordingPublicDelivery

        acceptance = RecordingPublicDelivery().send(row)
        receipt = PublicAcceptanceReceipt(
            public_row_id=row.public_row_id,
            idempotency_key=row.idempotency_key,
            acceptance=acceptance,
            accepted_at=NOW,
        )
        queue.complete_public_acceptance(row, receipt, now=NOW)
    lab.executor.execute(inbound(lab))
    assert lab.model.calls[-1].completion_events == ()
    message = lab.model.calls[-1].operational_messages[-1]
    assert message.status == ("accepted_by_manychat" if state == "delivered" else state)
    assert lab.boundary._connection.execute(
        "SELECT status FROM boundary_public_outbox WHERE public_row_id=?",
        (row.public_row_id,),
    ).fetchone() == (state,)
    assert lab.projector.run_once(now=NOW).inserted == 0


@pytest.mark.parametrize("consolidate", [False, True])
def test_crash_rolls_back_response_coverage_and_optional_cancellation(
    lab, monkeypatch, consolidate
):
    start(lab)
    if consolidate:
        lab.projector.run_once(now=NOW)
    before = lab.execution.list_outcome_projection_inputs()
    original = lab.boundary.commit_turn_v8

    def crash(**kwargs):
        def hook(stage):
            if stage == "before_commit":
                raise KeyboardInterrupt("simulated process crash")

        return original(**kwargs, fault_hook=hook)

    monkeypatch.setattr(lab.boundary, "commit_turn_v8", crash)
    with pytest.raises(KeyboardInterrupt):
        lab.executor.execute(inbound(lab)) if consolidate else lab.projector.run_once(
            now=NOW
        )
    assert count(lab, "boundary_events") == int(consolidate)
    assert count(lab, "boundary_public_outbox") == int(consolidate)
    assert all(
        row[0] == "pending"
        for row in lab.boundary._connection.execute(
            "SELECT status FROM boundary_public_outbox"
        )
    )
    monkeypatch.setattr(lab.boundary, "commit_turn_v8", original)
    lab.executor.execute(inbound(lab)) if consolidate else lab.projector.run_once(
        now=NOW
    )
    assert lab.execution.list_outcome_projection_inputs() == before
    assert lab.projector.run_once(now=NOW).inserted == 0
    reopened = SQLiteBoundaryStore.open_path_v8(lab.path / "boundary.sqlite3")
    reopened.close()


def test_customer_turn_wins_while_async_model_is_generating(lab, monkeypatch):
    start(lab)
    original = lab.model.complete_audited
    entered = False

    def interleave(request):
        nonlocal entered
        if not entered and request.trigger == "operation_result":
            entered = True
            lab.executor.execute(inbound(lab))
        return original(request)

    monkeypatch.setattr(lab.model, "complete_audited", interleave)
    assert lab.projector.run_once(now=NOW).inserted == 0
    assert count(lab, "boundary_events") == count(lab, "boundary_public_outbox") == 1
    assert count(lab, "boundary_commands") == 0
    assert lab.projector.run_once(now=NOW).inserted == 0


@pytest.mark.parametrize("with_fact", [False, True])
def test_send_fence_wins_during_customer_generation_and_reloads_context(
    lab, monkeypatch, with_fact
):
    start(lab)
    lab.projector.run_once(now=NOW)
    queue, row = claim(lab)
    if with_fact:
        from v2_contracts.model import ModelFact

        lab.model.override = lambda p: replace(
            p, facts=(ModelFact("email", "alice@example.com"),)
        )
    original = lab.model.complete_audited
    entered = False

    def interleave(request):
        nonlocal entered
        if not entered:
            entered = True
            queue.fence_public_delivery(row, now=NOW)
        return original(request)

    monkeypatch.setattr(lab.model, "complete_audited", interleave)
    lab.executor.execute(inbound(lab))
    assert len(lab.model.calls) == 3
    assert lab.model.calls[-2].completion_events
    assert not lab.model.calls[-1].completion_events
    assert lab.model.calls[-1].operational_messages[-1].status == "dispatch_fenced"
    assert count(lab, "boundary_events") == 2


def test_pending_customer_inbox_has_priority_over_background_generation(lab):
    from v2_application.inbox import SQLiteInbox

    start(lab)
    inbox = SQLiteInbox(lab.path / "inbox.sqlite3")
    lab.projector._inbox = inbox
    inbox.accept(inbound(lab).events[0])
    assert lab.projector.run_once(now=NOW).inserted == 0
    assert not lab.model.calls
    lab.executor.execute(inbound(lab))
    assert count(lab, "boundary_events") == 1


@pytest.mark.parametrize(
    "text",
    [
        "Your lodging is confirmed; the activity was not booked.",
        "Tu alojamiento está confirmado; el paseo no se reservó.",
    ],
)
def test_maya_language_and_words_are_not_rewritten_from_phone_locale(lab, text):
    start(lab)
    lab.model.override = lambda proposal: replace(proposal, reply_chunks=(text,))
    lab.projector.run_once(now=NOW)
    (row,) = lab.boundary._connection.execute(
        "SELECT chunk_json FROM boundary_public_outbox"
    )
    from reservation_boundary.conversation import PublicReplyChunk

    assert PublicReplyChunk.from_canonical_bytes(row[0].encode()).text == text
    assert len(lab.model.calls) == 1


@pytest.mark.parametrize("transport_state", ["accepted", "not_called", "unknown"])
def test_transport_recovery_uses_persisted_response_without_new_model_or_effects(
    lab, transport_state
):
    from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
    from tests.test_v2_turn_executor import RecordingPublicDelivery
    from v2_application.public_delivery import (
        BoundaryPublicDeliveryWorker,
        PublicDeliveryNotCalled,
        PublicDeliveryUnknown,
    )

    start(lab)
    lab.projector.run_once(now=NOW)

    class Transport(RecordingPublicDelivery):
        attempts = 0

        def send(self, claim):
            self.attempts += 1
            if self.attempts == 1 and transport_state == "not_called":
                raise PublicDeliveryNotCalled("offline adapter did not invoke provider")
            if transport_state == "unknown":
                self.calls.append(claim)
                raise PublicDeliveryUnknown("offline transport lost response")
            return super().send(claim)

    transport = Transport()

    def worker():
        return BoundaryPublicDeliveryWorker(
            boundary=SQLiteBoundaryWorkerStore(lab.boundary),
            delivery=transport,
            worker_id="worker:offline-delivery",
            lease_ttl=timedelta(seconds=30),
        )

    first = worker().run_once(now=NOW)
    assert (
        first.value
        == {
            "accepted": "accepted",
            "not_called": "retryable_failure",
            "unknown": "manual_review",
        }[transport_state]
    )
    second = worker().run_once(now=NOW + timedelta(seconds=31))
    assert second.value == ("accepted" if transport_state == "not_called" else "idle")
    assert len(lab.model.calls) == 1
    assert len(transport.calls) == 1
    assert transport.calls[0].chunk.text == TEXT
    assert lab.projector.run_once(now=NOW).inserted == 0
    assert count(lab, "boundary_commands") == count(lab, "boundary_outbox") == 0
    assert lab.payments._connection.execute(
        "SELECT count(*) FROM payment_initiations"
    ).fetchone() == (0,)


def test_model_unavailable_leaves_event_pending_without_system_fallback(lab):
    start(lab)

    def unavailable(_):
        raise RuntimeError("simulated model unavailable")

    lab.model.override = unavailable
    with pytest.raises(RuntimeError, match="model unavailable"):
        lab.projector.run_once(now=NOW)
    assert count(lab, "boundary_public_outbox") == count(lab, "boundary_events") == 0
    assert lab.public.pending_count() == 0
    lab.model.override = None
    assert lab.projector.run_once(now=NOW).inserted == 1
