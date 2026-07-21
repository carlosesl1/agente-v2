"""Phase 7 six-table schema contract."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

from reservation_boundary.schema import (
    SCHEMA_VERSION,
    TABLE_NAMES,
    render_postgresql,
    render_sqlite,
    schema_hash,
)


ROOT = Path(__file__).resolve().parents[1]
SQLITE_PATH = ROOT / "schemas/phase7/sqlite.sql"
POSTGRESQL_PATH = ROOT / "schemas/phase7/postgresql.sql"


class Phase7SchemaTests(unittest.TestCase):
    def test_artifacts_are_generated_and_check_mode_is_clean(self) -> None:
        self.assertEqual(SQLITE_PATH.read_text(), render_sqlite())
        self.assertEqual(POSTGRESQL_PATH.read_text(), render_postgresql())
        result = subprocess.run(
            [sys.executable, "-B", "scripts/generate_phase7_schema.py", "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sqlite_has_exact_six_strict_tables_and_foreign_keys(self) -> None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(render_sqlite())
        rows = connection.execute("PRAGMA table_list").fetchall()
        actual = {
            row[1]: row[5]
            for row in rows
            if row[1] not in {"sqlite_schema", "sqlite_temp_schema"}
        }
        self.assertEqual(tuple(sorted(actual)), tuple(sorted(TABLE_NAMES)))
        self.assertEqual(set(actual.values()), {1})
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        outbox_fks = connection.execute("PRAGMA foreign_key_list(boundary_outbox)").fetchall()
        grouped: dict[int, list[tuple[str, str]]] = {}
        for row in outbox_fks:
            grouped.setdefault(row[0], []).append((row[3], row[4]))
        self.assertIn(
            [("lead_key", "lead_key"), ("event_id", "event_id"), ("command_id", "command_id")],
            grouped.values(),
        )

    def test_columns_and_primary_keys_are_closed(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(render_sqlite())
        expected = {
            "boundary_state": (
                "lead_key",
                "version",
                "state_json",
                "state_hash",
                "fencing_token",
                "created_at",
                "updated_at",
            ),
            "boundary_events": (
                "lead_key",
                "event_id",
                "event_hash",
                "commit_hash",
                "state_version",
                "occurred_at",
            ),
            "boundary_commands": (
                "command_id",
                "lead_key",
                "event_id",
                "command_type",
                "command_json",
                "command_hash",
                "created_at",
            ),
            "boundary_outbox": (
                "message_id",
                "idempotency_key",
                "lead_key",
                "event_id",
                "workflow_id",
                "command_id",
                "kind",
                "template_id",
                "payload_json",
                "payload_hash",
                "created_at",
            ),
            "legacy_import_claims": (
                "lead_key",
                "snapshot_hash",
                "disposition",
                "state_hash",
                "claimed_at",
            ),
            "decision_comparisons": (
                "comparison_id",
                "lead_key",
                "event_id",
                "old_hash",
                "new_hash",
                "severity",
                "changed_fields_json",
                "created_at",
            ),
        }
        for table, columns in expected.items():
            with self.subTest(table=table):
                info = connection.execute(f"PRAGMA table_info({table})").fetchall()
                self.assertEqual(tuple(row[1] for row in info), columns)
                self.assertGreater(sum(row[5] for row in info), 0)

    def test_postgresql_is_static_only_and_dialect_specific(self) -> None:
        sql = render_postgresql()
        self.assertEqual(sql.count("CREATE TABLE"), 6)
        self.assertIn("jsonb", sql)
        self.assertIn("timestamptz", sql)
        self.assertNotIn("STRICT", sql)
        self.assertNotIn("sqlite", sql.casefold())
        self.assertEqual(SCHEMA_VERSION, 7)
        self.assertEqual(
            schema_hash("sqlite"),
            hashlib.sha256(render_sqlite().encode()).hexdigest(),
        )
        self.assertEqual(
            schema_hash("postgresql"),
            hashlib.sha256(render_postgresql().encode()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
