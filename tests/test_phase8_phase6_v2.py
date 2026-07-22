"""Phase 8 exact additive Phase6-v2 root authentication."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from reservation_followup import schema
from reservation_followup.sqlite_store import DataCorruption, SQLiteFollowupUnitOfWork


EXPECTED_TABLES = (
    "handoff_workflows",
    "handoff_events",
    "handoff_outbox",
    "handoff_receipts",
    "payment_workflows",
    "payment_events",
    "payment_evidence_claims",
    "payment_commands",
    "payment_ledger",
    "payment_outbox",
    "payment_receipts",
    "handoff_boundary_ingress_receipts",
    "payment_boundary_ingress_receipts",
    "followup_e2e_effect_authority",
)


class Phase6V2SchemaTests(unittest.TestCase):
    def database_path(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name) / "phase6-v2.sqlite3"

    @staticmethod
    def names(path: Path) -> tuple[str, ...]:
        with sqlite3.connect(path) as connection:
            return tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
                )
            )

    def test_empty_root_creates_exact_fourteen_strict_tables(self) -> None:
        self.assertEqual(getattr(schema, "PHASE6_V2_TABLES", None), EXPECTED_TABLES)
        path = self.database_path()
        store = getattr(SQLiteFollowupUnitOfWork, "open_v2")(path)
        store.close()
        self.assertEqual(self.names(path), EXPECTED_TABLES)
        with sqlite3.connect(path) as connection:
            strict = {
                row[1]: row[5]
                for row in connection.execute("PRAGMA table_list")
                if row[1] in EXPECTED_TABLES
            }
        self.assertEqual(strict, {name: 1 for name in EXPECTED_TABLES})

    def test_v1_root_is_not_migrated_or_accepted(self) -> None:
        path = self.database_path()
        SQLiteFollowupUnitOfWork.open(path).close()
        before = path.read_bytes()
        with self.assertRaises(DataCorruption):
            getattr(SQLiteFollowupUnitOfWork, "open_v2")(path)
        self.assertEqual(path.read_bytes(), before)

    def test_extra_schema_objects_and_table_ddl_drift_fail_closed(self) -> None:
        for mutation in ("table", "index", "trigger", "ddl"):
            with self.subTest(mutation=mutation):
                path = self.database_path()
                if mutation == "ddl":
                    sql = getattr(schema, "render_sqlite_v2")().replace(
                        "operation_id TEXT NOT NULL",
                        "operation_id TEXT NOT NULL CHECK (length(operation_id) = 64)",
                        1,
                    )
                    self.assertNotEqual(sql, getattr(schema, "render_sqlite_v2")())
                    with sqlite3.connect(path) as connection:
                        connection.executescript(sql)
                else:
                    getattr(SQLiteFollowupUnitOfWork, "open_v2")(path).close()
                    with sqlite3.connect(path) as connection:
                        if mutation == "table":
                            connection.execute("CREATE TABLE unexpected_root (id TEXT) STRICT")
                        elif mutation == "index":
                            connection.execute(
                                "CREATE INDEX unexpected_index ON payment_workflows (updated_at)"
                            )
                        else:
                            connection.execute(
                                "CREATE TRIGGER unexpected_trigger "
                                "AFTER UPDATE ON payment_workflows BEGIN SELECT 1; END"
                            )
                with self.assertRaises(DataCorruption):
                    getattr(SQLiteFollowupUnitOfWork, "open_v2")(path)


if __name__ == "__main__":
    unittest.main()
