"""Phase 8 exact additive Phase5-v6 root authentication."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from reservation_execution import schema
from reservation_execution.sqlite_store import DataCorruption, SQLiteUnitOfWork


EXPECTED_TABLES = (
    "schema_migrations",
    "workflows",
    "domain_events",
    "reservation_commands",
    "execution_ledger",
    "outbox_messages",
    "reservation_boundary_ingress_receipts",
    "reservation_e2e_effect_authority",
)


class Phase5V6SchemaTests(unittest.TestCase):
    def database_path(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name) / "phase5-v6.sqlite3"

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

    def test_empty_root_creates_exact_eight_strict_tables(self) -> None:
        self.assertEqual(getattr(schema, "PHASE5_V6_TABLES", None), EXPECTED_TABLES)
        path = self.database_path()
        store = getattr(SQLiteUnitOfWork, "open_v6")(path)
        store.close()
        self.assertEqual(self.names(path), EXPECTED_TABLES)
        with sqlite3.connect(path) as connection:
            strict = {
                row[1]: row[5]
                for row in connection.execute("PRAGMA table_list")
                if row[1] in EXPECTED_TABLES
            }
            migration = tuple(
                connection.execute(
                    "SELECT version, schema_hash FROM schema_migrations"
                )
            )
        self.assertEqual(strict, {name: 1 for name in EXPECTED_TABLES})
        self.assertEqual(len(migration), 1)
        self.assertEqual(migration[0][0], 6)
        self.assertEqual(len(migration[0][1]), 64)

    def test_v5_root_is_not_migrated_or_accepted(self) -> None:
        path = self.database_path()
        SQLiteUnitOfWork.open(path).close()
        before = path.read_bytes()
        with self.assertRaises(DataCorruption):
            getattr(SQLiteUnitOfWork, "open_v6")(path)
        self.assertEqual(path.read_bytes(), before)

    def test_extra_schema_objects_and_table_ddl_drift_fail_closed(self) -> None:
        for mutation in ("table", "index", "trigger", "ddl"):
            with self.subTest(mutation=mutation):
                path = self.database_path()
                if mutation == "ddl":
                    sql = getattr(schema, "render_sqlite_v6")().replace(
                        "operation_id TEXT NOT NULL",
                        "operation_id TEXT NOT NULL CHECK (length(operation_id) = 64)",
                        1,
                    )
                    self.assertNotEqual(sql, getattr(schema, "render_sqlite_v6")())
                    with sqlite3.connect(path) as connection:
                        connection.executescript(sql)
                        connection.execute(
                            "INSERT INTO schema_migrations "
                            "(version, schema_hash, applied_at) VALUES (6, ?, ?)",
                            ("0" * 64, "2027-01-01T00:00:00+00:00"),
                        )
                else:
                    getattr(SQLiteUnitOfWork, "open_v6")(path).close()
                    with sqlite3.connect(path) as connection:
                        if mutation == "table":
                            connection.execute("CREATE TABLE unexpected_root (id TEXT) STRICT")
                        elif mutation == "index":
                            connection.execute(
                                "CREATE INDEX unexpected_index ON workflows (updated_at)"
                            )
                        else:
                            connection.execute(
                                "CREATE TRIGGER unexpected_trigger AFTER UPDATE ON workflows "
                                "BEGIN SELECT 1; END"
                            )
                with self.assertRaises(DataCorruption):
                    getattr(SQLiteUnitOfWork, "open_v6")(path)


if __name__ == "__main__":
    unittest.main()
