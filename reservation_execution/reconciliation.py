"""One-shot recovery of expired durable execution leases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .sqlite_store import SQLiteUnitOfWork


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    pre_dispatch_released: int
    called_unknown: int

    def __post_init__(self) -> None:
        for field_name in ("pre_dispatch_released", "called_unknown"):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an exact integer")
            if value < 0:
                raise ValueError(f"{field_name} must be nonnegative")


class Reconciler:
    """Recover expired local execution state without external capabilities."""

    def __init__(self, store: SQLiteUnitOfWork) -> None:
        if type(store) is not SQLiteUnitOfWork:
            raise TypeError("store must be the exact SQLiteUnitOfWork type")
        self._store = store

    def run_once(self, *, now: datetime) -> ReconciliationResult:
        self._store.assert_execution_consistency()
        released = self._store.release_expired_pre_dispatch(now=now)
        unknown = self._store.mark_expired_fenced_unknown(now=now)
        self._store.assert_execution_consistency()
        return ReconciliationResult(
            pre_dispatch_released=released,
            called_unknown=unknown,
        )


__all__ = ["Reconciler", "ReconciliationResult"]
