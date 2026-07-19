from __future__ import annotations

from datetime import timedelta
import inspect
import json
import sqlite3
import unittest

from reservation_domain import (
    ExecutionCertainty,
    ManualReviewState,
)
from reservation_execution import LedgerStatus
from reservation_execution.adapter import PreparationFailure
from reservation_execution.reconciliation import Reconciler, ReconciliationResult
from reservation_execution.sqlite_store import DataCorruption

from tests.phase5_helpers import (
    T0,
    database_counts,
    fenced_store_fixture,
    queued_store_fixture,
    worker_fixture,
)

R0 = T0 + timedelta(minutes=1)


class Phase5ReconciliationTests(unittest.TestCase):
    def test_consistency_rejects_coordinated_impossible_claim_histories(self) -> None:
        cases = []

        queued, _, _, queued_command_id = queued_store_fixture(self)
        queued._connection.execute(
            "UPDATE execution_ledger SET fencing_token=1, claim_count=1 "
            "WHERE command_id=?",
            (queued_command_id,),
        )
        cases.append(("never_started_with_claim", queued))

        executing, _, _, executing_command_id = queued_store_fixture(self)
        executing.claim_command(
            worker_id="worker:expired",
            now=R0,
            lease_ttl=timedelta(seconds=30),
        )
        Reconciler(executing).run_once(now=R0 + timedelta(seconds=30))
        executing._connection.execute(
            "UPDATE execution_ledger SET fencing_token=0, claim_count=0 "
            "WHERE command_id=?",
            (executing_command_id,),
        )
        cases.append(("started_without_claim", executing))

        terminal, terminal_worker, _, _, terminal_command_id = worker_fixture(
            self,
            ExecutionCertainty.EFFECT_CONFIRMED,
        )
        terminal_worker.run_once(now=R0)
        terminal._connection.execute(
            "UPDATE execution_ledger SET fencing_token=0, claim_count=0 "
            "WHERE command_id=?",
            (terminal_command_id,),
        )
        cases.append(("terminal_without_claim", terminal))

        failed, failed_worker, _, _, failed_command_id = worker_fixture(
            self,
            PreparationFailure(
                reason="synthetic_preparation_failure",
                retryable=False,
                evidence=("a" * 64,),
            ),
        )
        failed_worker.run_once(now=R0)
        failed._connection.execute(
            "UPDATE execution_ledger SET preparation_failures=0 "
            "WHERE command_id=?",
            (failed_command_id,),
        )
        cases.append(("not_called_without_failure", failed))

        for name, store in cases:
            with self.subTest(name=name):
                with self.assertRaises(DataCorruption):
                    store.assert_execution_consistency()

    def test_consistency_rejects_temporally_impossible_active_leases(self) -> None:
        preparing, _, _, preparing_command_id = queued_store_fixture(self)
        preparing.claim_command(
            worker_id="worker:preparing",
            now=R0,
            lease_ttl=timedelta(seconds=30),
        )
        preparing._connection.execute(
            "UPDATE execution_ledger SET updated_at=? WHERE command_id=?",
            (
                preparing.load_command(preparing_command_id).created_at.isoformat(),
                preparing_command_id,
            ),
        )

        fenced, _, _, fenced_command_id = fenced_store_fixture(self, R0)
        fenced_ledger = fenced.load_ledger(fenced_command_id)
        impossible_fence = (
            fenced_ledger.lease_acquired_at - timedelta(seconds=1)
        ).isoformat()
        fenced._connection.execute(
            "UPDATE execution_ledger SET dispatch_fenced_at=? WHERE command_id=?",
            (impossible_fence, fenced_command_id),
        )

        for name, store in (("preparing", preparing), ("fenced", fenced)):
            with self.subTest(name=name):
                with self.assertRaises(DataCorruption):
                    store.assert_execution_consistency()

    def test_consistency_requires_the_inverse_summary_outbox_cardinality(self) -> None:
        store, _, _, _ = queued_store_fixture(self)
        row = store._connection.execute(
            "SELECT message_id FROM outbox_messages "
            "WHERE kind='summary_presented'"
        ).fetchone()
        self.assertIsNotNone(row)
        store._connection.execute(
            "DELETE FROM outbox_messages WHERE message_id=?",
            (row[0],),
        )

        with self.assertRaises(DataCorruption):
            store.assert_execution_consistency()

    def test_public_constructor_has_no_adapter_dispatch_or_callback(self) -> None:
        parameters = inspect.signature(Reconciler).parameters
        self.assertEqual(tuple(parameters), ("store",))
        source = inspect.getsource(Reconciler).lower()
        for forbidden in ("adapter", ".dispatch(", "requests", "socket", "http"):
            self.assertNotIn(forbidden, source)

    def test_result_is_exact_nonnegative_closed_value(self) -> None:
        self.assertEqual(
            ReconciliationResult(pre_dispatch_released=1, called_unknown=2),
            ReconciliationResult(pre_dispatch_released=1, called_unknown=2),
        )
        with self.assertRaises(ValueError):
            ReconciliationResult(pre_dispatch_released=-1, called_unknown=0)
        with self.assertRaises(TypeError):
            ReconciliationResult(pre_dispatch_released=True, called_unknown=0)

    def test_expired_pre_dispatch_claim_returns_to_queue_without_second_start_event(self) -> None:
        store, _, workflow_id, command_id = queued_store_fixture(self)
        first = store.claim_command(
            worker_id="worker:one",
            now=R0,
            lease_ttl=timedelta(seconds=30),
        )
        revision = store.load_workflow(workflow_id).meta.revision
        event_count = database_counts(store.path)[1]

        result = Reconciler(store).run_once(now=R0 + timedelta(seconds=30))
        released = store.load_ledger(command_id)
        second = store.claim_command(
            worker_id="worker:two",
            now=R0 + timedelta(seconds=31),
            lease_ttl=timedelta(seconds=30),
        )

        self.assertEqual(result.pre_dispatch_released, 1)
        self.assertEqual(result.called_unknown, 0)
        self.assertEqual(released.status, LedgerStatus.QUEUED)
        self.assertIsNone(released.claim_owner)
        self.assertEqual(second.command.command_id, first.command.command_id)
        self.assertEqual(
            second.lease.fencing_token,
            first.lease.fencing_token + 1,
        )
        self.assertEqual(store.load_workflow(workflow_id).meta.revision, revision)
        self.assertEqual(database_counts(store.path)[1], event_count)

    def test_live_pre_dispatch_and_live_fence_are_untouched(self) -> None:
        store, _, _, command_id = queued_store_fixture(self)
        store.claim_command(
            worker_id="worker:live",
            now=R0,
            lease_ttl=timedelta(seconds=30),
        )
        before = store.load_ledger(command_id)
        result = Reconciler(store).run_once(now=R0 + timedelta(seconds=29))
        self.assertEqual(result, ReconciliationResult(0, 0))
        self.assertEqual(store.load_ledger(command_id), before)

        fenced, _, _, fenced_command_id = fenced_store_fixture(self, R0)
        fenced_before = fenced.load_ledger(fenced_command_id)
        fenced_result = Reconciler(fenced).run_once(
            now=R0 + timedelta(seconds=29)
        )
        self.assertEqual(fenced_result, ReconciliationResult(0, 0))
        self.assertEqual(fenced.load_ledger(fenced_command_id), fenced_before)

    def test_expired_post_fence_becomes_unknown_atomically_without_adapter(self) -> None:
        store, _, workflow_id, command_id = fenced_store_fixture(self, R0)
        before = database_counts(store.path)

        result = Reconciler(store).run_once(now=R0 + timedelta(seconds=30))

        self.assertEqual(result, ReconciliationResult(0, 1))
        state = store.load_workflow(workflow_id)
        self.assertIsInstance(state, ManualReviewState)
        self.assertEqual(state.outcome.certainty, ExecutionCertainty.CALLED_UNKNOWN)
        self.assertEqual(
            state.outcome.normalized_status,
            "dispatch_outcome_unknown_after_expiry",
        )
        ledger = store.load_ledger(command_id)
        self.assertEqual(ledger.status, LedgerStatus.MANUAL_REVIEW)
        self.assertEqual(ledger.dispatch_slots_consumed, 1)
        self.assertIsNone(ledger.claim_owner)
        self.assertIsNotNone(ledger.outcome_json)
        self.assertEqual(database_counts(store.path)[1], before[1] + 2)
        self.assertEqual(database_counts(store.path)[4], before[4] + 1)
        outbox_id = store._connection.execute(
            "SELECT message_id FROM outbox_messages WHERE command_id=?",
            (command_id,),
        ).fetchone()[0]
        payload = json.loads(store.load_outbox(outbox_id).canonical_payload)
        self.assertEqual(
            payload,
            {"certainty": "called_unknown", "status": "manual_review_required"},
        )

    def test_repeated_reconciliation_is_idempotent_and_never_reopens_dispatch(self) -> None:
        store, _, _, command_id = fenced_store_fixture(self, R0)
        reconciler = Reconciler(store)
        first = reconciler.run_once(now=R0 + timedelta(seconds=30))
        counts = database_counts(store.path)
        second = reconciler.run_once(now=R0 + timedelta(minutes=2))
        claim = store.claim_command(
            worker_id="worker:forbidden",
            now=R0 + timedelta(minutes=3),
            lease_ttl=timedelta(seconds=30),
        )

        self.assertEqual(first.called_unknown, 1)
        self.assertEqual(second, ReconciliationResult(0, 0))
        self.assertEqual(database_counts(store.path), counts)
        self.assertIsNone(claim)
        self.assertEqual(
            store.load_ledger(command_id).dispatch_slots_consumed,
            1,
        )

    def test_consistency_rejects_impossible_requeued_claim_counters(self) -> None:
        for assignment in (
            "claim_count=0",
            "fencing_token=0",
            "preparation_failures=2",
        ):
            with self.subTest(assignment=assignment):
                store, path, _, command_id = queued_store_fixture(self)
                store.claim_command(
                    worker_id="worker:expired",
                    now=R0,
                    lease_ttl=timedelta(seconds=30),
                )
                Reconciler(store).run_once(now=R0 + timedelta(seconds=30))
                connection = sqlite3.connect(path)
                try:
                    connection.execute(
                        f"UPDATE execution_ledger SET {assignment} WHERE command_id=?",
                        (command_id,),
                    )
                    connection.commit()
                finally:
                    connection.close()

                with self.assertRaises(DataCorruption):
                    Reconciler(store).run_once(now=R0 + timedelta(minutes=2))

    def test_consistency_check_fails_before_releasing_another_expired_claim(self) -> None:
        store, path, _, command_id = queued_store_fixture(self)
        store.claim_command(
            worker_id="worker:expired",
            now=R0,
            lease_ttl=timedelta(seconds=30),
        )
        before = store.load_ledger(command_id)
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE execution_ledger SET status='outcome_recorded' "
                "WHERE command_id=?",
                (command_id,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(DataCorruption):
            Reconciler(store).run_once(now=R0 + timedelta(minutes=2))

        row = store._connection.execute(
            "SELECT claim_owner, fencing_token, dispatch_slots_consumed "
            "FROM execution_ledger WHERE command_id=?",
            (command_id,),
        ).fetchone()
        self.assertEqual(row, (before.claim_owner, before.fencing_token, 0))

    def test_store_consistency_detects_fenced_request_hash_rewrite(self) -> None:
        store, path, _, command_id = fenced_store_fixture(self, R0)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "UPDATE execution_ledger SET dispatch_request_hash=? "
                "WHERE command_id=?",
                ("f" * 64, command_id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(DataCorruption):
            Reconciler(store).run_once(now=R0 + timedelta(minutes=2))

    def test_pending_outbox_is_not_claimed_or_delivered(self) -> None:
        store, _, _, command_id = fenced_store_fixture(self, R0)
        Reconciler(store).run_once(now=R0 + timedelta(seconds=30))
        before = store._connection.execute(
            "SELECT status, claim_owner, fencing_token, delivery_attempts "
            "FROM outbox_messages WHERE command_id=?",
            (command_id,),
        ).fetchone()

        result = Reconciler(store).run_once(now=R0 + timedelta(minutes=2))
        after = store._connection.execute(
            "SELECT status, claim_owner, fencing_token, delivery_attempts "
            "FROM outbox_messages WHERE command_id=?",
            (command_id,),
        ).fetchone()

        self.assertEqual(result, ReconciliationResult(0, 0))
        self.assertEqual(before, ("pending", None, 0, 0))
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
