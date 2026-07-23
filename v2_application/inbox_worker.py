"""Durable inbox worker: execute one lead-isolated turn, then acknowledge its lease."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol

from v2_application.inbox import InboxClaim, SQLiteInbox
from v2_contracts.channel import InboundBatch


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
    ) -> None:
        if not isinstance(inbox, SQLiteInbox):
            raise TypeError("inbox must be a SQLiteInbox")
        if not callable(getattr(executor, "execute", None)):
            raise TypeError("executor must expose execute(batch)")
        if type(quiet_window) is not timedelta or quiet_window < timedelta(0):
            raise ValueError("quiet_window must be a non-negative timedelta")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive timedelta")
        self._inbox = inbox
        self._executor = executor
        self._quiet_window = quiet_window
        self._lease_ttl = lease_ttl

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
        committed = self._executor.execute(claim.batch)
        receipt_hash = committed.receipt.artifact_hash
        if (
            type(receipt_hash) is not str
            or len(receipt_hash) != 64
            or any(char not in "0123456789abcdef" for char in receipt_hash)
        ):
            raise ValueError("executor returned an invalid turn receipt hash")
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
