"""Closed public contracts for Maya critical-action approval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class CriticalActionKind(str, Enum):
    RESERVE_LODGING = "reserve_lodging"
    BOOK_ACTIVITY = "book_activity"
    BOOK_PACKAGE = "book_package"
    INITIATE_PAYMENT = "initiate_payment"
    MODIFY_RESERVATION = "modify_reservation"
    CANCEL_RESERVATION = "cancel_reservation"
    CHARGE_OR_CAPTURE = "charge_or_capture"
    REFUND = "refund"
    SHARE_HANDOFF_DATA = "share_handoff_data"


class ApprovalBasis(str, Enum):
    CONTEXTUAL_REFERENCE = "contextual_reference"


@dataclass(frozen=True, slots=True)
class PendingCriticalActionContext:
    """Public-only projection of the one currently pending critical proposal."""

    summary_version: int
    action_kinds: tuple[CriticalActionKind, ...]
    public_summary: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.summary_version) is not int
            or isinstance(self.summary_version, bool)
            or self.summary_version < 1
        ):
            raise ValueError("summary_version must be a positive exact integer")
        if type(self.action_kinds) is not tuple or not self.action_kinds:
            raise ValueError("action_kinds must be a non-empty exact tuple")
        if any(type(item) is not CriticalActionKind for item in self.action_kinds):
            raise TypeError("action_kinds must contain exact CriticalActionKind values")
        if len(set(self.action_kinds)) != len(self.action_kinds):
            raise ValueError("action_kinds must be unique")
        object.__setattr__(
            self,
            "action_kinds",
            tuple(sorted(self.action_kinds, key=lambda item: item.value)),
        )
        if (
            type(self.public_summary) is not str
            or not self.public_summary
            or self.public_summary != self.public_summary.strip()
            or "\x00" in self.public_summary
        ):
            raise ValueError("public_summary must be non-empty trimmed NUL-free text")
        if (
            type(self.expires_at) is not datetime
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("expires_at must be an exact UTC datetime")


__all__ = [
    "ApprovalBasis",
    "CriticalActionKind",
    "PendingCriticalActionContext",
]
