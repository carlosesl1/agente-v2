"""Durable boundary relay workers and canonical Phase 5 bundle construction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol

from reservation_boundary.effects import (
    CommandRelayClaim,
    HandoffRelayBundle,
    InternalJobKind,
    InternalRelayClaim,
    ReservationRelayBundle,
    TargetOperationReceipt,
    phase5_outbox_seed_bytes,
    target_operation_id,
)
from reservation_confirmation import SummaryLocale, prepare_summary
from reservation_domain import (
    build_commercial_draft,
    dumps_command,
    dumps_event,
    dumps_state,
    new_workflow,
    reduce,
)
from reservation_domain.types import (
    ConfirmationDecisionKind,
    ConfirmationReceived,
    ReadyToSummarizeState,
    ReservationCommand,
)
from reservation_execution import summary_outbox_message
from reservation_followup import (
    HandoffEffectPolicy,
    HandoffRequested,
    HandoffWorkflow,
)
from reservation_followup import (
    semantic_hash as handoff_semantic_hash,
)
from reservation_followup import (
    to_wire_json as to_handoff_wire_json,
)


class RelayWorkerDisposition(str, Enum):
    IDLE = "idle"
    RELAYED = "relayed"
    REPLAYED = "replayed"


@dataclass(frozen=True, slots=True)
class RelayWorkerResult:
    disposition: RelayWorkerDisposition
    relay_id: str | None = None
    receipt: TargetOperationReceipt | None = None


class CommandRelaySource(Protocol):
    def claim_command_relay(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> CommandRelayClaim | None: ...

    def complete_command_relay(
        self,
        claim: CommandRelayClaim,
        receipt: TargetOperationReceipt,
        *,
        now: datetime,
    ) -> None: ...

    def release_command_relay(
        self,
        claim: CommandRelayClaim,
        *,
        now: datetime,
    ) -> None: ...


class ReservationRelayTarget(Protocol):
    def accept_boundary_reservation(
        self,
        *,
        operation_id: str,
        source_turn_receipt_hash: str,
        bundle: ReservationRelayBundle,
    ) -> TargetOperationReceipt: ...


class HandoffRelayTarget(Protocol):
    def accept_boundary_handoff(
        self,
        *,
        operation_id: str,
        source_turn_receipt_hash: str,
        bundle: HandoffRelayBundle,
    ) -> TargetOperationReceipt: ...


def _utc(value: datetime, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value.astimezone(timezone.utc)


def _relay_id(prefix: str, command_id: str) -> str:
    digest = hashlib.sha256(
        b"v2-reservation-relay-event-v1\0"
        + prefix.encode()
        + b"\0"
        + command_id.encode()
    ).hexdigest()
    return f"{prefix}:{digest[:40]}"


def reservation_target_operation_id(
    *,
    bundle_hash: str,
    source_turn_receipt_hash: str,
) -> str:
    # Phase 5 intentionally reuses the original closed target-operation namespace.
    return target_operation_id(
        InternalJobKind.HANDOFF,
        bundle_hash,
        source_turn_receipt_hash,
    )


def build_reservation_relay_bundle(
    command: ReservationCommand,
) -> ReservationRelayBundle:
    """Build a minimal complete Phase 5 replay that emits the exact command."""

    if type(command) is not ReservationCommand:
        raise TypeError("command must be an exact ReservationCommand")
    summary_at = command.created_at - timedelta(microseconds=1)
    draft = build_commercial_draft(
        draft_id=command.draft_id,
        version=command.draft_version,
        created_at=summary_at,
        components=command.payload.components,
        customer=command.payload.customer,
        terms=command.payload.terms,
    )
    if draft.subject_signature != command.subject_signature:
        raise ValueError("command payload does not reproduce its subject signature")
    genesis = ReadyToSummarizeState(
        meta=new_workflow(
            workflow_id=command.workflow_id,
            started_at=summary_at,
        ).meta,
        draft=draft,
    )
    prepared = prepare_summary(
        genesis,
        locale=SummaryLocale.PT_BR,
        presented_at=summary_at,
    )
    summary_message = summary_outbox_message(
        workflow_id=command.workflow_id,
        prepared=prepared,
    )
    after_summary = reduce(genesis, prepared.event)
    confirmation = ConfirmationReceived(
        event_id=_relay_id("event", command.command_id),
        occurred_at=command.created_at,
        confirmation_event_id=_relay_id("confirmation", command.command_id),
        decision=ConfirmationDecisionKind.ACCEPT,
        target_draft_version=command.draft_version,
        subject_signature=command.subject_signature,
    )
    after_confirmation = reduce(after_summary.state, confirmation)
    if (
        len(after_confirmation.commands) != 1
        or after_confirmation.commands[0] != command
    ):
        raise ValueError("synthetic Phase 5 replay did not reproduce the exact command")
    return ReservationRelayBundle.create(
        genesis_state=dumps_state(genesis).encode("utf-8"),
        phase5_events=(
            dumps_event(prepared.event).encode("utf-8"),
            dumps_event(confirmation).encode("utf-8"),
        ),
        summary_outboxes=(phase5_outbox_seed_bytes(summary_message),),
        expected_final_state=dumps_state(after_confirmation.state).encode("utf-8"),
        command_ledger_seed=dumps_command(command).encode("utf-8"),
    )


def build_handoff_relay_bundle(request: HandoffRequested) -> HandoffRelayBundle:
    if type(request) is not HandoffRequested:
        raise TypeError("request must be exact HandoffRequested")
    policy = HandoffEffectPolicy.default_email_disabled()
    workflow = HandoffWorkflow.from_request(request, policy)
    return HandoffRelayBundle.create(
        request_bytes=to_handoff_wire_json(request).encode("utf-8"),
        policy_bytes=to_handoff_wire_json(policy).encode("utf-8"),
        history_bytes=(),
        expected_final_state_hash=handoff_semantic_hash(workflow),
    )


class BoundaryRelayWorker:
    def __init__(
        self,
        *,
        boundary: CommandRelaySource,
        reservation_target: ReservationRelayTarget,
        handoff_target: HandoffRelayTarget | None = None,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        if not callable(getattr(boundary, "claim_command_relay", None)):
            raise TypeError("boundary must expose command relay claim APIs")
        if not callable(
            getattr(reservation_target, "accept_boundary_reservation", None)
        ):
            raise TypeError("reservation_target must accept boundary reservations")
        if handoff_target is not None and not callable(
            getattr(handoff_target, "accept_boundary_handoff", None)
        ):
            raise TypeError("handoff_target must accept boundary handoffs or be None")
        if type(worker_id) is not str or not worker_id:
            raise ValueError("worker_id must be non-empty text")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        self._boundary = boundary
        self._reservation_target = reservation_target
        self._handoff_target = handoff_target
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> RelayWorkerResult:
        instant = _utc(now, "now")
        claim = self._boundary.claim_command_relay(
            worker_id=self._worker_id,
            now=instant,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return self._run_handoff(instant)
        if type(claim) is not CommandRelayClaim:
            raise TypeError("boundary returned a non-canonical command relay claim")
        try:
            bundle = ReservationRelayBundle.from_canonical_bytes(claim.bundle_bytes)
            receipt = self._reservation_target.accept_boundary_reservation(
                operation_id=claim.target_operation_id,
                source_turn_receipt_hash=claim.source_turn_receipt_hash,
                bundle=bundle,
            )
            if type(receipt) is not TargetOperationReceipt:
                raise TypeError("reservation target returned a non-canonical receipt")
            self._boundary.complete_command_relay(
                claim,
                receipt,
                now=instant,
            )
        except BaseException:
            self._boundary.release_command_relay(claim, now=instant)
            raise
        return RelayWorkerResult(
            RelayWorkerDisposition.RELAYED,
            claim.relay_id,
            receipt,
        )

    def _run_handoff(self, instant: datetime) -> RelayWorkerResult:
        claim_method = getattr(self._boundary, "claim_internal_job", None)
        if self._handoff_target is None or not callable(claim_method):
            return RelayWorkerResult(RelayWorkerDisposition.IDLE)
        claim = claim_method(
            worker_id=self._worker_id,
            now=instant,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return RelayWorkerResult(RelayWorkerDisposition.IDLE)
        if type(claim) is not InternalRelayClaim:
            raise TypeError("boundary returned a non-canonical internal relay claim")
        try:
            if claim.job_kind is not InternalJobKind.HANDOFF:
                raise ValueError("unsupported internal relay job kind")
            bundle = HandoffRelayBundle.from_canonical_bytes(claim.artifact_bytes)
            receipt = self._handoff_target.accept_boundary_handoff(
                operation_id=claim.target_operation_id,
                source_turn_receipt_hash=claim.source_turn_receipt_hash,
                bundle=bundle,
            )
            if type(receipt) is not TargetOperationReceipt:
                raise TypeError("handoff target returned a non-canonical receipt")
            self._boundary.complete_internal_job(claim, receipt, now=instant)
        except BaseException:
            self._boundary.release_internal_job(claim, now=instant)
            raise
        return RelayWorkerResult(
            RelayWorkerDisposition.RELAYED,
            claim.job_id,
            receipt,
        )


__all__ = [
    "BoundaryRelayWorker",
    "CommandRelayClaim",
    "RelayWorkerDisposition",
    "RelayWorkerResult",
    "build_handoff_relay_bundle",
    "build_reservation_relay_bundle",
    "reservation_target_operation_id",
]
