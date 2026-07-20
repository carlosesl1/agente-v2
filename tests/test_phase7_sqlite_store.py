"""Single-write fenced SQLite boundary store."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import unittest

from reservation_execution import OutboxMessage
from reservation_execution.types import OutboxKind
from reservation_boundary.legacy_state import import_legacy_state
from reservation_boundary.sqlite_store import (
    ConcurrencyConflict,
    IdentityConflict,
    LegacyStateReadPort,
    SQLiteBoundaryStore,
)
from reservation_boundary.types import BoundaryCommit, ImportDisposition, TypedFact
from tests.test_phase2_serialization import complete_flow
from tests.test_phase7_legacy_state import advanced_metadata, snapshot


T0 = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def collecting_import():
    value = snapshot()
    result = import_legacy_state(value)
    assert result.disposition is ImportDisposition.MIGRATED
    return value, result


def queued_import():
    states, _, command = complete_flow()
    queued = states[-1]
    value = snapshot(stage="fechamento", metadata=advanced_metadata(queued))
    result = import_legacy_state(value)
    assert result.disposition is ImportDisposition.MIGRATED
    return value, result, command


def outbox_for(command) -> OutboxMessage:
    payload = json.dumps(
        {"command_id": command.command_id, "status": "queued"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return OutboxMessage(
        message_id="outbox:phase7:synthetic:001",
        idempotency_key="outbox:idem:phase7:synthetic:001",
        workflow_id=command.workflow_id,
        command_id=command.command_id,
        kind=OutboxKind.SUMMARY_PRESENTED,
        template_id="template:phase7:synthetic:001",
        canonical_payload=payload,
        payload_hash=_sha(payload),
        created_at=T0,
    )


class Phase7SingleWriteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SQLiteBoundaryStore.open_memory()

    def tearDown(self) -> None:
        self.store.close()

    def counts(self) -> tuple[int, ...]:
        return tuple(
            self.store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "boundary_state",
                "boundary_events",
                "boundary_commands",
                "boundary_outbox",
                "legacy_import_claims",
                "decision_comparisons",
            )
        )

    def test_legacy_port_has_no_write_surface(self) -> None:
        surface = set(dir(LegacyStateReadPort))
        self.assertNotIn("write", surface)
        self.assertNotIn("upsert", surface)
        self.assertNotIn("delete", surface)
        self.assertIn("read_snapshot", surface)

    def test_genesis_is_single_winner_and_divergence_conflicts(self) -> None:
        source, imported = collecting_import()
        first = self.store.import_genesis(source, imported, claimed_at=T0)
        second = self.store.import_genesis(source, imported, claimed_at=T0)
        self.assertEqual(first, second)
        self.assertEqual(self.counts(), (1, 0, 0, 0, 1, 0))
        self.assertEqual(self.store.load_state(source.raw_fields["lead_key"]), first)

        divergent_source = snapshot(stage="hostel")
        divergent = import_legacy_state(divergent_source)
        with self.assertRaises(IdentityConflict):
            self.store.import_genesis(divergent_source, divergent, claimed_at=T0)
        self.assertEqual(self.counts(), (1, 0, 0, 0, 1, 0))

    def test_fencing_is_monotonic_and_stale_cas_never_writes(self) -> None:
        source, imported = collecting_import()
        self.store.import_genesis(source, imported, claimed_at=T0)
        _, first_token = self.store.acquire_fence(source.raw_fields["lead_key"])
        current, second_token = self.store.acquire_fence(source.raw_fields["lead_key"])
        self.assertEqual((first_token, second_token), (1, 2))
        next_state = replace(current.state, version=1, processed_event_ids=("event-001",))
        commit = BoundaryCommit(next_state, (), (), ())
        with self.assertRaises(ConcurrencyConflict):
            self.store.commit(
                event_id="event-001",
                event_hash="a" * 64,
                expected_version=0,
                fencing_token=first_token,
                commit=commit,
                committed_at=T0,
            )
        self.assertEqual(self.counts(), (1, 0, 0, 0, 1, 0))

    def test_event_dedupe_is_idempotent_and_conflict_is_closed(self) -> None:
        source, imported = collecting_import()
        self.store.import_genesis(source, imported, claimed_at=T0)
        current, token = self.store.acquire_fence(source.raw_fields["lead_key"])
        state = replace(current.state, version=1, processed_event_ids=("event-001",))
        commit = BoundaryCommit(state, (), (), ())
        first = self.store.commit(
            event_id="event-001",
            event_hash="a" * 64,
            expected_version=0,
            fencing_token=token,
            commit=commit,
            committed_at=T0,
        )
        duplicate = self.store.commit(
            event_id="event-001",
            event_hash="a" * 64,
            expected_version=1,
            fencing_token=token,
            commit=commit,
            committed_at=T0,
        )
        self.assertEqual(duplicate, first)
        with self.assertRaises(IdentityConflict):
            self.store.commit(
                event_id="event-001",
                event_hash="b" * 64,
                expected_version=1,
                fencing_token=token,
                commit=commit,
                committed_at=T0,
            )
        self.assertEqual(self.counts(), (1, 1, 0, 0, 1, 0))

    def test_state_command_and_outbox_commit_atomically(self) -> None:
        source, imported, command = queued_import()
        self.store.import_genesis(source, imported, claimed_at=T0)
        current, token = self.store.acquire_fence(source.raw_fields["lead_key"])
        next_state = replace(current.state, version=1)
        outbox = outbox_for(command)
        commit = BoundaryCommit(next_state, (command,), (outbox,), ())

        def fail_after_command(stage: str) -> None:
            if stage == "after_command_insert":
                raise RuntimeError("synthetic failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            self.store.commit(
                event_id="event-queued-001",
                event_hash="c" * 64,
                expected_version=0,
                fencing_token=token,
                commit=commit,
                committed_at=T0,
                fault_hook=fail_after_command,
            )
        self.assertEqual(self.counts(), (1, 0, 0, 0, 1, 0))
        self.assertEqual(self.store.load_state(source.raw_fields["lead_key"]), current)

        persisted = self.store.commit(
            event_id="event-queued-001",
            event_hash="c" * 64,
            expected_version=0,
            fencing_token=token,
            commit=commit,
            committed_at=T0,
        )
        self.assertEqual(persisted.state, next_state)
        self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0))

    def test_invalid_exact_scalars_fail_before_transaction(self) -> None:
        source, imported = collecting_import()
        self.store.import_genesis(source, imported, claimed_at=T0)
        current, token = self.store.acquire_fence(source.raw_fields["lead_key"])
        commit = BoundaryCommit(replace(current.state, version=1), (), (), ())
        for invalid in (True, 0.0, -1):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                self.store.commit(
                    event_id="event-invalid",
                    event_hash="d" * 64,
                    expected_version=invalid,
                    fencing_token=token,
                    commit=commit,
                    committed_at=T0,
                )
        self.assertEqual(self.counts(), (1, 0, 0, 0, 1, 0))


if __name__ == "__main__":
    unittest.main()
