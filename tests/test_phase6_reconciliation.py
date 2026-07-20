from __future__ import annotations

from datetime import timedelta
import hashlib
from pathlib import Path
import tempfile
import unittest

from reservation_followup.reconciliation import (
    PaymentReconciler,
    SettlementRecoveryDisposition,
)
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from reservation_followup.types import PaymentMethod, PaymentStatus
from tests.test_phase6_payment_claims import (
    LEASE_TTL,
    NOW,
    alternate_anchor,
    payment_fingerprint,
    pix_visual_evidence,
    prepare_payment,
)


class Phase6PaymentReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase6-task9-reconcile-")
        self.addCleanup(self.temporary.cleanup)
        self.store = SQLiteFollowupUnitOfWork.open(Path(self.temporary.name) / "reconcile.db")
        self.addCleanup(self.store.close)

    def _queue(self, suffix: str) -> str:
        e2e_tail = hashlib.sha256(suffix.encode("utf-8")).hexdigest()[:11].upper()
        state, event = prepare_payment(
            self.store,
            suffix=f"reconcile-{suffix}",
            method=PaymentMethod.PIX,
            evidence=pix_visual_evidence(
                normalized_e2e=f"E1234567820270201{e2e_tail}",
            ),
            anchor=alternate_anchor(f"reconcile-{suffix}"),
        )
        self.store.claim_payment_evidence(state.subject.payment_id, 3, event)
        return state.subject.payment_id

    def _reconciler(self) -> PaymentReconciler:
        return PaymentReconciler(store=self.store)

    def test_no_expired_ledger_is_idle_and_store_only(self) -> None:
        payment_id = self._queue("idle")
        claim = self.store.claim_settlement(
            worker_id="worker:settlement:reconcile:idle",
            now=NOW,
            lease_ttl=LEASE_TTL,
        )
        before = payment_fingerprint(self.store._connection)
        result = self._reconciler().run_once(now=NOW + timedelta(seconds=29))
        self.assertIs(result.disposition, SettlementRecoveryDisposition.IDLE)
        self.assertIsNone(result.settlement_command_id)
        self.assertEqual(payment_fingerprint(self.store._connection), before)
        self.assertIs(self.store.load_payment(payment_id).status, PaymentStatus.SETTLEMENT_QUEUED)
        self.assertFalse(hasattr(self._reconciler(), "settlement"))
        self.assertFalse(hasattr(self._reconciler(), "_settlement"))

    def test_exact_expiry_pre_fence_requeues_without_port_or_dispatch_slot(self) -> None:
        payment_id = self._queue("pre-fence")
        claim = self.store.claim_settlement(
            worker_id="worker:settlement:reconcile:pre",
            now=NOW,
            lease_ttl=LEASE_TTL,
        )
        result = self._reconciler().run_once(now=claim.lease.expires_at)
        self.assertIs(result.disposition, SettlementRecoveryDisposition.PRE_FENCE_REQUEUED)
        self.assertEqual(result.settlement_command_id, claim.command.settlement_command_id)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT status, claim_owner, fencing_token, claim_count, "
                "dispatch_slots_consumed, outcome_certainty FROM main.payment_ledger"
            ).fetchone(),
            ("queued", None, 1, 1, 0, None),
        )
        self.assertIs(self.store.load_payment(payment_id).status, PaymentStatus.SETTLEMENT_QUEUED)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT status FROM main.payment_evidence_claims"
            ).fetchone(),
            ("retryable",),
        )

    def test_exhausted_expired_pre_fence_claim_becomes_terminal_not_dispatched(self) -> None:
        payment_id = self._queue("pre-terminal")
        current_now = NOW
        last = None
        for attempt in range(3):
            last = self.store.claim_settlement(
                worker_id=f"worker:settlement:reconcile:budget:{attempt}",
                now=current_now,
                lease_ttl=LEASE_TTL,
            )
            current_now = last.lease.expires_at
        result = self._reconciler().run_once(now=current_now)
        self.assertIs(result.disposition, SettlementRecoveryDisposition.PRE_FENCE_TERMINAL)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT status, claim_owner, fencing_token, claim_count, "
                "dispatch_slots_consumed, outcome_certainty FROM main.payment_ledger"
            ).fetchone(),
            ("outcome_recorded", None, 3, 3, 0, "not_dispatched"),
        )
        self.assertIs(self.store.load_payment(payment_id).status, PaymentStatus.RETRYABLE)
        before = payment_fingerprint(self.store._connection)
        self.assertIs(
            self._reconciler().run_once(now=current_now + timedelta(seconds=1)).disposition,
            SettlementRecoveryDisposition.IDLE,
        )
        self.assertEqual(payment_fingerprint(self.store._connection), before)

    def test_exact_expiry_post_fence_becomes_unknown_manual_review_without_dispatch(self) -> None:
        payment_id = self._queue("post-fence")
        claim = self.store.claim_settlement(
            worker_id="worker:settlement:reconcile:post",
            now=NOW,
            lease_ttl=LEASE_TTL,
        )
        permit = self.store.fence_settlement(
            claim,
            claim.command.canonical_payload,
            now=NOW + timedelta(seconds=1),
        )
        result = self._reconciler().run_once(now=claim.lease.expires_at)
        self.assertIs(result.disposition, SettlementRecoveryDisposition.POST_FENCE_MANUAL_REVIEW)
        self.assertEqual(result.settlement_command_id, claim.command.settlement_command_id)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT status, claim_owner, fencing_token, claim_count, "
                "dispatch_slots_consumed, dispatch_request_hash, outcome_certainty "
                "FROM main.payment_ledger"
            ).fetchone(),
            ("manual_review", None, 1, 1, 1, permit.request_hash, "dispatched_unknown"),
        )
        state = self.store.load_payment(payment_id)
        self.assertIs(state.status, PaymentStatus.MANUAL_REVIEW)
        self.assertTrue(state.settlement_finish.outcome.requires_reconciliation)
        self.assertEqual(state.settlement_finish.outcome.claim_evidence, (permit.request_hash,))
        self.assertEqual(
            self.store._connection.execute(
                "SELECT status FROM main.payment_evidence_claims"
            ).fetchone(),
            ("manual_review",),
        )
        self.assertEqual(
            tuple(
                self.store._connection.execute(
                    "SELECT kind, status FROM main.payment_outbox"
                )
            ),
            (("manual_review", "pending"),),
        )

    def test_post_fence_recovery_is_one_shot_and_never_changes_slot_or_calls_port(self) -> None:
        payment_ids = (self._queue("post-one"), self._queue("post-two"))
        permits = []
        for index, payment_id in enumerate(payment_ids):
            claim = self.store.claim_settlement(
                worker_id=f"worker:settlement:reconcile:post:{index}",
                now=NOW,
                lease_ttl=LEASE_TTL,
            )
            permits.append(
                self.store.fence_settlement(
                    claim,
                    claim.command.canonical_payload,
                    now=NOW + timedelta(seconds=1),
                )
            )
        reconciler = self._reconciler()
        first = reconciler.run_once(now=NOW + LEASE_TTL)
        self.assertIs(first.disposition, SettlementRecoveryDisposition.POST_FENCE_MANUAL_REVIEW)
        statuses = tuple(
            row[0]
            for row in self.store._connection.execute(
                "SELECT status FROM main.payment_ledger ORDER BY settlement_command_id"
            )
        )
        self.assertEqual(statuses.count("manual_review"), 1)
        self.assertEqual(statuses.count("dispatch_fenced"), 1)
        self.assertEqual(
            tuple(
                row[0]
                for row in self.store._connection.execute(
                    "SELECT dispatch_slots_consumed FROM main.payment_ledger"
                )
            ),
            (1, 1),
        )
        second = reconciler.run_once(now=NOW + LEASE_TTL + timedelta(seconds=1))
        self.assertIs(second.disposition, SettlementRecoveryDisposition.POST_FENCE_MANUAL_REVIEW)
        self.assertIs(
            reconciler.run_once(now=NOW + LEASE_TTL + timedelta(seconds=2)).disposition,
            SettlementRecoveryDisposition.IDLE,
        )


if __name__ == "__main__":
    unittest.main()
