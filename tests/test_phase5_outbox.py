from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import unittest
from pathlib import Path

from reservation_execution import Lease, OutboxStatus
from reservation_execution.outbox import (
    OutboxWorkerDisposition,
)
from reservation_execution.sqlite_store import IdentityConflict, SQLiteUnitOfWork, StaleLease
from tests.phase5_helpers import (
    T0,
    outbox_fixture,
    receipt_for,
    successful_receipt,
)

NOW = T0 + timedelta(minutes=3)


class Phase5OutboxTests(unittest.TestCase):
    @staticmethod
    def _ledger_bytes(store: SQLiteUnitOfWork, command_id: str):
        return store._connection.execute(
            "SELECT status, claim_owner, fencing_token, lease_acquired_at, "
            "lease_expires_at, claim_count, preparation_failures, "
            "dispatch_slots_consumed, dispatch_request_hash, dispatch_fenced_at, "
            "outcome_json, outcome_hash, updated_at FROM execution_ledger "
            "WHERE command_id=?",
            (command_id,),
        ).fetchone()

    def test_delivery_marks_receipt_without_touching_ledger(self) -> None:
        store, worker, delivery, command_id, message_id = outbox_fixture(
            self,
            [successful_receipt(delivered_at=NOW)],
        )
        before = self._ledger_bytes(store, command_id)

        result = worker.run_once(now=NOW)

        self.assertEqual(result.disposition, OutboxWorkerDisposition.DELIVERED)
        self.assertEqual(result.message_id, message_id)
        self.assertEqual(self._ledger_bytes(store, command_id), before)
        snapshot = store.load_outbox_snapshot(message_id)
        self.assertEqual(snapshot.status, OutboxStatus.DELIVERED)
        self.assertEqual(snapshot.delivery_attempts, 1)
        self.assertEqual(snapshot.delivered_at, NOW)
        self.assertEqual(delivery.calls, 1)
        self.assertEqual(delivery.messages[0].idempotency_key, message_id)

    def test_delivery_failure_releases_only_message(self) -> None:
        store, worker, delivery, command_id, message_id = outbox_fixture(
            self,
            [RuntimeError("synthetic delivery failure")],
        )
        before = self._ledger_bytes(store, command_id)

        result = worker.run_once(now=NOW)

        self.assertEqual(result.disposition, OutboxWorkerDisposition.RETRYABLE_FAILURE)
        self.assertEqual(self._ledger_bytes(store, command_id), before)
        snapshot = store.load_outbox_snapshot(message_id)
        self.assertEqual(snapshot.status, OutboxStatus.PENDING)
        self.assertEqual(snapshot.delivery_attempts, 1)
        self.assertIsNone(snapshot.claim_owner)
        self.assertEqual(delivery.calls, 1)

    def test_expired_outbox_lease_is_reclaimable_at_exact_expiry(self) -> None:
        store, _, _, command_id, message_id = outbox_fixture(self, [])
        before = self._ledger_bytes(store, command_id)
        first = store.claim_outbox(
            worker_id="delivery:one",
            now=NOW,
            lease_ttl=timedelta(seconds=30),
        )
        second = store.claim_outbox(
            worker_id="delivery:two",
            now=NOW + timedelta(seconds=30),
            lease_ttl=timedelta(seconds=30),
        )

        self.assertEqual(second.message.message_id, first.message.message_id)
        self.assertEqual(second.message.message_id, message_id)
        self.assertEqual(second.lease.fencing_token, first.lease.fencing_token + 1)
        self.assertEqual(second.delivery_attempts, first.delivery_attempts + 1)
        self.assertEqual(self._ledger_bytes(store, command_id), before)

    def test_live_outbox_lease_is_not_reclaimable(self) -> None:
        store, _, _, command_id, _ = outbox_fixture(self, [])
        before = self._ledger_bytes(store, command_id)
        first = store.claim_outbox(
            worker_id="delivery:one",
            now=NOW,
            lease_ttl=timedelta(seconds=30),
        )

        other = store.claim_outbox(
            worker_id="delivery:two",
            now=NOW + timedelta(seconds=29),
            lease_ttl=timedelta(seconds=30),
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(other)
        self.assertNotEqual(other.message.message_id, first.message.message_id)
        self.assertEqual(self._ledger_bytes(store, command_id), before)

    def test_stale_delivery_token_cannot_mark_delivered_or_release(self) -> None:
        store, _, _, command_id, _ = outbox_fixture(self, [])
        before = self._ledger_bytes(store, command_id)
        first = store.claim_outbox(
            worker_id="delivery:one",
            now=NOW,
            lease_ttl=timedelta(seconds=30),
        )
        second = store.claim_outbox(
            worker_id="delivery:two",
            now=NOW + timedelta(seconds=30),
            lease_ttl=timedelta(seconds=30),
        )
        receipt = receipt_for(
            first.message,
            delivered_at=NOW + timedelta(seconds=31),
        )

        with self.assertRaises(StaleLease):
            store.complete_outbox(first, receipt, now=receipt.delivered_at)
        with self.assertRaises(StaleLease):
            store.release_outbox(first, now=receipt.delivered_at)

        snapshot = store.load_outbox_snapshot(second.message.message_id)
        self.assertEqual(snapshot.claim_owner, "delivery:two")
        self.assertEqual(self._ledger_bytes(store, command_id), before)

    def test_duplicate_receipt_is_idempotent_but_divergent_receipt_conflicts(self) -> None:
        store, _, _, command_id, _ = outbox_fixture(self, [])
        before = self._ledger_bytes(store, command_id)
        claim = store.claim_outbox(
            worker_id="delivery:one",
            now=NOW,
            lease_ttl=timedelta(seconds=30),
        )
        delivered_at = NOW + timedelta(seconds=1)
        receipt = receipt_for(claim.message, delivered_at=delivered_at)

        first = store.complete_outbox(claim, receipt, now=delivered_at)
        duplicate = store.complete_outbox(claim, receipt, now=delivered_at)
        divergent = receipt_for(
            claim.message,
            delivered_at=delivered_at,
            delivery_reference="delivery:other",
        )
        with self.assertRaises(IdentityConflict):
            store.complete_outbox(claim, divergent, now=delivered_at)

        self.assertEqual(first, duplicate)
        self.assertEqual(first.status, OutboxStatus.DELIVERED)
        self.assertEqual(self._ledger_bytes(store, command_id), before)

    def test_duplicate_receipt_remains_idempotent_when_replayed_later(self) -> None:
        store, _, _, command_id, _ = outbox_fixture(self, [])
        before = self._ledger_bytes(store, command_id)
        claim = store.claim_outbox(
            worker_id="delivery:one",
            now=NOW,
            lease_ttl=timedelta(seconds=30),
        )
        receipt = receipt_for(
            claim.message,
            delivered_at=NOW + timedelta(seconds=1),
        )
        first = store.complete_outbox(
            claim,
            receipt,
            now=NOW + timedelta(seconds=2),
        )

        replay = store.complete_outbox(
            claim,
            receipt,
            now=NOW + timedelta(days=1),
        )

        self.assertEqual(replay, first)
        self.assertEqual(replay.delivered_at, receipt.delivered_at)
        self.assertEqual(self._ledger_bytes(store, command_id), before)

    def test_duplicate_receipt_rejects_divergent_claim_message_bytes(self) -> None:
        store, _, _, command_id, _ = outbox_fixture(self, [])
        before = self._ledger_bytes(store, command_id)
        claim = store.claim_outbox(
            worker_id="delivery:one",
            now=NOW,
            lease_ttl=timedelta(seconds=30),
        )
        receipt = receipt_for(claim.message, delivered_at=NOW + timedelta(seconds=1))
        store.complete_outbox(claim, receipt, now=receipt.delivered_at)
        forged = replace(
            claim,
            message=replace(claim.message, template_id="template:forged"),
        )

        with self.assertRaises(IdentityConflict):
            store.complete_outbox(
                forged,
                receipt,
                now=NOW + timedelta(seconds=2),
            )

        self.assertEqual(self._ledger_bytes(store, command_id), before)

    def test_completed_message_is_not_claimed_again(self) -> None:
        store, _, _, _, _ = outbox_fixture(self, [])
        first = store.claim_outbox(
            worker_id="delivery:one",
            now=NOW,
            lease_ttl=timedelta(seconds=30),
        )
        receipt = receipt_for(first.message, delivered_at=NOW + timedelta(seconds=1))
        store.complete_outbox(first, receipt, now=receipt.delivered_at)
        second = store.claim_outbox(
            worker_id="delivery:two",
            now=NOW + timedelta(seconds=2),
            lease_ttl=timedelta(seconds=30),
        )
        self.assertIsNotNone(second)
        self.assertNotEqual(second.message.message_id, first.message.message_id)

    def test_worker_is_idle_after_all_messages_are_delivered(self) -> None:
        store, worker, delivery, _, _ = outbox_fixture(
            self,
            [
                successful_receipt(delivered_at=NOW),
                successful_receipt(delivered_at=NOW + timedelta(seconds=1)),
            ],
        )
        worker.run_once(now=NOW)
        worker.run_once(now=NOW + timedelta(seconds=1))

        result = worker.run_once(now=NOW + timedelta(seconds=2))

        self.assertEqual(result.disposition, OutboxWorkerDisposition.IDLE)
        self.assertIsNone(result.message_id)
        self.assertEqual(delivery.calls, 2)

    def test_base_exception_is_not_swallowed_and_leaves_durable_lease(self) -> None:
        store, worker, delivery, command_id, message_id = outbox_fixture(
            self,
            [KeyboardInterrupt()],
        )
        before = self._ledger_bytes(store, command_id)

        with self.assertRaises(KeyboardInterrupt):
            worker.run_once(now=NOW)

        snapshot = store.load_outbox_snapshot(message_id)
        self.assertEqual(snapshot.status, OutboxStatus.LEASED)
        self.assertEqual(snapshot.claim_owner, "delivery:scripted")
        self.assertEqual(delivery.calls, 1)
        self.assertEqual(self._ledger_bytes(store, command_id), before)

    def test_two_connections_have_exactly_one_winner_for_one_pending_message(self) -> None:
        store, _, _, _, _ = outbox_fixture(self, [])
        rows = tuple(
            store._connection.execute(
                "SELECT message_id FROM outbox_messages ORDER BY created_at, message_id"
            )
        )
        # Leave exactly one eligible message so the concurrency assertion is unambiguous.
        store._connection.execute(
            "UPDATE outbox_messages SET status='leased', claim_owner=?, "
            "fencing_token=1, lease_acquired_at=?, lease_expires_at=?, "
            "delivery_attempts=1, updated_at=? WHERE message_id=?",
            (
                "delivery:blocker",
                NOW.isoformat(),
                (NOW + timedelta(minutes=1)).isoformat(),
                NOW.isoformat(),
                rows[1][0],
            ),
        )
        path = store._connection.execute("PRAGMA database_list").fetchone()[2]
        other = SQLiteUnitOfWork.open(Path(path))
        self.addCleanup(other.close)

        first = store.claim_outbox(
            worker_id="delivery:one",
            now=NOW + timedelta(seconds=1),
            lease_ttl=timedelta(seconds=30),
        )
        second = other.claim_outbox(
            worker_id="delivery:two",
            now=NOW + timedelta(seconds=1),
            lease_ttl=timedelta(seconds=30),
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_divergent_claim_object_is_rejected_fail_closed(self) -> None:
        store, _, _, command_id, _ = outbox_fixture(self, [])
        before = self._ledger_bytes(store, command_id)
        claim = store.claim_outbox(
            worker_id="delivery:one",
            now=NOW,
            lease_ttl=timedelta(seconds=30),
        )
        forged = replace(
            claim,
            lease=Lease(
                owner=claim.lease.owner,
                fencing_token=claim.lease.fencing_token + 1,
                acquired_at=claim.lease.acquired_at,
                expires_at=claim.lease.expires_at,
            ),
            delivery_attempts=claim.delivery_attempts + 1,
        )
        with self.assertRaises(StaleLease):
            store.release_outbox(forged, now=NOW + timedelta(seconds=1))
        self.assertEqual(self._ledger_bytes(store, command_id), before)


if __name__ == "__main__":
    unittest.main()
