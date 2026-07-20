from __future__ import annotations

from datetime import timedelta
import hashlib
from pathlib import Path
import tempfile
import unittest

from reservation_followup.payment import PaymentSettlementCommand, SettlementOutcome
from reservation_followup.sqlite_store import DataCorruption, SQLiteFollowupUnitOfWork, StaleLease
from reservation_followup.types import PaymentMethod, PaymentStatus, SettlementCertainty
from reservation_followup.workers import (
    PaymentSettlementWorker,
    RetryableSettlementPreparationError,
    SettlementWorkerDisposition,
    TerminalSettlementPreparationError,
)
from tests.test_phase6_payment_claims import (
    LEASE_TTL,
    NOW,
    alternate_anchor,
    outcome,
    payment_fingerprint,
    pix_visual_evidence,
    prepare_payment,
)


class DispatchCrash(BaseException):
    pass


class FakeSettlementPort:
    settlement_id = "settlement-port:synthetic"
    settlement_version = 1

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.prepare_calls = 0
        self.dispatch_calls = 0
        self.prepared_commands: list[PaymentSettlementCommand] = []
        self.permits = []

    def prepare(self, request: PaymentSettlementCommand) -> str:
        self.prepare_calls += 1
        self.prepared_commands.append(request)
        if self.mode == "prepare_retryable":
            raise RetryableSettlementPreparationError("synthetic retryable")
        if self.mode == "prepare_terminal":
            raise TerminalSettlementPreparationError("synthetic terminal")
        if self.mode == "prepare_exception":
            raise RuntimeError("SYNTHETIC_PRIVATE_PREPARE_DETAIL")
        if self.mode == "prepare_invalid":
            return "synthetic:divergent-request"
        return request.canonical_payload

    def dispatch(self, permit) -> SettlementOutcome:
        self.dispatch_calls += 1
        self.permits.append(permit)
        if self.mode == "dispatch_exception":
            raise RuntimeError("SYNTHETIC_PRIVATE_DISPATCH_DETAIL")
        if self.mode == "dispatch_base_exception":
            raise DispatchCrash("synthetic crash")
        if self.mode == "invalid_return":
            return object()
        if self.mode == "malformed_outcome":
            malformed = outcome(
                SettlementCertainty.SETTLED,
                claim_evidence=(permit.request_hash,),
            )
            object.__delattr__(malformed, "payment_registered")
            return malformed
        if self.mode == "not_dispatched":
            return outcome(SettlementCertainty.NOT_DISPATCHED)
        if self.mode == "partial":
            return outcome(
                SettlementCertainty.PARTIAL_SETTLEMENT,
                claim_evidence=(permit.request_hash,),
            )
        if self.mode == "dispatched_no_effect":
            return outcome(
                SettlementCertainty.DISPATCHED_NO_EFFECT,
                claim_evidence=(permit.request_hash,),
            )
        return outcome(
            SettlementCertainty.SETTLED,
            claim_evidence=(permit.request_hash,),
        )


class Phase6PaymentSettlementWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase6-task9-worker-")
        self.addCleanup(self.temporary.cleanup)
        self.store = SQLiteFollowupUnitOfWork.open(Path(self.temporary.name) / "worker.db")
        self.addCleanup(self.store.close)
        self.payment_id = self._queue("one")

    def _queue(self, suffix: str) -> str:
        e2e_tail = hashlib.sha256(suffix.encode("utf-8")).hexdigest()[:11].upper()
        state, event = prepare_payment(
            self.store,
            suffix=f"worker-{suffix}",
            method=PaymentMethod.PIX,
            evidence=pix_visual_evidence(
                normalized_e2e=f"E1234567820270201{e2e_tail}",
            ),
            anchor=alternate_anchor(f"worker-{suffix}"),
        )
        self.store.claim_payment_evidence(state.subject.payment_id, 3, event)
        return state.subject.payment_id

    def _worker(self, port: FakeSettlementPort, *, worker_id: str = "worker:settlement:task9"):
        return PaymentSettlementWorker(
            store=self.store,
            settlement=port,
            worker_id=worker_id,
            lease_ttl=LEASE_TTL,
        )

    def _ledger(self):
        return self.store._connection.execute(
            "SELECT status, dispatch_slots_consumed, outcome_certainty, claim_owner "
            "FROM main.payment_ledger WHERE payment_id=?",
            (self.payment_id,),
        ).fetchone()

    def test_required_effect_projection_is_closed_and_wire_canonical(self) -> None:
        from reservation_followup.projection import (
            PaymentEffectJob,
            PaymentEffectKind,
            required_payment_effects,
        )
        from reservation_followup.serialization import from_wire_json, to_wire_json
        from reservation_followup.types import EffectRequirement, PaymentEffectPolicy

        policy = PaymentEffectPolicy(
            paid_state_transition=EffectRequirement.REQUIRED,
            customer_payment_confirmation=EffectRequirement.REQUIRED,
            internal_payment_email=EffectRequirement.OPTIONAL,
            booking_form=EffectRequirement.REQUIRED,
        )
        settled = outcome(SettlementCertainty.SETTLED, claim_evidence=("a" * 64,))
        jobs = required_payment_effects(settled, policy)
        self.assertEqual(
            tuple((job.kind, job.required) for job in jobs),
            (
                (PaymentEffectKind.PAID_STATE_TRANSITION, True),
                (PaymentEffectKind.CUSTOMER_PAYMENT_CONFIRMATION, True),
                (PaymentEffectKind.INTERNAL_PAYMENT_EMAIL, False),
                (PaymentEffectKind.BOOKING_FORM, True),
            ),
        )
        for job in jobs:
            self.assertEqual(from_wire_json(to_wire_json(job), PaymentEffectJob), job)
        unknown = outcome(
            SettlementCertainty.DISPATCHED_UNKNOWN,
            claim_evidence=("b" * 64,),
        )
        self.assertEqual(
            tuple(
                (job.kind, job.required)
                for job in required_payment_effects(unknown, policy)
            ),
            ((PaymentEffectKind.MANUAL_REVIEW, True),),
        )

    def test_prepare_retryable_failure_requeues_before_fence_and_never_dispatches(self) -> None:
        port = FakeSettlementPort("prepare_retryable")
        result = self._worker(port).run_once(now=NOW)
        self.assertIs(result.disposition, SettlementWorkerDisposition.PREPARATION_REQUEUED)
        self.assertEqual((port.prepare_calls, port.dispatch_calls), (1, 0))
        self.assertEqual(self._ledger(), ("queued", 0, None, None))
        self.assertIs(self.store.load_payment(self.payment_id).status, PaymentStatus.SETTLEMENT_QUEUED)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT status FROM main.payment_evidence_claims"
            ).fetchone(),
            ("retryable",),
        )

    def test_unknown_prepare_exception_is_sanitized_and_requeued_before_fence(self) -> None:
        port = FakeSettlementPort("prepare_exception")
        result = self._worker(port).run_once(now=NOW)
        self.assertIs(result.disposition, SettlementWorkerDisposition.PREPARATION_REQUEUED)
        self.assertEqual(port.dispatch_calls, 0)
        self.assertNotIn("PRIVATE", repr(result))
        self.assertNotIn("PRIVATE", repr(self._ledger()))

    def test_release_failure_drops_raw_prepare_exception_from_entire_chain(self) -> None:
        port = FakeSettlementPort("prepare_exception")

        def fail_release(*args, **kwargs):
            raise StaleLease("safe pre-fence release failure")

        self.store.release_pre_dispatch_settlement = fail_release
        with self.assertRaises(StaleLease) as caught:
            self._worker(port).run_once(now=NOW)
        chain = []
        current = caught.exception
        while current is not None and current not in chain:
            chain.append(current)
            current = current.__cause__ or current.__context__
        rendered = " ".join(
            f"{type(item).__name__}:{item}" for item in chain
        )
        self.assertNotIn("PRIVATE", rendered)
        self.assertNotIn("RuntimeError", rendered)
        self.assertEqual(port.dispatch_calls, 0)

    def test_outcome_write_failure_drops_raw_dispatch_exception_from_entire_chain(self) -> None:
        port = FakeSettlementPort("dispatch_exception")

        def fail_outcome(*args, **kwargs):
            raise StaleLease("safe post-fence outcome failure")

        self.store.record_settlement_outcome = fail_outcome
        with self.assertRaises(StaleLease) as caught:
            self._worker(port).run_once(now=NOW)
        chain = []
        current = caught.exception
        while current is not None and current not in chain:
            chain.append(current)
            current = current.__cause__ or current.__context__
        rendered = " ".join(
            f"{type(item).__name__}:{item}" for item in chain
        )
        self.assertNotIn("PRIVATE", rendered)
        self.assertNotIn("RuntimeError", rendered)
        self.assertEqual(port.dispatch_calls, 1)
        self.assertEqual(self._ledger()[0:3], ("dispatch_fenced", 1, None))

    def test_prepare_terminal_or_invalid_request_finishes_not_dispatched(self) -> None:
        for mode in ("prepare_terminal", "prepare_invalid"):
            with self.subTest(mode=mode):
                path = Path(self.temporary.name) / f"{mode}.db"
                store = SQLiteFollowupUnitOfWork.open(path)
                try:
                    state, event = prepare_payment(
                        store,
                        suffix=mode,
                        method=PaymentMethod.PIX,
                        evidence=pix_visual_evidence(),
                    )
                    store.claim_payment_evidence(state.subject.payment_id, 3, event)
                    port = FakeSettlementPort(mode)
                    result = PaymentSettlementWorker(
                        store=store,
                        settlement=port,
                        worker_id=f"worker:settlement:{mode}",
                        lease_ttl=LEASE_TTL,
                    ).run_once(now=NOW)
                    self.assertIs(
                        result.disposition,
                        SettlementWorkerDisposition.PREPARATION_TERMINAL,
                    )
                    self.assertEqual(port.dispatch_calls, 0)
                    self.assertIs(
                        store.load_payment(state.subject.payment_id).status,
                        PaymentStatus.RETRYABLE,
                    )
                    self.assertEqual(
                        store._connection.execute(
                            "SELECT status, dispatch_slots_consumed, outcome_certainty "
                            "FROM main.payment_ledger"
                        ).fetchone(),
                        ("outcome_recorded", 0, "not_dispatched"),
                    )
                    self.assertEqual(
                        tuple(
                            store._connection.execute(
                                "SELECT kind, status FROM main.payment_outbox"
                            )
                        ),
                        (("manual_review", "pending"),),
                    )
                    self.assertIs(
                        PaymentSettlementWorker(
                            store=store,
                            settlement=port,
                            worker_id=f"worker:settlement:{mode}:second",
                            lease_ttl=LEASE_TTL,
                        ).run_once(now=NOW + timedelta(seconds=1)).disposition,
                        SettlementWorkerDisposition.IDLE,
                    )
                finally:
                    store.close()

    def test_dispatch_exception_is_promoted_to_unknown_and_never_retried(self) -> None:
        port = FakeSettlementPort("dispatch_exception")
        result = self._worker(port).run_once(now=NOW)
        self.assertIs(result.disposition, SettlementWorkerDisposition.MANUAL_REVIEW)
        self.assertEqual((port.prepare_calls, port.dispatch_calls), (1, 1))
        self.assertEqual(self._ledger()[0:3], ("manual_review", 1, "dispatched_unknown"))
        self.assertIs(self.store.load_payment(self.payment_id).status, PaymentStatus.MANUAL_REVIEW)
        before = payment_fingerprint(self.store._connection)
        second = self._worker(port, worker_id="worker:settlement:task9:second").run_once(
            now=NOW + timedelta(seconds=1)
        )
        self.assertIs(second.disposition, SettlementWorkerDisposition.IDLE)
        self.assertEqual(port.dispatch_calls, 1)
        self.assertEqual(payment_fingerprint(self.store._connection), before)

    def test_malformed_exact_outcome_is_promoted_to_unknown_with_required_job(self) -> None:
        port = FakeSettlementPort("malformed_outcome")
        result = self._worker(port).run_once(now=NOW)
        self.assertIs(result.disposition, SettlementWorkerDisposition.MANUAL_REVIEW)
        self.assertEqual(port.dispatch_calls, 1)
        self.assertEqual(self._ledger()[0:3], ("manual_review", 1, "dispatched_unknown"))
        self.assertEqual(
            tuple(
                self.store._connection.execute(
                    "SELECT kind, status FROM main.payment_outbox"
                )
            ),
            (("manual_review", "pending"),),
        )

    def test_outbox_insert_failure_rolls_back_outcome_state_event_and_evidence(self) -> None:
        claim = self.store.claim_settlement(
            worker_id="worker:settlement:outbox-atomicity",
            now=NOW,
            lease_ttl=LEASE_TTL,
        )
        permit = self.store.fence_settlement(
            claim,
            claim.command.canonical_payload,
            now=NOW + timedelta(seconds=1),
        )
        self.store._connection.execute(
            "CREATE TRIGGER fail_payment_outbox BEFORE INSERT ON main.payment_outbox "
            "BEGIN SELECT RAISE(ABORT, 'synthetic payment outbox fault'); END"
        )
        before = payment_fingerprint(self.store._connection)
        with self.assertRaises(DataCorruption):
            self.store.record_settlement_outcome(
                claim,
                permit,
                outcome(
                    SettlementCertainty.SETTLED,
                    claim_evidence=(permit.request_hash,),
                ),
                now=NOW + timedelta(seconds=2),
            )
        self.assertEqual(payment_fingerprint(self.store._connection), before)
        self.assertEqual(self._ledger()[0:3], ("dispatch_fenced", 1, None))
        self.assertIs(self.store.load_payment(self.payment_id).status, PaymentStatus.SETTLING)

    def test_missing_required_outbox_job_fails_closed_on_load(self) -> None:
        self._worker(FakeSettlementPort("dispatch_exception")).run_once(now=NOW)
        self.store._connection.execute(
            "DELETE FROM main.payment_outbox WHERE payment_id=?",
            (self.payment_id,),
        )
        with self.assertRaises(DataCorruption):
            self.store.load_payment(self.payment_id)

    def test_partial_no_effect_invalid_return_and_not_dispatched_are_manual_review(self) -> None:
        expected = {
            "partial": SettlementCertainty.PARTIAL_SETTLEMENT.value,
            "dispatched_no_effect": SettlementCertainty.DISPATCHED_NO_EFFECT.value,
            "invalid_return": SettlementCertainty.DISPATCHED_UNKNOWN.value,
            "not_dispatched": SettlementCertainty.DISPATCHED_UNKNOWN.value,
        }
        for mode, certainty in expected.items():
            with self.subTest(mode=mode):
                path = Path(self.temporary.name) / f"dispatch-{mode}.db"
                store = SQLiteFollowupUnitOfWork.open(path)
                try:
                    state, event = prepare_payment(
                        store,
                        suffix=f"dispatch-{mode}",
                        method=PaymentMethod.PIX,
                        evidence=pix_visual_evidence(),
                    )
                    store.claim_payment_evidence(state.subject.payment_id, 3, event)
                    port = FakeSettlementPort(mode)
                    result = PaymentSettlementWorker(
                        store=store,
                        settlement=port,
                        worker_id=f"worker:settlement:dispatch:{mode}",
                        lease_ttl=LEASE_TTL,
                    ).run_once(now=NOW)
                    self.assertIs(result.disposition, SettlementWorkerDisposition.MANUAL_REVIEW)
                    self.assertEqual(port.dispatch_calls, 1)
                    self.assertEqual(
                        store._connection.execute(
                            "SELECT status, dispatch_slots_consumed, outcome_certainty "
                            "FROM main.payment_ledger"
                        ).fetchone(),
                        ("manual_review", 1, certainty),
                    )
                finally:
                    store.close()

    def test_settled_dispatch_persists_paid_and_worker_is_one_shot(self) -> None:
        second_payment_id = self._queue("two")
        port = FakeSettlementPort("settled")
        worker = self._worker(port)
        first = worker.run_once(now=NOW)
        self.assertIs(first.disposition, SettlementWorkerDisposition.SETTLED)
        self.assertEqual(port.dispatch_calls, 1)
        statuses = tuple(
            row[0]
            for row in self.store._connection.execute(
                "SELECT status FROM main.payment_workflows ORDER BY payment_id"
            )
        )
        self.assertEqual(statuses.count(PaymentStatus.PAID.value), 1)
        self.assertEqual(statuses.count(PaymentStatus.SETTLEMENT_QUEUED.value), 1)
        self.assertEqual(
            tuple(
                row[0]
                for row in self.store._connection.execute(
                    "SELECT kind FROM main.payment_outbox ORDER BY kind"
                )
            ),
            ("customer_payment_confirmation", "paid_state_transition"),
        )
        second = worker.run_once(now=NOW + timedelta(seconds=1))
        self.assertIs(second.disposition, SettlementWorkerDisposition.SETTLED)
        self.assertEqual(port.dispatch_calls, 2)
        self.assertIs(self.store.load_payment(second_payment_id).status, PaymentStatus.PAID)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM main.payment_outbox"
            ).fetchone(),
            (4,),
        )

    def test_base_exception_after_fence_propagates_and_leaves_one_fenced_slot(self) -> None:
        port = FakeSettlementPort("dispatch_base_exception")
        with self.assertRaises(DispatchCrash):
            self._worker(port).run_once(now=NOW)
        self.assertEqual(port.dispatch_calls, 1)
        self.assertEqual(self._ledger()[0:3], ("dispatch_fenced", 1, None))


if __name__ == "__main__":
    unittest.main()
