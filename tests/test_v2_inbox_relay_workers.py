from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from reservation_domain import reduce
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.phase5_helpers import workflow_events
from v2_application.inbox import SQLiteInbox
from v2_application.inbox_worker import InboxTurnWorker, InboxWorkerDisposition
from v2_application.relay_worker import (
    BoundaryRelayWorker,
    CommandRelayClaim,
    RelayWorkerDisposition,
    build_reservation_relay_bundle,
    reservation_target_operation_id,
)
from v2_contracts.channel import AcceptDisposition, InboundEvent
from v2_host.worker_main import WorkerCycle, WorkerQueue

NOW = datetime(2026, 7, 23, 20, 0, tzinfo=timezone.utc)
SOURCE_RECEIPT_HASH = "a" * 64


@dataclass(frozen=True, slots=True)
class FakeReceipt:
    artifact_hash: str


@dataclass(frozen=True, slots=True)
class FakeCommittedTurn:
    receipt: FakeReceipt
    replayed: bool


class ReplayExecutor:
    def __init__(self) -> None:
        self.receipts: dict[str, FakeReceipt] = {}
        self.calls = 0

    def execute(self, batch):
        self.calls += 1
        receipt = self.receipts.get(batch.batch_id)
        replayed = receipt is not None
        if receipt is None:
            receipt = FakeReceipt(hashlib.sha256(batch.batch_id.encode()).hexdigest())
            self.receipts[batch.batch_id] = receipt
        return FakeCommittedTurn(receipt, replayed)


class FailFirstCompletionInbox(SQLiteInbox):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.fail_completion = True

    def complete_claim(self, claim, *, turn_receipt_hash: str, now: datetime) -> None:
        if self.fail_completion:
            self.fail_completion = False
            raise RuntimeError("synthetic crash before inbox completion")
        super().complete_claim(claim, turn_receipt_hash=turn_receipt_hash, now=now)


def _event() -> InboundEvent:
    return InboundEvent(
        event_id="manychat-event:task4-001",
        lead_id="manychat:task4-lead",
        subscriber_id="task4-subscriber",
        conversation_id="conversation:task4",
        text="Quero reservar.",
        media_url=None,
        media_type=None,
        occurred_at=NOW - timedelta(seconds=10),
        payload_hash="b" * 64,
    )


def test_inbox_crash_after_turn_commit_replays_receipt_then_completes_claim(
    tmp_path: Path,
) -> None:
    inbox = FailFirstCompletionInbox(tmp_path / "inbox.sqlite3")
    assert inbox.accept(_event()) is AcceptDisposition.ACCEPTED
    executor = ReplayExecutor()
    worker = InboxTurnWorker(
        inbox=inbox,
        executor=executor,
        quiet_window=timedelta(0),
        lease_ttl=timedelta(seconds=10),
    )

    with pytest.raises(RuntimeError, match="synthetic crash"):
        worker.run_once(now=NOW)

    result = worker.run_once(now=NOW + timedelta(seconds=11))

    assert result.disposition is InboxWorkerDisposition.REPLAYED
    assert executor.calls == 2
    assert len(executor.receipts) == 1
    assert inbox.processed_count() == 1
    assert inbox.pending_count() == 0
    assert inbox.claimed_count() == 0


def _reservation_command():
    state, script = workflow_events(
        "cloudbeds",
        workflow_id="workflow:v2-task4-relay",
    )
    commands = []
    for event, _ in script:
        transition = reduce(state, event)
        state = transition.state
        commands.extend(transition.commands)
    assert len(commands) == 1
    return commands[0]


class OneRelaySource:
    def __init__(self, claim: CommandRelayClaim) -> None:
        self.claim = claim
        self.acks = []
        self.claim_calls = 0

    def claim_command_relay(
        self, *, worker_id: str, now: datetime, lease_ttl: timedelta
    ):
        self.claim_calls += 1
        if self.acks:
            return None
        return self.claim

    def complete_command_relay(self, claim, receipt, *, now: datetime) -> None:
        assert claim == self.claim
        self.acks.append(receipt)

    def release_command_relay(self, claim, *, now: datetime) -> None:
        raise AssertionError("successful relay must not be released")


class ReservationTarget:
    def __init__(self, store: SQLiteUnitOfWork) -> None:
        self.store = store
        self.calls = 0

    def accept_boundary_reservation(
        self,
        *,
        operation_id: str,
        source_turn_receipt_hash: str,
        bundle,
    ):
        self.calls += 1
        return self.store.accept_boundary_reservation(
            operation_id=operation_id,
            source_turn_receipt_hash=source_turn_receipt_hash,
            bundle=bundle,
        )


def test_boundary_command_relay_uses_real_phase5_bundle_and_target_receipt(
    tmp_path: Path,
) -> None:
    command = _reservation_command()
    bundle = build_reservation_relay_bundle(command)
    operation_id = reservation_target_operation_id(
        bundle_hash=bundle.artifact_hash,
        source_turn_receipt_hash=SOURCE_RECEIPT_HASH,
    )
    claim = CommandRelayClaim(
        relay_id="relay:task4-reservation",
        command_id=command.command_id,
        bundle_bytes=bundle.to_canonical_bytes(),
        bundle_hash=bundle.artifact_hash,
        source_turn_receipt_hash=SOURCE_RECEIPT_HASH,
        target_operation_id=operation_id,
        worker_id="worker:boundary-relay",
        fencing_token=1,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    source = OneRelaySource(claim)
    target_store = SQLiteUnitOfWork.open_v6(tmp_path / "reservation.sqlite3")
    target = ReservationTarget(target_store)
    worker = BoundaryRelayWorker(
        boundary=source,
        reservation_target=target,
        worker_id="worker:boundary-relay",
        lease_ttl=timedelta(seconds=30),
    )
    try:
        first = worker.run_once(now=NOW)
        second = worker.run_once(now=NOW + timedelta(seconds=1))

        assert first.disposition is RelayWorkerDisposition.RELAYED
        assert second.disposition is RelayWorkerDisposition.IDLE
        assert target.calls == 1
        assert len(source.acks) == 1
        assert source.acks[0].operation_id == operation_id
        assert target_store.load_command(command.command_id) == command
    finally:
        target_store.close()


class ConcreteRunner:
    def run_once(self, *, now: datetime):
        return now


class NoopWorker:
    def run_once(self, *, now: datetime):
        return None


def test_worker_cycle_exposes_all_concrete_stages_and_rejects_noop() -> None:
    workers = {queue: ConcreteRunner() for queue in WorkerQueue}
    cycle = WorkerCycle(workers)

    assert tuple(cycle.workers) == tuple(WorkerQueue)
    assert all(
        type(worker).__name__ not in {"NoopWorker", "FallbackWorker"}
        for worker in cycle.workers.values()
    )

    workers[WorkerQueue.RECONCILIATION] = NoopWorker()
    with pytest.raises(ValueError, match="noop/fallback"):
        WorkerCycle(workers)
