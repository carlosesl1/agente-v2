"""Package -> terminal activity -> new activity over real executor and SQLite.

All provider status/availability and model replies are explicitly controlled.
No live channel, provider, or payment worker is constructed.
"""
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from reservation_domain import AwaitingConfirmationState, ExecutionCertainty, ServiceKind, dumps_command
from reservation_execution import DispatchRequest
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from tests.test_v2_component_renewal import next_batch
from tests.test_v2_outcome_projector import _persist
from tests.test_v2_turn_executor import (
    BATCH, NOW, FakeActivityReadPort, FakeAuditedModel, FakeLodgingReadPort,
    FakeProfile, _executor,
)
from v2_application.active_execution import ReservationExecutionStatusResolver
from v2_application.reads import V2ReadService
from v2_application.reservations import ReservationAllocator
from v2_contracts.critical_actions import ApprovalBasis, CriticalActionKind
from v2_contracts.execution_context import ProviderReservationStatus
from v2_contracts.model import ModelFact, ModelProposal
from v2_contracts.providers import ReadKind, ReadRequest


class RejectionAwareModel(FakeAuditedModel):
    def complete_audited(self, request):
        if request.action_rejection is not None:
            self.proposals.insert(0, ModelProposal(request.source_event_id, "inform", ("Contratação bloqueada.",), (), (), ()))
        return super().complete_audited(request)


class MatchingLodgingReadPort(FakeLodgingReadPort):
    def read(self, request):
        observed = super().read(request)
        return replace(observed, public_payload={**observed.public_payload,
                       "adults": request.adults, "children": request.children})


@pytest.mark.parametrize("captured_not_dispatched", [True, False])
@pytest.mark.parametrize("explicit_start_date", [True, False])
@pytest.mark.parametrize("repeat_facts", [True, False, "first_only"])
def test_package_timeout_renews_only_activity_after_confirmation_and_reopen(tmp_path, explicit_start_date, repeat_facts, captured_not_dispatched):
    store = SQLiteBoundaryStore.open_path_v8(tmp_path / "boundary.sqlite3")
    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
    followup = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
    model = RejectionAwareModel(store, [])
    reads = V2ReadService({ReadKind.LODGING: MatchingLodgingReadPort(store), ReadKind.ACTIVITY: FakeActivityReadPort(store)})
    executor = _executor(store=store, model=model, profile=FakeProfile(store), reads=reads)
    lab = SimpleNamespace(store=store, model=model, executor=executor)
    try:
        first = next_batch(lab, "original-package", "Quero confirmar hospedagem e passeio.")
        facts = (
            ModelFact("service", "package"), ModelFact("language", "pt-BR"),
            ModelFact("start_date", date(2026, 8, 10)), ModelFact("end_date", date(2026, 8, 12)),
            ModelFact("activity_date", date(2026, 8, 12)), ModelFact("product_id", "product:buracao"),
            ModelFact("adults", 1), ModelFact("children", 0), ModelFact("payment_method", "stripe"),
            ModelFact("birth_date", date(1990, 1, 2)), ModelFact("gender", "m"),
        )
        lodging_read = ReadRequest("read:package-lodging", ReadKind.LODGING,
                                   check_in=date(2026, 8, 10), check_out=date(2026, 8, 12), adults=1, children=0)
        activity_read = ReadRequest("read:package-activity", ReadKind.ACTIVITY,
                                    activity_date=date(2026, 8, 12), product_id="product:buracao", adults=1, children=0)
        model.proposals[:] = [
            ModelProposal(first.batch_id, "inform", (), facts, (lodging_read, activity_read), ()),
            ModelProposal(first.batch_id, "select", ("Segue o resumo do pacote.",), facts, (), (),
                          target_offer_ids=("offer:" + "7" * 64, "offer:" + "6" * 64)),
        ]
        executor.execute(first)
        assert type(store.load_state(BATCH.lead_id).state.workflow) is AwaitingConfirmationState, store.load_state(BATCH.lead_id).state.workflow
        second = next_batch(lab, "original-package-confirm", "Confirmo o pacote.")
        confirmation = ModelProposal(
            second.batch_id, "confirm", ("Vou reservar.",), (), (), (),
            confirmed_summary_version=1,
            confirmed_action_kinds=(CriticalActionKind.BOOK_PACKAGE, CriticalActionKind.INITIATE_PAYMENT),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        )
        model.proposals[:] = [confirmation, confirmation]
        original = executor.execute(second)
        assert len(original.receipt.command_rows) == 2
        parent = store.load_state(BATCH.lead_id).state.workflow.command
        children = ReservationAllocator().allocate(parent).commands
        assert {c.payload.components[0].service for c in children} == {ServiceKind.LODGING, ServiceKind.ACTIVITY}
        _persist(execution, children)
        for _ in children:
            claim = execution.claim_command(worker_id="worker:controlled", now=NOW + timedelta(seconds=10), lease_ttl=timedelta(seconds=30))
            request = DispatchRequest.from_command(claim.command, dumps_command(claim.command))
            permit = execution.fence_dispatch(claim, request, now=NOW + timedelta(seconds=10))
            service = claim.command.payload.components[0].service
            native = "provider:cloudbeds:123" if service is ServiceKind.LODGING else "provider:bokun:id:456"
            outcome = claim.command.outcome(certainty=ExecutionCertainty.EFFECT_CONFIRMED,
                                           normalized_status="accepted", provider_reference=native, evidence=(request.payload_hash,))
            execution.record_outcome(permit, outcome, now=NOW + timedelta(seconds=10))
        before = execution.list_outcome_projection_inputs()
        executor._clock = SimpleNamespace(now=lambda: NOW + timedelta(seconds=20))
        from v2_adapters.execution_context import ReservationStatusReader
        native_reads = ReservationStatusReader(
            cloudbeds=SimpleNamespace(get_reservation=lambda native: {
                "success": True, "data": {"reservationID": native, "status": "confirmed",
                "balanceDetailed": {"paid": "0.00"}, "balance": "480.00", "currency": "BRL"}}),
            bokun=SimpleNamespace(get_booking=lambda native: {
                "bookingId": int(native), "status": "TIMEOUT", "paymentType": "NOT_PAID",
                "totalPaid": "0.00", "totalDue": "1300.00", "currency": "BRL"}),
            clock=SimpleNamespace(now=lambda: executor._clock.now()),
        )
        resolver = ReservationExecutionStatusResolver(
            execution, followup=followup, payment_store=SimpleNamespace(context_for_payment=lambda _: ()),
            lead_resolver=SimpleNamespace(lead_id_for_command=lambda _: BATCH.lead_id),
            reservation_status_reader=native_reads,
        )
        executor._execution_status_resolver = resolver
        if captured_not_dispatched:
            # Controlled financial context for this turn/restart witness. Native
            # receipt -> durable finish -> resolver is covered independently.
            from tests.test_v2_independent_purchase import captured_history
            captured_history(lab)
        activity_facts = tuple(
            replace(f, value="agency") if f.name == "service" else
            replace(f, value=date(2026, 8, 12)) if f.name == "start_date" else f
            for f in facts if f.name != "end_date" and (explicit_start_date or f.name != "start_date")
        )
        if not repeat_facts:
            # A previous informational turn already committed the agent's scope.
            # The next real-model frames legitimately carry only their deltas.
            remembered = next_batch(lab, "remember-activity-scope", "Só o passeio, com os dados já informados.")
            model.proposals[:] = [ModelProposal(remembered.batch_id, "inform", ("Dados mantidos.",), activity_facts, (), ())]
            executor.execute(remembered)
            assert not store.load_state(BATCH.lead_id).state.handoff
        third = next_batch(lab, "replace-activity", "Quero um novo passeio no lugar do expirado. A hospedagem fica como está.")
        delta_facts = activity_facts if repeat_facts else ()
        model.proposals[:] = [
            ModelProposal(third.batch_id, "inform", (), delta_facts, (replace(activity_read, request_id="read:replacement"),), (), selection_requested=True),
            ModelProposal(third.batch_id, "select", ("Novo resumo apenas do passeio.",), () if repeat_facts == "first_only" else delta_facts, (), (), target_offer_id="offer:" + "6" * 64),
        ]
        summary = executor.execute(third)
        assert not summary.receipt.command_rows
        assert type(store.load_state(BATCH.lead_id).state.workflow) is AwaitingConfirmationState
        assert tuple(c.service for c in store.load_state(BATCH.lead_id).state.workflow.draft.components) == (ServiceKind.ACTIVITY,)
        # Close/reopen real durable stores before the new authorization.
        store.close(); execution.close(); followup.close()
        store = SQLiteBoundaryStore.open_path_v8(tmp_path / "boundary.sqlite3")
        execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
        followup = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
        lab.store = model.store = store
        executor._store = store
        executor._profile = FakeProfile(store)
        executor._reads = V2ReadService({ReadKind.ACTIVITY: FakeActivityReadPort(store)})
        resolver._execution, resolver._followup = execution, followup
        fourth = next_batch(lab, "replace-activity-confirm", "Confirmo somente o novo passeio.")
        confirm = replace(confirmation, source_event_id=fourth.batch_id,
                          confirmed_action_kinds=(CriticalActionKind.BOOK_ACTIVITY, CriticalActionKind.INITIATE_PAYMENT))
        model.proposals[:] = [confirm, confirm]
        result = executor.execute(fourth)
        assert len(result.receipt.command_rows) == 1
        new_parent = store.load_state(BATCH.lead_id).state.workflow.command
        new_children = ReservationAllocator().allocate(new_parent).commands
        assert len(new_children) == 1
        assert new_children[0].payload.components[0].service is ServiceKind.ACTIVITY
        assert new_children[0].idempotency_key not in {c.idempotency_key for c in children}
        assert execution.list_outcome_projection_inputs() == before
        assert executor.execute(fourth).replayed
        assert store._connection.execute("SELECT count(*) FROM boundary_commands").fetchone()[0] == len(children) + len(new_children)
    finally:
        store.close(); execution.close(); followup.close()
