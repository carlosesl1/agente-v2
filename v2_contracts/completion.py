"""Internal, communication-only continuations; never customer channel events."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from v2_contracts.channel import _require_id, _require_utc


@dataclass(frozen=True, slots=True)
class CompletionEvent:
    event_id: str
    lead_id: str
    kind: str
    command_ids: tuple[str, ...]
    payment_id: str | None
    occurred_at: datetime
    payload_hash: str

    def __post_init__(self) -> None:
        for name in ("event_id", "lead_id"):
            _require_id(getattr(self, name), name)
        if self.kind not in {"reservation_result", "payment_offer", "payment_settlement"}:
            raise ValueError("unknown completion event kind")
        if type(self.command_ids) is not tuple:
            raise TypeError("command_ids must be an exact tuple")
        for identity in self.command_ids:
            _require_id(identity, "command_id")
        if self.payment_id is not None:
            _require_id(self.payment_id, "payment_id")
        if (self.kind in {"reservation_result", "payment_settlement"}) != bool(self.command_ids):
            raise ValueError("reservation events require command identities")
        if (self.kind in {"payment_offer", "payment_settlement"}) != (self.payment_id is not None):
            raise ValueError("payment events require a payment identity")
        _require_utc(self.occurred_at, "occurred_at")
        if (
            type(self.payload_hash) is not str
            or len(self.payload_hash) != 64
            or any(c not in "0123456789abcdef" for c in self.payload_hash)
        ):
            raise ValueError("payload_hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class CompletionTurn:
    lead_id: str
    events: tuple[CompletionEvent, ...]

    def __post_init__(self) -> None:
        _require_id(self.lead_id, "lead_id")
        if (
            not self.lead_id.startswith("manychat:")
            or not self.subscriber_id.isdecimal()
        ):
            raise ValueError("completion requires an authenticated ManyChat lead")
        if type(self.events) is not tuple or not self.events:
            raise ValueError("completion requires events")
        if any(
            type(e) is not CompletionEvent or e.lead_id != self.lead_id
            for e in self.events
        ):
            raise ValueError("completion events must belong to this lead")
        if len({e.event_id for e in self.events}) != len(self.events):
            raise ValueError("completion event identities must be unique")

    @property
    def subscriber_id(self) -> str:
        return self.lead_id.removeprefix("manychat:")

    @property
    def batch_id(self) -> str:
        value = "|".join(
            (self.lead_id, *(e.event_id + ":" + e.payload_hash for e in self.events))
        )
        return "completion-turn:" + hashlib.sha256(value.encode()).hexdigest()[:40]

    @property
    def combined_text(self) -> str:
        return ""
