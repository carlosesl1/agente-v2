"""Conservative one-shot recovery for expired settlement leases.

The reconciler receives only the local store. It never receives or calls a
settlement port, so post-fence recovery cannot repeat a financial dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .sqlite_store import SQLiteFollowupUnitOfWork
from .types import PreDispatchReleaseDisposition, _require_id


class SettlementRecoveryDisposition(str, Enum):
    IDLE = "idle"
    PRE_FENCE_REQUEUED = "pre_fence_requeued"
    PRE_FENCE_TERMINAL = "pre_fence_terminal"
    POST_FENCE_MANUAL_REVIEW = "post_fence_manual_review"


@dataclass(frozen=True, slots=True)
class SettlementRecoveryResult:
    disposition: SettlementRecoveryDisposition
    settlement_command_id: str | None

    def __post_init__(self) -> None:
        if type(self.disposition) is not SettlementRecoveryDisposition:
            raise ValueError("disposition must use SettlementRecoveryDisposition")
        if self.settlement_command_id is not None:
            object.__setattr__(
                self,
                "settlement_command_id",
                _require_id(
                    self.settlement_command_id,
                    "settlement_recovery_result.settlement_command_id",
                ),
            )
        if (self.disposition is SettlementRecoveryDisposition.IDLE) != (
            self.settlement_command_id is None
        ):
            raise ValueError("idle is the only recovery result without a command")

    @classmethod
    def idle(cls) -> "SettlementRecoveryResult":
        return cls(SettlementRecoveryDisposition.IDLE, None)


class PaymentReconciler:
    """Recover at most one expired ledger without any external capability."""

    def __init__(self, *, store: SQLiteFollowupUnitOfWork) -> None:
        if type(store) is not SQLiteFollowupUnitOfWork:
            raise TypeError("store must be exact SQLiteFollowupUnitOfWork")
        self._store = store

    def run_once(self, *, now: datetime) -> SettlementRecoveryResult:
        post_fence = self._store.recover_expired_fenced_settlement(now=now)
        if post_fence is not None:
            return SettlementRecoveryResult(
                SettlementRecoveryDisposition.POST_FENCE_MANUAL_REVIEW,
                post_fence,
            )
        pre_fence = self._store.recover_expired_pre_dispatch_settlement(now=now)
        if pre_fence is None:
            return SettlementRecoveryResult.idle()
        disposition, command_id = pre_fence
        if disposition is PreDispatchReleaseDisposition.REQUEUED:
            return SettlementRecoveryResult(
                SettlementRecoveryDisposition.PRE_FENCE_REQUEUED,
                command_id,
            )
        return SettlementRecoveryResult(
            SettlementRecoveryDisposition.PRE_FENCE_TERMINAL,
            command_id,
        )


__all__ = [
    "SettlementRecoveryDisposition",
    "SettlementRecoveryResult",
    "PaymentReconciler",
]
