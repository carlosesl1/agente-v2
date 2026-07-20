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
    HandoffCancellationCode,
    HandoffCancelled,
    HandoffEffectFailed,
    HandoffEffectFailureCode,
    HandoffEffectJob,
    HandoffEffectKind,
    HandoffEffectPolicy,
    HandoffReasonCode,
    HandoffTransitionStatus,
    PaymentEvidenceRecorded,
    PaymentMethod,
    PaymentMethodSelected,
    PaymentTransitionStatus,
    SettlementCertainty,
    SettlementFinished,
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
    StoreUnavailable,
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
from tests.test_phase6_payment_reducer import outcome

ROOT = Path(__file__).resolve().parents[1]

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

    def test_exact_schema_definition_partial_universe_and_triggers_fail_closed(self) -> None:
        cases = ("altered_definition", "partial_valid_universe", "trigger")
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                path = self.root / f"schema-hostile-{index}.db"
                connection = sqlite3.connect(path)
                if case == "altered_definition":
                    sql = (ROOT / "schemas/phase6/sqlite.sql").read_text(
                        encoding="utf-8"
                    )
                    mutated = sql.replace(
                        "CHECK (revision >= 0)",
                        "CHECK (revision >= -1)",
                        1,
                    )
                    self.assertNotEqual(mutated, sql)
                    connection.executescript(mutated)
                else:
                    connection.executescript(
                        (ROOT / "schemas/phase6/sqlite.sql").read_text(
                            encoding="utf-8"
                        )
                    )
                    if case == "partial_valid_universe":
                        connection.execute("DROP TABLE payment_receipts")
                    else:
                        connection.execute(
                            "CREATE TRIGGER hostile_after_handoff "
                            "AFTER INSERT ON handoff_workflows BEGIN SELECT 1; END"
                        )
                connection.commit()
                connection.close()
                with self.assertRaises(DataCorruption):
                    SQLiteFollowupUnitOfWork.open(path)

    def test_open_rejects_temp_objects_and_attached_databases_without_stealing_caller_connection(self) -> None:
        cases = ("temp_table", "temp_trigger", "attached_database")
        for case in cases:
            with self.subTest(case=case):
                connection = sqlite3.connect(":memory:")
                connection.executescript(
                    (ROOT / "schemas/phase6/sqlite.sql").read_text(encoding="utf-8")
                )
                connection.execute("PRAGMA foreign_keys = OFF")
                original_isolation_level = connection.isolation_level
                original_foreign_keys = connection.execute(
                    "PRAGMA foreign_keys"
                ).fetchone()
                if case == "temp_table":
                    connection.execute(
                        "CREATE TEMP TABLE handoff_workflows(shadow TEXT)"
                    )
                elif case == "temp_trigger":
                    connection.execute(
                        "CREATE TEMP TRIGGER shadow_handoff AFTER INSERT "
                        "ON main.handoff_workflows BEGIN SELECT 1; END"
                    )
                else:
                    connection.execute("ATTACH DATABASE ':memory:' AS hostile")
                try:
                    opened = None
                    error = None
                    try:
                        opened = SQLiteFollowupUnitOfWork.open(connection)
                    except BaseException as exc:
                        error = exc
                    self.assertIsInstance(error, DataCorruption)
                    if opened is not None:
                        opened.close()
                    else:
                        self.assertEqual(connection.execute("SELECT 1").fetchone(), (1,))
                        self.assertEqual(
                            connection.isolation_level,
                            original_isolation_level,
                        )
                        self.assertEqual(
                            connection.execute("PRAGMA foreign_keys").fetchone(),
                            original_foreign_keys,
                        )
                        if case == "attached_database":
                            self.assertIn(
                                "hostile",
                                {
                                    row[1]
                                    for row in connection.execute(
                                        "PRAGMA database_list"
                                    )
                                },
                            )
                finally:
                    try:
                        connection.close()
                    except sqlite3.Error:
                        pass

    def test_open_rejects_literal_sqlite_lookalike_extra_table(self) -> None:
        path = self.root / "sqlite-lookalike-extra.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            (ROOT / "schemas/phase6/sqlite.sql").read_text(encoding="utf-8")
        )
        connection.execute("CREATE TABLE sqliteXextra(value TEXT)")
        connection.commit()
        connection.close()
        opened = None
        error = None
        try:
            opened = SQLiteFollowupUnitOfWork.open(path)
        except BaseException as exc:
            error = exc
        self.assertIsInstance(error, DataCorruption)
        if opened is not None:
            opened.close()

    def test_open_maps_path_connection_failure_stably(self) -> None:
        target = self.root / "missing-parent" / "store.db"
        error = None
        try:
            SQLiteFollowupUnitOfWork.open(target)
        except BaseException as exc:
            error = exc
        self.assertIsInstance(error, StoreUnavailable)
        self.assertIsInstance(error.__cause__, sqlite3.Error)

    def test_open_maps_closed_caller_connection_failure_stably(self) -> None:
        closed = sqlite3.connect(":memory:")
        closed.close()
        error = None
        try:
            SQLiteFollowupUnitOfWork.open(closed)
        except BaseException as exc:
            error = exc
        self.assertIsInstance(error, StoreUnavailable)
        self.assertIsInstance(error.__cause__, sqlite3.Error)

    def test_open_maps_physical_corruption_to_data_corruption(self) -> None:
        malformed = self.root / "malformed.sqlite"
        malformed.write_bytes(b"not-a-sqlite-database")
        error = None
        try:
            SQLiteFollowupUnitOfWork.open(malformed)
        except BaseException as exc:
            error = exc
        self.assertIsInstance(error, DataCorruption)
        self.assertIsInstance(error.__cause__, sqlite3.DatabaseError)

    def test_open_rejects_preexisting_foreign_key_violations(self) -> None:
        path = self.root / "foreign-key-corruption.db"
        store = self.open_store(path)
        self.close_store(store)
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO handoff_events "
            "(event_id, handoff_id, revision, event_type, event_json, event_hash, occurred_at) "
            "VALUES (?, ?, 1, 'HandoffRequested', '{}', ?, ?)",
            (
                "event:orphan",
                "handoff:orphan",
                hashlib.sha256(b"{}").hexdigest(),
                T0.isoformat(),
            ),
        )
        connection.commit()
        connection.close()
        opened = None
        error = None
        try:
            opened = SQLiteFollowupUnitOfWork.open(path)
        except BaseException as exc:
            error = exc
        if opened is not None:
            opened.close()
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM handoff_events")
        connection.commit()
        connection.close()
        self.assertIsInstance(error, DataCorruption)

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
        self.assertEqual(database_fingerprint(self.path), before)
        with self.assertRaises(PaymentNotFound):
            store.load_payment("payment:store:missing")

        self.close_store(store)
        reopened = self.open_store()
        self.assertEqual(reopened.load_payment(opened.state.subject.payment_id), opened.state)
        self.assertEqual(reopened._connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
        self.assertEqual(reopened._connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_open_payment_exact_bootstrap_replay_is_noop_after_progression(self) -> None:
        store = self.open_store()
        anchor = confirmed_anchor()
        policy = payment_effect_policy()
        opened = store.open_payment(anchor, policy)
        selected = store.apply_payment(
            opened.state.subject.payment_id,
            0,
            method_event(opened.state),
        )
        before = database_fingerprint(self.path)
        replay = None
        error = None
        try:
            replay = store.open_payment(anchor, policy)
        except BaseException as exc:
            error = exc
        self.assertIsNone(error)
        self.assertIsNotNone(replay)
        self.assertIs(replay.status, PaymentTransitionStatus.NOOP)
        self.assertEqual(replay.state, selected.state)
        self.assertEqual(database_fingerprint(self.path), before)

        divergent_anchor = confirmed_anchor(
            confirmed_at=T0 + timedelta(seconds=1),
            payment_deadline=T0 + timedelta(days=2),
        )
        with self.assertRaises(IdentityConflict):
            store.open_payment(divergent_anchor, policy)
        self.assertEqual(database_fingerprint(self.path), before)

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
                with self.assertRaises(StoreError) as raised:
                    store.open_handoff(request, optional_email_policy())
                self.assertIsInstance(raised.exception.__cause__, sqlite3.DatabaseError)
                self.assertIn(f"fault:{index}", str(raised.exception.__cause__))
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
        with self.assertRaises(StoreError) as raised:
            store.open_payment(confirmed_anchor(), payment_effect_policy())
        self.assertIsInstance(raised.exception.__cause__, sqlite3.DatabaseError)
        self.assertIn("fault:payment-workflow", str(raised.exception.__cause__))
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

    def test_apply_handoff_persists_nonoperational_cancellation_and_duplicate_noop_after_reopen(self) -> None:
        store = self.open_store()
        request = handoff_requested()
        store.open_handoff(
            request,
            HandoffEffectPolicy.default_email_disabled(),
        )
        cancelled = HandoffCancelled(
            handoff_id=request.handoff_id,
            incident_key=request.incident_key,
            cancellation_code=HandoffCancellationCode.REQUEST_WITHDRAWN,
            cancelled_at=T0 + timedelta(seconds=1),
        )
        applied = store.apply_handoff(request.handoff_id, 1, cancelled)
        self.assertIs(applied.status, HandoffTransitionStatus.APPLIED)
        self.assertEqual(database_counts(self.path)["handoff_events"], 2)
        before = database_fingerprint(self.path)
        replay = store.apply_handoff(request.handoff_id, 0, cancelled)
        self.assertIs(replay.status, HandoffTransitionStatus.NOOP)
        self.assertEqual(replay.state, applied.state)
        self.assertEqual(database_fingerprint(self.path), before)

        divergent = replace(
            cancelled,
            cancelled_at=cancelled.cancelled_at + timedelta(seconds=1),
        )
        with self.assertRaises(IdentityConflict):
            store.apply_handoff(request.handoff_id, 2, divergent)
        self.assertEqual(database_fingerprint(self.path), before)

        self.close_store(store)
        reopened = self.open_store()
        self.assertEqual(reopened.load_handoff(request.handoff_id), applied.state)

    def test_operational_handoff_events_fail_before_write_and_reopen(self) -> None:
        path = self.root / "handoff-operational-boundary.db"
        store = self.open_store(path)
        request = handoff_requested()
        opened = store.open_handoff(
            request,
            HandoffEffectPolicy.default_email_disabled(),
        )
        ack_job = new_handoff(request, opened.state.policy).effect_jobs[0]
        events = (
            HandoffAcknowledged(
                handoff_id=request.handoff_id,
                incident_key=request.incident_key,
                effect_id=ack_job.effect_id,
                receipt_id="receipt:handoff:store:forged",
                acknowledged_at=T0 + timedelta(seconds=1),
            ),
            HandoffEffectFailed(
                handoff_id=request.handoff_id,
                incident_key=request.incident_key,
                effect_id=ack_job.effect_id,
                kind=HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT,
                failure_code=HandoffEffectFailureCode.EFFECT_UNAVAILABLE,
                failed_at=T0 + timedelta(seconds=1),
            ),
        )
        before = database_fingerprint(path)
        for event in events:
            with self.subTest(event=type(event).__name__), self.assertRaises(
                UnsupportedEffect
            ):
                store.apply_handoff(request.handoff_id, 1, event)
            self.assertEqual(database_fingerprint(path), before)
        self.close_store(store)
        reopened = self.open_store(path)
        self.assertEqual(reopened.load_handoff(request.handoff_id), opened.state)
        self.assertEqual(database_counts(path)["handoff_events"], 1)
        self.assertEqual(database_counts(path)["handoff_receipts"], 0)
        self.assertEqual(
            reopened._connection.execute(
                "SELECT status FROM handoff_outbox"
            ).fetchall(),
            [("pending",)],
        )

    def test_two_connections_only_one_expected_handoff_revision_wins(self) -> None:
        first = self.open_store()
        second = self.open_store()
        request = handoff_requested()
        first.open_handoff(request, HandoffEffectPolicy.default_email_disabled())
        self.assertEqual(second.load_handoff(request.handoff_id), first.load_handoff(request.handoff_id))
        cancelled = HandoffCancelled(
            handoff_id=request.handoff_id,
            incident_key=request.incident_key,
            cancellation_code=HandoffCancellationCode.REQUEST_WITHDRAWN,
            cancelled_at=T0 + timedelta(seconds=1),
        )
        first.apply_handoff(request.handoff_id, 1, cancelled)
        stale = handoff_requested(
            handoff_id=request.handoff_id,
            incident_key="incident:store:race:stale",
            source_event_id="source:event:store:race:stale",
            reason_code=HandoffReasonCode.OPERATIONAL_REVIEW,
            requested_at=T0 + timedelta(seconds=2),
        )
        before = database_fingerprint(self.path)
        with self.assertRaises(ConcurrencyConflict):
            second.apply_handoff(request.handoff_id, 1, stale)
        self.assertEqual(database_fingerprint(self.path), before)
        self.assertEqual(second.load_handoff(request.handoff_id).cancellation, cancelled)

    def test_two_connections_only_one_expected_payment_revision_wins(self) -> None:
        first = self.open_store()
        second = self.open_store()
        initial = first.open_payment(
            confirmed_anchor(),
            payment_effect_policy(),
        ).state
        self.assertEqual(second.load_payment(initial.subject.payment_id), initial)
        first_event = method_event(
            initial,
            event_id="payment:event:store:race:first",
        )
        first.apply_payment(initial.subject.payment_id, 0, first_event)
        stale_event = PaymentMethodSelected(
            event_id="payment:event:store:race:stale",
            payment_id=initial.subject.payment_id,
            method=PaymentMethod.WISE,
            selected_at=T0 + timedelta(seconds=1),
        )
        before = database_fingerprint(self.path)
        with self.assertRaises(ConcurrencyConflict):
            second.apply_payment(initial.subject.payment_id, 0, stale_event)
        self.assertEqual(database_fingerprint(self.path), before)
        self.assertEqual(
            second.load_payment(initial.subject.payment_id).history,
            (first_event,),
        )

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

        finished = SettlementFinished(
            event_id="payment:event:store:settlement-finished",
            payment_id=payment_id,
            payment_version=confirmed.state.subject.payment_version,
            economic_signature=confirmed.state.subject.economic_signature,
            settlement_command_id=command.settlement_command_id,
            outcome=outcome(SettlementCertainty.SETTLED),
            finished_at=T0 + timedelta(seconds=6),
        )
        with self.assertRaises(UnsupportedEffect):
            store.apply_payment(payment_id, 3, finished)
        self.assertEqual(database_fingerprint(self.path), before)

        self.close_store(store)
        reopened = self.open_store()
        self.assertEqual(reopened.load_payment(payment_id), confirmed.state)

    def test_task6_loader_rejects_every_noninitial_handoff_outbox_state(self) -> None:
        cases = (
            "pending_history",
            "leased",
            "delivered_without_receipt",
            "delivered_with_receipt",
        )
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                path = self.root / f"handoff-outbox-operational-{index}.db"
                store = self.open_store(path)
                request = handoff_requested(
                    handoff_id=f"handoff:store:outbox-operational:{index}",
                    incident_key=f"incident:store:outbox-operational:{index}",
                    source_event_id=f"source:event:store:outbox-operational:{index}",
                )
                store.open_handoff(
                    request,
                    HandoffEffectPolicy.default_email_disabled(),
                )
                changed_at = (T0 + timedelta(seconds=1)).isoformat()
                if case == "pending_history":
                    store._connection.execute(
                        "UPDATE handoff_outbox SET fencing_token=1, "
                        "delivery_attempts=1, updated_at=?",
                        (changed_at,),
                    )
                elif case == "leased":
                    store._connection.execute(
                        "UPDATE handoff_outbox SET status='leased', "
                        "claim_owner='worker:synthetic', fencing_token=1, "
                        "lease_acquired_at=?, lease_expires_at=?, updated_at=?",
                        (
                            changed_at,
                            (T0 + timedelta(seconds=2)).isoformat(),
                            changed_at,
                        ),
                    )
                else:
                    receipt_hash = "f" * 64
                    store._connection.execute(
                        "UPDATE handoff_outbox SET status='delivered', "
                        "fencing_token=1, delivery_attempts=1, delivered_at=?, "
                        "receipt_hash=?, updated_at=?",
                        (changed_at, receipt_hash, changed_at),
                    )
                    if case == "delivered_with_receipt":
                        message_id = store._connection.execute(
                            "SELECT message_id FROM handoff_outbox"
                        ).fetchone()[0]
                        store._connection.execute(
                            "INSERT INTO handoff_receipts "
                            "(receipt_id, idempotency_key, message_id, receipt_json, "
                            "receipt_hash, delivered_at) VALUES (?, ?, ?, '{}', ?, ?)",
                            (
                                f"receipt:store:outbox:{index}",
                                f"receipt:store:outbox:idem:{index}",
                                message_id,
                                receipt_hash,
                                changed_at,
                            ),
                        )
                error = None
                try:
                    store.load_handoff(request.handoff_id)
                except BaseException as exc:
                    error = exc
                self.assertIsInstance(error, DataCorruption)

    def test_task6_loader_rejects_receipt_even_with_initial_pending_outbox(self) -> None:
        path = self.root / "handoff-outbox-receipt-only.db"
        store = self.open_store(path)
        request = handoff_requested(
            handoff_id="handoff:store:receipt-only",
            incident_key="incident:store:receipt-only",
            source_event_id="source:event:store:receipt-only",
        )
        opened = store.open_handoff(
            request,
            HandoffEffectPolicy.default_email_disabled(),
        )
        message_id = store._connection.execute(
            "SELECT message_id FROM main.handoff_outbox"
        ).fetchone()[0]
        delivered_at = (T0 + timedelta(seconds=1)).isoformat()
        receipt_hash = "e" * 64
        store._connection.execute("PRAGMA foreign_keys = OFF")
        store._connection.execute(
            "INSERT INTO main.handoff_receipts "
            "(receipt_id, idempotency_key, message_id, receipt_json, "
            "receipt_hash, delivered_at) VALUES (?, ?, ?, '{}', ?, ?)",
            (
                "receipt:store:receipt-only",
                "receipt:store:receipt-only:idem",
                message_id,
                receipt_hash,
                delivered_at,
            ),
        )
        store._connection.execute("PRAGMA foreign_keys = ON")
        try:
            with self.assertRaises(DataCorruption):
                store.load_handoff(request.handoff_id)
        finally:
            store._connection.execute("PRAGMA foreign_keys = OFF")
            store._connection.execute("DELETE FROM main.handoff_receipts")
            store._connection.execute("PRAGMA foreign_keys = ON")
        self.assertEqual(store.load_handoff(request.handoff_id), opened.state)

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
