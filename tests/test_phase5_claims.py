from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta, timezone
import json
import sqlite3
from threading import Barrier
import unittest

from reservation_domain import (
    ExecutingState,
    ExecutionCertainty,
    FailedBeforeProviderState,
    dumps_command,
    loads_outcome,
)
from reservation_execution import (
    CommandClaim,
    DispatchRequest,
    LedgerStatus,
    OutboxKind,
    PreparationDisposition,
    PreparationFailure,
)
from reservation_execution.sqlite_store import (
    DispatchAlreadyFenced,
    IdentityConflict,
    SQLiteUnitOfWork,
    StaleLease,
)
from tests.phase5_helpers import T0, claim_fixture, database_counts


CLAIM_T0 = T0 + timedelta(seconds=10)


class Phase5ClaimTests(unittest.TestCase):
    def test_first_claim_transitions_state_and_increments_token(self) -> None:
        store, claim_at = claim_fixture(self)
        before = database_counts(store.path)
        claim = claim_at(CLAIM_T0)
        self.assertIsInstance(claim, CommandClaim)
        self.assertEqual(claim.lease.fencing_token, 1)
        self.assertEqual(claim.claim_count, 1)
        self.assertEqual(claim.preparation_failures, 0)
        ledger = store.load_ledger(claim.command.command_id)
        self.assertEqual(ledger.status, LedgerStatus.PREPARING)
        self.assertEqual(ledger.claim_count, 1)
        self.assertEqual(ledger.claim_owner, "worker:one")
        self.assertEqual(ledger.lease_acquired_at, CLAIM_T0)
        self.assertEqual(ledger.lease_expires_at, CLAIM_T0 + timedelta(seconds=30))
        state = store.load_workflow(claim.command.workflow_id)
        self.assertIsInstance(state, ExecutingState)
        self.assertEqual(claim.workflow_revision, state.meta.revision)
        self.assertEqual(database_counts(store.path), (before[0], before[1] + 1, *before[2:]))

    def test_second_worker_and_second_connection_cannot_claim_live_lease(self) -> None:
        store, claim_at = claim_fixture(self)
        first = claim_at(CLAIM_T0, worker="worker:one")
        self.assertIsNone(claim_at(CLAIM_T0 + timedelta(seconds=1), worker="worker:two"))
        second_connection = SQLiteUnitOfWork.open(store.path)
        self.addCleanup(second_connection.close)
        self.assertIsNone(
            second_connection.claim_command(
                worker_id="worker:two",
                now=CLAIM_T0 + timedelta(seconds=2),
                lease_ttl=timedelta(seconds=30),
            )
        )
        self.assertEqual(store.load_ledger(first.command.command_id).claim_count, 1)

    def test_two_connections_racing_produce_exactly_one_claim(self) -> None:
        store, _ = claim_fixture(self)
        path = store.path
        store.close()
        barrier = Barrier(2)

        def race(worker_id: str):
            contender = SQLiteUnitOfWork.open(path)
            try:
                barrier.wait(timeout=5)
                return contender.claim_command(
                    worker_id=worker_id,
                    now=CLAIM_T0,
                    lease_ttl=timedelta(seconds=30),
                )
            finally:
                contender.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(race, ("worker:race-one", "worker:race-two"))
            )
        claims = tuple(item for item in results if item is not None)
        self.assertEqual(len(claims), 1)
        reopened = SQLiteUnitOfWork.open(path)
        self.addCleanup(reopened.close)
        ledger = reopened.load_ledger(claims[0].command.command_id)
        self.assertEqual(ledger.claim_count, 1)
        self.assertEqual(ledger.fencing_token, 1)

    def test_expired_pre_dispatch_lease_is_recoverable_with_new_token_without_event(self) -> None:
        store, claim_at = claim_fixture(self)
        first = claim_at(CLAIM_T0, worker="worker:one")
        before = database_counts(store.path)
        before_revision = store.load_workflow(first.command.workflow_id).meta.revision
        second = claim_at(CLAIM_T0 + timedelta(seconds=30), worker="worker:two")
        self.assertEqual(second.command, first.command)
        self.assertEqual(second.lease.fencing_token, first.lease.fencing_token + 1)
        self.assertEqual(second.claim_count, 2)
        self.assertEqual(second.workflow_revision, before_revision)
        self.assertEqual(database_counts(store.path), before)

    def test_expired_claim_is_recoverable_after_close_and_reopen(self) -> None:
        store, claim_at = claim_fixture(self)
        first = claim_at(CLAIM_T0)
        path = store.path
        store.close()
        reopened = SQLiteUnitOfWork.open(path)
        self.addCleanup(reopened.close)
        second = reopened.claim_command(
            worker_id="worker:restart",
            now=CLAIM_T0 + timedelta(seconds=31),
            lease_ttl=timedelta(seconds=30),
        )
        self.assertEqual(second.command, first.command)
        self.assertEqual(second.lease.fencing_token, 2)
        self.assertEqual(reopened.load_workflow(first.command.workflow_id).meta.revision, 7)

    def test_renewal_extends_only_the_current_live_preparation_claim(self) -> None:
        store, claim_at = claim_fixture(self)
        claim = claim_at(CLAIM_T0)
        renewed = store.renew_command_lease(
            claim,
            now=CLAIM_T0 + timedelta(seconds=10),
            lease_ttl=timedelta(seconds=30),
        )
        self.assertEqual(renewed.command, claim.command)
        self.assertEqual(renewed.lease.owner, claim.lease.owner)
        self.assertEqual(renewed.lease.fencing_token, claim.lease.fencing_token)
        self.assertEqual(renewed.lease.acquired_at, claim.lease.acquired_at)
        self.assertEqual(renewed.lease.expires_at, CLAIM_T0 + timedelta(seconds=40))
        self.assertEqual(renewed.claim_count, claim.claim_count)
        with self.assertRaises(StaleLease):
            store.renew_command_lease(
                claim,
                now=CLAIM_T0 + timedelta(seconds=11),
                lease_ttl=timedelta(seconds=30),
            )

    def test_backdated_operation_after_renewal_is_stale_and_cannot_move_updated_at_backwards(self) -> None:
        store, claim_at = claim_fixture(self)
        claim = claim_at(CLAIM_T0)
        renewed = store.renew_command_lease(
            claim,
            now=CLAIM_T0 + timedelta(seconds=10),
            lease_ttl=timedelta(seconds=30),
        )
        request = DispatchRequest.from_command(
            renewed.command,
            dumps_command(renewed.command),
        )
        before = store.load_ledger(renewed.command.command_id)
        with self.assertRaises(StaleLease):
            store.fence_dispatch(
                renewed,
                request,
                now=CLAIM_T0 + timedelta(seconds=5),
            )
        failure = PreparationFailure(
            reason="synthetic_backdated_preparation",
            retryable=True,
            evidence=("e" * 64,),
        )
        with self.assertRaises(StaleLease):
            store.release_preparation_failure(
                renewed,
                failure,
                now=CLAIM_T0 + timedelta(seconds=5),
            )
        self.assertEqual(store.load_ledger(renewed.command.command_id), before)

    def test_stale_token_cannot_renew_fence_or_release(self) -> None:
        store, claim_at = claim_fixture(self)
        first = claim_at(CLAIM_T0, worker="worker:one")
        second = claim_at(CLAIM_T0 + timedelta(seconds=31), worker="worker:two")
        self.assertIsNotNone(second)
        request = DispatchRequest.from_command(first.command, dumps_command(first.command))
        with self.assertRaises(StaleLease):
            store.renew_command_lease(
                first,
                now=CLAIM_T0 + timedelta(seconds=32),
                lease_ttl=timedelta(seconds=30),
            )
        with self.assertRaises(StaleLease):
            store.fence_dispatch(first, request, now=CLAIM_T0 + timedelta(seconds=32))
        failure = PreparationFailure(
            reason="synthetic_preparation_failure",
            retryable=True,
            evidence=("d" * 64,),
        )
        with self.assertRaises(StaleLease):
            store.release_preparation_failure(
                first,
                failure,
                now=CLAIM_T0 + timedelta(seconds=32),
            )

    def test_fence_persists_exact_request_and_only_one_permit(self) -> None:
        store, claim_at = claim_fixture(self)
        claim = claim_at(CLAIM_T0)
        request = DispatchRequest.from_command(claim.command, dumps_command(claim.command))
        permit = store.fence_dispatch(
            claim,
            request,
            now=CLAIM_T0 + timedelta(seconds=2),
        )
        self.assertEqual(permit.dispatch_slot, 1)
        self.assertEqual(permit.command_id, claim.command.command_id)
        self.assertEqual(permit.request_hash, request.payload_hash)
        ledger = store.load_ledger(claim.command.command_id)
        self.assertEqual(ledger.status, LedgerStatus.DISPATCH_FENCED)
        self.assertEqual(ledger.dispatch_slots_consumed, 1)
        self.assertEqual(ledger.dispatch_request_hash, request.payload_hash)
        with self.assertRaises(DispatchAlreadyFenced):
            store.fence_dispatch(
                claim,
                request,
                now=CLAIM_T0 + timedelta(seconds=3),
            )

    def test_fence_rejects_request_not_identical_to_authorized_command(self) -> None:
        store, claim_at = claim_fixture(self)
        claim = claim_at(CLAIM_T0)
        canonical = DispatchRequest.from_command(claim.command, dumps_command(claim.command))
        divergent = replace(canonical, idempotency_key="idempotency:divergent")
        with self.assertRaises(ValueError):
            store.fence_dispatch(
                claim,
                divergent,
                now=CLAIM_T0 + timedelta(seconds=2),
            )
        ledger = store.load_ledger(claim.command.command_id)
        self.assertEqual(ledger.status, LedgerStatus.PREPARING)
        self.assertEqual(ledger.dispatch_slots_consumed, 0)

    def test_retryable_preparation_failure_requeues_same_command_and_preserves_executing(self) -> None:
        store, claim_at = claim_fixture(self)
        first = claim_at(CLAIM_T0)
        before_counts = database_counts(store.path)
        failure = PreparationFailure(
            reason="synthetic_retryable_preparation",
            retryable=True,
            evidence=("a" * 64,),
        )
        disposition = store.release_preparation_failure(
            first,
            failure,
            now=CLAIM_T0 + timedelta(seconds=2),
        )
        self.assertEqual(disposition, PreparationDisposition.REQUEUED)
        ledger = store.load_ledger(first.command.command_id)
        self.assertEqual(ledger.status, LedgerStatus.QUEUED)
        self.assertEqual(ledger.preparation_failures, 1)
        self.assertIsNone(ledger.claim_owner)
        self.assertEqual(ledger.dispatch_slots_consumed, 0)
        self.assertIsInstance(store.load_workflow(first.command.workflow_id), ExecutingState)
        self.assertEqual(database_counts(store.path), before_counts)
        second = claim_at(CLAIM_T0 + timedelta(seconds=3), worker="worker:two")
        self.assertEqual(second.command, first.command)
        self.assertEqual(second.lease.fencing_token, first.lease.fencing_token + 1)
        self.assertEqual(second.preparation_failures, 1)

    def test_preparation_terminal_outbox_comes_from_public_pure_projection(self) -> None:
        from reservation_execution import project_preparation_failure_outbox

        store, claim_at = claim_fixture(self)
        claim = claim_at(CLAIM_T0)
        failure = PreparationFailure(
            reason="synthetic_projection_preparation",
            retryable=False,
            evidence=("1" * 64,),
        )
        outcome = claim.command.outcome(
            certainty=ExecutionCertainty.NOT_CALLED,
            normalized_status=failure.reason,
            evidence=failure.evidence,
        )
        expected = project_preparation_failure_outbox(
            claim.command,
            outcome,
            created_at=CLAIM_T0 + timedelta(seconds=2),
        )
        store.release_preparation_failure(
            claim,
            failure,
            now=CLAIM_T0 + timedelta(seconds=2),
        )
        self.assertEqual(store.load_outbox(expected.message_id), expected)

    def test_definitive_preparation_failure_is_terminal_not_called_atomically(self) -> None:
        store, claim_at = claim_fixture(self)
        claim = claim_at(CLAIM_T0)
        before = database_counts(store.path)
        failure = PreparationFailure(
            reason="synthetic_definitive_preparation",
            retryable=False,
            evidence=("b" * 64, "a" * 64),
        )
        disposition = store.release_preparation_failure(
            claim,
            failure,
            now=CLAIM_T0 + timedelta(seconds=2),
        )
        self.assertEqual(disposition, PreparationDisposition.TERMINAL_NOT_CALLED)
        state = store.load_workflow(claim.command.workflow_id)
        self.assertIsInstance(state, FailedBeforeProviderState)
        self.assertEqual(state.outcome.certainty, ExecutionCertainty.NOT_CALLED)
        self.assertEqual(state.outcome.evidence, ("a" * 64, "b" * 64))
        ledger = store.load_ledger(claim.command.command_id)
        self.assertEqual(ledger.status, LedgerStatus.OUTCOME_RECORDED)
        self.assertEqual(ledger.preparation_failures, 1)
        self.assertEqual(ledger.dispatch_slots_consumed, 0)
        self.assertIsNone(ledger.claim_owner)
        self.assertEqual(loads_outcome(ledger.outcome_json), state.outcome)
        row = store._connection.execute(
            "SELECT message_id FROM outbox_messages WHERE command_id=?",
            (claim.command.command_id,),
        ).fetchone()
        message = store.load_outbox(row[0])
        self.assertEqual(message.kind, OutboxKind.EXECUTION_NOT_CALLED)
        self.assertEqual(message.template_id, "reservation.execution.not_called.v1")
        self.assertEqual(
            json.loads(message.canonical_payload),
            {
                "certainty": ExecutionCertainty.NOT_CALLED.value,
                "status": failure.reason,
            },
        )
        for forbidden in ("provider", "offer_id", "auth", "exception"):
            self.assertNotIn(forbidden, message.canonical_payload.casefold())
        self.assertEqual(database_counts(store.path), (before[0], before[1] + 1, before[2], before[3], before[4] + 1))
        self.assertIsNone(
            store.claim_command(
                worker_id="worker:later",
                now=CLAIM_T0 + timedelta(minutes=1),
                lease_ttl=timedelta(seconds=30),
            )
        )

    def test_third_retryable_failure_exhausts_budget_and_becomes_terminal(self) -> None:
        store, claim_at = claim_fixture(self)
        failure = PreparationFailure(
            reason="synthetic_retryable_preparation",
            retryable=True,
            evidence=("c" * 64,),
        )
        claim = claim_at(CLAIM_T0)
        self.assertEqual(
            store.release_preparation_failure(
                claim, failure, now=CLAIM_T0 + timedelta(seconds=1)
            ),
            PreparationDisposition.REQUEUED,
        )
        claim = claim_at(CLAIM_T0 + timedelta(seconds=2), worker="worker:two")
        self.assertEqual(
            store.release_preparation_failure(
                claim, failure, now=CLAIM_T0 + timedelta(seconds=3)
            ),
            PreparationDisposition.REQUEUED,
        )
        claim = claim_at(CLAIM_T0 + timedelta(seconds=4), worker="worker:three")
        self.assertEqual(
            store.release_preparation_failure(
                claim, failure, now=CLAIM_T0 + timedelta(seconds=5)
            ),
            PreparationDisposition.TERMINAL_NOT_CALLED,
        )
        ledger = store.load_ledger(claim.command.command_id)
        self.assertEqual(ledger.preparation_failures, 3)
        self.assertEqual(ledger.status, LedgerStatus.OUTCOME_RECORDED)

    def test_terminal_outbox_collision_rolls_back_event_state_and_ledger(self) -> None:
        from reservation_execution import project_preparation_failure_outbox

        store, claim_at = claim_fixture(self)
        claim = claim_at(CLAIM_T0)
        failure = PreparationFailure(
            reason="synthetic_terminal_collision",
            retryable=False,
            evidence=("f" * 64,),
        )
        outcome = claim.command.outcome(
            certainty=ExecutionCertainty.NOT_CALLED,
            normalized_status=failure.reason,
            evidence=failure.evidence,
        )
        message = project_preparation_failure_outbox(
            claim.command,
            outcome,
            created_at=CLAIM_T0 + timedelta(seconds=2),
        )
        with store._transaction("synthetic_collision_fixture"):
            store._insert_outbox(message)
        before_state = store.load_workflow(claim.command.workflow_id)
        before_ledger = store.load_ledger(claim.command.command_id)
        before_counts = database_counts(store.path)
        with self.assertRaises(IdentityConflict):
            store.release_preparation_failure(
                claim,
                failure,
                now=CLAIM_T0 + timedelta(seconds=2),
            )
        self.assertEqual(store.load_workflow(claim.command.workflow_id), before_state)
        self.assertEqual(store.load_ledger(claim.command.command_id), before_ledger)
        self.assertEqual(database_counts(store.path), before_counts)

    def test_exact_lease_expiry_is_stale_for_owner_and_eligible_for_reclaim(self) -> None:
        store, claim_at = claim_fixture(self)
        first = claim_at(CLAIM_T0)
        request = DispatchRequest.from_command(first.command, dumps_command(first.command))
        with self.assertRaises(StaleLease):
            store.fence_dispatch(
                first,
                request,
                now=first.lease.expires_at,
            )
        second = claim_at(first.lease.expires_at, worker="worker:boundary")
        self.assertEqual(second.lease.fencing_token, 2)

    def test_temporal_and_identity_inputs_are_closed(self) -> None:
        store, _ = claim_fixture(self)
        invalid_calls = (
            {"worker_id": " worker:one ", "now": CLAIM_T0, "lease_ttl": timedelta(seconds=30)},
            {"worker_id": "worker:one", "now": CLAIM_T0.replace(tzinfo=None), "lease_ttl": timedelta(seconds=30)},
            {
                "worker_id": "worker:one",
                "now": CLAIM_T0.astimezone(timezone(timedelta(hours=1))),
                "lease_ttl": timedelta(seconds=30),
            },
            {"worker_id": "worker:one", "now": CLAIM_T0, "lease_ttl": timedelta(0)},
            {"worker_id": "worker:one", "now": CLAIM_T0, "lease_ttl": timedelta(seconds=-1)},
        )
        for kwargs in invalid_calls:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises((TypeError, ValueError)):
                    store.claim_command(**kwargs)

    def test_claim_sqlite_failure_is_mapped_without_raw_exception(self) -> None:
        store, _ = claim_fixture(self)
        store._connection.execute("DROP TABLE execution_ledger")
        with self.assertRaisesRegex(Exception, "claim_command") as raised:
            store.claim_command(
                worker_id="worker:one",
                now=CLAIM_T0,
                lease_ttl=timedelta(seconds=30),
            )
        self.assertIsInstance(raised.exception.__cause__, sqlite3.Error)


if __name__ == "__main__":
    unittest.main()
