from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import sqlite3
import unittest

from reservation_domain import (
    ExecutionCertainty,
    FailedNoEffectState,
    ManualReviewState,
    SucceededState,
    dumps_command,
    dumps_outcome,
)
from reservation_execution import (
    DispatchRequest,
    LedgerStatus,
    PreparationDisposition,
    PreparationFailure,
    project_outcome_outbox,
    project_preparation_failure_outbox,
)
from reservation_execution.sqlite_store import (
    DataCorruption,
    IdentityConflict,
    StaleLease,
)

from tests.phase5_helpers import T0, claim_fixture, database_counts, worker_fixture

RUN_T0 = T0 + timedelta(minutes=1)


def _command_outbox_rows(store, command_id: str):
    connection = sqlite3.connect(store.path)
    try:
        return connection.execute(
            "SELECT kind, template_id, payload_json, status FROM outbox_messages "
            "WHERE command_id=? ORDER BY created_at, message_id",
            (command_id,),
        ).fetchall()
    finally:
        connection.close()


class Phase5WorkerTests(unittest.TestCase):
    def test_not_called_general_projection_is_identical_to_preparation_projection(self) -> None:
        store, claim_at = claim_fixture(self)
        command = claim_at(RUN_T0).command
        outcome = command.outcome(
            certainty=ExecutionCertainty.NOT_CALLED,
            normalized_status="synthetic_preparation_failure",
            evidence=("b" * 64,),
        )

        self.assertEqual(
            project_outcome_outbox(command, outcome, created_at=RUN_T0),
            project_preparation_failure_outbox(
                command,
                outcome,
                created_at=RUN_T0,
            ),
        )

    def test_execution_outbox_loader_rejects_coherent_internal_outcome_rewrite(self) -> None:
        store, worker, _, workflow_id, command_id = worker_fixture(
            self,
            ExecutionCertainty.EFFECT_CONFIRMED,
        )
        worker.run_once(now=RUN_T0)
        command = store.load_command(command_id)
        ledger = store.load_ledger(command_id)
        divergent = command.outcome(
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
            normalized_status="forged_effect_confirmed",
            provider_reference="provider:forged",
            evidence=(ledger.dispatch_request_hash,),
        )
        raw = dumps_outcome(divergent)
        store._connection.execute(
            "UPDATE execution_ledger SET outcome_json=?, outcome_hash=? "
            "WHERE command_id=?",
            (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(), command_id),
        )
        row = store._connection.execute(
            "SELECT message_id FROM outbox_messages WHERE command_id=?",
            (command_id,),
        ).fetchone()
        self.assertIsInstance(store.load_workflow(workflow_id), SucceededState)

        with self.assertRaises(DataCorruption):
            store.load_outbox(row[0])

    def _fenced(self):
        store, claim_at = claim_fixture(self)
        claim = claim_at(RUN_T0)
        request = DispatchRequest.from_command(
            claim.command,
            dumps_command(claim.command),
        )
        permit = store.fence_dispatch(claim, request, now=RUN_T0)
        return store, claim, request, permit

    def test_identical_outcome_replay_is_idempotent_and_divergence_conflicts(self) -> None:
        store, claim, request, permit = self._fenced()
        outcome = claim.command.outcome(
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
            normalized_status="synthetic_effect_confirmed",
            provider_reference="provider:synthetic",
            evidence=(request.payload_hash,),
        )

        first = store.record_outcome(permit, outcome, now=RUN_T0)
        counts = database_counts(store.path)
        duplicate = store.record_outcome(
            permit,
            outcome,
            now=RUN_T0 + timedelta(seconds=1),
        )
        divergent = claim.command.outcome(
            certainty=ExecutionCertainty.CALLED_NO_EFFECT,
            normalized_status="synthetic_no_effect",
            provider_reference="provider:synthetic",
            evidence=(request.payload_hash,),
        )

        self.assertFalse(first.duplicate)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(database_counts(store.path), counts)
        with self.assertRaises(IdentityConflict):
            store.record_outcome(
                permit,
                divergent,
                now=RUN_T0 + timedelta(seconds=2),
            )
        self.assertEqual(database_counts(store.path), counts)

    def test_outbox_collision_rolls_back_unknown_events_state_and_ledger(self) -> None:
        store, claim, request, permit = self._fenced()
        outcome = claim.command.outcome(
            certainty=ExecutionCertainty.CALLED_UNKNOWN,
            normalized_status="synthetic_unknown",
            provider_reference="provider:synthetic",
            evidence=(request.payload_hash,),
        )
        collision = project_outcome_outbox(
            claim.command,
            outcome,
            created_at=RUN_T0,
        )
        store._insert_outbox(collision)
        counts = database_counts(store.path)
        before_state = store.load_workflow(claim.command.workflow_id)
        before_ledger = store.load_ledger(claim.command.command_id)

        with self.assertRaises(IdentityConflict):
            store.record_outcome(permit, outcome, now=RUN_T0)

        self.assertEqual(database_counts(store.path), counts)
        self.assertEqual(store.load_workflow(claim.command.workflow_id), before_state)
        self.assertEqual(store.load_ledger(claim.command.command_id), before_ledger)

    def test_expired_permit_cannot_record_outcome_or_change_fenced_ledger(self) -> None:
        store, claim, request, permit = self._fenced()
        outcome = claim.command.outcome(
            certainty=ExecutionCertainty.CALLED_NO_EFFECT,
            normalized_status="synthetic_no_effect",
            provider_reference="provider:synthetic",
            evidence=(request.payload_hash,),
        )
        before = store.load_ledger(claim.command.command_id)

        with self.assertRaises(StaleLease):
            store.record_outcome(
                permit,
                outcome,
                now=permit.lease.expires_at,
            )

        self.assertEqual(store.load_ledger(claim.command.command_id), before)

    def test_direct_post_fence_not_called_is_rejected_without_mutation(self) -> None:
        store, claim, request, permit = self._fenced()
        outcome = claim.command.outcome(
            certainty=ExecutionCertainty.NOT_CALLED,
            normalized_status="synthetic_not_called",
            evidence=(request.payload_hash,),
        )
        counts = database_counts(store.path)
        before = store.load_ledger(claim.command.command_id)

        with self.assertRaisesRegex(ValueError, "post-fence not_called"):
            store.record_outcome(permit, outcome, now=RUN_T0)

        self.assertEqual(database_counts(store.path), counts)
        self.assertEqual(store.load_ledger(claim.command.command_id), before)

    def test_execution_outbox_loader_rejects_coherent_payload_and_hash_tampering(self) -> None:
        store, worker, _, _, command_id = worker_fixture(
            self,
            ExecutionCertainty.EFFECT_CONFIRMED,
        )
        worker.run_once(now=RUN_T0)
        row = store._connection.execute(
            "SELECT message_id FROM outbox_messages WHERE command_id=?",
            (command_id,),
        ).fetchone()
        tampered = json.dumps(
            {"certainty": "effect_confirmed", "status": "forged_success"},
            sort_keys=True,
            separators=(",", ":"),
        )
        store._connection.execute(
            "UPDATE outbox_messages SET payload_json=?, payload_hash=? "
            "WHERE message_id=?",
            (
                tampered,
                hashlib.sha256(tampered.encode("utf-8")).hexdigest(),
                row[0],
            ),
        )

        with self.assertRaises(DataCorruption):
            store.load_outbox(row[0])

    def test_retryable_preparation_failure_requeues_without_dispatch_or_fence(self) -> None:
        failure = PreparationFailure(
            reason="synthetic_preparation_failure",
            retryable=True,
            evidence=("a" * 64,),
        )
        store, worker, adapter, workflow_id, command_id = worker_fixture(
            self,
            failure,
        )

        result = worker.run_once(now=RUN_T0)

        self.assertEqual(result.preparation, PreparationDisposition.REQUEUED)
        self.assertEqual(adapter.prepare_calls, 1)
        self.assertEqual(adapter.dispatch_calls, 0)
        ledger = store.load_ledger(command_id)
        self.assertEqual(ledger.status, LedgerStatus.QUEUED)
        self.assertEqual(ledger.dispatch_slots_consumed, 0)
        self.assertEqual(store.load_workflow(workflow_id).TYPE, "executing")

    def test_live_outcome_rejects_forged_owner_before_any_write(self) -> None:
        store, claim_at = claim_fixture(self)
        claim = claim_at(RUN_T0)
        request = DispatchRequest.from_command(
            claim.command,
            dumps_command(claim.command),
        )
        permit = store.fence_dispatch(claim, request, now=RUN_T0)
        outcome = claim.command.outcome(
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
            normalized_status="synthetic_effect_confirmed",
            provider_reference="provider:synthetic",
            evidence=(request.payload_hash,),
        )
        forged = replace(
            permit,
            lease=replace(permit.lease, owner="worker:forged"),
        )

        with self.assertRaises(StaleLease):
            store.record_outcome(forged, outcome, now=RUN_T0)

        ledger = store.load_ledger(claim.command.command_id)
        self.assertEqual(ledger.status, LedgerStatus.DISPATCH_FENCED)
        self.assertIsNone(ledger.outcome_json)

    def test_effect_confirmed_calls_dispatch_once_and_persists_success(self) -> None:
        store, worker, adapter, workflow_id, command_id = worker_fixture(
            self,
            ExecutionCertainty.EFFECT_CONFIRMED,
        )

        result = worker.run_once(now=RUN_T0)

        self.assertFalse(result.idle)
        self.assertEqual(adapter.prepare_calls, 1)
        self.assertEqual(adapter.dispatch_calls, 1)
        state = store.load_workflow(workflow_id)
        self.assertIsInstance(state, SucceededState)
        ledger = store.load_ledger(command_id)
        self.assertEqual(ledger.status, LedgerStatus.OUTCOME_RECORDED)
        self.assertEqual(ledger.dispatch_slots_consumed, 1)
        self.assertIsNone(ledger.claim_owner)
        self.assertEqual(len(_command_outbox_rows(store, command_id)), 1)

    def test_called_no_effect_is_terminal_without_retry(self) -> None:
        store, worker, adapter, workflow_id, command_id = worker_fixture(
            self,
            ExecutionCertainty.CALLED_NO_EFFECT,
        )

        worker.run_once(now=RUN_T0)
        second = worker.run_once(now=RUN_T0 + timedelta(minutes=1))

        self.assertIsInstance(store.load_workflow(workflow_id), FailedNoEffectState)
        self.assertTrue(second.idle)
        self.assertEqual(adapter.dispatch_calls, 1)
        self.assertEqual(
            store.load_ledger(command_id).status,
            LedgerStatus.OUTCOME_RECORDED,
        )

    def test_called_unknown_goes_to_manual_review_without_redispatch(self) -> None:
        store, worker, adapter, workflow_id, command_id = worker_fixture(
            self,
            ExecutionCertainty.CALLED_UNKNOWN,
        )

        worker.run_once(now=RUN_T0)
        second = worker.run_once(now=RUN_T0 + timedelta(minutes=1))

        state = store.load_workflow(workflow_id)
        self.assertIsInstance(state, ManualReviewState)
        self.assertTrue(second.idle)
        self.assertEqual(adapter.dispatch_calls, 1)
        self.assertEqual(store.load_ledger(command_id).status, LedgerStatus.MANUAL_REVIEW)
        rows = _command_outbox_rows(store, command_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "execution_manual_review")

    def test_exception_after_fence_becomes_unknown_and_second_run_never_dispatches(self) -> None:
        store, worker, adapter, workflow_id, command_id = worker_fixture(
            self,
            RuntimeError("synthetic dispatch failure"),
        )

        worker.run_once(now=RUN_T0)
        worker.run_once(now=RUN_T0 + timedelta(minutes=1))

        self.assertEqual(adapter.dispatch_calls, 1)
        state = store.load_workflow(workflow_id)
        self.assertIsInstance(state, ManualReviewState)
        self.assertEqual(state.outcome.certainty, ExecutionCertainty.CALLED_UNKNOWN)
        raw_outcome = store.load_ledger(command_id).outcome_json
        self.assertIsNotNone(raw_outcome)
        self.assertNotIn("synthetic dispatch failure", raw_outcome)

    def test_dispatch_returning_not_called_is_contract_violation_promoted_to_unknown(self) -> None:
        store, worker, adapter, workflow_id, command_id = worker_fixture(
            self,
            ExecutionCertainty.NOT_CALLED,
        )

        worker.run_once(now=RUN_T0)

        state = store.load_workflow(workflow_id)
        self.assertIsInstance(state, ManualReviewState)
        self.assertEqual(state.command.command_id, command_id)
        self.assertEqual(state.outcome.certainty, ExecutionCertainty.CALLED_UNKNOWN)
        self.assertEqual(state.outcome.normalized_status, "invalid_post_fence_not_called")
        self.assertEqual(adapter.dispatch_calls, 1)

    def test_base_exception_after_fence_is_not_swallowed_or_redispatched(self) -> None:
        store, worker, adapter, _, command_id = worker_fixture(
            self,
            KeyboardInterrupt(),
        )

        with self.assertRaises(KeyboardInterrupt):
            worker.run_once(now=RUN_T0)

        ledger = store.load_ledger(command_id)
        self.assertEqual(ledger.status, LedgerStatus.DISPATCH_FENCED)
        self.assertEqual(ledger.dispatch_slots_consumed, 1)
        self.assertIsNone(ledger.outcome_json)
        self.assertTrue(worker.run_once(now=RUN_T0 + timedelta(minutes=1)).idle)
        self.assertEqual(adapter.dispatch_calls, 1)

    def test_public_outcome_payload_is_closed_and_excludes_private_fields(self) -> None:
        store, worker, _, _, command_id = worker_fixture(
            self,
            ExecutionCertainty.EFFECT_CONFIRMED,
        )

        worker.run_once(now=RUN_T0)

        rows = _command_outbox_rows(store, command_id)
        self.assertEqual(len(rows), 1)
        kind, template_id, raw_payload, status = rows[0]
        self.assertEqual(kind, "execution_succeeded")
        self.assertEqual(template_id, "reservation.execution.succeeded.v1")
        self.assertEqual(status, "pending")
        payload = json.loads(raw_payload)
        self.assertEqual(
            payload,
            {"certainty": "effect_confirmed", "status": "execution_succeeded"},
        )
        for forbidden in ("provider", "offer", "auth", "reference", "evidence"):
            self.assertNotIn(forbidden, raw_payload.lower())


if __name__ == "__main__":
    unittest.main()
