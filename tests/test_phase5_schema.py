from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import hashlib
from pathlib import Path
import re
import sqlite3
import unittest

from reservation_execution.schema import (
    SCHEMA_VERSION,
    ColumnContract,
    TableContract,
    render_postgresql,
    render_sqlite,
    schema_contract,
    schema_hash,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = "2027-01-01T00:00:00+00:00"
LATER = "2027-01-01T00:01:00+00:00"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

EXPECTED_COLUMNS = {
    "schema_migrations": ("version", "schema_hash", "applied_at"),
    "workflows": (
        "workflow_id",
        "revision",
        "state_type",
        "state_json",
        "state_hash",
        "created_at",
        "updated_at",
    ),
    "domain_events": (
        "event_id",
        "workflow_id",
        "revision",
        "occurred_at",
        "event_type",
        "event_json",
        "event_hash",
    ),
    "reservation_commands": (
        "command_id",
        "idempotency_key",
        "workflow_id",
        "draft_id",
        "draft_version",
        "subject_signature",
        "operation",
        "command_json",
        "command_hash",
        "created_at",
    ),
    "execution_ledger": (
        "command_id",
        "status",
        "claim_owner",
        "fencing_token",
        "lease_acquired_at",
        "lease_expires_at",
        "claim_count",
        "preparation_failures",
        "dispatch_slots_consumed",
        "dispatch_request_hash",
        "dispatch_fenced_at",
        "outcome_json",
        "outcome_hash",
        "updated_at",
    ),
    "outbox_messages": (
        "message_id",
        "idempotency_key",
        "workflow_id",
        "command_id",
        "kind",
        "template_id",
        "payload_json",
        "payload_hash",
        "status",
        "claim_owner",
        "fencing_token",
        "lease_acquired_at",
        "lease_expires_at",
        "delivery_attempts",
        "delivered_at",
        "receipt_hash",
        "created_at",
        "updated_at",
    ),
}

EXPECTED_PRIMARY_KEYS = {
    "schema_migrations": ("version",),
    "workflows": ("workflow_id",),
    "domain_events": ("event_id",),
    "reservation_commands": ("command_id",),
    "execution_ledger": ("command_id",),
    "outbox_messages": ("message_id",),
}

EXPECTED_FOREIGN_KEYS = {
    "schema_migrations": set(),
    "workflows": set(),
    "domain_events": {("workflow_id", "workflows", "workflow_id")},
    "reservation_commands": {("workflow_id", "workflows", "workflow_id")},
    "execution_ledger": {
        ("command_id", "reservation_commands", "command_id")
    },
    "outbox_messages": {
        ("workflow_id", "workflows", "workflow_id"),
        ("command_id", "reservation_commands", "command_id"),
    },
}

EXPECTED_UNIQUES = {
    "schema_migrations": set(),
    "workflows": set(),
    "domain_events": {("workflow_id", "revision")},
    "reservation_commands": {
        ("idempotency_key",),
        ("workflow_id",),
        ("workflow_id", "draft_id", "draft_version", "operation"),
    },
    "execution_ledger": set(),
    "outbox_messages": {("idempotency_key",)},
}


class Phase5SchemaTests(unittest.TestCase):
    def open_database(self) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(render_sqlite())
        return connection

    def insert_workflow(
        self,
        connection: sqlite3.Connection,
        suffix: str,
        *,
        revision: int = 0,
        state_hash: str = HASH_A,
        created_at: str = NOW,
        updated_at: str = NOW,
    ) -> str:
        workflow_id = f"workflow:schema:{suffix}"
        connection.execute(
            "INSERT INTO workflows "
            "(workflow_id, revision, state_type, state_json, state_hash, "
            "created_at, updated_at) VALUES (?, ?, 'collecting_trip_context', "
            "'{}', ?, ?, ?)",
            (workflow_id, revision, state_hash, created_at, updated_at),
        )
        return workflow_id

    def insert_command(
        self,
        connection: sqlite3.Connection,
        suffix: str,
        workflow_id: str,
        *,
        idempotency_key: str | None = None,
        draft_version: int = 1,
        subject_signature: str = HASH_B,
        operation: str = "reserve_lodging",
        command_hash: str = HASH_C,
        created_at: str = NOW,
    ) -> str:
        command_id = f"command:schema:{suffix}"
        connection.execute(
            "INSERT INTO reservation_commands "
            "(command_id, idempotency_key, workflow_id, draft_id, draft_version, "
            "subject_signature, operation, command_json, command_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)",
            (
                command_id,
                idempotency_key or f"idem:schema:{suffix}",
                workflow_id,
                f"draft:schema:{suffix}",
                draft_version,
                subject_signature,
                operation,
                command_hash,
                created_at,
            ),
        )
        return command_id

    def create_command_graph(
        self, connection: sqlite3.Connection, suffix: str
    ) -> str:
        workflow_id = self.insert_workflow(connection, suffix)
        return self.insert_command(connection, suffix, workflow_id)

    def insert_ledger(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        **overrides: object,
    ) -> None:
        values: dict[str, object] = {
            "command_id": command_id,
            "status": "queued",
            "claim_owner": None,
            "fencing_token": 0,
            "lease_acquired_at": None,
            "lease_expires_at": None,
            "claim_count": 0,
            "preparation_failures": 0,
            "dispatch_slots_consumed": 0,
            "dispatch_request_hash": None,
            "dispatch_fenced_at": None,
            "outcome_json": None,
            "outcome_hash": None,
            "updated_at": NOW,
        }
        values.update(overrides)
        connection.execute(
            "INSERT INTO execution_ledger "
            "(command_id, status, claim_owner, fencing_token, lease_acquired_at, "
            "lease_expires_at, claim_count, preparation_failures, "
            "dispatch_slots_consumed, dispatch_request_hash, dispatch_fenced_at, "
            "outcome_json, outcome_hash, updated_at) VALUES "
            "(:command_id, :status, :claim_owner, :fencing_token, "
            ":lease_acquired_at, :lease_expires_at, :claim_count, "
            ":preparation_failures, :dispatch_slots_consumed, "
            ":dispatch_request_hash, :dispatch_fenced_at, :outcome_json, "
            ":outcome_hash, :updated_at)",
            values,
        )

    def insert_outbox(
        self,
        connection: sqlite3.Connection,
        workflow_id: str,
        suffix: str,
        **overrides: object,
    ) -> None:
        values: dict[str, object] = {
            "message_id": f"message:schema:{suffix}",
            "idempotency_key": f"outbox-idem:schema:{suffix}",
            "workflow_id": workflow_id,
            "command_id": None,
            "kind": "summary_presented",
            "template_id": "reservation.summary.v1",
            "payload_json": "{}",
            "payload_hash": HASH_A,
            "status": "pending",
            "claim_owner": None,
            "fencing_token": 0,
            "lease_acquired_at": None,
            "lease_expires_at": None,
            "delivery_attempts": 0,
            "delivered_at": None,
            "receipt_hash": None,
            "created_at": NOW,
            "updated_at": NOW,
        }
        values.update(overrides)
        connection.execute(
            "INSERT INTO outbox_messages "
            "(message_id, idempotency_key, workflow_id, command_id, kind, "
            "template_id, payload_json, payload_hash, status, claim_owner, "
            "fencing_token, lease_acquired_at, lease_expires_at, delivery_attempts, "
            "delivered_at, receipt_hash, created_at, updated_at) VALUES "
            "(:message_id, :idempotency_key, :workflow_id, :command_id, :kind, "
            ":template_id, :payload_json, :payload_hash, :status, :claim_owner, "
            ":fencing_token, :lease_acquired_at, :lease_expires_at, "
            ":delivery_attempts, :delivered_at, :receipt_hash, :created_at, "
            ":updated_at)",
            values,
        )

    def test_contract_classes_and_six_table_column_universes_are_exact(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(ColumnContract)),
            ("name", "sqlite_type", "postgresql_type", "nullable", "check"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(TableContract)),
            ("name", "columns", "table_constraints"),
        )
        sample = ColumnContract("sample", "TEXT", "text")
        with self.assertRaises(FrozenInstanceError):
            sample.name = "changed"  # type: ignore[misc]
        self.assertFalse(hasattr(sample, "__dict__"))

        contract = schema_contract()
        self.assertEqual(tuple(table.name for table in contract), tuple(EXPECTED_COLUMNS))
        self.assertEqual(
            {table.name: tuple(column.name for column in table.columns) for table in contract},
            EXPECTED_COLUMNS,
        )
        self.assertEqual(len(contract), 6)

    def test_generated_sql_matches_tracked_artifacts_and_is_deterministic(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 5)
        sqlite_sql = render_sqlite()
        postgresql_sql = render_postgresql()
        self.assertEqual((ROOT / "schemas/phase5/sqlite.sql").read_text(encoding="utf-8"), sqlite_sql)
        self.assertEqual(
            (ROOT / "schemas/phase5/postgresql.sql").read_text(encoding="utf-8"),
            postgresql_sql,
        )
        self.assertEqual(render_sqlite(), sqlite_sql)
        self.assertEqual(render_postgresql(), postgresql_sql)
        self.assertTrue(sqlite_sql.endswith("\n"))
        self.assertTrue(postgresql_sql.endswith("\n"))

        for dialect, sql in (("sqlite", sqlite_sql), ("postgresql", postgresql_sql)):
            with self.subTest(dialect=dialect):
                self.assertEqual(len(re.findall(r"(?m)^CREATE TABLE ", sql)), 6)
                self.assertEqual(sql.count(";"), 6)
                self.assertIsNone(
                    re.search(
                        r"\b(?:INSERT|UPDATE|DELETE|MERGE|CREATE\s+TRIGGER|"
                        r"CREATE\s+EXTENSION)\b",
                        sql,
                        re.IGNORECASE,
                    )
                )
                self.assertNotRegex(sql, r"(?i)\bCASCADE\b")
                expected_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                self.assertEqual(schema_hash(dialect), expected_hash)
                self.assertRegex(schema_hash(dialect), r"\A[0-9a-f]{64}\Z")

        with self.assertRaises(ValueError):
            schema_hash("mysql")

    def test_sqlite_schema_executes_and_has_exact_columns_and_types(self) -> None:
        connection = self.open_database()
        names = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid"
            )
        )
        self.assertEqual(names, tuple(EXPECTED_COLUMNS))
        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            with self.subTest(table=table_name):
                table_info = list(connection.execute(f"PRAGMA table_info('{table_name}')"))
                self.assertEqual(tuple(row[1] for row in table_info), expected_columns)
                self.assertTrue(all(row[2] in {"TEXT", "INTEGER"} for row in table_info))

    def test_sqlite_primary_foreign_unique_keys_and_no_triggers_are_exact(self) -> None:
        connection = self.open_database()
        for table_name in EXPECTED_COLUMNS:
            with self.subTest(table=table_name):
                table_info = list(connection.execute(f"PRAGMA table_info('{table_name}')"))
                primary_key = tuple(
                    row[1] for row in sorted(table_info, key=lambda row: row[5]) if row[5]
                )
                self.assertEqual(primary_key, EXPECTED_PRIMARY_KEYS[table_name])
                by_name = {row[1]: row for row in table_info}
                for primary_column in primary_key:
                    self.assertEqual(by_name[primary_column][3], 1)

                foreign_rows = list(
                    connection.execute(f"PRAGMA foreign_key_list('{table_name}')")
                )
                foreign_keys = {(row[3], row[2], row[4]) for row in foreign_rows}
                self.assertEqual(foreign_keys, EXPECTED_FOREIGN_KEYS[table_name])
                self.assertTrue(all(row[5] == "NO ACTION" and row[6] == "NO ACTION" for row in foreign_rows))

                unique_columns: set[tuple[str, ...]] = set()
                for index_row in connection.execute(f"PRAGMA index_list('{table_name}')"):
                    if index_row[2] and index_row[3] == "u":
                        unique_columns.add(
                            tuple(
                                row[2]
                                for row in connection.execute(
                                    f"PRAGMA index_info('{index_row[1]}')"
                                )
                            )
                        )
                self.assertEqual(unique_columns, EXPECTED_UNIQUES[table_name])

        triggers = list(
            connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        )
        self.assertEqual(triggers, [])
        self.assertNotIn("CASCADE", render_sqlite().upper())

    def test_sqlite_foreign_keys_and_named_logical_uniques_fail_closed(self) -> None:
        connection = self.open_database()
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO domain_events "
                "(event_id, workflow_id, revision, occurred_at, event_type, "
                "event_json, event_hash) VALUES "
                "('event:missing', 'workflow:missing', 1, ?, 'Started', '{}', ?)",
                (NOW, HASH_A),
            )

        first_workflow = self.insert_workflow(connection, "unique-one")
        second_workflow = self.insert_workflow(connection, "unique-two")
        self.insert_command(
            connection,
            "unique-one",
            first_workflow,
            idempotency_key="idem:schema:shared",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_command(
                connection,
                "unique-two",
                second_workflow,
                idempotency_key="idem:schema:shared",
            )

        connection.execute(
            "INSERT INTO domain_events "
            "(event_id, workflow_id, revision, occurred_at, event_type, event_json, "
            "event_hash) VALUES ('event:one', ?, 1, ?, 'Started', '{}', ?)",
            (first_workflow, NOW, HASH_A),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO domain_events "
                "(event_id, workflow_id, revision, occurred_at, event_type, "
                "event_json, event_hash) VALUES "
                "('event:two', ?, 1, ?, 'StartedAgain', '{}', ?)",
                (first_workflow, NOW, HASH_B),
            )

        self.insert_outbox(
            connection,
            first_workflow,
            "unique-outbox-one",
            idempotency_key="outbox-idem:schema:shared",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_outbox(
                connection,
                second_workflow,
                "unique-outbox-two",
                idempotency_key="outbox-idem:schema:shared",
            )

    def test_all_hash_columns_have_portable_closed_checks(self) -> None:
        expected_hash_columns = {
            "schema_migrations": {"schema_hash"},
            "workflows": {"state_hash"},
            "domain_events": {"event_hash"},
            "reservation_commands": {"subject_signature", "command_hash"},
            "execution_ledger": {"dispatch_request_hash", "outcome_hash"},
            "outbox_messages": {"payload_hash", "receipt_hash"},
        }
        for table in schema_contract():
            columns = {column.name: column for column in table.columns}
            for column_name in expected_hash_columns[table.name]:
                with self.subTest(table=table.name, column=column_name):
                    check = columns[column_name].check
                    self.assertIsNotNone(check)
                    assert check is not None
                    self.assertIn("length(", check)
                    self.assertIn("lower(", check)
                    self.assertIn("replace(", check)
                    self.assertNotIn("GLOB", check.upper())
                    self.assertNotIn("REGEXP", check.upper())

    def test_malformed_hashes_and_non_utc_sqlite_timestamps_are_rejected(self) -> None:
        connection = self.open_database()
        for index, malformed_hash in enumerate(("a" * 63, "A" * 64, "g" * 64)):
            with self.subTest(hash=malformed_hash[:4]):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_workflow(
                        connection,
                        f"bad-hash-{index}",
                        state_hash=malformed_hash,
                    )

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO schema_migrations (version, schema_hash, applied_at) "
                "VALUES (5, ?, ?)",
                ("z" * 64, NOW),
            )
        malformed_times = (
            "2027-01-01T00:00:00Z",
            "junk+00:00",
            "2027-01-01 00:00:00+00:00",
            "2027-01-01T00:00+00:00",
            "2027-01-01T00:00:00+03:00",
            "2027-01-01T00:00:00.00001+00:00",
        )
        for index, malformed_time in enumerate(malformed_times):
            with self.subTest(timestamp=malformed_time):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_workflow(
                        connection,
                        f"bad-time-{index}",
                        created_at=malformed_time,
                    )

        self.insert_workflow(
            connection,
            "microsecond-time",
            created_at="2027-01-01T00:00:00.000001+00:00",
        )

        dispatch_command = self.create_command_graph(connection, "bad-dispatch-hash")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_ledger(
                connection,
                dispatch_command,
                status="dispatch_fenced",
                claim_owner="worker:schema:a",
                lease_acquired_at=NOW,
                lease_expires_at=LATER,
                claim_count=1,
                dispatch_slots_consumed=1,
                dispatch_request_hash="x" * 64,
                dispatch_fenced_at=NOW,
            )

        outcome_command = self.create_command_graph(connection, "bad-outcome-hash")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_ledger(
                connection,
                outcome_command,
                status="outcome_recorded",
                outcome_json='{"kind":"not_called"}',
                outcome_hash="A" * 64,
            )

        workflow_id = self.insert_workflow(connection, "bad-receipt-hash")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_outbox(
                connection,
                workflow_id,
                "bad-receipt-hash",
                status="delivered",
                delivery_attempts=1,
                delivered_at=NOW,
                receipt_hash="0" * 63,
            )

    def test_unknown_operation_ledger_status_outbox_status_and_kind_are_rejected(self) -> None:
        connection = self.open_database()
        workflow_id = self.insert_workflow(connection, "bad-operation")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_command(
                connection,
                "bad-operation",
                workflow_id,
                operation="create_reservation",
            )

        command_id = self.insert_command(
            connection, "bad-ledger-status", workflow_id
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_ledger(connection, command_id, status="unknown")

        for index, overrides in enumerate(
            ({"status": "unknown"}, {"kind": "unknown"})
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_outbox(
                        connection,
                        workflow_id,
                        f"unknown-outbox-{index}",
                        **overrides,
                    )

        for index, operation in enumerate(
            ("reserve_lodging", "book_activity", "reserve_package")
        ):
            accepted_workflow = self.insert_workflow(
                connection, f"operation-{index}"
            )
            self.insert_command(
                connection,
                f"operation-{index}",
                accepted_workflow,
                operation=operation,
            )

    def test_revision_and_all_counters_reject_out_of_range_values(self) -> None:
        connection = self.open_database()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_workflow(connection, "negative-revision", revision=-1)

        workflow_id = self.insert_workflow(connection, "event-revision")
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO domain_events "
                "(event_id, workflow_id, revision, occurred_at, event_type, "
                "event_json, event_hash) VALUES "
                "('event:negative', ?, -1, ?, 'Started', '{}', ?)",
                (workflow_id, NOW, HASH_A),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_command(
                connection,
                "zero-draft-version",
                workflow_id,
                draft_version=0,
            )

        invalid_ledger_counters = (
            {"fencing_token": -1},
            {"claim_count": -1},
            {"preparation_failures": -1},
            {"preparation_failures": 4},
            {"dispatch_slots_consumed": -1},
            {"dispatch_slots_consumed": 2},
        )
        for index, overrides in enumerate(invalid_ledger_counters):
            command_id = self.create_command_graph(
                connection, f"ledger-counter-{index}"
            )
            with self.subTest(overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_ledger(connection, command_id, **overrides)

        for index, overrides in enumerate(
            ({"fencing_token": -1}, {"delivery_attempts": -1})
        ):
            counter_workflow = self.insert_workflow(
                connection, f"outbox-counter-{index}"
            )
            with self.subTest(overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_outbox(
                        connection,
                        counter_workflow,
                        f"outbox-counter-{index}",
                        **overrides,
                    )

    def test_execution_ledger_rejects_every_invalid_cross_constraint_family(self) -> None:
        lease = {
            "claim_owner": "worker:schema:a",
            "lease_acquired_at": NOW,
            "lease_expires_at": LATER,
        }
        dispatch = {
            "dispatch_slots_consumed": 1,
            "dispatch_request_hash": HASH_A,
            "dispatch_fenced_at": NOW,
        }
        outcome = {"outcome_json": '{"kind":"not_called"}', "outcome_hash": HASH_B}
        cases: tuple[dict[str, object], ...] = (
            {"status": "preparing", "claim_owner": "worker:schema:a", "lease_acquired_at": NOW, "claim_count": 1},
            {"dispatch_request_hash": HASH_A},
            {"dispatch_fenced_at": NOW},
            {"status": "dispatch_fenced", **lease, "claim_count": 1, "dispatch_slots_consumed": 1, "dispatch_fenced_at": NOW},
            {"status": "outcome_recorded", "outcome_json": "{}"},
            {"status": "outcome_recorded", "outcome_hash": HASH_A},
            {"status": "queued", **lease},
            {"status": "queued", **dispatch},
            {"status": "queued", **outcome},
            {"status": "preparing", "claim_count": 1},
            {"status": "preparing", **lease, "claim_count": 0},
            {"status": "preparing", **lease, "claim_count": 1, "fencing_token": 0},
            {
                "status": "preparing",
                "claim_owner": "worker:schema:a",
                "lease_acquired_at": LATER,
                "lease_expires_at": NOW,
                "claim_count": 1,
                "fencing_token": 1,
            },
            {"status": "preparing", **lease, "claim_count": 1, **outcome},
            {"status": "dispatch_fenced", "claim_count": 1, **dispatch},
            {"status": "dispatch_fenced", **lease, "claim_count": 0, **dispatch},
            {"status": "dispatch_fenced", **lease, "claim_count": 1},
            {"status": "dispatch_fenced", **lease, "claim_count": 1, **dispatch, **outcome},
            {"status": "outcome_recorded", **lease, **outcome},
            {"status": "outcome_recorded"},
            {"status": "manual_review", **outcome},
            {"status": "manual_review", **dispatch},
            {"status": "manual_review", **lease, **dispatch, **outcome},
        )
        connection = self.open_database()
        for index, overrides in enumerate(cases):
            command_id = self.create_command_graph(connection, f"ledger-cross-{index}")
            with self.subTest(index=index, overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_ledger(connection, command_id, **overrides)

    def test_outcome_recorded_accepts_zero_or_one_dispatch_but_manual_review_requires_one(self) -> None:
        connection = self.open_database()
        before_dispatch = self.create_command_graph(connection, "outcome-zero")
        self.insert_ledger(
            connection,
            before_dispatch,
            status="outcome_recorded",
            outcome_json='{"kind":"not_called"}',
            outcome_hash=HASH_A,
        )
        after_dispatch = self.create_command_graph(connection, "outcome-one")
        self.insert_ledger(
            connection,
            after_dispatch,
            status="outcome_recorded",
            dispatch_slots_consumed=1,
            dispatch_request_hash=HASH_B,
            dispatch_fenced_at=NOW,
            outcome_json='{"kind":"effect_confirmed"}',
            outcome_hash=HASH_C,
        )
        manual_review = self.create_command_graph(connection, "manual-review")
        self.insert_ledger(
            connection,
            manual_review,
            status="manual_review",
            dispatch_slots_consumed=1,
            dispatch_request_hash=HASH_A,
            dispatch_fenced_at=NOW,
            outcome_json='{"kind":"called_unknown"}',
            outcome_hash=HASH_B,
        )
        rows = list(
            connection.execute(
                "SELECT status, dispatch_slots_consumed FROM execution_ledger "
                "ORDER BY command_id"
            )
        )
        self.assertEqual(
            rows,
            [("manual_review", 1), ("outcome_recorded", 1), ("outcome_recorded", 0)],
        )

    def test_outbox_accepts_exact_pending_leased_delivered_matrix(self) -> None:
        connection = self.open_database()
        pending_workflow = self.insert_workflow(connection, "outbox-pending")
        self.insert_outbox(connection, pending_workflow, "outbox-pending")

        leased_workflow = self.insert_workflow(connection, "outbox-leased")
        self.insert_outbox(
            connection,
            leased_workflow,
            "outbox-leased",
            status="leased",
            claim_owner="delivery:schema:a",
            fencing_token=1,
            lease_acquired_at=NOW,
            lease_expires_at=LATER,
        )

        delivered_workflow = self.insert_workflow(connection, "outbox-delivered")
        self.insert_outbox(
            connection,
            delivered_workflow,
            "outbox-delivered",
            status="delivered",
            delivery_attempts=1,
            delivered_at=NOW,
            receipt_hash=HASH_C,
        )
        rows = list(
            connection.execute(
                "SELECT status FROM outbox_messages ORDER BY status"
            )
        )
        self.assertEqual(rows, [("delivered",), ("leased",), ("pending",)])

    def test_outbox_rejects_every_invalid_cross_constraint_family(self) -> None:
        lease = {
            "claim_owner": "delivery:schema:a",
            "lease_acquired_at": NOW,
            "lease_expires_at": LATER,
        }
        receipt = {"delivered_at": NOW, "receipt_hash": HASH_C}
        cases: tuple[dict[str, object], ...] = (
            {"status": "leased", "claim_owner": "delivery:schema:a"},
            {"status": "delivered", "delivery_attempts": 1, "delivered_at": NOW},
            {"status": "delivered", "delivery_attempts": 1, "receipt_hash": HASH_C},
            {"status": "pending", **lease},
            {"status": "pending", "delivery_attempts": 1, **receipt},
            {"status": "leased"},
            {"status": "leased", **lease, "fencing_token": 0},
            {
                "status": "leased",
                "claim_owner": "delivery:schema:a",
                "lease_acquired_at": LATER,
                "lease_expires_at": NOW,
                "fencing_token": 1,
            },
            {"status": "leased", **lease, "delivery_attempts": 1, **receipt},
            {"status": "delivered", **lease, "delivery_attempts": 1, **receipt},
            {"status": "delivered", "delivery_attempts": 1},
            {"status": "delivered", "delivery_attempts": 0, **receipt},
        )
        connection = self.open_database()
        for index, overrides in enumerate(cases):
            workflow_id = self.insert_workflow(connection, f"outbox-cross-{index}")
            with self.subTest(index=index, overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_outbox(
                        connection,
                        workflow_id,
                        f"outbox-cross-{index}",
                        **overrides,
                    )

    def test_postgresql_is_text_only_with_portable_types_and_logical_identities(self) -> None:
        sqlite_sql = render_sqlite()
        postgresql_sql = render_postgresql()
        self.assertIn(" bigint NOT NULL", postgresql_sql)
        self.assertIn(" timestamptz NOT NULL", postgresql_sql)
        self.assertIn(" timestamptz", postgresql_sql)
        self.assertNotRegex(
            postgresql_sql,
            r"(?i)\b(?:PRAGMA|AUTOINCREMENT|GLOB)\b",
        )
        self.assertNotIn("CREATE TYPE", postgresql_sql.upper())
        self.assertNotIn("JSONB", postgresql_sql.upper())

        logical_constraints = (
            "CONSTRAINT pk_schema_migrations PRIMARY KEY (version)",
            "CONSTRAINT pk_workflows PRIMARY KEY (workflow_id)",
            "CONSTRAINT pk_domain_events PRIMARY KEY (event_id)",
            "CONSTRAINT fk_domain_events_workflow FOREIGN KEY (workflow_id) REFERENCES workflows (workflow_id)",
            "CONSTRAINT uq_domain_events_workflow_revision UNIQUE (workflow_id, revision)",
            "CONSTRAINT pk_reservation_commands PRIMARY KEY (command_id)",
            "CONSTRAINT fk_reservation_commands_workflow FOREIGN KEY (workflow_id) REFERENCES workflows (workflow_id)",
            "CONSTRAINT uq_reservation_commands_idempotency_key UNIQUE (idempotency_key)",
            "CONSTRAINT uq_reservation_commands_workflow UNIQUE (workflow_id)",
            "CONSTRAINT uq_reservation_commands_identity UNIQUE (workflow_id, draft_id, draft_version, operation)",
            "CONSTRAINT pk_execution_ledger PRIMARY KEY (command_id)",
            "CONSTRAINT fk_execution_ledger_command FOREIGN KEY (command_id) REFERENCES reservation_commands (command_id)",
            "CONSTRAINT pk_outbox_messages PRIMARY KEY (message_id)",
            "CONSTRAINT fk_outbox_messages_workflow FOREIGN KEY (workflow_id) REFERENCES workflows (workflow_id)",
            "CONSTRAINT fk_outbox_messages_command FOREIGN KEY (command_id) REFERENCES reservation_commands (command_id)",
            "CONSTRAINT uq_outbox_messages_idempotency_key UNIQUE (idempotency_key)",
        )
        for constraint in logical_constraints:
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, sqlite_sql)
                self.assertIn(constraint, postgresql_sql)
        for table_name, column_names in EXPECTED_COLUMNS.items():
            self.assertIn(f"CREATE TABLE {table_name} (", postgresql_sql)
            for column_name in column_names:
                self.assertRegex(
                    postgresql_sql,
                    rf"(?m)^    {re.escape(column_name)} (?:text|bigint|timestamptz)\b",
                )


if __name__ == "__main__":
    unittest.main()
