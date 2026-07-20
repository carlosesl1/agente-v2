from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
from threading import Barrier
import unittest

from reservation_followup import (
    EffectRequirement,
    PaymentEffectJob,
    PaymentEffectKind,
    PaymentEffectPolicy,
    PaymentMethod,
    PaymentMethodSelected,
    PaymentOutboxClaim,
    PaymentOutboxWorker,
    PaymentOutboxWorkerDisposition,
    PaymentReceipt,
    PaymentStatus,
    SettlementCertainty,
    from_wire_json,
)
from reservation_followup.sqlite_store import (
    DataCorruption,
    IdentityConflict,
    SQLiteFollowupUnitOfWork,
    StaleLease,
)
from tests.test_phase6_payment_claims import (
    LEASE_TTL,
    NOW,
    alternate_anchor,
    outcome,
    pix_visual_evidence,
    prepare_payment,
)


class DeliveryCrash(BaseException):
    pass


class FakePaymentEffectDelivery:
    delivery_id = "payment-effect-delivery:synthetic"
    delivery_version = 1

    def __init__(self, mode: str = "success") -> None:
        self.mode = mode
        self.calls = 0
        self.claims: list[PaymentOutboxClaim] = []

    def deliver(self, claim: PaymentOutboxClaim) -> PaymentReceipt:
        self.calls += 1
        self.claims.append(claim)
        if self.mode == "exception":
            raise RuntimeError("SYNTHETIC_PRIVATE_PAYMENT_EFFECT_DETAIL")
        if self.mode == "base_exception":
            raise DeliveryCrash("synthetic payment effect crash")
        if self.mode == "invalid":
            return object()
        return PaymentReceipt.for_claim(
            claim,
            receipt_id=f"payment:receipt:synthetic:{claim.message_id}",
            delivery_reference=f"payment:delivery:reference:{claim.message_id}",
            delivered_at=claim.lease_acquired_at,
        )


class Phase6PaymentOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase6-task10-outbox-")
        self.addCleanup(self.temporary.cleanup)
        self.db_path = Path(self.temporary.name) / "outbox.db"
        self.store = SQLiteFollowupUnitOfWork.open(self.db_path)
        self.addCleanup(self.store.close)

    @staticmethod
    def policy(
        *,
        internal: EffectRequirement = EffectRequirement.DISABLED,
        booking: EffectRequirement = EffectRequirement.DISABLED,
    ) -> PaymentEffectPolicy:
        return PaymentEffectPolicy(
            paid_state_transition=EffectRequirement.REQUIRED,
            customer_payment_confirmation=EffectRequirement.REQUIRED,
            internal_payment_email=internal,
            booking_form=booking,
        )

    def settle(
        self,
        suffix: str,
        *,
        policy: PaymentEffectPolicy | None = None,
        certainty: SettlementCertainty = SettlementCertainty.SETTLED,
    ) -> str:
        e2e_tail = hashlib.sha256(suffix.encode("utf-8")).hexdigest()[:11].upper()
        state, event = prepare_payment(
            self.store,
            suffix=f"task10-{suffix}",
            method=PaymentMethod.PIX,
            evidence=pix_visual_evidence(
                normalized_e2e=f"E1234567820270201{e2e_tail}",
            ),
            anchor=alternate_anchor(f"task10-{suffix}"),
            policy=policy or self.policy(),
        )
        self.store.claim_payment_evidence(state.subject.payment_id, 3, event)
        claim = self.store.claim_settlement(
            worker_id=f"worker:settlement:task10:{suffix}",
            now=NOW,
            lease_ttl=LEASE_TTL,
        )
        permit = self.store.fence_settlement(
            claim,
            claim.command.canonical_payload,
            now=NOW + timedelta(seconds=1),
        )
        result = outcome(
            certainty,
            claim_evidence=(permit.request_hash,),
        )
        self.store.record_settlement_outcome(
            claim,
            permit,
            result,
            now=NOW + timedelta(seconds=2),
        )
        return state.subject.payment_id

    def worker(
        self,
        delivery: FakePaymentEffectDelivery,
        *,
        worker_id: str = "worker:payment-effect:task10",
    ) -> PaymentOutboxWorker:
        return PaymentOutboxWorker(
            store=self.store,
            delivery=delivery,
            worker_id=worker_id,
            lease_ttl=LEASE_TTL,
        )

    def ledger_fingerprint(self, payment_id: str):
        return self.store._connection.execute(
            "SELECT settlement_command_id, status, fencing_token, claim_count, "
            "dispatch_slots_consumed, dispatch_request_hash, outcome_certainty, "
            "outcome_hash, outcome_recorded_at FROM main.payment_ledger "
            "WHERE payment_id=?",
            (payment_id,),
        ).fetchone()

    def test_persisted_policy_matrix_is_exact_and_required_jobs_precede_optional(self) -> None:
        payment_id = self.settle(
            "policy",
            policy=self.policy(
                internal=EffectRequirement.OPTIONAL,
                booking=EffectRequirement.REQUIRED,
            ),
        )
        rows = tuple(
            self.store._connection.execute(
                "SELECT kind, payload_json FROM main.payment_outbox "
                "WHERE payment_id=? ORDER BY kind",
                (payment_id,),
            )
        )
        self.assertEqual(
            tuple(row[0] for row in rows),
            (
                "booking_form",
                "customer_payment_confirmation",
                "internal_payment_email",
                "paid_state_transition",
            ),
        )
        claims = []
        for index in range(4):
            claim = self.store.claim_payment_outbox(
                worker_id=f"worker:payment-effect:policy:{index}",
                delivery_id="payment-effect-delivery:policy",
                delivery_version=1,
                now=NOW + timedelta(seconds=3 + index),
                lease_ttl=LEASE_TTL,
            )
            claims.append(claim)
            receipt = PaymentReceipt.for_claim(
                claim,
                receipt_id=f"payment:receipt:policy:{index}",
                delivery_reference=f"payment:delivery:policy:{index}",
                delivered_at=NOW + timedelta(seconds=3 + index),
            )
            self.store.complete_payment_outbox(
                claim,
                receipt,
                now=NOW + timedelta(seconds=3 + index),
            )
        self.assertEqual(
            tuple((claim.message.kind, claim.message.required) for claim in claims),
            (
                (PaymentEffectKind.PAID_STATE_TRANSITION, True),
                (PaymentEffectKind.CUSTOMER_PAYMENT_CONFIRMATION, True),
                (PaymentEffectKind.BOOKING_FORM, True),
                (PaymentEffectKind.INTERNAL_PAYMENT_EMAIL, False),
            ),
        )

    def test_booking_policy_required_optional_and_disabled_is_exact(self) -> None:
        cases = (
            (EffectRequirement.REQUIRED, True),
            (EffectRequirement.OPTIONAL, False),
            (EffectRequirement.DISABLED, None),
        )
        for index, (requirement, expected_required) in enumerate(cases):
            with self.subTest(requirement=requirement):
                payment_id = self.settle(
                    f"booking-{index}",
                    policy=self.policy(booking=requirement),
                )
                row = self.store._connection.execute(
                    "SELECT payload_json FROM main.payment_outbox "
                    "WHERE payment_id=? AND kind='booking_form'",
                    (payment_id,),
                ).fetchone()
                if expected_required is None:
                    self.assertIsNone(row)
                else:
                    self.assertIsNotNone(row)
                    job = from_wire_json(row[0], PaymentEffectJob)
                    self.assertIs(job.kind, PaymentEffectKind.BOOKING_FORM)
                    self.assertIs(job.required, expected_required)

    def test_claim_exact_expiry_reclaims_and_stale_claim_cannot_mutate(self) -> None:
        self.settle("expiry")
        first = self.store.claim_payment_outbox(
            worker_id="worker:payment-effect:first",
            delivery_id="payment-effect-delivery:expiry",
            delivery_version=1,
            now=NOW + timedelta(seconds=3),
            lease_ttl=LEASE_TTL,
        )
        second = self.store.claim_payment_outbox(
            worker_id="worker:payment-effect:second",
            delivery_id="payment-effect-delivery:expiry",
            delivery_version=1,
            now=first.lease_expires_at,
            lease_ttl=LEASE_TTL,
        )
        self.assertEqual(second.message_id, first.message_id)
        self.assertGreater(second.fencing_token, first.fencing_token)
        self.assertGreaterEqual(second.fencing_token, second.delivery_attempts)
        receipt = PaymentReceipt.for_claim(
            first,
            receipt_id="payment:receipt:stale",
            delivery_reference="payment:delivery:stale",
            delivered_at=first.lease_expires_at,
        )
        with self.assertRaises(StaleLease):
            self.store.complete_payment_outbox(first, receipt, now=first.lease_expires_at)
        with self.assertRaises(StaleLease):
            self.store.release_payment_outbox(first, now=first.lease_expires_at)

    def test_two_connections_claim_one_manual_review_job_once(self) -> None:
        self.settle(
            "claim-cas",
            certainty=SettlementCertainty.DISPATCHED_UNKNOWN,
        )
        barrier = Barrier(2)

        def claim(index: int):
            store = SQLiteFollowupUnitOfWork.open(self.db_path)
            try:
                barrier.wait()
                return store.claim_payment_outbox(
                    worker_id=f"worker:payment-effect:cas:{index}",
                    delivery_id="payment-effect-delivery:cas",
                    delivery_version=1,
                    now=NOW + timedelta(seconds=3),
                    lease_ttl=LEASE_TTL,
                )
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = tuple(executor.map(claim, range(2)))
        winners = tuple(candidate for candidate in claims if candidate is not None)
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].fencing_token, 1)
        self.assertEqual(winners[0].delivery_attempts, 1)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM main.payment_outbox WHERE status='leased'"
            ).fetchone(),
            (1,),
        )

    def test_success_receipt_is_atomic_replay_safe_and_worker_one_shot(self) -> None:
        payment_id = self.settle("success")
        delivery = FakePaymentEffectDelivery()
        worker = self.worker(delivery)
        before_ledger = self.ledger_fingerprint(payment_id)
        first = worker.run_once(now=NOW + timedelta(seconds=3))
        self.assertIs(first.disposition, PaymentOutboxWorkerDisposition.DELIVERED)
        self.assertEqual(delivery.calls, 1)
        self.assertEqual(self.ledger_fingerprint(payment_id), before_ledger)
        claim = delivery.claims[0]
        receipt = PaymentReceipt.for_claim(
            claim,
            receipt_id=f"payment:receipt:synthetic:{claim.message_id}",
            delivery_reference=f"payment:delivery:reference:{claim.message_id}",
            delivered_at=claim.lease_acquired_at,
        )
        replay = self.store.complete_payment_outbox(
            claim,
            receipt,
            now=NOW + timedelta(seconds=11),
        )
        self.assertEqual(replay.message_id, claim.message_id)
        with self.assertRaises(IdentityConflict):
            self.store.complete_payment_outbox(
                replace(claim, delivery_id="payment-effect-delivery:replay-forged"),
                receipt,
                now=NOW + timedelta(seconds=11),
            )
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM main.payment_receipts"
            ).fetchone(),
            (1,),
        )
        second = worker.run_once(now=NOW + timedelta(seconds=4))
        self.assertIs(second.disposition, PaymentOutboxWorkerDisposition.DELIVERED)
        self.assertEqual(delivery.calls, 2)
        self.assertIs(
            worker.run_once(now=NOW + timedelta(seconds=5)).disposition,
            PaymentOutboxWorkerDisposition.IDLE,
        )

    def test_delivery_failure_requeues_without_ledger_or_paid_state_regression(self) -> None:
        payment_id = self.settle("failure")
        delivery = FakePaymentEffectDelivery("exception")
        before_ledger = self.ledger_fingerprint(payment_id)
        before_state = self.store.load_payment(payment_id)
        result = self.worker(delivery).run_once(now=NOW + timedelta(seconds=3))
        self.assertIs(result.disposition, PaymentOutboxWorkerDisposition.RETRYABLE_FAILURE)
        self.assertEqual(delivery.calls, 1)
        self.assertEqual(self.ledger_fingerprint(payment_id), before_ledger)
        after_state = self.store.load_payment(payment_id)
        self.assertEqual(after_state, before_state)
        self.assertIs(after_state.status, PaymentStatus.PAID)
        self.assertNotIn("PRIVATE", repr(result))
        self.assertEqual(
            self.store._connection.execute(
                "SELECT status, claim_owner, fencing_token, delivery_attempts "
                "FROM main.payment_outbox WHERE message_id=?",
                (result.message_id,),
            ).fetchone(),
            ("pending", None, 1, 1),
        )

    def test_invalid_delivery_return_is_retryable_and_sanitized(self) -> None:
        payment_id = self.settle("invalid-return")
        before_ledger = self.ledger_fingerprint(payment_id)
        delivery = FakePaymentEffectDelivery("invalid")
        result = self.worker(delivery).run_once(now=NOW + timedelta(seconds=3))
        self.assertIs(result.disposition, PaymentOutboxWorkerDisposition.RETRYABLE_FAILURE)
        self.assertEqual(self.ledger_fingerprint(payment_id), before_ledger)
        self.assertNotIn("object", repr(result))

    def test_optional_email_failure_leaves_paid_and_required_jobs_deliverable(self) -> None:
        payment_id = self.settle(
            "optional",
            policy=self.policy(internal=EffectRequirement.OPTIONAL),
        )
        delivery = FakePaymentEffectDelivery()
        worker = self.worker(delivery)
        for offset in range(2):
            self.assertIs(
                worker.run_once(now=NOW + timedelta(seconds=3 + offset)).disposition,
                PaymentOutboxWorkerDisposition.DELIVERED,
            )
        delivery.mode = "exception"
        failed = worker.run_once(now=NOW + timedelta(seconds=5))
        self.assertIs(failed.disposition, PaymentOutboxWorkerDisposition.RETRYABLE_FAILURE)
        self.assertIs(self.store.load_payment(payment_id).status, PaymentStatus.PAID)
        self.assertIs(delivery.claims[-1].message.kind, PaymentEffectKind.INTERNAL_PAYMENT_EMAIL)
        self.assertFalse(delivery.claims[-1].message.required)

    def test_manual_review_delivery_never_changes_settlement_or_creates_command(self) -> None:
        payment_id = self.settle(
            "manual",
            certainty=SettlementCertainty.DISPATCHED_UNKNOWN,
        )
        before_ledger = self.ledger_fingerprint(payment_id)
        before_commands = self.store._connection.execute(
            "SELECT COUNT(*) FROM main.payment_commands"
        ).fetchone()
        delivery = FakePaymentEffectDelivery()
        result = self.worker(delivery).run_once(now=NOW + timedelta(seconds=3))
        self.assertIs(result.disposition, PaymentOutboxWorkerDisposition.DELIVERED)
        self.assertIs(delivery.claims[0].message.kind, PaymentEffectKind.MANUAL_REVIEW)
        self.assertEqual(self.ledger_fingerprint(payment_id), before_ledger)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM main.payment_commands"
            ).fetchone(),
            before_commands,
        )
        self.assertIs(self.store.load_payment(payment_id).status, PaymentStatus.MANUAL_REVIEW)

    def test_missing_required_job_fails_closed_without_recreating_it(self) -> None:
        payment_id = self.settle("missing-required")
        before_commands = self.store._connection.execute(
            "SELECT COUNT(*) FROM main.payment_commands"
        ).fetchone()
        self.store._connection.execute(
            "DELETE FROM main.payment_outbox WHERE payment_id=?",
            (payment_id,),
        )
        with self.assertRaises(DataCorruption):
            self.store.load_payment(payment_id)
        with self.assertRaises(DataCorruption):
            self.store.claim_payment_outbox(
                worker_id="worker:payment-effect:missing-required",
                delivery_id="payment-effect-delivery:missing-required",
                delivery_version=1,
                now=NOW + timedelta(seconds=3),
                lease_ttl=LEASE_TTL,
            )
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM main.payment_commands"
            ).fetchone(),
            before_commands,
        )
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM main.payment_outbox WHERE payment_id=?",
                (payment_id,),
            ).fetchone(),
            (0,),
        )

    def test_old_event_cannot_regress_paid_state(self) -> None:
        payment_id = self.settle("old-event")
        before_revision = self.store._connection.execute(
            "SELECT revision FROM main.payment_workflows WHERE payment_id=?",
            (payment_id,),
        ).fetchone()[0]
        before_events = self.store._connection.execute(
            "SELECT COUNT(*) FROM main.payment_events WHERE payment_id=?",
            (payment_id,),
        ).fetchone()
        transition = self.store.apply_payment(
            payment_id,
            before_revision,
            PaymentMethodSelected(
                event_id="payment:event:task10:old-method",
                payment_id=payment_id,
                method=PaymentMethod.WISE,
                selected_at=NOW,
            ),
        )
        self.assertIs(transition.state.status, PaymentStatus.PAID)
        self.assertIs(self.store.load_payment(payment_id).status, PaymentStatus.PAID)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT revision FROM main.payment_workflows WHERE payment_id=?",
                (payment_id,),
            ).fetchone(),
            (before_revision,),
        )
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM main.payment_events WHERE payment_id=?",
                (payment_id,),
            ).fetchone(),
            before_events,
        )

    def test_invalid_receipt_and_divergent_replay_fail_without_write(self) -> None:
        self.settle("receipt")
        claim = self.store.claim_payment_outbox(
            worker_id="worker:payment-effect:receipt",
            delivery_id="payment-effect-delivery:receipt",
            delivery_version=1,
            now=NOW + timedelta(seconds=3),
            lease_ttl=LEASE_TTL,
        )
        before = tuple(self.store._connection.iterdump())
        forged = PaymentReceipt.for_claim(
            replace(claim, delivery_id="payment-effect-delivery:forged"),
            receipt_id="payment:receipt:forged",
            delivery_reference="payment:delivery:forged",
            delivered_at=NOW + timedelta(seconds=4),
        )
        with self.assertRaises(IdentityConflict):
            self.store.complete_payment_outbox(
                claim,
                forged,
                now=NOW + timedelta(seconds=4),
            )
        self.assertEqual(tuple(self.store._connection.iterdump()), before)

    def test_receipt_insert_failure_rolls_back_delivery_atomically(self) -> None:
        self.settle("receipt-rollback")
        claim = self.store.claim_payment_outbox(
            worker_id="worker:payment-effect:receipt-rollback",
            delivery_id="payment-effect-delivery:receipt-rollback",
            delivery_version=1,
            now=NOW + timedelta(seconds=3),
            lease_ttl=LEASE_TTL,
        )
        receipt = PaymentReceipt.for_claim(
            claim,
            receipt_id="payment:receipt:rollback",
            delivery_reference="payment:delivery:rollback",
            delivered_at=NOW + timedelta(seconds=3),
        )
        self.store._connection.execute(
            "CREATE TEMP TRIGGER fail_payment_receipt_insert "
            "BEFORE INSERT ON main.payment_receipts BEGIN "
            "SELECT RAISE(ABORT, 'synthetic receipt failure'); END"
        )
        before = tuple(self.store._connection.iterdump())
        with self.assertRaises(DataCorruption):
            self.store.complete_payment_outbox(
                claim,
                receipt,
                now=NOW + timedelta(seconds=3),
            )
        self.assertEqual(tuple(self.store._connection.iterdump()), before)

    def test_base_exception_propagates_and_leaves_one_live_lease(self) -> None:
        self.settle("base")
        delivery = FakePaymentEffectDelivery("base_exception")
        with self.assertRaises(DeliveryCrash):
            self.worker(delivery).run_once(now=NOW + timedelta(seconds=3))
        self.assertEqual(delivery.calls, 1)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT status, fencing_token, delivery_attempts FROM main.payment_outbox "
                "WHERE status='leased'"
            ).fetchone(),
            ("leased", 1, 1),
        )

    def test_object_mutated_claim_and_receipt_fail_closed(self) -> None:
        self.settle("mutated")
        claim = self.store.claim_payment_outbox(
            worker_id="worker:payment-effect:mutated",
            delivery_id="payment-effect-delivery:mutated",
            delivery_version=1,
            now=NOW + timedelta(seconds=3),
            lease_ttl=LEASE_TTL,
        )
        receipt = PaymentReceipt.for_claim(
            claim,
            receipt_id="payment:receipt:mutated",
            delivery_reference="payment:delivery:mutated",
            delivered_at=NOW + timedelta(seconds=4),
        )
        object.__setattr__(claim, "fencing_token", 99)
        object.__setattr__(receipt, "message_id", "payment-effect:forged")
        with self.assertRaises((ValueError, StaleLease, IdentityConflict)):
            self.store.complete_payment_outbox(
                claim,
                receipt,
                now=NOW + timedelta(seconds=4),
            )


if __name__ == "__main__":
    unittest.main()
