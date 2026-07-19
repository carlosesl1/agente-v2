"""Pure structured handoff workflow for Phase 6.

This module owns no transport, persistence, provider, or lexical-routing capability.
It accepts only closed typed events and returns immutable transitions/effect jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from types import MappingProxyType
from typing import TypeAlias

from reservation_domain import ExecutionCertainty, ExecutionOutcome

from .types import (
    ConfirmedReservationAnchor,
    EffectRequirement,
    HandoffEffectPolicy,
    HandoffStatus,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def _require_id(value: str, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be an opaque identifier")
    normalized = value.strip()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return normalized


def _require_hash(value: str, field_name: str) -> str:
    if type(value) is not str or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _require_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_enum(value: Enum, enum_type: type[Enum], field_name: str) -> None:
    if type(value) is not enum_type:
        raise ValueError(f"{field_name} must be a {enum_type.__name__}")


def _effect_id(
    *,
    handoff_id: str,
    incident_key: str,
    kind: HandoffEffectKind,
) -> str:
    material = "\x00".join((handoff_id, incident_key, kind.value))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"handoff-effect:{digest}"


class HandoffReasonCode(str, Enum):
    CUSTOMER_REQUESTED = "customer_requested"
    SAFETY_REVIEW = "safety_review"
    PROVIDER_UNCERTAIN = "provider_uncertain"
    OPERATIONAL_REVIEW = "operational_review"


class HandoffEffectKind(str, Enum):
    CUSTOMER_ACKNOWLEDGEMENT = "customer_acknowledgement"
    INTERNAL_EMAIL = "internal_email"


class HandoffEffectFailureCode(str, Enum):
    EFFECT_UNAVAILABLE = "effect_unavailable"
    EFFECT_REJECTED = "effect_rejected"
    EFFECT_UNKNOWN = "effect_unknown"


class HandoffCancellationCode(str, Enum):
    REQUEST_WITHDRAWN = "request_withdrawn"
    OPERATOR_CANCELLED = "operator_cancelled"


class HandoffTransitionStatus(str, Enum):
    APPLIED = "applied"
    NOOP = "noop"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class HandoffTransitionReason(str, Enum):
    HANDOFF_OPENED = "handoff_opened"
    IDENTICAL_REPLAY = "identical_replay"
    DIVERGENT_INCIDENT = "divergent_incident"
    ACKNOWLEDGEMENT_RECORDED = "acknowledgement_recorded"
    EFFECT_FAILURE_RECORDED = "effect_failure_recorded"
    HANDOFF_CANCELLED = "handoff_cancelled"
    EVENT_NOT_APPLICABLE = "event_not_applicable"


class PublicNextAction(str, Enum):
    WAIT_FOR_HUMAN = "wait_for_human"
    NO_ACTION = "no_action"


@dataclass(frozen=True, slots=True)
class HandoffRequested:
    handoff_id: str
    lead_key_hash: str
    incident_key: str
    reason_code: HandoffReasonCode
    source_event_id: str
    reservation_anchor: ConfirmedReservationAnchor | None
    requested_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "handoff_id",
            _require_id(self.handoff_id, "handoff_requested.handoff_id"),
        )
        object.__setattr__(
            self,
            "lead_key_hash",
            _require_hash(self.lead_key_hash, "handoff_requested.lead_key_hash"),
        )
        object.__setattr__(
            self,
            "incident_key",
            _require_id(self.incident_key, "handoff_requested.incident_key"),
        )
        _require_enum(
            self.reason_code,
            HandoffReasonCode,
            "handoff_requested.reason_code",
        )
        object.__setattr__(
            self,
            "source_event_id",
            _require_id(self.source_event_id, "handoff_requested.source_event_id"),
        )
        if (
            self.reservation_anchor is not None
            and type(self.reservation_anchor) is not ConfirmedReservationAnchor
        ):
            raise ValueError(
                "handoff_requested.reservation_anchor must be an exact "
                "ConfirmedReservationAnchor or None"
            )
        requested_at = _require_utc(
            self.requested_at,
            "handoff_requested.requested_at",
        )
        if (
            self.reservation_anchor is not None
            and requested_at < self.reservation_anchor.confirmed_at
        ):
            raise ValueError("handoff request predates its confirmed reservation anchor")
        object.__setattr__(self, "requested_at", requested_at)


@dataclass(frozen=True, slots=True)
class HandoffAcknowledged:
    handoff_id: str
    incident_key: str
    effect_id: str
    receipt_id: str
    acknowledged_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "handoff_id",
            _require_id(self.handoff_id, "handoff_acknowledged.handoff_id"),
        )
        object.__setattr__(
            self,
            "incident_key",
            _require_id(self.incident_key, "handoff_acknowledged.incident_key"),
        )
        object.__setattr__(
            self,
            "effect_id",
            _require_id(self.effect_id, "handoff_acknowledged.effect_id"),
        )
        object.__setattr__(
            self,
            "receipt_id",
            _require_id(self.receipt_id, "handoff_acknowledged.receipt_id"),
        )
        object.__setattr__(
            self,
            "acknowledged_at",
            _require_utc(
                self.acknowledged_at,
                "handoff_acknowledged.acknowledged_at",
            ),
        )


@dataclass(frozen=True, slots=True)
class HandoffEffectFailed:
    handoff_id: str
    incident_key: str
    effect_id: str
    kind: HandoffEffectKind
    failure_code: HandoffEffectFailureCode
    failed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "handoff_id",
            _require_id(self.handoff_id, "handoff_effect_failed.handoff_id"),
        )
        object.__setattr__(
            self,
            "incident_key",
            _require_id(self.incident_key, "handoff_effect_failed.incident_key"),
        )
        object.__setattr__(
            self,
            "effect_id",
            _require_id(self.effect_id, "handoff_effect_failed.effect_id"),
        )
        _require_enum(
            self.kind,
            HandoffEffectKind,
            "handoff_effect_failed.kind",
        )
        _require_enum(
            self.failure_code,
            HandoffEffectFailureCode,
            "handoff_effect_failed.failure_code",
        )
        object.__setattr__(
            self,
            "failed_at",
            _require_utc(self.failed_at, "handoff_effect_failed.failed_at"),
        )


@dataclass(frozen=True, slots=True)
class HandoffCancelled:
    handoff_id: str
    incident_key: str
    cancellation_code: HandoffCancellationCode
    cancelled_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "handoff_id",
            _require_id(self.handoff_id, "handoff_cancelled.handoff_id"),
        )
        object.__setattr__(
            self,
            "incident_key",
            _require_id(self.incident_key, "handoff_cancelled.incident_key"),
        )
        _require_enum(
            self.cancellation_code,
            HandoffCancellationCode,
            "handoff_cancelled.cancellation_code",
        )
        object.__setattr__(
            self,
            "cancelled_at",
            _require_utc(self.cancelled_at, "handoff_cancelled.cancelled_at"),
        )


HandoffEvent: TypeAlias = (
    HandoffRequested
    | HandoffAcknowledged
    | HandoffEffectFailed
    | HandoffCancelled
)
_EVENT_TYPES = (
    HandoffRequested,
    HandoffAcknowledged,
    HandoffEffectFailed,
    HandoffCancelled,
)


@dataclass(frozen=True, slots=True)
class HandoffEffectJob:
    effect_id: str
    handoff_id: str
    incident_key: str
    kind: HandoffEffectKind
    required: bool
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "effect_id",
            _require_id(self.effect_id, "handoff_effect_job.effect_id"),
        )
        handoff_id = _require_id(
            self.handoff_id,
            "handoff_effect_job.handoff_id",
        )
        incident_key = _require_id(
            self.incident_key,
            "handoff_effect_job.incident_key",
        )
        _require_enum(self.kind, HandoffEffectKind, "handoff_effect_job.kind")
        if type(self.required) is not bool:
            raise ValueError("handoff_effect_job.required must be a boolean")
        if (
            self.kind is HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT
            and self.required is not True
        ):
            raise ValueError("customer acknowledgement job must be required")
        if (
            self.kind is HandoffEffectKind.INTERNAL_EMAIL
            and self.required is not False
        ):
            raise ValueError("internal e-mail job must be optional")
        expected_id = _effect_id(
            handoff_id=handoff_id,
            incident_key=incident_key,
            kind=self.kind,
        )
        if self.effect_id != expected_id:
            raise ValueError("handoff_effect_job.effect_id is not canonical")
        object.__setattr__(self, "handoff_id", handoff_id)
        object.__setattr__(self, "incident_key", incident_key)
        object.__setattr__(
            self,
            "created_at",
            _require_utc(self.created_at, "handoff_effect_job.created_at"),
        )

    @classmethod
    def customer_acknowledgement(
        cls,
        state: HandoffWorkflow,
    ) -> HandoffEffectJob:
        if type(state) is not HandoffWorkflow:
            raise TypeError("state must be the exact HandoffWorkflow type")
        request = state.request
        return cls(
            effect_id=_effect_id(
                handoff_id=request.handoff_id,
                incident_key=request.incident_key,
                kind=HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT,
            ),
            handoff_id=request.handoff_id,
            incident_key=request.incident_key,
            kind=HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT,
            required=True,
            created_at=request.requested_at,
        )

    @classmethod
    def internal_email(
        cls,
        state: HandoffWorkflow,
        *,
        required: bool,
    ) -> HandoffEffectJob:
        if type(state) is not HandoffWorkflow:
            raise TypeError("state must be the exact HandoffWorkflow type")
        if required is not False:
            raise ValueError("internal e-mail cannot be required")
        if state.policy.internal_email is not EffectRequirement.OPTIONAL:
            raise ValueError("internal e-mail effect is not enabled")
        request = state.request
        return cls(
            effect_id=_effect_id(
                handoff_id=request.handoff_id,
                incident_key=request.incident_key,
                kind=HandoffEffectKind.INTERNAL_EMAIL,
            ),
            handoff_id=request.handoff_id,
            incident_key=request.incident_key,
            kind=HandoffEffectKind.INTERNAL_EMAIL,
            required=False,
            created_at=request.requested_at,
        )


@dataclass(frozen=True, slots=True)
class HandoffWorkflow:
    request: HandoffRequested
    policy: HandoffEffectPolicy
    status: HandoffStatus
    queue_active: bool
    acknowledgement: HandoffAcknowledged | None
    effect_failures: tuple[HandoffEffectFailed, ...]
    cancellation: HandoffCancelled | None
    conflicting_request: HandoffRequested | None

    def __post_init__(self) -> None:
        if type(self.request) is not HandoffRequested:
            raise ValueError("handoff_workflow.request must be exact HandoffRequested")
        if type(self.policy) is not HandoffEffectPolicy:
            raise ValueError("handoff_workflow.policy must be exact HandoffEffectPolicy")
        _require_enum(self.status, HandoffStatus, "handoff_workflow.status")
        if type(self.queue_active) is not bool:
            raise ValueError("handoff_workflow.queue_active must be a boolean")
        if self.acknowledgement is not None:
            if type(self.acknowledgement) is not HandoffAcknowledged:
                raise ValueError(
                    "handoff_workflow.acknowledgement must be exact or None"
                )
            _require_event_binding(self.request, self.acknowledgement)
            expected_ack_id = _effect_id(
                handoff_id=self.request.handoff_id,
                incident_key=self.request.incident_key,
                kind=HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT,
            )
            if self.acknowledgement.effect_id != expected_ack_id:
                raise ValueError("handoff acknowledgement has divergent effect ID")
            if self.acknowledgement.acknowledged_at < self.request.requested_at:
                raise ValueError("handoff acknowledgement predates the request")
        if type(self.effect_failures) is not tuple:
            raise ValueError("handoff_workflow.effect_failures must be an exact tuple")
        for failure in self.effect_failures:
            if type(failure) is not HandoffEffectFailed:
                raise ValueError("handoff_workflow effect failure has wrong type")
            _require_event_binding(self.request, failure)
            if (
                failure.kind is HandoffEffectKind.INTERNAL_EMAIL
                and self.policy.internal_email is not EffectRequirement.OPTIONAL
            ):
                raise ValueError("handoff workflow records a disabled e-mail failure")
            expected_failure_id = _effect_id(
                handoff_id=self.request.handoff_id,
                incident_key=self.request.incident_key,
                kind=failure.kind,
            )
            if failure.effect_id != expected_failure_id:
                raise ValueError("handoff workflow failure has divergent effect ID")
            if failure.failed_at < self.request.requested_at:
                raise ValueError("handoff effect failure predates the request")
        if len({failure.effect_id for failure in self.effect_failures}) != len(
            self.effect_failures
        ):
            raise ValueError("handoff_workflow has duplicate effect failures")
        if self.cancellation is not None:
            if type(self.cancellation) is not HandoffCancelled:
                raise ValueError("handoff_workflow.cancellation must be exact or None")
            _require_event_binding(self.request, self.cancellation)
            if self.cancellation.cancelled_at < self.request.requested_at:
                raise ValueError("handoff cancellation predates the request")
        if self.conflicting_request is not None:
            if type(self.conflicting_request) is not HandoffRequested:
                raise ValueError(
                    "handoff_workflow.conflicting_request must be exact or None"
                )
            if self.conflicting_request == self.request:
                raise ValueError("conflicting request cannot equal the original request")
            if not (
                self.conflicting_request.incident_key == self.request.incident_key
                or self.conflicting_request.handoff_id == self.request.handoff_id
            ):
                raise ValueError("conflicting request is unrelated to the workflow")
        if self.status is HandoffStatus.REQUESTED:
            if self.queue_active or self.acknowledgement is not None:
                raise ValueError("requested handoff cannot have active queue or receipt")
        elif self.status in (HandoffStatus.ACTIVE, HandoffStatus.ACKNOWLEDGEMENT_PENDING):
            if not self.queue_active or self.acknowledgement is not None:
                raise ValueError("pending handoff status matrix is inconsistent")
        elif self.status is HandoffStatus.ACKNOWLEDGED:
            if not self.queue_active or self.acknowledgement is None:
                raise ValueError("acknowledged handoff requires queue and receipt")
        elif self.status is HandoffStatus.MANUAL_REVIEW:
            if not self.queue_active:
                raise ValueError("manual-review handoff must keep queue active")
        elif self.status is HandoffStatus.COMPLETED:
            if self.queue_active:
                raise ValueError("completed handoff cannot keep queue active")
        elif self.status is HandoffStatus.CANCELLED:
            if self.queue_active or self.cancellation is None:
                raise ValueError("cancelled handoff requires cancellation and closed queue")

    @classmethod
    def from_request(
        cls,
        event: HandoffRequested,
        policy: HandoffEffectPolicy,
    ) -> HandoffWorkflow:
        if type(event) is not HandoffRequested:
            raise TypeError("event must be exact HandoffRequested")
        if type(policy) is not HandoffEffectPolicy:
            raise TypeError("policy must be exact HandoffEffectPolicy")
        return cls(
            request=event,
            policy=policy,
            status=HandoffStatus.ACKNOWLEDGEMENT_PENDING,
            queue_active=True,
            acknowledgement=None,
            effect_failures=(),
            cancellation=None,
            conflicting_request=None,
        )


@dataclass(frozen=True, slots=True)
class HandoffTransition:
    state: HandoffWorkflow
    status: HandoffTransitionStatus
    reason: HandoffTransitionReason
    events: tuple[HandoffEvent, ...]
    effect_jobs: tuple[HandoffEffectJob, ...]

    def __post_init__(self) -> None:
        if type(self.state) is not HandoffWorkflow:
            raise ValueError("handoff_transition.state must be exact HandoffWorkflow")
        _require_enum(
            self.status,
            HandoffTransitionStatus,
            "handoff_transition.status",
        )
        _require_enum(
            self.reason,
            HandoffTransitionReason,
            "handoff_transition.reason",
        )
        if type(self.events) is not tuple or any(
            type(event) not in _EVENT_TYPES for event in self.events
        ):
            raise ValueError("handoff_transition.events has wrong type")
        if type(self.effect_jobs) is not tuple or any(
            type(job) is not HandoffEffectJob for job in self.effect_jobs
        ):
            raise ValueError("handoff_transition.effect_jobs has wrong type")
        if self.status in (
            HandoffTransitionStatus.NOOP,
            HandoffTransitionStatus.REJECTED,
        ) and (self.events or self.effect_jobs):
            raise ValueError("non-applied transition cannot emit events or jobs")
        if self.status is HandoffTransitionStatus.CONFLICT and (
            len(self.events) != 1 or self.effect_jobs
        ):
            raise ValueError("conflict transition must record one event and no jobs")
        if self.status is HandoffTransitionStatus.APPLIED and not self.events:
            raise ValueError("applied transition must record an event")


@dataclass(frozen=True, slots=True)
class PublicHandoffProjection:
    public_text: str
    next_action: PublicNextAction
    reservation_outcome: ExecutionOutcome | None

    def __post_init__(self) -> None:
        if type(self.public_text) is not str:
            raise ValueError("public_handoff_projection.public_text must be text")
        canonical_text = " ".join(self.public_text.split())
        if not canonical_text or canonical_text != self.public_text:
            raise ValueError("public_handoff_projection.public_text is not canonical")
        _require_enum(
            self.next_action,
            PublicNextAction,
            "public_handoff_projection.next_action",
        )
        if (
            self.reservation_outcome is not None
            and type(self.reservation_outcome) is not ExecutionOutcome
        ):
            raise ValueError(
                "public_handoff_projection.reservation_outcome must be exact or None"
            )


_EVENT_NAMES = MappingProxyType(
    {
        HandoffRequested: "handoff_requested",
        HandoffAcknowledged: "handoff_acknowledged",
        HandoffEffectFailed: "handoff_effect_failed",
        HandoffCancelled: "handoff_cancelled",
    }
)

_TRANSITION_MATRIX = MappingProxyType(
    {
        "requested": MappingProxyType(
            {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "reject",
                "handoff_effect_failed": "reject",
                "handoff_cancelled": "apply_cancellation",
            }
        ),
        "active": MappingProxyType(
            {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "apply_acknowledgement",
                "handoff_effect_failed": "record_effect_failure",
                "handoff_cancelled": "apply_cancellation",
            }
        ),
        "acknowledgement_pending": MappingProxyType(
            {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "apply_acknowledgement",
                "handoff_effect_failed": "record_effect_failure",
                "handoff_cancelled": "apply_cancellation",
            }
        ),
        "acknowledged": MappingProxyType(
            {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "apply_acknowledgement",
                "handoff_effect_failed": "record_effect_failure",
                "handoff_cancelled": "apply_cancellation",
            }
        ),
        "manual_review": MappingProxyType(
            {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "apply_acknowledgement",
                "handoff_effect_failed": "record_effect_failure",
                "handoff_cancelled": "apply_cancellation",
            }
        ),
        "completed": MappingProxyType(
            {
                "handoff_requested": "replay_only",
                "handoff_acknowledged": "replay_only",
                "handoff_effect_failed": "replay_only",
                "handoff_cancelled": "reject",
            }
        ),
        "cancelled": MappingProxyType(
            {
                "handoff_requested": "replay_only",
                "handoff_acknowledged": "replay_only",
                "handoff_effect_failed": "replay_only",
                "handoff_cancelled": "apply_cancellation",
            }
        ),
    }
)


def handoff_transition_matrix() -> dict[str, dict[str, str]]:
    """Return a detached literal transition matrix for bilateral validation."""

    return {status: dict(row) for status, row in _TRANSITION_MATRIX.items()}


def _require_event_binding(
    request: HandoffRequested,
    event: HandoffAcknowledged | HandoffEffectFailed | HandoffCancelled,
) -> None:
    if (
        event.handoff_id != request.handoff_id
        or event.incident_key != request.incident_key
    ):
        raise ValueError("handoff event belongs to another workflow or incident")


def _transition(
    state: HandoffWorkflow,
    status: HandoffTransitionStatus,
    reason: HandoffTransitionReason,
    *,
    events: tuple[HandoffEvent, ...] = (),
    effect_jobs: tuple[HandoffEffectJob, ...] = (),
) -> HandoffTransition:
    return HandoffTransition(
        state=state,
        status=status,
        reason=reason,
        events=events,
        effect_jobs=effect_jobs,
    )


def new_handoff(
    event: HandoffRequested,
    policy: HandoffEffectPolicy,
) -> HandoffTransition:
    """Open one structured handoff and derive policy-bounded effect jobs."""

    if type(event) is not HandoffRequested:
        raise TypeError("event must be exact HandoffRequested")
    if type(policy) is not HandoffEffectPolicy:
        raise TypeError("policy must be exact HandoffEffectPolicy")
    state = HandoffWorkflow.from_request(event, policy)
    jobs = [HandoffEffectJob.customer_acknowledgement(state)]
    if policy.internal_email is EffectRequirement.OPTIONAL:
        jobs.append(HandoffEffectJob.internal_email(state, required=False))
    return _transition(
        state,
        HandoffTransitionStatus.APPLIED,
        HandoffTransitionReason.HANDOFF_OPENED,
        events=(event,),
        effect_jobs=tuple(jobs),
    )


def _reduce_request(
    state: HandoffWorkflow,
    event: HandoffRequested,
    *,
    replay_only: bool,
) -> HandoffTransition:
    if event == state.request or event == state.conflicting_request:
        return _transition(
            state,
            HandoffTransitionStatus.NOOP,
            HandoffTransitionReason.IDENTICAL_REPLAY,
        )
    if state.conflicting_request is not None:
        raise ValueError("handoff already records a different conflicting request")
    related = (
        event.incident_key == state.request.incident_key
        or event.handoff_id == state.request.handoff_id
    )
    if replay_only:
        if related:
            raise ValueError("terminal handoff rejects divergent request replay")
        raise ValueError("handoff request is unrelated to the workflow")
    if not related:
        raise ValueError("handoff request is unrelated to the workflow")
    conflicted = replace(
        state,
        status=HandoffStatus.MANUAL_REVIEW,
        queue_active=True,
        conflicting_request=event,
    )
    return _transition(
        conflicted,
        HandoffTransitionStatus.CONFLICT,
        HandoffTransitionReason.DIVERGENT_INCIDENT,
        events=(event,),
    )


def _reduce_acknowledgement(
    state: HandoffWorkflow,
    event: HandoffAcknowledged,
    *,
    replay_only: bool,
) -> HandoffTransition:
    _require_event_binding(state.request, event)
    expected = HandoffEffectJob.customer_acknowledgement(state)
    if event.effect_id != expected.effect_id:
        raise ValueError("acknowledgement references a divergent effect")
    if state.acknowledgement is not None:
        if event == state.acknowledgement:
            return _transition(
                state,
                HandoffTransitionStatus.NOOP,
                HandoffTransitionReason.IDENTICAL_REPLAY,
            )
        raise ValueError("acknowledgement replay has divergent receipt")
    if replay_only:
        return _transition(
            state,
            HandoffTransitionStatus.REJECTED,
            HandoffTransitionReason.EVENT_NOT_APPLICABLE,
        )
    acknowledged = replace(
        state,
        status=HandoffStatus.ACKNOWLEDGED,
        queue_active=True,
        acknowledgement=event,
    )
    return _transition(
        acknowledged,
        HandoffTransitionStatus.APPLIED,
        HandoffTransitionReason.ACKNOWLEDGEMENT_RECORDED,
        events=(event,),
    )


def _expected_failure_job(
    state: HandoffWorkflow,
    event: HandoffEffectFailed,
) -> HandoffEffectJob:
    if event.kind is HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT:
        return HandoffEffectJob.customer_acknowledgement(state)
    if event.kind is HandoffEffectKind.INTERNAL_EMAIL:
        return HandoffEffectJob.internal_email(state, required=False)
    raise TypeError("unsupported handoff effect kind")


def _reduce_effect_failure(
    state: HandoffWorkflow,
    event: HandoffEffectFailed,
    *,
    replay_only: bool,
) -> HandoffTransition:
    _require_event_binding(state.request, event)
    expected = _expected_failure_job(state, event)
    if event.effect_id != expected.effect_id:
        raise ValueError("effect failure references a divergent effect")
    for existing in state.effect_failures:
        if existing.effect_id != event.effect_id:
            continue
        if existing == event:
            return _transition(
                state,
                HandoffTransitionStatus.NOOP,
                HandoffTransitionReason.IDENTICAL_REPLAY,
            )
        raise ValueError("effect failure replay has divergent payload")
    if replay_only:
        return _transition(
            state,
            HandoffTransitionStatus.REJECTED,
            HandoffTransitionReason.EVENT_NOT_APPLICABLE,
        )
    next_status = state.status
    if (
        event.kind is HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT
        and state.acknowledgement is None
    ):
        next_status = HandoffStatus.MANUAL_REVIEW
    failed = replace(
        state,
        status=next_status,
        queue_active=True,
        effect_failures=state.effect_failures + (event,),
    )
    return _transition(
        failed,
        HandoffTransitionStatus.APPLIED,
        HandoffTransitionReason.EFFECT_FAILURE_RECORDED,
        events=(event,),
    )


def _reduce_cancellation(
    state: HandoffWorkflow,
    event: HandoffCancelled,
    *,
    reject: bool,
) -> HandoffTransition:
    _require_event_binding(state.request, event)
    if state.cancellation is not None:
        if event == state.cancellation:
            return _transition(
                state,
                HandoffTransitionStatus.NOOP,
                HandoffTransitionReason.IDENTICAL_REPLAY,
            )
        raise ValueError("cancellation replay has divergent payload")
    if reject:
        return _transition(
            state,
            HandoffTransitionStatus.REJECTED,
            HandoffTransitionReason.EVENT_NOT_APPLICABLE,
        )
    cancelled = replace(
        state,
        status=HandoffStatus.CANCELLED,
        queue_active=False,
        cancellation=event,
    )
    return _transition(
        cancelled,
        HandoffTransitionStatus.APPLIED,
        HandoffTransitionReason.HANDOFF_CANCELLED,
        events=(event,),
    )


def reduce_handoff(
    state: HandoffWorkflow,
    event: HandoffEvent,
) -> HandoffTransition:
    """Reduce one exact structured event through the literal state/event matrix."""

    if type(state) is not HandoffWorkflow:
        raise TypeError("state must be exact HandoffWorkflow")
    event_type = type(event)
    event_name = _EVENT_NAMES.get(event_type)
    if event_name is None:
        raise TypeError("event must be an exact closed handoff event type")
    action = _TRANSITION_MATRIX[state.status.value][event_name]
    if action == "reject":
        return _transition(
            state,
            HandoffTransitionStatus.REJECTED,
            HandoffTransitionReason.EVENT_NOT_APPLICABLE,
        )
    if event_type is HandoffRequested:
        return _reduce_request(state, event, replay_only=action == "replay_only")
    if event_type is HandoffAcknowledged:
        return _reduce_acknowledgement(
            state,
            event,
            replay_only=action == "replay_only",
        )
    if event_type is HandoffEffectFailed:
        return _reduce_effect_failure(
            state,
            event,
            replay_only=action == "replay_only",
        )
    if event_type is HandoffCancelled:
        return _reduce_cancellation(state, event, reject=action == "reject")
    raise TypeError("unsupported handoff event type")


def _reservation_sentence(outcome: ExecutionOutcome | None) -> str:
    if outcome is None:
        return "A reserva não foi criada."
    if outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED:
        return "A reserva foi criada."
    if outcome.certainty in (
        ExecutionCertainty.NOT_CALLED,
        ExecutionCertainty.CALLED_NO_EFFECT,
    ):
        return "A reserva não foi criada."
    if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN:
        return "Ainda não sabemos se a reserva foi criada."
    raise ValueError("unsupported reservation outcome certainty")


def _canonical_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError(f"{field_name} must be text or None")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def project_handoff_public_reply(
    state: HandoffWorkflow,
    reservation_outcome: ExecutionOutcome | None,
    *,
    stale_confirmation_question: str | None = None,
    stale_missing_slots_question: str | None = None,
    prior_followup_text: str | None = None,
) -> PublicHandoffProjection:
    """Project a deterministic public reply with terminal handoff precedence."""

    if type(state) is not HandoffWorkflow:
        raise TypeError("state must be exact HandoffWorkflow")
    if reservation_outcome is not None and type(reservation_outcome) is not ExecutionOutcome:
        raise ValueError("reservation_outcome must be exact ExecutionOutcome or None")
    _canonical_optional_text(
        stale_confirmation_question,
        "stale_confirmation_question",
    )
    _canonical_optional_text(
        stale_missing_slots_question,
        "stale_missing_slots_question",
    )
    _canonical_optional_text(prior_followup_text, "prior_followup_text")

    anchor = state.request.reservation_anchor
    if anchor is not None:
        if reservation_outcome is None:
            reservation_outcome = anchor.reservation_outcome
        elif reservation_outcome != anchor.reservation_outcome:
            raise ValueError("reservation outcome diverges from the confirmed anchor")

    reservation_text = _reservation_sentence(reservation_outcome)
    if state.status is HandoffStatus.CANCELLED:
        handoff_text = "O atendimento humano foi cancelado."
        next_action = PublicNextAction.NO_ACTION
    elif state.status is HandoffStatus.COMPLETED:
        handoff_text = "O atendimento humano foi concluído."
        next_action = PublicNextAction.NO_ACTION
    else:
        handoff_text = "O atendimento humano foi acionado. Aguarde o contato da equipe."
        next_action = PublicNextAction.WAIT_FOR_HUMAN

    return PublicHandoffProjection(
        public_text=" ".join((reservation_text, handoff_text)),
        next_action=next_action,
        reservation_outcome=reservation_outcome,
    )


__all__ = [
    "HandoffReasonCode",
    "HandoffEffectKind",
    "HandoffEffectFailureCode",
    "HandoffCancellationCode",
    "HandoffTransitionStatus",
    "HandoffTransitionReason",
    "PublicNextAction",
    "HandoffRequested",
    "HandoffAcknowledged",
    "HandoffEffectFailed",
    "HandoffCancelled",
    "HandoffEvent",
    "HandoffEffectJob",
    "HandoffWorkflow",
    "HandoffTransition",
    "PublicHandoffProjection",
    "handoff_transition_matrix",
    "new_handoff",
    "reduce_handoff",
    "project_handoff_public_reply",
]
