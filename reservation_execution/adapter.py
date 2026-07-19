"""Caller-supplied execution adapter port with no default capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from reservation_domain import ExecutionOutcome, ReservationCommand

from .types import DispatchRequest, _require_hash, _require_id


@dataclass(frozen=True, slots=True)
class PreparationFailure(Exception):
    reason: str
    retryable: bool
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason",
            _require_id(self.reason, "preparation_failure.reason"),
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


__all__ = ["PreparationFailure", "ExecutionAdapter"]
