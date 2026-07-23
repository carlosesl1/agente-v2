"""Closed model request/proposal contracts for Maya V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import re
from typing import Final

from v2_contracts.providers import ReadObservation, ReadRequest


_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_ALLOWED_INTENTS: Final = frozenset(
    ("inform", "select", "adjust", "confirm", "request_handoff")
)
_ALLOWED_FACTS: Final = frozenset(
    ("language", "service", "start_date", "end_date", "adults", "children")
)


class InvalidModelProposal(ValueError):
    """Raised when a model response violates the closed V2 grammar."""


def _text(value: object, name: str, *, identifier: bool = False) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise InvalidModelProposal(f"{name} must be a non-empty exact string")
    if identifier and _ID_RE.fullmatch(value) is None:
        raise InvalidModelProposal(f"{name} must be a canonical identifier")
    return value


def _closed_dict(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise InvalidModelProposal(f"{name} must be an exact dict")
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = json.loads(payload)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidModelProposal(f"{name} must be closed JSON") from exc
    return decoded


@dataclass(frozen=True, slots=True)
class ModelFact:
    name: str
    value: str | int | date

    def __post_init__(self) -> None:
        if self.name not in _ALLOWED_FACTS:
            raise InvalidModelProposal("fact name is outside the V2 catalog")
        if self.name in ("language", "service"):
            _text(self.value, f"fact {self.name}")
        elif self.name in ("start_date", "end_date"):
            if type(self.value) is not date:
                raise InvalidModelProposal(f"fact {self.name} must be an exact date")
        elif type(self.value) is not int or self.value < (1 if self.name == "adults" else 0):
            raise InvalidModelProposal(f"fact {self.name} has an invalid integer")


@dataclass(frozen=True, slots=True)
class EffectProposal:
    kind: str
    arguments: dict[str, object]

    def __post_init__(self) -> None:
        _text(self.kind, "effect kind", identifier=True)
        object.__setattr__(self, "arguments", _closed_dict(self.arguments, "effect arguments"))


@dataclass(frozen=True, slots=True)
class ModelRequest:
    request_id: str
    lead_id: str
    source_event_id: str
    message: str
    locale: str
    state_version: int
    observations: tuple[ReadObservation, ...] = ()

    def __post_init__(self) -> None:
        _text(self.request_id, "request_id", identifier=True)
        _text(self.lead_id, "lead_id", identifier=True)
        _text(self.source_event_id, "source_event_id", identifier=True)
        _text(self.message, "message")
        _text(self.locale, "locale")
        if type(self.state_version) is not int or self.state_version < 0:
            raise InvalidModelProposal("state_version must be a non-negative exact integer")
        if type(self.observations) is not tuple or any(
            type(item) is not ReadObservation for item in self.observations
        ):
            raise InvalidModelProposal("observations must contain exact ReadObservation values")


@dataclass(frozen=True, slots=True)
class ModelProposal:
    source_event_id: str
    intent: str
    reply_chunks: tuple[str, ...]
    facts: tuple[ModelFact, ...]
    read_requests: tuple[ReadRequest, ...]
    effect_proposals: tuple[EffectProposal, ...]
    target_offer_id: str | None = None
    confirmed_summary_version: int | None = None

    def __post_init__(self) -> None:
        _text(self.source_event_id, "source_event_id", identifier=True)
        if self.intent not in _ALLOWED_INTENTS:
            raise InvalidModelProposal("intent is outside the closed V2 grammar")
        if type(self.reply_chunks) is not tuple or any(
            type(item) is not str or not item or item != item.strip()
            for item in self.reply_chunks
        ):
            raise InvalidModelProposal("reply_chunks must contain non-empty exact strings")
        if type(self.facts) is not tuple or any(type(item) is not ModelFact for item in self.facts):
            raise InvalidModelProposal("facts must contain exact ModelFact values")
        if len({item.name for item in self.facts}) != len(self.facts):
            raise InvalidModelProposal("facts must have unique names")
        if type(self.read_requests) is not tuple or any(
            type(item) is not ReadRequest for item in self.read_requests
        ):
            raise InvalidModelProposal("read_requests must contain exact ReadRequest values")
        if type(self.effect_proposals) is not tuple or any(
            type(item) is not EffectProposal for item in self.effect_proposals
        ):
            raise InvalidModelProposal("effect_proposals must contain exact EffectProposal values")
        if self.target_offer_id is not None:
            _text(self.target_offer_id, "target_offer_id", identifier=True)
        if self.intent == "select" and self.target_offer_id is None:
            raise InvalidModelProposal("select intent requires target_offer_id")
        if self.intent != "select" and self.target_offer_id is not None:
            raise InvalidModelProposal("target_offer_id is allowed only for select")
        if self.confirmed_summary_version is not None and (
            type(self.confirmed_summary_version) is not int
            or self.confirmed_summary_version < 1
        ):
            raise InvalidModelProposal("confirmed_summary_version must be positive")
        if self.intent == "confirm" and self.confirmed_summary_version is None:
            raise InvalidModelProposal("confirm intent requires confirmed_summary_version")
        if self.intent != "confirm" and self.confirmed_summary_version is not None:
            raise InvalidModelProposal(
                "confirmed_summary_version is allowed only for confirm"
            )


@dataclass(frozen=True, slots=True)
class TurnResult:
    batch_id: str
    state_version: int
    reply_chunks: tuple[str, ...]
    deduplicated: bool

    def __post_init__(self) -> None:
        _text(self.batch_id, "batch_id", identifier=True)
        if type(self.state_version) is not int or self.state_version < 0:
            raise ValueError("state_version must be non-negative")
        if type(self.reply_chunks) is not tuple or any(type(item) is not str for item in self.reply_chunks):
            raise TypeError("reply_chunks must be exact strings")
        if type(self.deduplicated) is not bool:
            raise TypeError("deduplicated must be an exact bool")


__all__ = [
    "EffectProposal",
    "InvalidModelProposal",
    "ModelFact",
    "ModelProposal",
    "ModelRequest",
    "TurnResult",
]
