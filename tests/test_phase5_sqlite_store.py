from __future__ import annotations

from dataclasses import replace
from datetime import timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from reservation_domain import (
    EVENT_TYPES,
    ExecutionQueuedState,
    STATE_TYPES,
    SummaryRecorded,
    TransitionStatus,
    new_workflow,
    reduce,
)
from reservation_execution.schema import SCHEMA_VERSION, schema_hash
from reservation_execution.types import LedgerStatus, OutboxKind
from reservation_execution.sqlite_store import (
    ConcurrencyConflict,
    DataCorruption,
    IdentityConflict,
    PersistedTransition,
    SQLiteUnitOfWork,
    StoreError,
    WorkflowNotFound,
)
from tests.phase5_helpers import T0, database_counts, persist_script, workflow_events


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
        self.assertFalse(hasattr(store, "connection"))
        row = store._connection.execute(  # internal configuration assertion
            "SELECT version, schema_hash FROM schema_migrations"
        ).fetchone()
        self.assertEqual(row, (SCHEMA_VERSION, schema_hash("sqlite")))
        self.assertEqual(
            store._connection.execute("PRAGMA foreign_keys").fetchone()[0],
            1,
        )
        self.assertEqual(
            store._connection.execute("PRAGMA journal_mode").fetchone()[0],
            "wal",
        )
        self.assertEqual(
            store._connection.execute("PRAGMA synchronous").fetchone()[0],
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

    def test_constructor_is_factory_guarded(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        with self.assertRaises(TypeError):
            SQLiteUnitOfWork(self.path, connection)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            SQLiteUnitOfWork(
                self.path,
                connection,
                _factory_token=object(),
            )

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

    def test_duplicate_detects_event_revision_swapped_against_history_order(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:revision-tamper"
        )
        store.create_workflow(initial)
        first = store.apply_event(initial.meta.workflow_id, 0, script[0][0])
        second = store.apply_event(
            initial.meta.workflow_id,
            first.state.meta.revision,
            script[1][0],
        )
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE domain_events SET revision=3 WHERE event_id=?",
            (script[0][0].event_id,),
        )
        connection.execute(
            "UPDATE domain_events SET revision=1 WHERE event_id=?",
            (script[1][0].event_id,),
        )
        connection.execute(
            "UPDATE domain_events SET revision=2 WHERE event_id=?",
            (script[0][0].event_id,),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(DataCorruption):
            store.apply_event(
                initial.meta.workflow_id,
                second.state.meta.revision,
                script[0][0],
            )

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
        with self.assertRaisesRegex(ValueError, "only allowed for SummaryRecorded"):
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

    def test_caller_outbox_is_only_allowed_for_summary(self) -> None:
        store = self.open_store()
        initial, script = workflow_events(
            "cloudbeds", workflow_id="workflow:store:outbox-boundary"
        )
        store.create_workflow(initial)
        summary_message = next(outbox[0] for _, outbox in script if outbox)
        before = database_counts(self.path)
        with self.assertRaisesRegex(ValueError, "only allowed for SummaryRecorded"):
            store.apply_event(
                initial.meta.workflow_id,
                0,
                script[0][0],
                outbox=(summary_message,),
            )
        self.assertEqual(database_counts(self.path), before)
        self.assertEqual(store.load_workflow(initial.meta.workflow_id), initial)

    def test_commit_failure_rolls_back_and_leaves_connection_reusable(self) -> None:
        store = self.open_store()
        initial, _ = workflow_events(
            "cloudbeds", workflow_id="workflow:store:commit-failure"
        )

        def deny_commit(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        store._connection.set_authorizer(deny_commit)
        with self.assertRaises(StoreError) as raised:
            store.create_workflow(initial)
        self.assertIsInstance(raised.exception.__cause__, sqlite3.DatabaseError)
        self.assertFalse(store._connection.in_transaction)
        store._connection.set_authorizer(None)
        self.assertEqual(database_counts(self.path), (0, 0, 0, 0, 0))
        store.create_workflow(initial)
        self.assertEqual(store.load_workflow(initial.meta.workflow_id), initial)

    def test_sqlite_failures_are_mapped_to_stable_store_error(self) -> None:
        store = self.open_store()
        initial, _ = workflow_events(
            "bokun", workflow_id="workflow:store:sqlite-error"
        )

        def deny_begin(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_TRANSACTION and arg1 == "BEGIN":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        store._connection.set_authorizer(deny_begin)
        with self.assertRaises(StoreError) as raised:
            store.create_workflow(initial)
        self.assertIsInstance(raised.exception.__cause__, sqlite3.DatabaseError)
        self.assertFalse(store._connection.in_transaction)
        store._connection.set_authorizer(None)

    def test_close_maps_sqlite_failure_and_becomes_idempotent(self) -> None:
        path = Path(self.temporary.name) / "close-failure.db"
        store = SQLiteUnitOfWork.open(path)
        store._connection.close()
        with self.assertRaises(StoreError) as raised:
            store.close()
        self.assertIsInstance(raised.exception.__cause__, sqlite3.DatabaseError)
        store.close()

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


class Phase5AtomicCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase5-atomic-")
        self.path = Path(self.temporary.name) / "phase5.db"
        self.store = SQLiteUnitOfWork.open(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    @staticmethod
    def _fingerprint(path: Path) -> str:
        connection = sqlite3.connect(path)
        try:
            rows = tuple(
                (table, tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1")))
                for table in (
                    "workflows",
                    "domain_events",
                    "reservation_commands",
                    "execution_ledger",
                    "outbox_messages",
                )
            )
        finally:
            connection.close()
        return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()

    def _persist_before_summary(self, provider: str, workflow_id: str):
        initial, script = workflow_events(provider, workflow_id=workflow_id)
        self.store.create_workflow(initial)
        results = persist_script(self.store, workflow_id, script[:4])
        return initial, script, results[-1].state

    def test_public_summary_projection_recomposes_phase4_artifact(self) -> None:
        from reservation_confirmation import SummaryLocale, prepare_summary
        from reservation_execution import LedgerSnapshot, summary_outbox_message

        self.assertEqual(LedgerSnapshot.__module__, "reservation_execution.projection")
        workflow_id = "workflow:atomic:projection"
        initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
        state = initial
        for event, _ in script[:4]:
            state = reduce(state, event).state
        prepared = prepare_summary(
            state,
            locale=SummaryLocale.PT_BR,
            presented_at=script[4][0].occurred_at,
        )
        self.assertEqual(
            summary_outbox_message(workflow_id=workflow_id, prepared=prepared),
            script[4][1][0],
        )

    def test_summary_requires_exact_matching_outbox_and_persists_it_atomically(self) -> None:
        workflow_id = "workflow:atomic:summary"
        _, script, before_state = self._persist_before_summary("cloudbeds", workflow_id)
        event, outbox = script[4]
        self.assertIsInstance(event, SummaryRecorded)
        before_counts = database_counts(self.path)

        for invalid_outbox in ((), (outbox[0], outbox[0])):
            with self.subTest(outbox_count=len(invalid_outbox)):
                with self.assertRaisesRegex(ValueError, "exactly one outbox"):
                    self.store.apply_event(
                        workflow_id,
                        before_state.meta.revision,
                        event,
                        outbox=invalid_outbox,
                    )
                self.assertEqual(self.store.load_workflow(workflow_id), before_state)
                self.assertEqual(database_counts(self.path), before_counts)

        divergent = replace(outbox[0], kind=OutboxKind.EXECUTION_SUCCEEDED)
        with self.assertRaises(IdentityConflict):
            self.store.apply_event(
                workflow_id,
                before_state.meta.revision,
                event,
                outbox=(divergent,),
            )
        payload = json.loads(outbox[0].canonical_payload)
        payload["content"] += " synthetic divergence"
        divergent_payload = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        divergent = replace(
            outbox[0],
            canonical_payload=divergent_payload,
            payload_hash=hashlib.sha256(divergent_payload.encode("utf-8")).hexdigest(),
        )
        with self.assertRaises(IdentityConflict):
            self.store.apply_event(
                workflow_id,
                before_state.meta.revision,
                event,
                outbox=(divergent,),
            )
        self.assertEqual(self.store.load_workflow(workflow_id), before_state)
        self.assertEqual(database_counts(self.path), before_counts)

        applied = self.store.apply_event(
            workflow_id,
            before_state.meta.revision,
            event,
            outbox=outbox,
        )
        self.assertEqual(self.store.load_outbox(outbox[0].message_id), outbox[0])
        self.assertEqual(applied.state.meta.revision, before_state.meta.revision + 1)
        self.assertEqual(database_counts(self.path), (1, 5, 0, 0, 1))

    def test_confirmation_persists_state_event_command_and_ledger(self) -> None:
        workflow_id = "workflow:atomic:command"
        initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
        self.store.create_workflow(initial)
        final = persist_script(self.store, workflow_id, script)[-1]
        self.assertIsInstance(final.state, ExecutionQueuedState)
        self.assertEqual(len(final.commands), 1)
        command = final.commands[0]
        self.assertEqual(self.store.load_command(command.command_id), command)
        ledger = self.store.load_ledger(command.command_id)
        self.assertEqual(ledger.status, LedgerStatus.QUEUED)
        self.assertEqual(ledger.command_id, command.command_id)
        self.assertEqual(ledger.fencing_token, 0)
        self.assertEqual(ledger.claim_count, 0)
        self.assertEqual(ledger.preparation_failures, 0)
        self.assertEqual(ledger.dispatch_slots_consumed, 0)
        self.assertEqual(ledger.updated_at, command.created_at)
        self.assertEqual(database_counts(self.path), (1, 6, 1, 1, 1))

    def test_duplicate_events_add_no_outbox_command_or_ledger(self) -> None:
        workflow_id = "workflow:atomic:duplicates"
        initial, script = workflow_events("bokun", workflow_id=workflow_id)
        self.store.create_workflow(initial)
        final = persist_script(self.store, workflow_id, script)[-1]
        before = database_counts(self.path)

        confirmation_replay = self.store.apply_event(
            workflow_id,
            final.state.meta.revision,
            script[-1][0],
            outbox=script[-1][1],
        )
        self.assertTrue(confirmation_replay.duplicate)
        summary_replay = self.store.apply_event(
            workflow_id,
            final.state.meta.revision,
            script[4][0],
            outbox=script[4][1],
        )
        self.assertTrue(summary_replay.duplicate)
        with self.assertRaisesRegex(ValueError, "exactly one outbox"):
            self.store.apply_event(
                workflow_id,
                final.state.meta.revision,
                script[4][0],
                outbox=(),
            )
        with self.assertRaises(IdentityConflict):
            self.store.apply_event(
                workflow_id,
                final.state.meta.revision,
                script[4][0],
                outbox=(
                    replace(
                        script[4][1][0],
                        kind=OutboxKind.EXECUTION_SUCCEEDED,
                    ),
                ),
            )
        self.assertEqual(database_counts(self.path), before)

    def test_tampered_command_ledger_or_outbox_is_detected_without_state_change(self) -> None:
        cases = (
            ("reservation_commands", "command_json", "{}", "load_command"),
            (
                "execution_ledger",
                "updated_at",
                (T0 + timedelta(seconds=99)).isoformat(),
                "load_ledger",
            ),
            ("outbox_messages", "payload_json", "{}", "load_outbox"),
        )
        for index, (table, column, value, loader) in enumerate(cases):
            with self.subTest(table=table, column=column):
                path = Path(self.temporary.name) / f"tamper-{index}.db"
                store = SQLiteUnitOfWork.open(path)
                self.addCleanup(store.close)
                workflow_id = f"workflow:atomic:tamper:{index}"
                initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
                store.create_workflow(initial)
                final = persist_script(store, workflow_id, script)[-1]
                command_id = final.commands[0].command_id
                message_id = script[4][1][0].message_id
                before_state = store.load_workflow(workflow_id)
                key_name = "message_id" if table == "outbox_messages" else "command_id"
                key_value = message_id if table == "outbox_messages" else command_id
                connection = sqlite3.connect(path)
                connection.execute(
                    f"UPDATE {table} SET {column}=? WHERE {key_name}=?",
                    (value, key_value),
                )
                connection.commit()
                connection.close()
                with self.assertRaises(DataCorruption):
                    getattr(store, loader)(key_value)
                self.assertEqual(store.load_workflow(workflow_id), before_state)

    def test_coherently_tampered_outbox_payload_and_hash_is_detected(self) -> None:
        workflow_id = "workflow:atomic:coherent-outbox-tamper"
        initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
        self.store.create_workflow(initial)
        persist_script(self.store, workflow_id, script)
        message = script[4][1][0]
        payload = json.loads(message.canonical_payload)
        payload["content"] += " synthetic alteration"
        divergent = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE outbox_messages SET payload_json=?, payload_hash=? "
            "WHERE message_id=?",
            (
                divergent,
                hashlib.sha256(divergent.encode("utf-8")).hexdigest(),
                message.message_id,
            ),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(DataCorruption):
            self.store.load_outbox(message.message_id)

    def test_ledger_snapshot_rejects_invalid_status_matrix_and_non_utc_values(self) -> None:
        workflow_id = "workflow:atomic:ledger-snapshot"
        initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
        self.store.create_workflow(initial)
        final = persist_script(self.store, workflow_id, script)[-1]
        snapshot = self.store.load_ledger(final.commands[0].command_id)
        with self.assertRaises(ValueError):
            replace(snapshot, status=LedgerStatus.PREPARING)
        shifted = snapshot.updated_at.astimezone(
            timezone(timedelta(hours=1))
        )
        self.assertEqual(shifted, snapshot.updated_at)
        with self.assertRaises(ValueError):
            replace(snapshot, updated_at=shifted)

    def test_secondary_outbox_projection_query_maps_sqlite_failure(self) -> None:
        workflow_id = "workflow:atomic:outbox-read-error"
        initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
        self.store.create_workflow(initial)
        persist_script(self.store, workflow_id, script[:5])
        message_id = script[4][1][0].message_id

        def deny_domain_event_read(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_READ and arg1 == "domain_events":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.store._connection.set_authorizer(deny_domain_event_read)
        with self.assertRaises(StoreError) as raised:
            self.store.load_outbox(message_id)
        self.assertIsInstance(raised.exception.__cause__, sqlite3.DatabaseError)
        self.store._connection.set_authorizer(None)

    def test_every_statement_fault_rolls_back_after_reopen(self) -> None:
        statement_cases = (
            ("domain_events", "INSERT", False),
            ("workflows", "UPDATE", False),
            ("reservation_commands", "INSERT", False),
            ("execution_ledger", "INSERT", False),
            ("outbox_messages", "INSERT", True),
        )
        for index, (table, operation, summary_fault) in enumerate(statement_cases):
            with self.subTest(table=table):
                path = Path(self.temporary.name) / f"fault-{index}.db"
                store = SQLiteUnitOfWork.open(path)
                workflow_id = f"workflow:atomic:fault:{index}"
                initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
                store.create_workflow(initial)
                prefix = script[:4] if summary_fault else script[:5]
                persist_script(store, workflow_id, prefix)
                before_state = store.load_workflow(workflow_id)
                before_counts = database_counts(path)
                before_fingerprint = self._fingerprint(path)
                trigger_name = f"fault_{table}"
                store._connection.execute(
                    f"CREATE TEMP TRIGGER {trigger_name} BEFORE {operation} ON main.{table} "
                    f"BEGIN SELECT RAISE(ABORT, 'fault:{table}'); END"
                )
                event, outbox = script[4] if summary_fault else script[5]
                with self.assertRaises(StoreError):
                    store.apply_event(
                        workflow_id,
                        before_state.meta.revision,
                        event,
                        outbox=outbox,
                    )
                store.close()
                reopened = SQLiteUnitOfWork.open(path)
                self.addCleanup(reopened.close)
                self.assertEqual(reopened.load_workflow(workflow_id), before_state)
                self.assertEqual(database_counts(path), before_counts)
                self.assertEqual(self._fingerprint(path), before_fingerprint)


if __name__ == "__main__":
    unittest.main()
