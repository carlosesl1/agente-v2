"""Caller-supplied execution adapter port with no default capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from reservation_domain import ExecutionOutcome, ReservationCommand

from .types import DispatchPermit, DispatchRequest, _require_hash, _require_id


_PREPARATION_FAILURE_REASONS = frozenset(
    {
        "active_handoff",
        "booking_profile_incomplete",
        "canary_passenger_count_unsupported",
        "command_serialization_failed",
        "package_peer_manual_review",
        "command_validation_failed",
        "effect_guard_unavailable",
        "private_binding_component_count",
        "private_binding_mismatch",
        "private_binding_resolver_unavailable",
        "private_binding_unavailable",
        "synthetic_preparation_failure",
        "synthetic_timeout",
        "unsupported_operation",
        "write_gate_closed",
    }
)


def _require_preparation_failure_reason(value: str) -> str:
    reason = _require_id(value, "preparation_failure.reason")
    if reason not in _PREPARATION_FAILURE_REASONS:
        raise ValueError("preparation_failure.reason is outside the closed vocabulary")
    return reason


@dataclass(frozen=True, slots=True)
class PreparationFailure(Exception):
    reason: str
    retryable: bool
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason",
            _require_preparation_failure_reason(self.reason),
        )
        if type(self.retryable) is not bool:
            raise ValueError("preparation_failure.retryable must be a boolean")
        if type(self.evidence) is not tuple:
            raise ValueError("preparation_failure.evidence must be a tuple")
        normalized = tuple(
            sorted(
                {
                    _require_hash(item, "preparation_failure.evidence")
                    for item in self.evidence
                }
            )
        )
        object.__setattr__(self, "evidence", normalized)


@runtime_checkable
class ExecutionAdapter(Protocol):
    adapter_id: str
    adapter_version: int

    def prepare(self, command: ReservationCommand) -> DispatchRequest: ...

    def dispatch(
        self,
        request: DispatchRequest,
        *,
        idempotency_key: str,
    ) -> ExecutionOutcome: ...


@runtime_checkable
class FencedExecutionAdapter(Protocol):
    adapter_id: str
    adapter_version: int

    def prepare(self, command: ReservationCommand) -> DispatchRequest: ...

    def dispatch_fenced(
        self,
        permit: DispatchPermit,
        request: DispatchRequest,
        *,
        idempotency_key: str,
    ) -> ExecutionOutcome: ...


__all__ = ["PreparationFailure", "ExecutionAdapter", "FencedExecutionAdapter"]
