from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from reservation_followup import (
    EffectRequirement,
    FinancialConfirmationReceived,
    FinancialSummaryRecorded,
    HandoffAcknowledged,
    HandoffEffectJob,
    HandoffEffectPolicy,
    HandoffReasonCode,
    HandoffTransitionStatus,
    PaymentEvidenceRecorded,
    PaymentMethod,
    PaymentMethodSelected,
    PaymentTransitionStatus,
    SettlementStarted,
    financial_summary_hash,
    from_wire_json,
    new_handoff,
    new_payment,
    reduce_payment,
    semantic_hash,
    to_wire_json,
)
from reservation_followup.schema import schema_contract
from reservation_followup.sqlite_store import (
    ConcurrencyConflict,
    DataCorruption,
    HandoffNotFound,
    IdentityConflict,
    PaymentNotFound,
    SQLiteFollowupUnitOfWork,
    StoreError,
    UnsupportedEffect,
)
from tests.phase6_helpers import (
    T0,
    confirmed_anchor,
    handoff_requested,
    optional_email_policy,
    payment_effect_policy,
    payment_evidence_trust,
    pix_visual_evidence,
)

TABLES = tuple(table.name for table in schema_contract())
HANDOFF_TABLES = (
    "handoff_workflows",
    "handoff_events",
    "handoff_outbox",
    "handoff_receipts",
)
PAYMENT_TABLES = (
    "payment_workflows",
    "payment_events",
    "payment_evidence_claims",
    "payment_commands",
    "payment_ledger",
    "payment_outbox",
    "payment_receipts",
)


def database_counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
    finally:
        connection.close()


def database_fingerprint(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        rows = tuple(
            (table, tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1")))
            for table in TABLES
        )
    finally:
        connection.close()
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def method_event(state, *, event_id: str = "payment:event:store:method:1"):
    return PaymentMethodSelected(
        event_id=event_id,
        payment_id=state.subject.payment_id,
        method=PaymentMethod.PIX,
        selected_at=T0 + timedelta(seconds=1),
    )


def summary_event(state, *, event_id: str = "payment:event:store:summary:1"):
    return FinancialSummaryRecorded(
        event_id=event_id,
        subject=state.subject,
        summary_hash=financial_summary_hash(state.subject),
        recorded_at=T0 + timedelta(seconds=2),
    )


def confirmation_event(
    state,
    *,
    event_id: str = "payment:event:store:confirmation:1",
):
    assert state.summary is not None
    return FinancialConfirmationReceived(
        event_id=event_id,
        payment_id=state.subject.payment_id,
        payment_version=state.subject.payment_version,
        economic_signature=state.subject.economic_signature,
        summary_hash=state.summary.summary_hash,
        confirmation_id="payment:confirmation:store:1",
        confirmed_at=T0 + timedelta(seconds=3),
    )


def evidence_event(state, *, event_id: str = "payment:event:store:evidence:1"):
    return PaymentEvidenceRecorded(
        event_id=event_id,
        payment_id=state.subject.payment_id,
        payment_version=state.subject.payment_version,
        economic_signature=state.subject.economic_signature,
        evidence=pix_visual_evidence(),
        trust=payment_evidence_trust(),
        recorded_at=T0 + timedelta(seconds=4),
    )


class SyntheticAbort(BaseException):
    pass


class Phase6FollowupStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase6-followup-store-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "followup.db"
        self.stores: list[SQLiteFollowupUnitOfWork] = []

    def tearDown(self) -> None:
        for store in reversed(self.stores):
            store.close()
        for path in self.root.glob("*.db"):
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                connection.close()
        self.temporary.cleanup()

    def open_store(self, path: Path | None = None) -> SQLiteFollowupUnitOfWork:
        store = SQLiteFollowupUnitOfWork.open(path or self.path)
        self.stores.append(store)
        return store

    def close_store(self, store: SQLiteFollowupUnitOfWork) -> None:
        store.close()
        self.stores.remove(store)

    def assert_all_zero(self, path: Path) -> None:
        self.assertEqual(database_counts(path), {table: 0 for table in TABLES})

    def test_open_initializes_exact_eleven_tables_for_path_or_connection(self) -> None:
        store = self.open_store()
        names = tuple(
            row[0]
            for row in store._connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        )
        self.assertEqual(names, TABLES)
        self.assertNotIn("schema_migrations", names)
        self.assertEqual(
            store._connection.execute("PRAGMA foreign_keys").fetchone(),
            (1,),
        )
        self.assertEqual(
            store._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall(),
            [],
        )
        self.assertFalse(hasattr(store, "connection"))

        connection = sqlite3.connect(":memory:")
        memory_store = SQLiteFollowupUnitOfWork.open(connection)
        self.assertEqual(
            tuple(
                row[0]
                for row in memory_store._connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
                )
            ),
            TABLES,
        )
        memory_store.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

        string_store = SQLiteFollowupUnitOfWork.open(":memory:")
        string_store.close()

    def test_constructor_factory_guard_and_schema_drift_fail_closed(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        with self.assertRaises(TypeError):
            SQLiteFollowupUnitOfWork(connection)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            SQLiteFollowupUnitOfWork(connection, _factory_token=object())
        for invalid in (True, 1, 1.0, object()):
            with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                SQLiteFollowupUnitOfWork.open(invalid)  # type: ignore[arg-type]

        drift_path = self.root / "drift.db"
        drift = sqlite3.connect(drift_path)
        drift.execute("CREATE TABLE unrelated(value TEXT)")
        drift.commit()
        drift.close()
        with self.assertRaises(DataCorruption):
            SQLiteFollowupUnitOfWork.open(drift_path)
        drift = sqlite3.connect(drift_path)
        try:
            self.assertEqual(
                drift.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall(),
                [("unrelated",)],
            )
        finally:
            drift.close()

    def test_open_handoff_persists_request_state_and_policy_jobs_then_reopens(self) -> None:
        for index, policy in enumerate(
            (HandoffEffectPolicy.default_email_disabled(), optional_email_policy())
        ):
            with self.subTest(policy=policy.internal_email.value):
                path = self.root / f"handoff-open-{index}.db"
                store = self.open_store(path)
                request = handoff_requested(
                    handoff_id=f"handoff:store:open:{index}",
                    incident_key=f"incident:store:open:{index}",
                    source_event_id=f"source:event:store:open:{index}",
                )
                opened = store.open_handoff(request, policy)
                self.assertIs(opened.status, HandoffTransitionStatus.APPLIED)
                self.assertEqual(store.load_handoff(request.handoff_id), opened.state)
                counts = database_counts(path)
                self.assertEqual(counts["handoff_workflows"], 1)
                self.assertEqual(counts["handoff_events"], 1)
                self.assertEqual(
                    counts["handoff_outbox"],
                    1 if policy.internal_email is EffectRequirement.DISABLED else 2,
                )
                self.assertTrue(all(counts[table] == 0 for table in PAYMENT_TABLES))

                expected_jobs = new_handoff(request, policy).effect_jobs
                rows = tuple(
                    store._connection.execute(
                        "SELECT effect_id, kind, payload_json, payload_hash, status, "
                        "fencing_token, delivery_attempts FROM handoff_outbox "
                        "ORDER BY created_at, kind"
                    )
                )
                self.assertEqual({row[0] for row in rows}, {job.effect_id for job in expected_jobs})
                self.assertEqual({row[1] for row in rows}, {job.kind.value for job in expected_jobs})
                self.assertEqual(
                    {from_wire_json(row[2], HandoffEffectJob) for row in rows},
                    set(expected_jobs),
                )
                self.assertTrue(
                    all(
                        row[3] == hashlib.sha256(row[2].encode("utf-8")).hexdigest()
                        and row[4:] == ("pending", 0, 0)
                        for row in rows
                    )
                )

                self.close_store(store)
                reopened = self.open_store(path)
                self.assertEqual(reopened.load_handoff(request.handoff_id), opened.state)
                self.assertEqual(reopened._connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
                self.assertEqual(reopened._connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_open_handoff_exact_replay_is_noop_and_all_identities_are_immutable(self) -> None:
        store = self.open_store()
        request = handoff_requested()
        policy = HandoffEffectPolicy.default_email_disabled()
        first = store.open_handoff(request, policy)
        before = database_fingerprint(self.path)
        replay = store.open_handoff(request, policy)
        self.assertIs(replay.status, HandoffTransitionStatus.NOOP)
        self.assertEqual(replay.state, first.state)
        self.assertEqual(database_fingerprint(self.path), before)

        conflicts = (
            replace(request, reason_code=HandoffReasonCode.SAFETY_REVIEW),
            handoff_requested(
                handoff_id="handoff:store:other-id",
                source_event_id="source:event:store:other-id",
            ),
            handoff_requested(
                handoff_id="handoff:store:other-event-owner",
                incident_key="incident:store:other-event-owner",
                source_event_id=request.source_event_id,
            ),
        )
        for conflict in conflicts:
            with self.subTest(conflict=conflict), self.assertRaises(IdentityConflict):
                store.open_handoff(conflict, policy)
            self.assertEqual(database_fingerprint(self.path), before)
        with self.assertRaises(HandoffNotFound):
            store.load_handoff("handoff:store:missing")

    def test_open_payment_persists_only_anchor_bounded_workflow_and_reopens(self) -> None:
        store = self.open_store()
        anchor = confirmed_anchor()
        policy = payment_effect_policy()
        opened = store.open_payment(anchor, policy)
        expected = new_payment(anchor, policy)
        self.assertEqual(opened, expected)
        self.assertEqual(store.load_payment(opened.state.subject.payment_id), opened.state)
        counts = database_counts(self.path)
        self.assertEqual(counts["payment_workflows"], 1)
        self.assertEqual(sum(counts[table] for table in PAYMENT_TABLES[1:]), 0)
        self.assertEqual(sum(counts[table] for table in HANDOFF_TABLES), 0)
        self.assertNotIn("reservation_commands", TABLES)

        before = database_fingerprint(self.path)
        replay = store.open_payment(anchor, policy)
        self.assertIs(replay.status, PaymentTransitionStatus.NOOP)
        self.assertEqual(replay.state, opened.state)
        self.assertEqual(database_fingerprint(self.path), before)

        divergent_anchor = confirmed_anchor(
            confirmed_at=T0 + timedelta(seconds=1),
            payment_deadline=T0 + timedelta(days=2),
        )
        self.assertEqual(
            new_payment(divergent_anchor, policy).state.subject.payment_id,
            opened.state.subject.payment_id,
        )
        with self.assertRaises(IdentityConflict):
            store.open_payment(divergent_anchor, policy)
        with self.assertRaises(PaymentNotFound):
            store.load_payment("payment:store:missing")

        self.close_store(store)
        reopened = self.open_store()
        self.assertEqual(reopened.load_payment(opened.state.subject.payment_id), opened.state)
        self.assertEqual(reopened._connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
        self.assertEqual(reopened._connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_every_handoff_open_statement_fault_rolls_back_after_reopen(self) -> None:
        fault_cases = (
            ("handoff_workflows", None),
            ("handoff_events", None),
            ("handoff_outbox", "customer_acknowledgement"),
            ("handoff_outbox", "internal_email"),
        )
        for index, (table, kind) in enumerate(fault_cases):
            with self.subTest(table=table, kind=kind):
                path = self.root / f"handoff-fault-{index}.db"
                store = self.open_store(path)
                when = "" if kind is None else f" WHEN NEW.kind='{kind}'"
                operation = "UPDATE" if table == "handoff_workflows:update" else "INSERT"
                actual_table = table.removesuffix(":update")
                store._connection.execute(
                    f"CREATE TEMP TRIGGER fault_{index} BEFORE {operation} "
                    f"ON main.{actual_table}{when} "
                    f"BEGIN SELECT RAISE(ABORT, 'fault:{index}'); END"
                )
                request = handoff_requested(
                    handoff_id=f"handoff:store:fault:{index}",
                    incident_key=f"incident:store:fault:{index}",
                    source_event_id=f"source:event:store:fault:{index}",
                )
                with self.assertRaises(StoreError):
                    store.open_handoff(request, optional_email_policy())
                self.close_store(store)
                reopened = self.open_store(path)
                self.assert_all_zero(path)
                with self.assertRaises(HandoffNotFound):
                    reopened.load_handoff(request.handoff_id)

    def test_every_payment_open_statement_fault_rolls_back_after_reopen(self) -> None:
        path = self.root / "payment-fault-workflow.db"
        store = self.open_store(path)
        store._connection.execute(
            "CREATE TEMP TRIGGER fault_payment_workflow "
            "BEFORE INSERT ON main.payment_workflows "
            "BEGIN SELECT RAISE(ABORT, 'fault:payment-workflow'); END"
        )
        with self.assertRaises(StoreError):
            store.open_payment(confirmed_anchor(), payment_effect_policy())
        self.close_store(store)
        reopened = self.open_store(path)
        self.assert_all_zero(path)
        expected_id = new_payment(
            confirmed_anchor(), payment_effect_policy()
        ).state.subject.payment_id
        with self.assertRaises(PaymentNotFound):
            reopened.load_payment(expected_id)

    def test_transaction_rolls_back_baseexception_and_commit_failure_is_reusable(self) -> None:
        store = self.open_store()
        raw = "{}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self.assertRaises(SyntheticAbort):
            with store._transaction("synthetic_baseexception"):
                store._connection.execute(
                    "INSERT INTO handoff_workflows "
                    "(handoff_id, incident_key, revision, status, lead_key_hash, "
                    "state_json, state_hash, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "handoff:store:baseexception",
                        "incident:store:baseexception",
                        0,
                        "requested",
                        "a" * 64,
                        raw,
                        digest,
                        T0.isoformat(),
                        T0.isoformat(),
                    ),
                )
                raise SyntheticAbort()
        self.assertFalse(store._connection.in_transaction)
        self.assert_all_zero(self.path)

        def deny_commit(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        store._connection.set_authorizer(deny_commit)
        with self.assertRaises(StoreError) as raised:
            store.open_payment(confirmed_anchor(), payment_effect_policy())
        self.assertIsInstance(raised.exception.__cause__, sqlite3.DatabaseError)
        self.assertFalse(store._connection.in_transaction)
        store._connection.set_authorizer(None)
        self.assert_all_zero(self.path)
        opened = store.open_payment(confirmed_anchor(), payment_effect_policy())
        self.assertEqual(store.load_payment(opened.state.subject.payment_id), opened.state)

    def test_begin_failure_is_mapped_without_open_transaction(self) -> None:
        store = self.open_store()

        def deny_begin(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_TRANSACTION and arg1 == "BEGIN":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        store._connection.set_authorizer(deny_begin)
        with self.assertRaises(StoreError) as raised:
            store.open_payment(confirmed_anchor(), payment_effect_policy())
        self.assertIsInstance(raised.exception.__cause__, sqlite3.DatabaseError)
        self.assertFalse(store._connection.in_transaction)
        store._connection.set_authorizer(None)
        self.assert_all_zero(self.path)

    def test_apply_handoff_persists_event_state_and_duplicate_noop_after_reopen(self) -> None:
        store = self.open_store()
        request = handoff_requested()
        opened = store.open_handoff(
            request,
            HandoffEffectPolicy.default_email_disabled(),
        )
        ack_job = new_handoff(request, opened.state.policy).effect_jobs[0]
        acknowledged = HandoffAcknowledged(
            handoff_id=request.handoff_id,
            incident_key=request.incident_key,
            effect_id=ack_job.effect_id,
            receipt_id="receipt:handoff:store:1",
            acknowledged_at=T0 + timedelta(seconds=1),
        )
        applied = store.apply_handoff(request.handoff_id, 1, acknowledged)
        self.assertIs(applied.status, HandoffTransitionStatus.APPLIED)
        self.assertEqual(database_counts(self.path)["handoff_events"], 2)
        before = database_fingerprint(self.path)
        replay = store.apply_handoff(request.handoff_id, 0, acknowledged)
        self.assertIs(replay.status, HandoffTransitionStatus.NOOP)
        self.assertEqual(replay.state, applied.state)
        self.assertEqual(database_fingerprint(self.path), before)

        divergent = replace(
            acknowledged,
            acknowledged_at=acknowledged.acknowledged_at + timedelta(seconds=1),
        )
        with self.assertRaises(IdentityConflict):
            store.apply_handoff(request.handoff_id, 2, divergent)
        self.assertEqual(database_fingerprint(self.path), before)

        self.close_store(store)
        reopened = self.open_store()
        self.assertEqual(reopened.load_handoff(request.handoff_id), applied.state)

    def test_two_connections_only_one_expected_handoff_revision_wins(self) -> None:
        first = self.open_store()
        second = self.open_store()
        request = handoff_requested()
        first.open_handoff(request, HandoffEffectPolicy.default_email_disabled())
        self.assertEqual(second.load_handoff(request.handoff_id), first.load_handoff(request.handoff_id))
        ack_job = new_handoff(request, HandoffEffectPolicy.default_email_disabled()).effect_jobs[0]
        acknowledged = HandoffAcknowledged(
            handoff_id=request.handoff_id,
            incident_key=request.incident_key,
            effect_id=ack_job.effect_id,
            receipt_id="receipt:handoff:store:race",
            acknowledged_at=T0 + timedelta(seconds=1),
        )
        first.apply_handoff(request.handoff_id, 1, acknowledged)
        stale = replace(acknowledged, receipt_id="receipt:handoff:store:stale")
        before = database_fingerprint(self.path)
        with self.assertRaises(ConcurrencyConflict):
            second.apply_handoff(request.handoff_id, 1, stale)
        self.assertEqual(database_fingerprint(self.path), before)
        self.assertEqual(second.load_handoff(request.handoff_id).acknowledgement, acknowledged)

    def test_apply_payment_persists_nonoperational_events_and_old_duplicate_is_noop(self) -> None:
        store = self.open_store()
        opened = store.open_payment(confirmed_anchor(), payment_effect_policy())
        payment_id = opened.state.subject.payment_id
        selected_event = method_event(opened.state)
        selected = store.apply_payment(payment_id, 0, selected_event)
        summarized_event = summary_event(selected.state)
        summarized = store.apply_payment(payment_id, 1, summarized_event)
        self.assertEqual(summarized.state.history, (selected_event, summarized_event))
        self.assertEqual(database_counts(self.path)["payment_events"], 2)

        before = database_fingerprint(self.path)
        replay = store.apply_payment(payment_id, 0, selected_event)
        self.assertIs(replay.status, PaymentTransitionStatus.NOOP)
        self.assertEqual(replay.state, summarized.state)
        self.assertEqual(database_fingerprint(self.path), before)

        for invalid in (-1, True, 1.0):
            with self.subTest(expected_revision=invalid), self.assertRaises(ValueError):
                store.apply_payment(
                    payment_id,
                    invalid,  # type: ignore[arg-type]
                    confirmation_event(summarized.state),
                )
        with self.assertRaises(TypeError):
            store.apply_payment(payment_id, 2, object())  # type: ignore[arg-type]
        self.assertEqual(database_fingerprint(self.path), before)

        self.close_store(store)
        reopened = self.open_store()
        self.assertEqual(reopened.load_payment(payment_id), summarized.state)

    def test_payment_event_identity_is_global_and_divergence_conflicts(self) -> None:
        store = self.open_store()
        first = store.open_payment(confirmed_anchor(), payment_effect_policy()).state
        other_anchor = confirmed_anchor(
            reservation_workflow_id="workflow:reservation:synthetic:other",
            payment_target_id="target:reservation:synthetic:other",
            receiver_profile_id="receiver:profile:synthetic:other",
            amount_minor=13000,
        )
        second = store.open_payment(other_anchor, payment_effect_policy()).state
        event_id = "payment:event:store:global-identity"
        store.apply_payment(first.subject.payment_id, 0, method_event(first, event_id=event_id))
        divergent = method_event(second, event_id=event_id)
        before = database_fingerprint(self.path)
        with self.assertRaises(IdentityConflict):
            store.apply_payment(second.subject.payment_id, 0, divergent)
        self.assertEqual(database_fingerprint(self.path), before)

    def test_payment_settlement_command_and_operational_events_fail_before_write(self) -> None:
        store = self.open_store()
        opened = store.open_payment(confirmed_anchor(), payment_effect_policy())
        payment_id = opened.state.subject.payment_id
        selected = store.apply_payment(payment_id, 0, method_event(opened.state))
        summarized = store.apply_payment(payment_id, 1, summary_event(selected.state))
        confirmed = store.apply_payment(
            payment_id,
            2,
            confirmation_event(summarized.state),
        )
        event = evidence_event(confirmed.state)
        pure_transition = reduce_payment(confirmed.state, event)
        self.assertEqual(len(pure_transition.commands), 1)
        before = database_fingerprint(self.path)
        with self.assertRaises(UnsupportedEffect):
            store.apply_payment(payment_id, 3, event)
        self.assertEqual(database_fingerprint(self.path), before)
        self.assertEqual(sum(database_counts(self.path)[table] for table in PAYMENT_TABLES[2:]), 0)

        command = pure_transition.commands[0]
        operational = SettlementStarted(
            event_id="payment:event:store:settlement-started",
            payment_id=payment_id,
            payment_version=confirmed.state.subject.payment_version,
            economic_signature=confirmed.state.subject.economic_signature,
            settlement_command_id=command.settlement_command_id,
            idempotency_key=command.idempotency_key,
            started_at=T0 + timedelta(seconds=5),
        )
        with self.assertRaises(UnsupportedEffect):
            store.apply_payment(payment_id, 3, operational)
        self.assertEqual(database_fingerprint(self.path), before)

        self.close_store(store)
        reopened = self.open_store()
        self.assertEqual(reopened.load_payment(payment_id), confirmed.state)

    def test_handoff_state_event_and_outbox_canonical_tamper_are_detected(self) -> None:
        tamper_cases = ("state_pretty", "state_hash", "revision", "status", "event", "outbox")
        for index, tamper in enumerate(tamper_cases):
            with self.subTest(tamper=tamper):
                path = self.root / f"handoff-tamper-{index}.db"
                store = self.open_store(path)
                request = handoff_requested(
                    handoff_id=f"handoff:store:tamper:{index}",
                    incident_key=f"incident:store:tamper:{index}",
                    source_event_id=f"source:event:store:tamper:{index}",
                )
                opened = store.open_handoff(
                    request,
                    HandoffEffectPolicy.default_email_disabled(),
                )
                connection = sqlite3.connect(path)
                if tamper == "state_pretty":
                    raw = to_wire_json(opened.state)
                    pretty = json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, indent=1)
                    connection.execute(
                        "UPDATE handoff_workflows SET state_json=?, state_hash=?",
                        (pretty, hashlib.sha256(pretty.encode("utf-8")).hexdigest()),
                    )
                elif tamper == "state_hash":
                    connection.execute(
                        "UPDATE handoff_workflows SET state_hash=?",
                        ("c" * 64,),
                    )
                elif tamper == "revision":
                    connection.execute("UPDATE handoff_workflows SET revision=2")
                elif tamper == "status":
                    connection.execute("UPDATE handoff_workflows SET status='active'")
                elif tamper == "event":
                    divergent = replace(request, reason_code=HandoffReasonCode.SAFETY_REVIEW)
                    raw = to_wire_json(divergent)
                    connection.execute(
                        "UPDATE handoff_events SET event_json=?, event_hash=?",
                        (raw, semantic_hash(divergent)),
                    )
                else:
                    job = new_handoff(request, opened.state.policy).effect_jobs[0]
                    divergent = replace(job, created_at=job.created_at + timedelta(seconds=1))
                    raw = to_wire_json(divergent)
                    connection.execute(
                        "UPDATE handoff_outbox SET payload_json=?, payload_hash=?, "
                        "created_at=?, updated_at=?",
                        (
                            raw,
                            semantic_hash(divergent),
                            divergent.created_at.isoformat(),
                            divergent.created_at.isoformat(),
                        ),
                    )
                connection.commit()
                connection.close()
                with self.assertRaises(DataCorruption):
                    store.load_handoff(request.handoff_id)

    def test_payment_state_event_metadata_and_canonical_tamper_are_detected(self) -> None:
        tamper_cases = (
            "state_pretty",
            "state_hash",
            "revision",
            "payment_version",
            "economic_signature",
            "status",
            "event",
            "event_revision",
        )
        for index, tamper in enumerate(tamper_cases):
            with self.subTest(tamper=tamper):
                path = self.root / f"payment-tamper-{index}.db"
                store = self.open_store(path)
                opened = store.open_payment(confirmed_anchor(), payment_effect_policy())
                event = method_event(
                    opened.state,
                    event_id=f"payment:event:store:tamper:{index}",
                )
                selected = store.apply_payment(opened.state.subject.payment_id, 0, event)
                connection = sqlite3.connect(path)
                if tamper == "state_pretty":
                    raw = to_wire_json(selected.state)
                    pretty = json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, indent=1)
                    connection.execute(
                        "UPDATE payment_workflows SET state_json=?, state_hash=?",
                        (pretty, hashlib.sha256(pretty.encode("utf-8")).hexdigest()),
                    )
                elif tamper == "state_hash":
                    connection.execute(
                        "UPDATE payment_workflows SET state_hash=?",
                        ("d" * 64,),
                    )
                elif tamper == "revision":
                    connection.execute("UPDATE payment_workflows SET revision=2")
                elif tamper == "payment_version":
                    connection.execute("UPDATE payment_workflows SET payment_version=2")
                elif tamper == "economic_signature":
                    connection.execute(
                        "UPDATE payment_workflows SET economic_signature=?",
                        ("e" * 64,),
                    )
                elif tamper == "status":
                    connection.execute("UPDATE payment_workflows SET status='awaiting_method'")
                elif tamper == "event":
                    divergent = replace(event, method=PaymentMethod.WISE)
                    raw = to_wire_json(divergent)
                    connection.execute(
                        "UPDATE payment_events SET event_json=?, event_hash=?",
                        (raw, semantic_hash(divergent)),
                    )
                else:
                    connection.execute("UPDATE payment_events SET revision=2")
                connection.commit()
                connection.close()
                with self.assertRaises(DataCorruption):
                    store.load_payment(opened.state.subject.payment_id)

    def test_exact_types_bool_as_int_guards_and_close_idempotency(self) -> None:
        store = self.open_store()
        request = handoff_requested()
        store.open_handoff(request, HandoffEffectPolicy.default_email_disabled())
        for invalid in (-1, True, 1.0):
            with self.subTest(expected_revision=invalid), self.assertRaises(ValueError):
                store.apply_handoff(
                    request.handoff_id,
                    invalid,  # type: ignore[arg-type]
                    request,
                )
        with self.assertRaises(TypeError):
            store.apply_handoff(request.handoff_id, 1, object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            store.load_handoff(True)  # type: ignore[arg-type]
        self.close_store(store)
        store.close()
        with self.assertRaises(StoreError):
            store.load_handoff(request.handoff_id)

        failed_close = SQLiteFollowupUnitOfWork.open(self.root / "close-failure.db")
        failed_close._connection.close()
        with self.assertRaises(StoreError) as raised:
            failed_close.close()
        self.assertIsInstance(raised.exception.__cause__, sqlite3.DatabaseError)
        failed_close.close()


if __name__ == "__main__":
    unittest.main()
