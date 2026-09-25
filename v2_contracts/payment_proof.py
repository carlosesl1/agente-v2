"""Maya's untrusted document observation. No payment authority or expected values."""

from dataclasses import dataclass, fields
from datetime import datetime
import re


@dataclass(frozen=True, slots=True)
class PaymentProofObservation:
    source_event_id: str
    source_sha256: str
    payment_id: str | None
    method: str | None
    status: str
    amount_minor: int | None
    currency: str | None
    recipient_identifier: str | None
    recipient_name: str | None
    payer_name: str | None
    transaction_id: str | None
    transferred_at: str | None
    issues: tuple[str, ...] = ()

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("proof byte hash is invalid")
        if self.method not in (None, "pix", "wise") or self.status not in (
            "completed",
            "pending",
            "scheduled",
            "failed",
            "unknown",
        ):
            raise ValueError("proof method/status invalid")
        if self.amount_minor is not None and (
            type(self.amount_minor) is not int or self.amount_minor < 1
        ):
            raise ValueError("proof amount invalid")
        if self.currency is not None and not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("proof currency invalid")
        for f in fields(self):
            if f.name in ("issues", "amount_minor"):
                continue
            value = getattr(self, f.name)
            if value is not None and (
                type(value) is not str
                or not value
                or value != value.strip()
                or len(value) > 256
                or "\x00" in value
            ):
                raise ValueError("proof field invalid")
        if type(self.source_event_id) is not str or not self.source_event_id:
            raise ValueError("proof event is required")
        if self.transferred_at is not None:
            stamp = datetime.fromisoformat(self.transferred_at)
            if stamp.utcoffset() is None:
                raise ValueError("proof transaction needs timezone")
        if (
            type(self.issues) is not tuple
            or len(self.issues) > 12
            or any(type(v) is not str or not v or len(v) > 256 for v in self.issues)
        ):
            raise ValueError("proof issues invalid")

    def to_dict(self):
        return {
            f.name: list(self.issues) if f.name == "issues" else getattr(self, f.name)
            for f in fields(self)
        }

    @classmethod
    def from_dict(cls, value):
        if (
            type(value) is not dict
            or set(value) != {f.name for f in fields(cls)}
            or type(value.get("issues")) is not list
        ):
            raise ValueError("proof fields mismatch")
        return cls(**{**value, "issues": tuple(value["issues"])})


def proof_schema():
    nullable_text = {"type": ["string", "null"], "maxLength": 256}
    props = {f.name: dict(nullable_text) for f in fields(PaymentProofObservation)}
    props.update(
        source_event_id={"type": "string"},
        source_sha256={"type": "string", "pattern": "^[0-9a-f]{64}$"},
        method={"type": ["string", "null"], "enum": [None, "pix", "wise"]},
        status={
            "type": "string",
            "enum": ["completed", "pending", "scheduled", "failed", "unknown"],
        },
        amount_minor={"type": ["integer", "null"], "minimum": 1},
        issues={
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "maxLength": 256},
        },
    )
    return {
        "anyOf": [
            {"type": "null"},
            {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            },
        ]
    }
