"""Durable inbox worker: execute one lead-isolated turn, then acknowledge its lease."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol

from v2_application.inbox import InboxClaim, SQLiteInbox
from v2_contracts.channel import InboundBatch
from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
)
from v2_ops.recording import DegradationReason, NullOpsRecorder, OpsRecorder
from v2_ops.serialization import serialize_ops_value


class TurnExecutionReceipt(Protocol):
    artifact_hash: str


class CommittedTurn(Protocol):
    receipt: TurnExecutionReceipt
    replayed: bool


class TurnExecutor(Protocol):
    def execute(self, batch: InboundBatch) -> CommittedTurn: ...


class InboxWorkerDisposition(str, Enum):
    IDLE = "idle"
    COMMITTED = "committed"
    REPLAYED = "replayed"


@dataclass(frozen=True, slots=True)
class InboxWorkerResult:
    disposition: InboxWorkerDisposition
    batch_id: str | None = None
    turn_receipt_hash: str | None = None


class InboxTurnWorker:
    def __init__(
        self,
        *,
        inbox: SQLiteInbox,
        executor: TurnExecutor,
        quiet_window: timedelta,
        lease_ttl: timedelta,
        ops_recorder: OpsRecorder | None = None,
        ops_full_content: bool = False,
    ) -> None:
        if not isinstance(inbox, SQLiteInbox):
            raise TypeError("inbox must be a SQLiteInbox")
        if not callable(getattr(executor, "execute", None)):
            raise TypeError("executor must expose execute(batch)")
        if type(quiet_window) is not timedelta or quiet_window < timedelta(0):
            raise ValueError("quiet_window must be a non-negative timedelta")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive timedelta")
        if type(ops_full_content) is not bool:
            raise TypeError("ops_full_content must be an exact bool")
        self._inbox = inbox
        self._executor = executor
        self._quiet_window = quiet_window
        self._lease_ttl = lease_ttl
        self._ops_recorder = NullOpsRecorder() if ops_recorder is None else ops_recorder
        self._ops_full_content = ops_full_content

    def _record_claim(self, claim: InboxClaim, *, now: datetime) -> None:
        recorder = self._ops_recorder
        batch = claim.batch
        primary = batch.events[0]
        for event in batch.events:
            execution = OpsExecution(
                execution_id=event.event_id,
                lead_id=event.lead_id,
                received_at=event.occurred_at,
                trace_completeness=TraceCompleteness.PARTIAL_TRACE,
            )
            try:
                recorder.start_execution(execution)
            except BaseException:
                try:
                    recorder.report_degradation(
                        DegradationReason.EXECUTION_START_FAILED,
                        execution_id=event.event_id,
                        node_id=None,
                    )
                except BaseException:
                    pass
        try:
            summary, full = serialize_ops_value(batch)
            node = OpsNodeStart(
                execution_id=primary.event_id,
                node_type=NodeType.INBOX_CLAIM,
                ordinal=4,
                started_at=now,
                input_summary=summary,
                input_full=full if self._ops_full_content else None,
                technical_metadata={"batch_id": batch.batch_id},
            )
        except BaseException:
            try:
                recorder.report_degradation(
                    DegradationReason.RESULT_SERIALIZATION_FAILED,
                    execution_id=primary.event_id,
                    node_id=None,
                )
            except BaseException:
                pass
            return
        try:
            recorder.start_node(node)
        except BaseException:
            try:
                recorder.report_degradation(
                    DegradationReason.NODE_START_FAILED,
                    execution_id=primary.event_id,
                    node_id=node.node_id,
                )
            except BaseException:
                pass
        finished = OpsNodeFinish.from_start(
            node,
            status=ExecutionStatus.COMPLETED,
            completed_at=now,
            output_summary={
                "status": "claimed",
                "event_count": len(batch.events),
            },
        )
        try:
            recorder.finish_node(finished)
        except BaseException:
            try:
                recorder.report_degradation(
                    DegradationReason.NODE_FINISH_FAILED,
                    execution_id=primary.event_id,
                    node_id=node.node_id,
                )
            except BaseException:
                pass

    def _finish_coalesced_events(self, claim: InboxClaim, *, now: datetime) -> None:
        for event in claim.batch.events[1:]:
            execution = OpsExecution(
                execution_id=event.event_id,
                lead_id=event.lead_id,
                received_at=event.occurred_at,
                status=ExecutionStatus.COMPLETED,
                trace_completeness=TraceCompleteness.LEDGER_ONLY,
                completed_at=max(now, event.occurred_at),
                terminal_reason="coalesced_into_primary_event",
            )
            try:
                self._ops_recorder.finish_execution(execution)
            except BaseException:
                try:
                    self._ops_recorder.report_degradation(
                        DegradationReason.EXECUTION_FINISH_FAILED,
                        execution_id=event.event_id,
                        node_id=None,
                    )
                except BaseException:
                    pass

    def run_once(self, *, now: datetime) -> InboxWorkerResult:
        if (
            type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
        ):
            raise ValueError("now must be an exact UTC datetime")
        instant = now.astimezone(timezone.utc)
        claim = self._inbox.claim_ready(
            now=instant,
            quiet_window=self._quiet_window,
            lease_for=self._lease_ttl,
        )
        if claim is None:
            return InboxWorkerResult(InboxWorkerDisposition.IDLE)
        if type(claim) is not InboxClaim:
            raise TypeError("inbox returned a non-canonical claim")
        self._record_claim(claim, now=instant)
        committed = self._executor.execute(claim.batch)
        receipt_hash = committed.receipt.artifact_hash
        if (
            type(receipt_hash) is not str
            or len(receipt_hash) != 64
            or any(char not in "0123456789abcdef" for char in receipt_hash)
        ):
            raise ValueError("executor returned an invalid turn receipt hash")
        self._finish_coalesced_events(claim, now=instant)
        self._inbox.complete_claim(
            claim,
            turn_receipt_hash=receipt_hash,
            now=instant,
        )
        disposition = (
            InboxWorkerDisposition.REPLAYED
            if committed.replayed
            else InboxWorkerDisposition.COMMITTED
        )
        return InboxWorkerResult(disposition, claim.batch_id, receipt_hash)


__all__ = [
    "InboxTurnWorker",
    "InboxWorkerDisposition",
    "InboxWorkerResult",
]
