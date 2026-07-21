"""Pure ordering owner for one durable Phase 7 turn."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Protocol

from reservation_domain import ReservationCommand
from reservation_followup import PaymentSettlementCommand

from reservation_boundary.serialization import semantic_hash
from reservation_boundary.sqlite_store import IdentityConflict, StateNotFound
from reservation_boundary.types import (
    BoundaryCommit,
    BoundaryState,
    ConversationIntent,
    ImportDisposition,
    ImportReason,
    ImportResult,
    IntentRequest,
    KernelDecision,
    LegacyLeadSnapshot,
    ToolDispatchRequest,
    TurnEnvelope,
    TurnPlan,
    TurnPlanReason,
    VersionedBoundaryState,
)


class CoordinationError(RuntimeError):
    """Base class for fail-closed turn coordination outcomes."""


class TurnDeadlineExceeded(CoordinationError):
    reason = TurnPlanReason.DEADLINE_EXCEEDED


class TurnEventConflict(CoordinationError):
    reason = TurnPlanReason.MANUAL_REVIEW


class InvalidIntent(CoordinationError):
    reason = TurnPlanReason.MANUAL_REVIEW


class InvalidKernelDecision(CoordinationError):
    reason = TurnPlanReason.MANUAL_REVIEW


class TurnImportRejected(CoordinationError):
    reason = TurnPlanReason.MANUAL_REVIEW

    def __init__(self, disposition: ImportDisposition, import_reason: object) -> None:
        super().__init__(f"legacy import did not migrate: {disposition.value}")
        self.disposition = disposition
        self.import_reason = import_reason


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class TurnLockPort(Protocol):
    def claim(
        self,
        *,
        lead_key: str,
        event_id: str,
        now: datetime,
        deadline_at: datetime,
    ) -> AbstractContextManager[None]: ...


class BoundaryStorePort(Protocol):
    def event_hash(self, lead_key: str, event_id: str) -> str | None: ...

    def load_state(self, lead_key: str) -> VersionedBoundaryState: ...

    def import_genesis(
        self,
        snapshot: LegacyLeadSnapshot,
        result: ImportResult,
        *,
        claimed_at: datetime,
    ) -> VersionedBoundaryState: ...

    def acquire_fence(self, lead_key: str) -> tuple[VersionedBoundaryState, int]: ...

    def commit(self, **kwargs: object) -> VersionedBoundaryState: ...


class LegacyReaderPort(Protocol):
    def read_snapshot(self, lead_key: str) -> LegacyLeadSnapshot | None: ...


class ImporterPort(Protocol):
    def import_snapshot(self, snapshot: LegacyLeadSnapshot) -> ImportResult: ...


class IntentPort(Protocol):
    def interpret(self, request: IntentRequest) -> ConversationIntent: ...


class KernelPort(Protocol):
    def reduce(self, state: BoundaryState, intent: ConversationIntent) -> KernelDecision: ...


def _utc(value: object, field_name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise TypeError(f"{field_name} must be an exact UTC datetime")
    return value


def _before_deadline(now: datetime, deadline: datetime) -> None:
    exact_now = _utc(now, "clock.now")
    if exact_now >= deadline:
        raise TurnDeadlineExceeded("turn deadline exceeded")


def _validate_decision(
    current: VersionedBoundaryState,
    event_id: str,
    decision: object,
) -> KernelDecision:
    if type(decision) is not KernelDecision:
        raise InvalidKernelDecision("kernel returned a noncanonical decision")
    state = decision.state
    if state.lead_key != current.state.lead_key or state.version != current.version:
        raise InvalidKernelDecision("kernel changed lead identity or durable version")
    if event_id in state.processed_event_ids:
        raise InvalidKernelDecision("kernel pre-recorded the event outside the coordinator")
    if decision.read_requests:
        if any(type(item) is not ToolDispatchRequest for item in decision.read_requests):
            raise InvalidKernelDecision("kernel returned a forged read request")
        raise InvalidKernelDecision("read requests must be resolved before durable commit")
    if decision.facts:
        raise InvalidKernelDecision("facts must already be reduced into boundary state")
    for command in decision.commands:
        if type(command) is ReservationCommand:
            if state.workflow is None or command.workflow_id != state.workflow.meta.workflow_id:
                raise InvalidKernelDecision("reservation command does not bind current workflow")
        elif type(command) is PaymentSettlementCommand:
            payment_ids = {payment.subject.payment_id for payment in state.payments}
            if command.payment_id not in payment_ids:
                raise InvalidKernelDecision("payment command does not bind current payment")
        else:
            raise InvalidKernelDecision("command is outside BoundaryCommand")
    return decision


class TurnCoordinator:
    """Validate, dedupe, import, fence, reduce, persist, then return."""

    def __init__(
        self,
        *,
        lock: TurnLockPort,
        store: BoundaryStorePort,
        legacy_reader: LegacyReaderPort,
        importer: ImporterPort,
        intent: IntentPort,
        kernel: KernelPort,
        clock: ClockPort,
    ) -> None:
        self._lock = lock
        self._store = store
        self._legacy_reader = legacy_reader
        self._importer = importer
        self._intent = intent
        self._kernel = kernel
        self._clock = clock

    def _load_or_import(
        self,
        envelope: TurnEnvelope,
        *,
        claimed_at: datetime,
    ) -> VersionedBoundaryState:
        try:
            return self._store.load_state(envelope.lead_key)
        except StateNotFound:
            pass
        snapshot = self._legacy_reader.read_snapshot(envelope.lead_key)
        if snapshot is None or type(snapshot) is not LegacyLeadSnapshot:
            raise TurnImportRejected(
                ImportDisposition.MANUAL_REVIEW,
                "legacy_snapshot_missing",
            )
        if snapshot.raw_fields.get("lead_key") != envelope.lead_key:
            raise TurnImportRejected(
                ImportDisposition.REJECTED,
                ImportReason.CONFLICTING_IDENTITY,
            )
        result = self._importer.import_snapshot(snapshot)
        if type(result) is not ImportResult:
            raise TurnImportRejected(
                ImportDisposition.REJECTED,
                "invalid_import_result",
            )
        if result.disposition is not ImportDisposition.MIGRATED or result.state is None:
            raise TurnImportRejected(result.disposition, result.reason)
        try:
            return self._store.import_genesis(
                snapshot,
                result,
                claimed_at=claimed_at,
            )
        except IdentityConflict:
            return self._store.load_state(envelope.lead_key)

    def coordinate(self, envelope: TurnEnvelope) -> TurnPlan:
        if type(envelope) is not TurnEnvelope:
            raise TypeError("envelope must be the exact TurnEnvelope type")
        started_at = self._clock.now()
        _before_deadline(started_at, envelope.deadline)
        event_hash = semantic_hash(envelope)
        claim = self._lock.claim(
            lead_key=envelope.lead_key,
            event_id=envelope.event_id,
            now=started_at,
            deadline_at=envelope.deadline,
        )
        if not isinstance(claim, AbstractContextManager):
            raise TypeError("lock.claim must return a context manager")
        with claim:
            persisted_event_hash = self._store.event_hash(
                envelope.lead_key,
                envelope.event_id,
            )
            if persisted_event_hash is not None:
                if persisted_event_hash != event_hash:
                    raise TurnEventConflict("event id was reused with different content")
                current = self._store.load_state(envelope.lead_key)
                return TurnPlan(
                    current.state,
                    (),
                    (),
                    (),
                    True,
                    TurnPlanReason.DUPLICATE,
                )

            current = self._load_or_import(envelope, claimed_at=started_at)
            current, fencing_token = self._store.acquire_fence(envelope.lead_key)
            _before_deadline(self._clock.now(), envelope.deadline)
            request = IntentRequest(
                current.state,
                envelope.message,
                envelope.event_id,
                envelope.deadline,
            )
            intent = self._intent.interpret(request)
            if type(intent) is not ConversationIntent or intent.source_event_id != envelope.event_id:
                raise InvalidIntent("intent does not bind the source event")
            decision = _validate_decision(
                current,
                envelope.event_id,
                self._kernel.reduce(current.state, intent),
            )
            commit_time = self._clock.now()
            _before_deadline(commit_time, envelope.deadline)
            next_state = replace(
                decision.state,
                version=current.version + 1,
                processed_event_ids=(
                    *decision.state.processed_event_ids,
                    envelope.event_id,
                ),
            )
            boundary_commit = BoundaryCommit(
                next_state,
                decision.commands,
                decision.outbox,
                (),
            )
            persisted = self._store.commit(
                event_id=envelope.event_id,
                event_hash=event_hash,
                expected_version=current.version,
                fencing_token=fencing_token,
                commit=boundary_commit,
                committed_at=commit_time,
            )
            if persisted.state != next_state:
                raise InvalidKernelDecision("store returned a divergent committed state")
            return TurnPlan(
                persisted.state,
                (),
                decision.commands,
                decision.outbox,
                False,
                TurnPlanReason.COMPLETED,
            )


__all__ = (
    "BoundaryStorePort",
    "ClockPort",
    "CoordinationError",
    "ImporterPort",
    "IntentPort",
    "InvalidIntent",
    "InvalidKernelDecision",
    "KernelPort",
    "LegacyReaderPort",
    "TurnCoordinator",
    "TurnDeadlineExceeded",
    "TurnEventConflict",
    "TurnImportRejected",
    "TurnLockPort",
)
