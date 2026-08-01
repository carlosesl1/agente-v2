"""Closed semantic decision returned by contextual confirmation review."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Final

_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


class InvalidContextualConfirmationReview(ValueError):
    """Raised when the narrow confirmation-review contract is invalid."""


class ContextualConfirmationDecision(str, Enum):
    """Closed, non-authoritative semantic decisions from the model reviewer."""

    APPROVE = "approve"
    REJECT = "reject"
    ADJUST = "adjust"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ContextualConfirmationReview:
    """Typed semantic verdict bound to one exact source event."""

    source_event_id: str
    decision: ContextualConfirmationDecision

    def __post_init__(self) -> None:
        if (
            type(self.source_event_id) is not str
            or _ID_RE.fullmatch(self.source_event_id) is None
        ):
            raise InvalidContextualConfirmationReview(
                "source_event_id must be a canonical identifier"
            )
        if type(self.decision) is not ContextualConfirmationDecision:
            raise InvalidContextualConfirmationReview(
                "decision must be an exact ContextualConfirmationDecision"
            )


__all__ = [
    "ContextualConfirmationDecision",
    "ContextualConfirmationReview",
    "InvalidContextualConfirmationReview",
]
