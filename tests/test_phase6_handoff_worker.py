from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import sqlite3
import tempfile
import traceback
import unittest
from pathlib import Path
from threading import Barrier

from reservation_followup import (
    EffectRequirement,
    HandoffEffectKind,
    HandoffEffectPolicy,
    HandoffStatus,
    from_wire_json,
    to_wire_json,
)
from reservation_followup.sqlite_store import (
    DataCorruption,
    IdentityConflict,
    SQLiteFollowupUnitOfWork,
    StaleLease,
    StoreError,
)
from reservation_followup.types import HandoffOutboxClaim, HandoffReceipt
from reservation_followup.workers import (
    HandoffOutboxWorker,
    HandoffWorkerDisposition,
)
from tests.phase6_helpers import T0, handoff_requested, optional_email_policy

NOW = T0 + timedelta(minutes=1)
TTL = timedelta(seconds=30)


class ScriptedDelivery:
    delivery_id = "delivery:scripted:handoff"
    delivery_version = 1

    def __init__(self, actions=()):
        self.actions = list(actions)
        self.messages = []

    def deliver(self, message):
        self.messages.append(message)
        if not self.actions:
            raise AssertionError("unexpected synthetic delivery")
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        delivered_at = action
        return HandoffReceipt.for_message(
            message,
            receipt_id=f"receipt:{len(self.messages)}:synthetic",
            delivery_reference=f"delivery-reference:{len(self.messages)}",
            delivery_id=self.delivery_id,
            delivery_version=self.delivery_version,
            delivered_at=delivered_at,
        )


class Phase6HandoffWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase6-task7-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "followup.db"
        self.store = SQLiteFollowupUnitOfWork.open(self.path)
        self.addCleanup(self.store.close)

    def open_handoff(self, *, optional_email: bool = True):
        request = handoff_requested()
        policy = (
            optional_email_policy()
            if optional_email
            else HandoffEffectPolicy.default_email_disabled()
        )
        transition = self.store.open_handoff(request, policy)
        return request, transition

    def claim(self, *, worker_id="worker:handoff:one", now=NOW, ttl=TTL):
        return self.store.claim_handoff_outbox(
            worker_id=worker_id,
            delivery_id=ScriptedDelivery.delivery_id,
            delivery_version=ScriptedDelivery.delivery_version,
            now=now,
            lease_ttl=ttl,
        )

    @staticmethod
    def payment_fingerprint_for(connection):
        return tuple(
            tuple(
                connection.execute(
                    f"SELECT * FROM main.{table} ORDER BY 1"
                )
            )
            for table in (
                "payment_workflows",
                "payment_events",
                "payment_evidence_claims",
                "payment_commands",
                "payment_ledger",
                "payment_outbox",
                "payment_receipts",
            )
        )

    def domain_fingerprint(self):
        return self.payment_fingerprint_for(self.store._connection)

    def handoff_fingerprint(self):
        return tuple(
            tuple(
                self.store._connection.execute(
                    f"SELECT * FROM main.{table} ORDER BY 1"
                )
            )
            for table in (
                "handoff_workflows",
                "handoff_events",
                "handoff_outbox",
                "handoff_receipts",
            )
        )

    def persistence_fingerprint(self):
        return self.handoff_fingerprint(), self.domain_fingerprint()

    def outbox_row(self, message_id: str):
        return self.store._connection.execute(
            "SELECT status, claim_owner, fencing_token, lease_acquired_at, "
            "lease_expires_at, delivery_attempts, delivered_at, receipt_hash, updated_at "
            "FROM main.handoff_outbox WHERE message_id=?",
            (message_id,),
        ).fetchone()

    def rewrite_receipt_record(
        self,
        message_id: str,
        mutate,
        *,
        receipt_id: str | None = None,
    ) -> None:
        raw = self.store._connection.execute(
            "SELECT receipt_json FROM main.handoff_receipts WHERE message_id=?",
            (message_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        mutate(payload)
        hostile = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        hostile_hash = hashlib.sha256(hostile.encode("utf-8")).hexdigest()
        self.store._connection.execute("PRAGMA defer_foreign_keys=ON")
        self.store._connection.execute("BEGIN IMMEDIATE")
        try:
            if receipt_id is None:
                self.store._connection.execute(
                    "UPDATE main.handoff_receipts SET receipt_json=?, receipt_hash=? "
                    "WHERE message_id=?",
                    (hostile, hostile_hash, message_id),
                )
            else:
                self.store._connection.execute(
                    "UPDATE main.handoff_receipts SET receipt_id=?, receipt_json=?, "
                    "receipt_hash=? WHERE message_id=?",
                    (receipt_id, hostile, hostile_hash, message_id),
                )
            self.store._connection.execute(
                "UPDATE main.handoff_outbox SET receipt_hash=? WHERE message_id=?",
                (hostile_hash, message_id),
            )
            self.store._connection.commit()
        except BaseException:
            self.store._connection.rollback()
            raise

    def test_disabled_internal_email_creates_no_row_and_claims_ack_first(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        rows = tuple(
            self.store._connection.execute(
                "SELECT kind FROM main.handoff_outbox WHERE handoff_id=?",
                (request.handoff_id,),
            )
        )
        self.assertEqual(rows, ((HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT.value,),))
        claim = self.claim()
        self.assertEqual(claim.message.kind, HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT)

    def test_claim_reclaims_at_exact_expiry_and_stale_claim_cannot_mutate(self) -> None:
        self.open_handoff()
        first = self.claim(worker_id="worker:handoff:first")
        live_other = self.claim(
            worker_id="worker:handoff:other",
            now=NOW + timedelta(seconds=29),
        )
        self.assertIsNotNone(live_other)
        self.assertNotEqual(live_other.message.effect_id, first.message.effect_id)
        second = self.claim(
            worker_id="worker:handoff:second",
            now=NOW + timedelta(seconds=30),
        )
        self.assertEqual(second.message, first.message)
        self.assertEqual(second.fencing_token, first.fencing_token + 1)
        self.assertEqual(second.delivery_attempts, first.delivery_attempts + 1)
        with self.assertRaises(StaleLease):
            self.store.release_handoff_outbox(first, now=NOW + timedelta(seconds=31))
        receipt = HandoffReceipt.for_message(
            first.message,
            receipt_id="receipt:stale:synthetic",
            delivery_reference="delivery-reference:stale",
            delivery_id=first.delivery_id,
            delivery_version=first.delivery_version,
            delivered_at=NOW + timedelta(seconds=31),
        )
        with self.assertRaises(StaleLease):
            self.store.complete_handoff_outbox(
                first, receipt, now=NOW + timedelta(seconds=31)
            )

    def test_live_claim_rejects_owner_delivery_token_and_attempt_substitutions_orthogonally(self) -> None:
        self.open_handoff(optional_email=False)
        claim = self.claim(worker_id="worker:handoff:orthogonal")
        mutations = (
            replace(claim, worker_id="worker:handoff:other"),
            replace(claim, delivery_id="delivery:scripted:other"),
            replace(claim, delivery_version=claim.delivery_version + 1),
            replace(claim, fencing_token=claim.fencing_token + 1),
        )
        before = self.persistence_fingerprint()
        for forged in mutations:
            with self.subTest(forged=forged), self.assertRaises(StaleLease):
                self.store.release_handoff_outbox(forged, now=NOW)
            self.assertEqual(self.persistence_fingerprint(), before)

    def test_claim_contract_rejects_bool_counters_and_nonpositive_ttl(self) -> None:
        self.open_handoff()
        with self.assertRaises(ValueError):
            self.store.claim_handoff_outbox(
                worker_id="worker:handoff:one",
                delivery_id=ScriptedDelivery.delivery_id,
                delivery_version=True,
                now=NOW,
                lease_ttl=TTL,
            )
        with self.assertRaises(ValueError):
            self.store.claim_handoff_outbox(
                worker_id="worker:handoff:one",
                delivery_id=ScriptedDelivery.delivery_id,
                delivery_version=1,
                now=NOW,
                lease_ttl=timedelta(0),
            )

    def test_success_receipt_acknowledges_atomically_and_is_idempotent(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        before_payment = self.domain_fingerprint()
        claim = self.claim()
        receipt = HandoffReceipt.for_message(
            claim.message,
            receipt_id="receipt:ack:synthetic",
            delivery_reference="delivery-reference:ack",
            delivery_id=claim.delivery_id,
            delivery_version=claim.delivery_version,
            delivered_at=NOW + timedelta(seconds=1),
        )
        first = self.store.complete_handoff_outbox(
            claim, receipt, now=NOW + timedelta(seconds=1)
        )
        before_replay = self.persistence_fingerprint()
        replay = self.store.complete_handoff_outbox(
            claim, receipt, now=NOW + timedelta(days=1)
        )
        self.assertEqual(self.persistence_fingerprint(), before_replay)
        state = self.store.load_handoff(request.handoff_id)
        self.assertEqual(state.status, HandoffStatus.ACKNOWLEDGED)
        self.assertIsNotNone(state.acknowledgement)
        self.assertEqual(first.state, replay.state)
        self.assertEqual(self.domain_fingerprint(), before_payment)
        self.store.close()
        reopened = SQLiteFollowupUnitOfWork.open(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.load_handoff(request.handoff_id), state)
        self.assertEqual(reopened._connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
        self.assertEqual(reopened._connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_divergent_receipt_and_forged_claim_conflict_without_write(self) -> None:
        self.open_handoff(optional_email=False)
        claim = self.claim()
        receipt = HandoffReceipt.for_message(
            claim.message,
            receipt_id="receipt:ack:synthetic",
            delivery_reference="delivery-reference:ack",
            delivery_id=claim.delivery_id,
            delivery_version=claim.delivery_version,
            delivered_at=NOW + timedelta(seconds=1),
        )
        self.store.complete_handoff_outbox(claim, receipt, now=receipt.delivered_at)
        before = self.persistence_fingerprint()
        with self.assertRaises(IdentityConflict):
            self.store.complete_handoff_outbox(
                claim,
                replace(receipt, delivery_reference="delivery-reference:other"),
                now=NOW + timedelta(seconds=2),
            )
        forged_worker = replace(claim, worker_id="worker:handoff:forged")
        with self.assertRaises(IdentityConflict):
            self.store.complete_handoff_outbox(
                forged_worker,
                receipt,
                now=NOW + timedelta(seconds=2),
            )
        forged = replace(claim, message=replace(claim.message, created_at=T0 + timedelta(seconds=1)))
        with self.assertRaises(IdentityConflict):
            self.store.complete_handoff_outbox(
                forged, receipt, now=NOW + timedelta(seconds=2)
            )
        self.assertEqual(self.persistence_fingerprint(), before)

    def test_receipt_chronology_and_delivery_identity_fail_closed(self) -> None:
        self.open_handoff(optional_email=False)
        claim = self.claim()
        base = dict(
            message=claim.message,
            receipt_id="receipt:chronology:synthetic",
            delivery_reference="delivery-reference:chronology",
            delivery_id=claim.delivery_id,
            delivery_version=claim.delivery_version,
        )
        before = self.persistence_fingerprint()
        too_early = HandoffReceipt.for_message(
            delivered_at=T0 - timedelta(seconds=1),
            **base,
        )
        future = HandoffReceipt.for_message(
            delivered_at=NOW + timedelta(seconds=2),
            **base,
        )
        wrong_delivery = HandoffReceipt.for_message(
            delivered_at=NOW + timedelta(seconds=1),
            **{**base, "delivery_id": "delivery:forged:handoff"},
        )
        for receipt in (too_early, future):
            with self.subTest(receipt=receipt), self.assertRaises(ValueError):
                self.store.complete_handoff_outbox(claim, receipt, now=NOW)
            self.assertEqual(self.persistence_fingerprint(), before)
        with self.assertRaises(IdentityConflict):
            self.store.complete_handoff_outbox(
                claim,
                wrong_delivery,
                now=NOW + timedelta(seconds=1),
            )
        self.assertEqual(self.persistence_fingerprint(), before)

    def test_delivery_failure_releases_message_records_failure_and_isolates_domains(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        before_payment = self.domain_fingerprint()
        delivery = ScriptedDelivery((RuntimeError("raw-provider-secret-sentinel"),))
        worker = HandoffOutboxWorker(
            store=self.store,
            delivery=delivery,
            worker_id="worker:handoff:scripted",
            lease_ttl=TTL,
        )
        result = worker.run_once(now=NOW)
        self.assertEqual(result.disposition, HandoffWorkerDisposition.RETRYABLE_FAILURE)
        row = self.outbox_row(result.message_id)
        self.assertEqual(row[0], "pending")
        self.assertIsNone(row[1])
        state = self.store.load_handoff(request.handoff_id)
        self.assertEqual(state.status, HandoffStatus.MANUAL_REVIEW)
        self.assertEqual(len(state.effect_failures), 1)
        self.assertEqual(self.domain_fingerprint(), before_payment)

    def test_optional_email_failure_does_not_regress_ack_or_queue(self) -> None:
        request, _ = self.open_handoff(optional_email=True)
        before_payment = self.domain_fingerprint()
        ack_claim = self.claim()
        ack_receipt = HandoffReceipt.for_message(
            ack_claim.message,
            receipt_id="receipt:ack:before-email",
            delivery_reference="delivery-reference:ack-before-email",
            delivery_id=ack_claim.delivery_id,
            delivery_version=ack_claim.delivery_version,
            delivered_at=NOW + timedelta(seconds=1),
        )
        self.store.complete_handoff_outbox(
            ack_claim, ack_receipt, now=ack_receipt.delivered_at
        )
        email_claim = self.claim(now=NOW + timedelta(seconds=2))
        self.assertEqual(email_claim.message.kind, HandoffEffectKind.INTERNAL_EMAIL)
        self.store.release_handoff_outbox(email_claim, now=NOW + timedelta(seconds=3))
        state = self.store.load_handoff(request.handoff_id)
        self.assertEqual(state.status, HandoffStatus.ACKNOWLEDGED)
        self.assertTrue(state.queue_active)
        self.assertEqual(len(state.effect_failures), 1)
        self.assertEqual(self.domain_fingerprint(), before_payment)

    def test_optional_email_failure_is_reclaimable_and_later_success_preserves_state(self) -> None:
        request, _ = self.open_handoff(optional_email=True)
        before_payment = self.domain_fingerprint()
        ack_claim = self.claim()
        ack_receipt = HandoffReceipt.for_message(
            ack_claim.message,
            receipt_id="receipt:ack:optional-retry",
            delivery_reference="delivery-reference:ack-optional-retry",
            delivery_id=ack_claim.delivery_id,
            delivery_version=ack_claim.delivery_version,
            delivered_at=NOW,
        )
        self.store.complete_handoff_outbox(ack_claim, ack_receipt, now=NOW)
        email_claim = self.claim(now=NOW + timedelta(seconds=1))
        failed = self.store.release_handoff_outbox(
            email_claim,
            now=NOW + timedelta(seconds=2),
        )
        retry = self.claim(now=NOW + timedelta(seconds=3))
        self.assertEqual(retry.message, email_claim.message)
        self.assertEqual(retry.fencing_token, email_claim.fencing_token + 1)
        self.assertEqual(retry.delivery_attempts, email_claim.delivery_attempts + 1)
        email_receipt = HandoffReceipt.for_message(
            retry.message,
            receipt_id="receipt:email:optional-retry-success",
            delivery_reference="delivery-reference:email-optional-retry-success",
            delivery_id=retry.delivery_id,
            delivery_version=retry.delivery_version,
            delivered_at=NOW + timedelta(seconds=3),
        )
        completed = self.store.complete_handoff_outbox(
            retry,
            email_receipt,
            now=NOW + timedelta(seconds=3),
        )
        state = self.store.load_handoff(request.handoff_id)
        self.assertEqual(failed.state, completed.state)
        self.assertEqual(state.status, HandoffStatus.ACKNOWLEDGED)
        self.assertTrue(state.queue_active)
        self.assertEqual(len(state.effect_failures), 1)
        self.assertEqual(self.domain_fingerprint(), before_payment)

    def test_worker_delivers_at_most_one_when_two_messages_are_pending(self) -> None:
        request, _ = self.open_handoff(optional_email=True)
        delivery = ScriptedDelivery((NOW, NOW))
        worker = HandoffOutboxWorker(
            store=self.store,
            delivery=delivery,
            worker_id="worker:handoff:one-shot",
            lease_ttl=TTL,
        )
        before_payment = self.domain_fingerprint()
        email_before = self.store._connection.execute(
            "SELECT * FROM main.handoff_outbox WHERE handoff_id=? AND kind=?",
            (request.handoff_id, HandoffEffectKind.INTERNAL_EMAIL.value),
        ).fetchone()
        result = worker.run_once(now=NOW)
        self.assertEqual(result.disposition, HandoffWorkerDisposition.DELIVERED)
        self.assertEqual(len(delivery.messages), 1)
        self.assertEqual(
            delivery.messages[0].kind,
            HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT,
        )
        self.assertEqual(
            tuple(
                self.store._connection.execute(
                    "SELECT kind, status FROM main.handoff_outbox "
                    "WHERE handoff_id=? ORDER BY kind",
                    (request.handoff_id,),
                )
            ),
            (
                (HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT.value, "delivered"),
                (HandoffEffectKind.INTERNAL_EMAIL.value, "pending"),
            ),
        )
        self.assertEqual(
            self.store._connection.execute(
                "SELECT * FROM main.handoff_outbox WHERE handoff_id=? AND kind=?",
                (request.handoff_id, HandoffEffectKind.INTERNAL_EMAIL.value),
            ).fetchone(),
            email_before,
        )
        state = self.store.load_handoff(request.handoff_id)
        self.assertEqual(state.effect_failures, ())
        self.assertEqual(
            tuple(
                row[0]
                for row in self.store._connection.execute(
                    "SELECT event_type FROM main.handoff_events WHERE handoff_id=? "
                    "ORDER BY revision",
                    (request.handoff_id,),
                )
            ),
            ("HandoffRequested", "HandoffAcknowledged"),
        )
        self.assertEqual(self.domain_fingerprint(), before_payment)

    def test_worker_one_shot_then_idle_and_does_not_swallow_base_exception(self) -> None:
        self.open_handoff(optional_email=False)
        delivery = ScriptedDelivery((NOW,))
        worker = HandoffOutboxWorker(
            store=self.store,
            delivery=delivery,
            worker_id="worker:handoff:scripted",
            lease_ttl=TTL,
        )
        delivered = worker.run_once(now=NOW)
        idle = worker.run_once(now=NOW + timedelta(seconds=2))
        self.assertEqual(delivered.disposition, HandoffWorkerDisposition.DELIVERED)
        self.assertEqual(idle.disposition, HandoffWorkerDisposition.IDLE)
        self.assertEqual(len(delivery.messages), 1)

        request = handoff_requested(
            handoff_id="handoff:base-exception:1",
            incident_key="incident:base-exception:1",
            source_event_id="source:base-exception:1",
        )
        self.store.open_handoff(request, HandoffEffectPolicy.default_email_disabled())
        crashing = ScriptedDelivery((KeyboardInterrupt(),))
        crash_worker = HandoffOutboxWorker(
            store=self.store,
            delivery=crashing,
            worker_id="worker:handoff:crashing",
            lease_ttl=TTL,
        )
        with self.assertRaises(KeyboardInterrupt):
            crash_worker.run_once(now=NOW + timedelta(seconds=3))
        row = self.store._connection.execute(
            "SELECT status, claim_owner FROM main.handoff_outbox WHERE handoff_id=?",
            (request.handoff_id,),
        ).fetchone()
        self.assertEqual(row[0], "leased")
        self.assertIsNotNone(row[1])

    def test_release_failure_does_not_chain_raw_delivery_exception(self) -> None:
        self.open_handoff(optional_email=False)
        delivery = ScriptedDelivery((RuntimeError("raw-provider-secret-sentinel"),))
        worker = HandoffOutboxWorker(
            store=self.store,
            delivery=delivery,
            worker_id="worker:handoff:scripted",
            lease_ttl=TTL,
        )
        self.store.release_handoff_outbox = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StaleLease("synthetic stale")
        )
        with self.assertRaises(StaleLease) as caught:
            worker.run_once(now=NOW)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(
            "raw-provider-secret-sentinel",
            "".join(traceback.format_exception(caught.exception)),
        )

    def test_two_connections_have_exactly_one_winner_under_real_contention(self) -> None:
        for round_index in range(10):
            request = handoff_requested(
                handoff_id=f"handoff:contention:{round_index}",
                incident_key=f"incident:contention:{round_index}",
                source_event_id=f"source:contention:{round_index}",
            )
            self.store.open_handoff(
                request,
                HandoffEffectPolicy.default_email_disabled(),
            )
            barrier = Barrier(2)

            def contender(worker_id: str):
                store = SQLiteFollowupUnitOfWork.open(self.path)
                try:
                    barrier.wait(timeout=5)
                    return store.claim_handoff_outbox(
                        worker_id=worker_id,
                        delivery_id=ScriptedDelivery.delivery_id,
                        delivery_version=ScriptedDelivery.delivery_version,
                        now=NOW,
                        lease_ttl=TTL,
                    )
                finally:
                    store.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = tuple(
                    executor.map(
                        contender,
                        (
                            f"worker:handoff:first:{round_index}",
                            f"worker:handoff:second:{round_index}",
                        ),
                    )
                )
            winners = tuple(claim for claim in results if claim is not None)
            self.assertEqual(len(winners), 1)
            self.assertEqual(winners[0].message.handoff_id, request.handoff_id)
            self.assertEqual(
                self.store._connection.execute(
                    "SELECT status, fencing_token, delivery_attempts "
                    "FROM main.handoff_outbox WHERE handoff_id=?",
                    (request.handoff_id,),
                ).fetchone(),
                ("leased", 1, 1),
            )

    def test_internal_email_success_preserves_acknowledgement_and_queue(self) -> None:
        request, _ = self.open_handoff(optional_email=True)
        before_payment = self.domain_fingerprint()
        ack_claim = self.claim()
        ack_receipt = HandoffReceipt.for_message(
            ack_claim.message,
            receipt_id="receipt:ack:email-success",
            delivery_reference="delivery-reference:ack-email-success",
            delivery_id=ack_claim.delivery_id,
            delivery_version=ack_claim.delivery_version,
            delivered_at=NOW,
        )
        self.store.complete_handoff_outbox(ack_claim, ack_receipt, now=NOW)
        before = self.store.load_handoff(request.handoff_id)
        email_claim = self.claim(now=NOW + timedelta(seconds=1))
        email_receipt = HandoffReceipt.for_message(
            email_claim.message,
            receipt_id="receipt:email:success",
            delivery_reference="delivery-reference:email-success",
            delivery_id=email_claim.delivery_id,
            delivery_version=email_claim.delivery_version,
            delivered_at=NOW + timedelta(seconds=1),
        )
        result = self.store.complete_handoff_outbox(
            email_claim,
            email_receipt,
            now=NOW + timedelta(seconds=1),
        )
        after = self.store.load_handoff(request.handoff_id)
        self.assertEqual(result.state, before)
        self.assertEqual(after, before)
        self.assertEqual(after.status, HandoffStatus.ACKNOWLEDGED)
        self.assertTrue(after.queue_active)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM main.handoff_receipts"
            ).fetchone(),
            (2,),
        )
        self.assertEqual(self.domain_fingerprint(), before_payment)

    def test_complete_faults_roll_back_every_atomic_write_after_reopen(self) -> None:
        cases = (
            (
                "outbox",
                "CREATE TEMP TRIGGER fault_outbox BEFORE UPDATE OF status "
                "ON main.handoff_outbox WHEN NEW.status='delivered' "
                "BEGIN SELECT RAISE(ABORT, 'fault:outbox'); END",
            ),
            (
                "receipt",
                "CREATE TEMP TRIGGER fault_receipt BEFORE INSERT "
                "ON main.handoff_receipts "
                "BEGIN SELECT RAISE(ABORT, 'fault:receipt'); END",
            ),
            (
                "workflow",
                "CREATE TEMP TRIGGER fault_workflow BEFORE UPDATE "
                "ON main.handoff_workflows "
                "BEGIN SELECT RAISE(ABORT, 'fault:workflow'); END",
            ),
            (
                "event",
                "CREATE TEMP TRIGGER fault_event BEFORE INSERT "
                "ON main.handoff_events WHEN NEW.event_type='HandoffAcknowledged' "
                "BEGIN SELECT RAISE(ABORT, 'fault:event'); END",
            ),
        )
        for index, (label, trigger) in enumerate(cases):
            with self.subTest(label=label):
                path = Path(self.temporary.name) / f"fault-{index}.db"
                store = SQLiteFollowupUnitOfWork.open(path)
                self.addCleanup(store.close)
                before_payment = self.payment_fingerprint_for(store._connection)
                request = handoff_requested(
                    handoff_id=f"handoff:fault:{index}",
                    incident_key=f"incident:fault:{index}",
                    source_event_id=f"source:fault:{index}",
                )
                store.open_handoff(
                    request,
                    HandoffEffectPolicy.default_email_disabled(),
                )
                claim = store.claim_handoff_outbox(
                    worker_id="worker:handoff:fault",
                    delivery_id=ScriptedDelivery.delivery_id,
                    delivery_version=ScriptedDelivery.delivery_version,
                    now=NOW,
                    lease_ttl=TTL,
                )
                receipt = HandoffReceipt.for_message(
                    claim.message,
                    receipt_id=f"receipt:fault:{index}",
                    delivery_reference=f"delivery-reference:fault:{index}",
                    delivery_id=claim.delivery_id,
                    delivery_version=claim.delivery_version,
                    delivered_at=NOW,
                )
                store._connection.execute(trigger)
                with self.assertRaises(StoreError) as caught:
                    store.complete_handoff_outbox(claim, receipt, now=NOW)
                self.assertIsInstance(caught.exception.__cause__, sqlite3.DatabaseError)
                store.close()
                reopened = SQLiteFollowupUnitOfWork.open(path)
                try:
                    state = reopened.load_handoff(request.handoff_id)
                    row = reopened._connection.execute(
                        "SELECT status, claim_owner, delivered_at, receipt_hash "
                        "FROM main.handoff_outbox WHERE handoff_id=?",
                        (request.handoff_id,),
                    ).fetchone()
                    self.assertEqual(row[0], "leased")
                    self.assertIsNotNone(row[1])
                    self.assertIsNone(row[2])
                    self.assertIsNone(row[3])
                    self.assertEqual(state.status, HandoffStatus.ACKNOWLEDGEMENT_PENDING)
                    self.assertEqual(
                        reopened._connection.execute(
                            "SELECT COUNT(*) FROM main.handoff_receipts"
                        ).fetchone(),
                        (0,),
                    )
                    self.assertEqual(
                        reopened._connection.execute(
                            "SELECT COUNT(*) FROM main.handoff_events WHERE handoff_id=?",
                            (request.handoff_id,),
                        ).fetchone(),
                        (1,),
                    )
                    self.assertEqual(
                        self.payment_fingerprint_for(reopened._connection),
                        before_payment,
                    )
                    self.assertEqual(
                        reopened._connection.execute("PRAGMA quick_check").fetchone(),
                        ("ok",),
                    )
                    self.assertEqual(
                        reopened._connection.execute("PRAGMA foreign_key_check").fetchall(),
                        [],
                    )
                finally:
                    reopened.close()

    def test_receipt_record_tamper_is_data_corruption_before_load(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        claim = self.claim()
        receipt = HandoffReceipt.for_message(
            claim.message,
            receipt_id="receipt:record:tamper",
            delivery_reference="delivery-reference:record-tamper",
            delivery_id=claim.delivery_id,
            delivery_version=claim.delivery_version,
            delivered_at=NOW,
        )
        self.store.complete_handoff_outbox(claim, receipt, now=NOW)
        raw = self.store._connection.execute(
            "SELECT receipt_json FROM main.handoff_receipts WHERE message_id=?",
            (claim.message.effect_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["data"]["worker_id"] = ""
        hostile = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        hostile_hash = hashlib.sha256(hostile.encode("utf-8")).hexdigest()
        self.store._connection.execute("PRAGMA defer_foreign_keys=ON")
        self.store._connection.execute("BEGIN IMMEDIATE")
        try:
            self.store._connection.execute(
                "UPDATE main.handoff_receipts SET receipt_json=?, receipt_hash=? "
                "WHERE message_id=?",
                (hostile, hostile_hash, claim.message.effect_id),
            )
            self.store._connection.execute(
                "UPDATE main.handoff_outbox SET receipt_hash=? WHERE message_id=?",
                (hostile_hash, claim.message.effect_id),
            )
            self.store._connection.commit()
        except BaseException:
            self.store._connection.rollback()
            raise
        with self.assertRaises(DataCorruption):
            self.store.load_handoff(request.handoff_id)

    def test_valid_worker_substitution_with_bilateral_rehash_is_corruption(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        claim = self.claim(worker_id="worker:handoff:original")
        receipt = HandoffReceipt.for_message(
            claim.message,
            receipt_id="receipt:record:valid-worker-tamper",
            delivery_reference="delivery-reference:valid-worker-tamper",
            delivery_id=claim.delivery_id,
            delivery_version=claim.delivery_version,
            delivered_at=NOW,
        )
        self.store.complete_handoff_outbox(claim, receipt, now=NOW)
        raw = self.store._connection.execute(
            "SELECT receipt_json FROM main.handoff_receipts WHERE message_id=?",
            (claim.message.effect_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["data"]["worker_id"] = "worker:handoff:substituted"
        hostile = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        hostile_hash = hashlib.sha256(hostile.encode("utf-8")).hexdigest()
        self.store._connection.execute("PRAGMA defer_foreign_keys=ON")
        self.store._connection.execute("BEGIN IMMEDIATE")
        try:
            self.store._connection.execute(
                "UPDATE main.handoff_receipts SET receipt_json=?, receipt_hash=? "
                "WHERE message_id=?",
                (hostile, hostile_hash, claim.message.effect_id),
            )
            self.store._connection.execute(
                "UPDATE main.handoff_outbox SET receipt_hash=? WHERE message_id=?",
                (hostile_hash, claim.message.effect_id),
            )
            self.store._connection.commit()
        except BaseException:
            self.store._connection.rollback()
            raise
        with self.assertRaises(DataCorruption):
            self.store.load_handoff(request.handoff_id)

    def test_coherent_worker_and_claim_owner_substitution_is_corruption(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        claim = self.claim(worker_id="worker:handoff:historical")
        receipt = HandoffReceipt.for_message(
            claim.message,
            receipt_id="receipt:record:coherent-worker-tamper",
            delivery_reference="delivery-reference:coherent-worker-tamper",
            delivery_id=claim.delivery_id,
            delivery_version=claim.delivery_version,
            delivered_at=NOW,
        )
        self.store.complete_handoff_outbox(claim, receipt, now=NOW)
        substituted_worker = "worker:handoff:coherently-substituted"
        material = "\x00".join(
            (substituted_worker, claim.delivery_id, str(claim.delivery_version))
        )
        substituted_owner = (
            "handoff-claim:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
        )

        def mutate(payload):
            payload["data"]["worker_id"] = substituted_worker
            payload["data"]["claim_owner"] = substituted_owner

        self.rewrite_receipt_record(claim.message.effect_id, mutate)
        with self.assertRaises(DataCorruption):
            self.store.load_handoff(request.handoff_id)

    def test_ack_receipt_id_must_match_persisted_acknowledgement_event(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        claim = self.claim()
        receipt = HandoffReceipt.for_message(
            claim.message,
            receipt_id="receipt:ack:historical",
            delivery_reference="delivery-reference:ack-historical",
            delivery_id=claim.delivery_id,
            delivery_version=claim.delivery_version,
            delivered_at=NOW,
        )
        self.store.complete_handoff_outbox(claim, receipt, now=NOW)
        substituted_receipt_id = "receipt:ack:coherently-substituted"

        def mutate(payload):
            inner = json.loads(payload["data"]["receipt_json"])
            inner["data"]["receipt_id"] = substituted_receipt_id
            payload["data"]["receipt_json"] = json.dumps(
                inner,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )

        self.rewrite_receipt_record(
            claim.message.effect_id,
            mutate,
            receipt_id=substituted_receipt_id,
        )
        with self.assertRaises(DataCorruption):
            self.store.load_handoff(request.handoff_id)

    def test_inner_receipt_json_must_be_canonical(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        claim = self.claim()
        receipt = HandoffReceipt.for_message(
            claim.message,
            receipt_id="receipt:record:noncanonical-inner",
            delivery_reference="delivery-reference:noncanonical-inner",
            delivery_id=claim.delivery_id,
            delivery_version=claim.delivery_version,
            delivered_at=NOW,
        )
        self.store.complete_handoff_outbox(claim, receipt, now=NOW)

        def mutate(payload):
            inner = json.loads(payload["data"]["receipt_json"])
            payload["data"]["receipt_json"] = json.dumps(
                inner,
                ensure_ascii=False,
                sort_keys=False,
                indent=2,
                allow_nan=False,
            )

        self.rewrite_receipt_record(claim.message.effect_id, mutate)
        with self.assertRaises(DataCorruption):
            self.store.load_handoff(request.handoff_id)

    def test_required_failure_then_success_acknowledges_without_erasing_history(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        failed_claim = self.claim()
        self.store.release_handoff_outbox(failed_claim, now=NOW)
        failed_state = self.store.load_handoff(request.handoff_id)
        self.assertEqual(failed_state.status, HandoffStatus.MANUAL_REVIEW)
        retry = self.claim(now=NOW + timedelta(seconds=1))
        receipt = HandoffReceipt.for_message(
            retry.message,
            receipt_id="receipt:required:retry-success",
            delivery_reference="delivery-reference:required-retry-success",
            delivery_id=retry.delivery_id,
            delivery_version=retry.delivery_version,
            delivered_at=NOW + timedelta(seconds=1),
        )
        self.store.complete_handoff_outbox(
            retry,
            receipt,
            now=NOW + timedelta(seconds=1),
        )
        state = self.store.load_handoff(request.handoff_id)
        self.assertEqual(state.status, HandoffStatus.ACKNOWLEDGED)
        self.assertTrue(state.queue_active)
        self.assertEqual(state.effect_failures, failed_state.effect_failures)
        self.assertEqual(state.acknowledgement.receipt_id, receipt.receipt_id)

    def test_receipt_wire_roundtrip_is_closed_and_claim_is_frozen(self) -> None:
        self.open_handoff(optional_email=False)
        claim = self.claim()
        receipt = HandoffReceipt.for_message(
            claim.message,
            receipt_id="receipt:wire:synthetic",
            delivery_reference="delivery-reference:wire",
            delivery_id=claim.delivery_id,
            delivery_version=claim.delivery_version,
            delivered_at=NOW,
        )
        raw = to_wire_json(receipt)
        self.assertEqual(from_wire_json(raw, HandoffReceipt), receipt)
        with self.assertRaises((AttributeError, TypeError)):
            claim.worker_id = "worker:mutated"
        with self.assertRaises((AttributeError, TypeError)):
            receipt.delivery_id = "delivery:mutated"

    def test_operational_tamper_is_rejected_before_claim(self) -> None:
        request, _ = self.open_handoff(optional_email=False)
        self.store._connection.execute(
            "UPDATE main.handoff_outbox SET fencing_token=1, delivery_attempts=1 "
            "WHERE handoff_id=?",
            (request.handoff_id,),
        )
        with self.assertRaises(DataCorruption):
            self.claim()


if __name__ == "__main__":
    unittest.main()
