"""Pure durable TurnCoordinator ordering and failure contracts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from reservation_boundary.coordinator import (
    InvalidIntent,
    InvalidKernelDecision,
    TurnCoordinator,
    TurnDeadlineExceeded,
    TurnEventConflict,
    TurnImportRejected,
)
from reservation_boundary.legacy_state import import_legacy_state
from reservation_boundary.sqlite_store import IdentityConflict, SQLiteBoundaryStore
from reservation_boundary.types import (
    ConversationIntent,
    ConversationIntentKind,
    ImportDisposition,
    ImportReason,
    KernelDecision,
    NormalizedMessage,
    TurnEnvelope,
    TurnPlanReason,
)
from reservation_execution import OutboxMessage
from reservation_execution.types import OutboxKind
from tests.test_phase2_serialization import complete_flow
from tests.test_phase7_legacy_state import snapshot


T0 = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, values: tuple[datetime, ...] = (T0,)) -> None:
        self.values = values
        self.calls = 0

    def now(self) -> datetime:
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value


class FakeLock:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.calls = 0

    @contextmanager
    def claim(self, *, lead_key: str, event_id: str, now: datetime, deadline_at: datetime):
        self.calls += 1
        self.trace.append("claim")
        yield


class FakeLegacyReader:
    def __init__(self, trace: list[str], value) -> None:
        self.trace = trace
        self.value = value
        self.calls = 0

    def read_snapshot(self, lead_key: str):
        self.calls += 1
        self.trace.append("load_legacy")
        return self.value


class FakeIntent:
    def __init__(
        self,
        trace: list[str],
        *,
        source_event_id: str = "event-001",
    ) -> None:
        self.trace = trace
        self.source_event_id = source_event_id
        self.calls = 0
        self.on_call = None

    def interpret(self, request):
        self.calls += 1
        self.trace.append("intent")
        if self.on_call is not None:
            self.on_call()
        return ConversationIntent(
            ConversationIntentKind.INFORM,
            self.source_event_id,
        )


class FakeKernel:
    def __init__(
        self,
        trace: list[str],
        *,
        command=None,
        outbox=None,
        read_request=None,
    ) -> None:
        self.trace = trace
        self.command = command
        self.outbox = outbox
        self.read_request = read_request
        self.calls = 0

    def reduce(self, state, intent):
        self.calls += 1
        self.trace.append("reduce")
        commands = () if self.command is None else (self.command,)
        outbox = () if self.outbox is None else (self.outbox,)
        reads = () if self.read_request is None else ()
        decision = KernelDecision(state, commands, outbox, reads, ())
        if self.read_request is not None:
            object.__setattr__(decision, "read_requests", (self.read_request,))
        return decision


class TracingStore:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.inner = SQLiteBoundaryStore.open_memory()
        self.loads = 0
        self.commits = 0
        self.imports = 0

    def close(self) -> None:
        self.inner.close()

    def event_hash(self, lead_key: str, event_id: str):
        self.trace.append("event_lookup")
        return self.inner.event_hash(lead_key, event_id)

    def turn_transaction(self, *, deadline_guard):
        return self.inner.turn_transaction(deadline_guard=deadline_guard)

    def load_state(self, lead_key: str):
        self.loads += 1
        self.trace.append("load_new")
        return self.inner.load_state(lead_key)

    def import_genesis(self, value, result, *, claimed_at):
        self.imports += 1
        self.trace.append("import_genesis")
        return self.inner.import_genesis(value, result, claimed_at=claimed_at)

    def acquire_fence(self, lead_key: str):
        self.trace.append("fence")
        return self.inner.acquire_fence(lead_key)

    def commit(self, **kwargs):
        self.commits += 1
        self.trace.append("commit")
        return self.inner.commit(**kwargs)


class RacingStore(TracingStore):
    def import_genesis(self, value, result, *, claimed_at):
        self.imports += 1
        self.trace.append("import_genesis_race")
        self.inner.import_genesis(value, result, claimed_at=claimed_at)
        raise IdentityConflict("synthetic race loser")


class TracingImporter:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def import_snapshot(self, value):
        self.trace.append("import")
        return import_legacy_state(value)


def envelope(
    *,
    event_id: str = "event-001",
    text: str = "hello",
    deadline: datetime = T0 + timedelta(seconds=30),
) -> TurnEnvelope:
    return TurnEnvelope(
        "lead-synthetic-001",
        event_id,
        NormalizedMessage(text, "en"),
        T0,
        deadline,
    )


def coordinator(
    trace: list[str],
    *,
    store: TracingStore | None = None,
    legacy_value=None,
    clock: FakeClock | None = None,
    intent: FakeIntent | None = None,
    kernel: FakeKernel | None = None,
):
    selected_store = store or TracingStore(trace)
    selected_intent = intent or FakeIntent(trace)
    selected_kernel = kernel or FakeKernel(trace)
    selected_lock = FakeLock(trace)
    selected_legacy = FakeLegacyReader(
        trace,
        snapshot() if legacy_value is None else legacy_value,
    )
    value = TurnCoordinator(
        lock=selected_lock,
        store=selected_store,
        legacy_reader=selected_legacy,
        importer=TracingImporter(trace),
        intent=selected_intent,
        kernel=selected_kernel,
        clock=clock or FakeClock(),
    )
    return value, selected_store, selected_lock, selected_legacy, selected_intent, selected_kernel


class Phase7CoordinatorTests(unittest.TestCase):
    def tearDown(self) -> None:
        for store in getattr(self, "stores", ()):
            store.close()

    def keep(self, store: TracingStore) -> None:
        if not hasattr(self, "stores"):
            self.stores = []
        self.stores.append(store)

    def test_persists_before_return_in_exact_order(self) -> None:
        trace: list[str] = []
        value, store, _, _, _, _ = coordinator(trace)
        self.keep(store)
        plan = value.coordinate(envelope())
        self.assertEqual(
            trace,
            [
                "claim",
                "event_lookup",
                "load_new",
                "load_legacy",
                "import",
                "import_genesis",
                "fence",
                "intent",
                "reduce",
                "commit",
            ],
        )
        self.assertFalse(plan.deduplicated)
        self.assertIs(plan.reason, TurnPlanReason.COMPLETED)
        self.assertEqual(plan.state.version, 1)
        self.assertEqual(store.commits, 1)

    def test_intent_runs_outside_turn_transaction(self) -> None:
        trace: list[str] = []
        store = TracingStore(trace)
        intent = FakeIntent(trace)
        intent.on_call = lambda: self.assertFalse(store.inner._connection.in_transaction)
        value, _, _, _, _, _ = coordinator(trace, store=store, intent=intent)
        self.keep(store)

        result = value.coordinate(envelope())

        self.assertIs(result.reason, TurnPlanReason.COMPLETED)

    def test_expired_deadline_has_zero_writes_and_zero_calls(self) -> None:
        trace: list[str] = []
        value, store, lock, _, intent, kernel = coordinator(
            trace,
            clock=FakeClock((T0 + timedelta(minutes=1),)),
        )
        self.keep(store)
        with self.assertRaises(TurnDeadlineExceeded):
            value.coordinate(envelope(deadline=T0 + timedelta(seconds=1)))
        self.assertEqual(trace, [])
        self.assertEqual((lock.calls, intent.calls, kernel.calls, store.imports, store.commits), (0, 0, 0, 0, 0))
        self.assertEqual(
            store.inner._connection.execute("SELECT count(*) FROM boundary_state").fetchone()[0],
            0,
        )

    def test_deadline_expiry_never_leaves_event_command_or_outbox_rows(self) -> None:
        cases = (
            ((T0, T0 + timedelta(minutes=1)), (0, 0)),
            ((T0, T0, T0 + timedelta(minutes=1)), (0, 0)),
            ((T0, T0, T0, T0 + timedelta(minutes=1)), (1, 1)),
            ((T0, T0, T0, T0, T0 + timedelta(minutes=1)), (1, 1)),
        )
        for values, expected_preparation in cases:
            with self.subTest(values=values):
                trace: list[str] = []
                value, store, _, _, _, _ = coordinator(
                    trace,
                    clock=FakeClock(values),
                )
                self.keep(store)
                with self.assertRaises(TurnDeadlineExceeded):
                    value.coordinate(envelope(deadline=T0 + timedelta(seconds=30)))
                counts = tuple(
                    store.inner._connection.execute(
                        f"SELECT count(*) FROM {table}"
                    ).fetchone()[0]
                    for table in (
                        "boundary_state",
                        "boundary_events",
                        "boundary_commands",
                        "boundary_outbox",
                        "legacy_import_claims",
                    )
                )
                self.assertEqual(counts[1:4], (0, 0, 0))
                self.assertEqual((counts[0], counts[4]), expected_preparation)

    def test_post_commit_replay_skips_load_legacy_intent_reduce_and_commit(self) -> None:
        trace: list[str] = []
        value, store, _, legacy, intent, kernel = coordinator(trace)
        self.keep(store)
        first = value.coordinate(envelope())
        trace.clear()
        second = value.coordinate(envelope())
        self.assertEqual(trace, ["claim", "event_lookup", "load_new"])
        self.assertTrue(second.deduplicated)
        self.assertIs(second.reason, TurnPlanReason.DUPLICATE)
        self.assertEqual(second.state, first.state)
        self.assertEqual((legacy.calls, intent.calls, kernel.calls, store.commits), (1, 1, 1, 1))

    def test_replay_ignores_changed_operational_timestamps(self) -> None:
        trace: list[str] = []
        value, store, _, _, intent, kernel = coordinator(trace)
        self.keep(store)
        first_envelope = envelope()
        first = value.coordinate(first_envelope)
        replay = TurnEnvelope(
            first_envelope.lead_key,
            first_envelope.event_id,
            first_envelope.message,
            T0 + timedelta(seconds=1),
            T0 + timedelta(seconds=31),
        )

        second = value.coordinate(replay)

        self.assertTrue(second.deduplicated)
        self.assertEqual(second.state, first.state)
        self.assertEqual((intent.calls, kernel.calls, store.commits), (1, 1, 1))

    def test_same_event_with_different_message_is_conflict(self) -> None:
        trace: list[str] = []
        value, store, _, _, intent, kernel = coordinator(trace)
        self.keep(store)
        value.coordinate(envelope())
        with self.assertRaises(TurnEventConflict):
            value.coordinate(envelope(text="different"))
        self.assertEqual((intent.calls, kernel.calls, store.commits), (1, 1, 1))

    def test_genesis_cas_loser_reloads_new_state_and_never_rereads_legacy(self) -> None:
        trace: list[str] = []
        store = RacingStore(trace)
        value, _, _, legacy, _, _ = coordinator(trace, store=store)
        self.keep(store)
        result = value.coordinate(envelope())
        self.assertEqual(result.state.version, 1)
        self.assertEqual(legacy.calls, 1)
        self.assertEqual(store.loads, 2)
        self.assertEqual(trace.count("load_legacy"), 1)

    def test_manual_or_rejected_import_never_calls_intent_or_kernel(self) -> None:
        trace: list[str] = []
        manual = snapshot(metadata={})
        value, store, _, _, intent, kernel = coordinator(trace, legacy_value=manual)
        self.keep(store)
        with self.assertRaises(TurnImportRejected) as raised:
            value.coordinate(envelope())
        self.assertIs(raised.exception.reason, TurnPlanReason.MANUAL_REVIEW)
        self.assertEqual((intent.calls, kernel.calls, store.commits), (0, 0, 0))

    def test_snapshot_identity_must_bind_requested_lead_before_genesis(self) -> None:
        trace: list[str] = []
        foreign = snapshot(lead_key="lead-synthetic-foreign")
        value, store, _, _, intent, kernel = coordinator(
            trace,
            legacy_value=foreign,
        )
        self.keep(store)

        with self.assertRaises(TurnImportRejected) as raised:
            value.coordinate(envelope())

        self.assertIs(raised.exception.disposition, ImportDisposition.REJECTED)
        self.assertIs(raised.exception.import_reason, ImportReason.CONFLICTING_IDENTITY)
        self.assertEqual((store.imports, store.commits, intent.calls, kernel.calls), (0, 0, 0, 0))
        self.assertEqual(
            store.inner._connection.execute("SELECT count(*) FROM boundary_state").fetchone()[0],
            0,
        )

    def test_invalid_intent_is_rejected_before_reduce_or_event_commit(self) -> None:
        trace: list[str] = []
        invalid = FakeIntent(trace, source_event_id="different-event")
        value, store, _, _, _, kernel = coordinator(trace, intent=invalid)
        self.keep(store)
        with self.assertRaises(InvalidIntent):
            value.coordinate(envelope())
        self.assertEqual(kernel.calls, 0)
        self.assertEqual(store.commits, 0)
        self.assertEqual(
            store.inner._connection.execute("SELECT count(*) FROM boundary_events").fetchone()[0],
            0,
        )

    def test_command_mismatch_and_unresolved_read_are_rejected_before_commit(self) -> None:
        _, _, foreign_command = complete_flow()
        for kernel in (
            FakeKernel([], command=foreign_command),
            FakeKernel([], read_request=object()),
        ):
            trace: list[str] = []
            kernel.trace = trace
            value, store, _, _, _, _ = coordinator(trace, kernel=kernel)
            self.keep(store)
            with self.assertRaises(InvalidKernelDecision):
                value.coordinate(envelope())
            self.assertEqual(store.commits, 0)

    def test_outbox_must_bind_current_workflow_before_store_commit(self) -> None:
        payload = '{"status":"queued"}'
        foreign = OutboxMessage(
            "outbox:foreign",
            "outbox:foreign:idem",
            "workflow-foreign",
            None,
            OutboxKind.SUMMARY_PRESENTED,
            "template:foreign",
            payload,
            hashlib.sha256(payload.encode()).hexdigest(),
            T0,
        )
        trace: list[str] = []
        value, store, _, _, _, _ = coordinator(
            trace,
            kernel=FakeKernel(trace, outbox=foreign),
        )
        self.keep(store)
        with self.assertRaises(InvalidKernelDecision):
            value.coordinate(envelope())
        self.assertEqual(store.commits, 0)


if __name__ == "__main__":
    unittest.main()
