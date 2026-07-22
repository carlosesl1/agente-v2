"""Phase 8 follow-up outbox v2 lease/deadline/CAS hardening."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from tests.phase6_helpers import T0, handoff_requested, optional_email_policy


class Phase8FollowupOutboxV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase8-followup-outbox-v2-")
        self.path = Path(self.temporary.name) / "followup.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_both_v2_outboxes_have_closed_hardening_columns(self) -> None:
        expected = {
            "dispatch_slots_consumed": ("INTEGER", 1),
            "qualification_id": ("TEXT", 0),
            "epoch": ("INTEGER", 0),
            "scenario_id": ("TEXT", 0),
            "generation_id": ("TEXT", 0),
            "allocation_id": ("TEXT", 0),
            "effect_authorization_binding_hash": ("TEXT", 0),
            "dispatch_deadline_at": ("TEXT", 0),
            "cas_revision": ("INTEGER", 1),
        }
        with SQLiteFollowupUnitOfWork.open_v2(self.path) as store:
            for table in ("handoff_outbox", "payment_outbox"):
                columns = {
                    row[1]: (row[2], row[3])
                    for row in store._connection.execute(f"PRAGMA table_info({table})")
                }
                self.assertEqual(
                    {name: columns.get(name) for name in expected},
                    expected,
                )
                sql = store._connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0]
                for state in ("dispatch_fenced", "cancelled", "manual_review"):
                    self.assertIn(state, sql)

    def test_first_claim_seals_deadline_and_reclaim_never_extends_it(self) -> None:
        with SQLiteFollowupUnitOfWork.open_v2(self.path) as store:
            store.open_handoff(handoff_requested(), optional_email_policy())
            first_now = T0 + timedelta(seconds=1)
            first = store.claim_handoff_outbox(
                worker_id="worker:phase8:v2:1",
                delivery_id="delivery:phase8:v2:1",
                delivery_version=1,
                now=first_now,
                lease_ttl=timedelta(seconds=10),
            )
            self.assertIsNotNone(first)
            assert first is not None
            deadline = first_now + timedelta(seconds=10)
            self.assertEqual(
                store._connection.execute(
                    "SELECT dispatch_deadline_at, dispatch_slots_consumed, cas_revision "
                    "FROM handoff_outbox WHERE message_id=?",
                    (first.message.effect_id,),
                ).fetchone(),
                (deadline.isoformat(), 0, 1),
            )
            store.release_handoff_outbox(first, now=first_now + timedelta(seconds=1))
            self.assertEqual(
                store._connection.execute(
                    "SELECT dispatch_deadline_at, dispatch_slots_consumed, cas_revision "
                    "FROM handoff_outbox WHERE message_id=?",
                    (first.message.effect_id,),
                ).fetchone(),
                (deadline.isoformat(), 0, 2),
            )
            before = store._connection.total_changes
            with self.assertRaises(ValueError):
                store.claim_handoff_outbox(
                    worker_id="worker:phase8:v2:2",
                    delivery_id="delivery:phase8:v2:2",
                    delivery_version=1,
                    now=first_now + timedelta(seconds=2),
                    lease_ttl=timedelta(seconds=20),
                )
            self.assertEqual(store._connection.total_changes, before)
            self.assertEqual(
                store._connection.execute(
                    "SELECT status, dispatch_deadline_at, dispatch_slots_consumed, "
                    "cas_revision FROM handoff_outbox WHERE message_id=?",
                    (first.message.effect_id,),
                ).fetchone(),
                ("pending", deadline.isoformat(), 0, 2),
            )


if __name__ == "__main__":
    unittest.main()
