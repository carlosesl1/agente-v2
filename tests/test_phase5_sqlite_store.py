from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from reservation_domain import (
    AwaitingConfirmationState,
    EVENT_TYPES,
    STATE_TYPES,
    TransitionStatus,
    dumps_event,
    new_workflow,
    reduce,
)
from reservation_execution.schema import SCHEMA_VERSION, schema_hash
from reservation_execution.sqlite_store import (
    ConcurrencyConflict,
    DataCorruption,
    IdentityConflict,
    PersistedTransition,
    SQLiteUnitOfWork,
    UnsupportedEffect,
    WorkflowNotFound,
)
from tests.phase5_helpers import database_counts, workflow_events


class Phase5SQLiteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase5-store-")
        self.path = Path(self.temporary.name) / "phase5.db"
        self.stores: list[SQLiteUnitOfWork] = []

    def tearDown(self) -> None:
        for store in reversed(self.stores):
            store.close()
        self.temporary.cleanup()

    def open_store(self) -> SQLiteUnitOfWork:
        store = SQLiteUnitOfWork.open(self.path)
        self.stores.append(store)
        return store

    def test_open_initializes_and_validates_exact_migration(self) -> None:
        store = self.open_store()
        row = store.connection.execute(
            "SELECT version, schema_hash FROM schema_migrations"
        ).fetchone()
        self.assertEqual(row, (SCHEMA_VERSION, schema_hash("sqlite")))
        self.assertEqual(
            store.connection.execute("PRAGMA foreign_keys").fetchone()[0],
            1,
        )
        self.assertEqual(
            store.connection.execute("PRAGMA journal_mode").fetchone()[0],
            "wal",
        )
        self.assertEqual(
            store.connection.execute("PRAGMA synchronous").fetchone()[0],
            2,
        )

        store.close()
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE schema_migrations SET schema_hash=? WHERE version=?",
            ("a" * 64, SCHEMA_VERSION),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(DataCorruption):
            SQLiteUnitOfWork.open(self.path)

    def test_partial_or_unknown_schema_fails_without_repair(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.commit()
        connection.close()
        with self.assertRaises(DataCorruption):
            SQLiteUnitOfWork.open(self.path)
        connection = sqlite3.connect(self.path)
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        connection.close()
        self.assertEqual(names, {"unrelated"})

    def test_create_close_reopen_and_load_exact_state_for_both_providers(self) -> None:
        for provider in ("cloudbeds", "bokun"):
            with self.subTest(provider=provider):
                path = Path(self.temporary.name) / f"{provider}.db"
                initial, _ = workflow_events(
                    provider,
                    workflow_id=f"workflow:store:restart:{provider}",
                )
                first = SQLiteUnitOfWork.open(path)
                first.create_workflow(initial)
                first.close()
                reopened = SQLiteUnitOfWork.open(path)
                self.addCleanup(reopened.close)
                self.assertEqual(
                    reopened.load_workflow(initial.meta.workflow_id),
                    initial,
                )

    def test_create_workflow_is_idempotent_and_divergence_conflicts(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:create"
        )
        store.create_workflow(initial)
        store.create_workflow(initial)
        self.assertEqual(database_counts(self.path), (1, 0, 0, 0, 0))

        divergent = new_workflow(
            workflow_id=initial.meta.workflow_id,
            started_at=initial.meta.last_event_at + timedelta(seconds=1),
        )
        with self.assertRaises(IdentityConflict):
            store.create_workflow(divergent)
        advanced = reduce(initial, script[0][0]).state
        with self.assertRaises(ValueError):
            store.create_workflow(advanced)
        self.assertEqual(database_counts(self.path), (1, 0, 0, 0, 0))

    def test_load_missing_workflow_is_explicit(self) -> None:
        store = self.open_store()
        with self.assertRaises(WorkflowNotFound):
            store.load_workflow("workflow:store:missing")

    def test_apply_event_requires_exact_expected_revision_and_rolls_back(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:revision"
        )
        store.create_workflow(initial)
        first_event, first_outbox = script[0]
        applied = store.apply_event(
            initial.meta.workflow_id,
            0,
            first_event,
            outbox=first_outbox,
        )
        self.assertIsInstance(applied, PersistedTransition)
        self.assertFalse(applied.duplicate)
        self.assertEqual(applied.status, TransitionStatus.APPLIED)
        before = database_counts(self.path)
        stale_event, stale_outbox = script[1]
        with self.assertRaises(ConcurrencyConflict):
            store.apply_event(
                initial.meta.workflow_id,
                0,
                stale_event,
                outbox=stale_outbox,
            )
        for invalid in (-1, True, 1.0):
            with self.subTest(expected_revision=invalid):
                with self.assertRaises(ValueError):
                    store.apply_event(
                        initial.meta.workflow_id,
                        invalid,  # type: ignore[arg-type]
                        stale_event,
                        outbox=stale_outbox,
                    )
        self.assertEqual(database_counts(self.path), before)
        self.assertEqual(
            store.load_workflow(initial.meta.workflow_id).meta.revision,
            1,
        )

    def test_two_connections_serialize_and_only_one_expected_revision_wins(self) -> None:
        first_store = self.open_store()
        second_store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:two-connections"
        )
        first_store.create_workflow(initial)
        self.assertEqual(second_store.load_workflow(initial.meta.workflow_id), initial)
        first_store.apply_event(initial.meta.workflow_id, 0, script[0][0])
        before = database_counts(self.path)
        with self.assertRaises(ConcurrencyConflict):
            second_store.apply_event(initial.meta.workflow_id, 0, script[1][0])
        self.assertEqual(database_counts(self.path), before)
        self.assertEqual(
            second_store.load_workflow(initial.meta.workflow_id).meta.revision,
            1,
        )

    def test_event_transition_survives_close_and_reopen(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "bokun", workflow_id="workflow:store:event-restart"
        )
        store.create_workflow(initial)
        applied = store.apply_event(initial.meta.workflow_id, 0, script[0][0])
        store.close()
        reopened = self.open_store()
        self.assertEqual(reopened.load_workflow(initial.meta.workflow_id), applied.state)
        self.assertEqual(database_counts(self.path), (1, 1, 0, 0, 0))

    def test_same_event_same_hash_is_idempotent_but_divergence_conflicts(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:duplicate"
        )
        store.create_workflow(initial)
        event, outbox = script[0]
        first = store.apply_event(
            initial.meta.workflow_id,
            0,
            event,
            outbox=outbox,
        )
        duplicate = store.apply_event(
            initial.meta.workflow_id,
            first.state.meta.revision,
            event,
            outbox=outbox,
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.state, first.state)
        self.assertEqual(duplicate.status, TransitionStatus.IGNORED)
        self.assertEqual(duplicate.reason, "duplicate_event")
        self.assertEqual(database_counts(self.path)[1], 1)

        conflict = replace(
            event,
            occurred_at=event.occurred_at + timedelta(seconds=1),
        )
        with self.assertRaises(IdentityConflict):
            store.apply_event(
                initial.meta.workflow_id,
                first.state.meta.revision,
                conflict,
            )
        self.assertEqual(database_counts(self.path)[1], 1)

    def test_old_duplicate_after_later_event_returns_current_state_without_write(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "bokun", workflow_id="workflow:store:old-duplicate"
        )
        store.create_workflow(initial)
        first = store.apply_event(initial.meta.workflow_id, 0, script[0][0])
        second = store.apply_event(
            initial.meta.workflow_id,
            first.state.meta.revision,
            script[1][0],
        )
        before = database_counts(self.path)
        duplicate = store.apply_event(
            initial.meta.workflow_id,
            second.state.meta.revision,
            script[0][0],
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.state, second.state)
        self.assertEqual(database_counts(self.path), before)

    def test_duplicate_cannot_silently_discard_caller_outbox(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:duplicate-outbox"
        )
        store.create_workflow(initial)
        event = script[0][0]
        first = store.apply_event(initial.meta.workflow_id, 0, event)
        summary_message = next(outbox[0] for _, outbox in script if outbox)
        before = database_counts(self.path)
        with self.assertRaisesRegex(UnsupportedEffect, "outbox"):
            store.apply_event(
                initial.meta.workflow_id,
                first.state.meta.revision,
                event,
                outbox=(summary_message,),
            )
        self.assertEqual(database_counts(self.path), before)

    def test_event_identity_is_global_and_bound_to_workflow(self) -> None:
        store = self.open_store()
        first_initial, first_script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:event-owner:one"
        )
        second_initial, _ = workflow_events(
            "cloudbeds", workflow_id="workflow:store:event-owner:two"
        )
        store.create_workflow(first_initial)
        store.create_workflow(second_initial)
        event = first_script[0][0]
        store.apply_event(first_initial.meta.workflow_id, 0, event)
        before = database_counts(self.path)
        with self.assertRaises(IdentityConflict):
            store.apply_event(second_initial.meta.workflow_id, 0, event)
        self.assertEqual(database_counts(self.path), before)

    def test_tampered_state_hash_or_serialization_fails_before_reduce(self) -> None:
        for tamper_sql, value in (
            ("UPDATE workflows SET state_json=?", "{}"),
            ("UPDATE workflows SET state_hash=?", "b" * 64),
            ("UPDATE workflows SET revision=?", 1),
            ("UPDATE workflows SET state_type=?", "searching"),
            ("UPDATE workflows SET created_at=?", "2026-10-31T11:59:58+00:00"),
            ("UPDATE workflows SET updated_at=?", "2026-10-31T11:59:58+00:00"),
        ):
            with self.subTest(tamper_sql=tamper_sql):
                path = Path(self.temporary.name) / (
                    hashlib.sha256(tamper_sql.encode("utf-8")).hexdigest() + ".db"
                )
                initial, _ = workflow_events(
                    "cloudbeds",
                    workflow_id="workflow:store:tamper:" + path.stem[:12],
                )
                store = SQLiteUnitOfWork.open(path)
                self.addCleanup(store.close)
                store.create_workflow(initial)
                connection = sqlite3.connect(path)
                connection.execute(
                    tamper_sql + " WHERE workflow_id=?",
                    (value, initial.meta.workflow_id),
                )
                connection.commit()
                connection.close()
                with self.assertRaises(DataCorruption):
                    store.load_workflow(initial.meta.workflow_id)

    def test_helper_sequence_is_exact_and_authorizes_only_on_final_event(self) -> None:
        expected_types = (
            "StartSearch",
            "LookupRecorded",
            "OfferChosen",
            "DraftRequested",
            "SummaryRecorded",
            "ConfirmationReceived",
        )
        for provider in ("cloudbeds", "bokun"):
            initial, script = workflow_events(
                provider,
                workflow_id=f"workflow:store:helper:{provider}",
            )
            with self.subTest(provider=provider):
                self.assertEqual(
                    tuple(type(event).__name__ for event, _ in script),
                    expected_types,
                )
                self.assertEqual(
                    tuple(len(outbox) for _, outbox in script),
                    (0, 0, 0, 0, 1, 0),
                )
                state = initial
                for index, (event, _) in enumerate(script):
                    transition = reduce(state, event)
                    self.assertEqual(
                        len(transition.commands),
                        1 if index == len(script) - 1 else 0,
                    )
                    state = transition.state

    def test_helper_accepts_maximum_length_valid_workflow_id(self) -> None:
        workflow_id = "workflow:" + "x" * 119
        self.assertEqual(len(workflow_id), 128)
        initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
        self.assertEqual(initial.meta.workflow_id, workflow_id)
        self.assertEqual(len(script), 6)
        self.assertTrue(all(len(event.event_id) <= 128 for event, _ in script))

    def test_tampered_event_is_detected_on_duplicate_resolution(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:event-tamper"
        )
        store.create_workflow(initial)
        event = script[0][0]
        first = store.apply_event(initial.meta.workflow_id, 0, event)
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE domain_events SET event_json='{}' WHERE event_id=?",
            (event.event_id,),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(DataCorruption):
            store.apply_event(
                initial.meta.workflow_id,
                first.state.meta.revision,
                event,
            )

    def test_task4_rejects_outbox_and_command_effects_with_full_rollback(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:unsupported"
        )
        store.create_workflow(initial)
        summary_message = next(outbox[0] for _, outbox in script if outbox)
        before = database_counts(self.path)
        with self.assertRaisesRegex(UnsupportedEffect, "outbox"):
            store.apply_event(
                initial.meta.workflow_id,
                0,
                script[0][0],
                outbox=(summary_message,),
            )
        self.assertEqual(database_counts(self.path), before)
        self.assertEqual(store.load_workflow(initial.meta.workflow_id), initial)

        state = initial
        for event, _ in script[:-1]:
            state = reduce(state, event).state
        self.assertIsInstance(state, AwaitingConfirmationState)
        revision_zero = replace(
            state,
            meta=replace(
                state.meta,
                revision=0,
                seen_event_ids=(),
                seen_event_hashes=(),
            ),
        )
        command_store_path = Path(self.temporary.name) / "command-effect.db"
        command_store = SQLiteUnitOfWork.open(command_store_path)
        self.addCleanup(command_store.close)
        command_store.create_workflow(revision_zero)
        before_command = database_counts(command_store_path)
        with self.assertRaisesRegex(UnsupportedEffect, "command"):
            command_store.apply_event(
                revision_zero.meta.workflow_id,
                0,
                script[-1][0],
            )
        self.assertEqual(database_counts(command_store_path), before_command)
        self.assertEqual(
            command_store.load_workflow(revision_zero.meta.workflow_id),
            revision_zero,
        )

    def test_closed_type_universes_are_enforced(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:types"
        )
        store.create_workflow(initial)
        self.assertIn(type(initial), STATE_TYPES)
        self.assertIn(type(script[0][0]), EVENT_TYPES)
        with self.assertRaises(TypeError):
            store.create_workflow(object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            store.apply_event(
                initial.meta.workflow_id,
                0,
                object(),  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
