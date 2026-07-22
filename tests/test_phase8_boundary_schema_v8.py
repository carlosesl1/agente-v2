"""Phase 8 exact eleven-table boundary schema and startup authentication."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from reservation_boundary import schema
from reservation_boundary.sqlite_store import DataCorruption, SQLiteBoundaryStore


V8_TABLES = (
    "boundary_state",
    "boundary_events",
    "boundary_event_sources",
    "boundary_turn_artifacts",
    "boundary_commands",
    "boundary_command_relays",
    "boundary_outbox",
    "boundary_public_outbox",
    "boundary_dispatch_authority",
    "legacy_import_claims",
    "decision_comparisons",
)

EXPECTED_COLUMNS = {
    "boundary_state": (
        "lead_key", "version", "state_json", "state_hash", "fencing_token",
        "created_at", "updated_at",
    ),
    "boundary_events": (
        "lead_key", "aggregate_turn_id", "event_hash", "commit_hash",
        "turn_receipt_json", "turn_receipt_hash", "state_version", "occurred_at",
    ),
    "boundary_event_sources": (
        "lead_key", "aggregate_turn_id", "source_index", "source_event_id",
        "source_event_hash", "source_event_json", "source_turn_receipt_hash",
    ),
    "boundary_turn_artifacts": (
        "lead_key", "aggregate_turn_id", "artifact_index", "artifact_id",
        "artifact_kind", "frame_sequence", "frame_reference", "artifact_json",
        "artifact_hash", "source_turn_receipt_hash",
    ),
    "boundary_commands": (
        "command_id", "lead_key", "aggregate_turn_id", "command_type",
        "command_json", "command_hash", "source_turn_receipt_hash", "created_at",
    ),
    "boundary_command_relays": (
        "relay_id", "command_id", "lead_key", "aggregate_turn_id", "bundle_json",
        "bundle_hash", "source_turn_receipt_hash", "status", "owner",
        "fencing_token", "lease_acquired_at", "lease_expires_at", "claim_count",
        "preparation_failures", "target_receipt_json", "target_receipt_hash",
        "acked_at", "updated_at",
    ),
    "boundary_outbox": (
        "job_id", "job_kind", "lead_key", "aggregate_turn_id", "artifact_json",
        "artifact_hash", "source_turn_receipt_hash", "qualification_id", "epoch",
        "target_operation_id", "status", "owner", "fencing_token",
        "lease_acquired_at", "lease_expires_at", "claim_count",
        "preparation_failures", "target_receipt_json", "target_receipt_hash",
        "acked_at", "updated_at",
    ),
    "boundary_public_outbox": (
        "public_row_id", "lead_key", "aggregate_turn_id", "chunk_index",
        "idempotency_key", "target_binding_hash", "channel_id", "channel_scope", "chunk_json",
        "chunk_hash", "predecessor_chunk_hash", "status", "owner",
        "fencing_token", "lease_acquired_at", "lease_expires_at", "claim_count",
        "preparation_failures", "dispatch_slots_consumed", "authorization_kind",
        "authorization_id", "scope_subject_id", "allocation_id",
        "immutable_generation", "qualification_id", "scenario_id",
        "capability_policy_digest", "effect_authorization_binding_digest",
        "effective_turn_binding_digest", "source_turn_receipt_hash",
        "delivery_receipt_json", "delivery_receipt_hash", "deadline_at",
        "created_at", "updated_at",
    ),
    "boundary_dispatch_authority": (
        "authorization_id", "scope_subject_id", "channel_scope", "generation",
        "allocation_id", "row_kind", "authorization_kind", "qualification_id",
        "scenario_id", "contract_digest", "effect_authorization_binding_digest",
        "capability_policy_digest", "target_binding_hash", "allowed_chunk_ordinal",
        "allocation_manifest_hash", "state", "public_row_id", "cas_revision",
        "closure_receipt_hash", "created_at", "updated_at", "fenced_at",
    ),
    "legacy_import_claims": (
        "lead_key", "snapshot_hash", "disposition", "state_hash", "claimed_at",
    ),
    "decision_comparisons": (
        "comparison_id", "lead_key", "aggregate_turn_id", "old_hash", "new_hash",
        "severity", "changed_fields_json", "created_at",
    ),
}

EXPECTED_EXPLICIT_INDEXES = {
    "idx_boundary_command_relays_claim",
    "idx_boundary_dispatch_authority_state",
    "idx_boundary_outbox_claim",
    "idx_boundary_public_outbox_claim",
    "idx_boundary_turn_artifacts_frame",
}
EXPECTED_TRIGGERS = {
    "trg_boundary_dispatch_authority_single_open_insert",
    "trg_boundary_dispatch_authority_single_open_update",
}


class Phase8BoundarySchemaV8Tests(unittest.TestCase):
    def _render(self) -> str:
        render = getattr(schema, "render_sqlite_v8", None)
        self.assertIsNotNone(render, "render_sqlite_v8 must have an owner")
        assert render is not None
        return render()

    def _open_v8(self, path: Path) -> SQLiteBoundaryStore:
        factory = getattr(SQLiteBoundaryStore, "open_path_v8", None)
        self.assertIsNotNone(factory, "open_path_v8 must authenticate the v8 root")
        assert factory is not None
        return factory(path)

    def test_v8_sqlite_has_exact_eleven_strict_tables(self) -> None:
        tables = getattr(schema, "BOUNDARY_V8_TABLES", None)
        version = getattr(schema, "SCHEMA_VERSION_V8", None)
        self.assertEqual(tables, V8_TABLES)
        self.assertEqual(version, 8)

        connection = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(self._render())
        actual = {
            row[1]: row[5]
            for row in connection.execute("PRAGMA table_list")
            if row[1] not in {"sqlite_schema", "sqlite_temp_schema"}
        }
        self.assertEqual(set(actual), set(V8_TABLES))
        self.assertEqual(set(actual.values()), {1})
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_v8_columns_keys_indexes_triggers_and_foreign_keys_are_closed(self) -> None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(self._render())

        for table, expected in EXPECTED_COLUMNS.items():
            with self.subTest(table=table):
                info = connection.execute(f"PRAGMA table_info({table})").fetchall()
                self.assertEqual(tuple(row[1] for row in info), expected)
                self.assertGreater(sum(row[5] for row in info), 0)

        explicit_indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            )
        }
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        self.assertEqual(explicit_indexes, EXPECTED_EXPLICIT_INDEXES)
        self.assertEqual(triggers, EXPECTED_TRIGGERS)

        event_source_fks = connection.execute(
            "PRAGMA foreign_key_list(boundary_event_sources)"
        ).fetchall()
        self.assertEqual(
            {(row[2], row[3], row[4]) for row in event_source_fks},
            {
                ("boundary_events", "lead_key", "lead_key"),
                ("boundary_events", "aggregate_turn_id", "aggregate_turn_id"),
            },
        )
        public_fks = connection.execute(
            "PRAGMA foreign_key_list(boundary_public_outbox)"
        ).fetchall()
        self.assertEqual(
            {row[2] for row in public_fks},
            {"boundary_events", "boundary_dispatch_authority"},
        )

    def test_open_path_v8_accepts_only_empty_or_exact_v8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("absent.sqlite3", "empty.sqlite3"):
                path = root / name
                if name.startswith("empty"):
                    path.touch()
                store = self._open_v8(path)
                self.assertEqual(
                    {
                        row[0]
                        for row in store._connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    },
                    set(V8_TABLES),
                )
                store.close()
                reopened = self._open_v8(path)
                reopened.close()

            v7 = root / "v7.sqlite3"
            connection = sqlite3.connect(v7)
            connection.executescript(schema.render_sqlite())
            connection.close()
            with self.assertRaisesRegex(DataCorruption, "v8"):
                self._open_v8(v7)

            extra = root / "extra.sqlite3"
            store = self._open_v8(extra)
            store.close()
            connection = sqlite3.connect(extra)
            connection.execute("CREATE TABLE unexpected(value TEXT) STRICT")
            connection.close()
            with self.assertRaises(DataCorruption):
                self._open_v8(extra)

    def test_startup_rejects_extra_index_trigger_or_normalized_ddl_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mutations = {
                "index": "CREATE INDEX injected_index ON boundary_state(version)",
                "trigger": (
                    "CREATE TRIGGER injected_trigger AFTER UPDATE ON boundary_state "
                    "BEGIN SELECT 1; END"
                ),
            }
            for name, statement in mutations.items():
                with self.subTest(name=name):
                    path = root / f"{name}.sqlite3"
                    store = self._open_v8(path)
                    store.close()
                    connection = sqlite3.connect(path)
                    connection.execute(statement)
                    connection.close()
                    with self.assertRaisesRegex(DataCorruption, "DDL"):
                        self._open_v8(path)

            divergent = root / "divergent.sqlite3"
            altered = self._render().replace(
                "version INTEGER NOT NULL CHECK (version >= 0)",
                "version INTEGER NOT NULL CHECK (version >= 1)",
                1,
            )
            connection = sqlite3.connect(divergent)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(altered)
            connection.close()
            with self.assertRaisesRegex(DataCorruption, "DDL"):
                self._open_v8(divergent)

            fingerprint = getattr(schema, "sqlite_v8_schema_fingerprint", None)
            self.assertIsNotNone(fingerprint)
            assert fingerprint is not None
            connection = sqlite3.connect(":memory:")
            self.addCleanup(connection.close)
            connection.executescript(self._render())
            value = fingerprint(connection)
            self.assertEqual(len(value), 64)
            self.assertEqual(value, value.lower())


if __name__ == "__main__":
    unittest.main()
