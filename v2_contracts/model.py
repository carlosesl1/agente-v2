"""Closed model request/proposal contracts for Maya V2."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date
from typing import ClassVar, Final

from v2_contracts.providers import ReadObservation, ReadRequest

_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_INTENTS: Final = frozenset(
    ("inform", "select", "adjust", "confirm", "request_handoff")
)
_ALLOWED_FACTS: Final = frozenset(
    (
        "language",
        "service",
        "start_date",
        "end_date",
        "adults",
        "children",
        "payment_method",
    )
)
_ALLOWED_PAYMENT_METHODS: Final = frozenset(("stripe", "wise", "pix"))


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
        elif self.name == "payment_method":
            if (
                type(self.value) is not str
                or self.value not in _ALLOWED_PAYMENT_METHODS
            ):
                raise InvalidModelProposal(
                    "fact payment_method must be stripe, wise or pix"
                )
        elif self.name in ("start_date", "end_date"):
            if type(self.value) is not date:
                raise InvalidModelProposal(f"fact {self.name} must be an exact date")
        elif type(self.value) is not int or self.value < (
            1 if self.name == "adults" else 0
        ):
            raise InvalidModelProposal(f"fact {self.name} has an invalid integer")


@dataclass(frozen=True, slots=True)
class EffectProposal:
    kind: str
    arguments: dict[str, object]

    def __post_init__(self) -> None:
        _text(self.kind, "effect kind", identifier=True)
        object.__setattr__(
            self, "arguments", _closed_dict(self.arguments, "effect arguments")
        )


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
            raise InvalidModelProposal(
                "state_version must be a non-negative exact integer"
            )
        if type(self.observations) is not tuple or any(
            type(item) is not ReadObservation for item in self.observations
        ):
            raise InvalidModelProposal(
                "observations must contain exact ReadObservation values"
            )


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
            raise InvalidModelProposal(
                "reply_chunks must contain non-empty exact strings"
            )
        if type(self.facts) is not tuple or any(
            type(item) is not ModelFact for item in self.facts
        ):
            raise InvalidModelProposal("facts must contain exact ModelFact values")
        if len({item.name for item in self.facts}) != len(self.facts):
            raise InvalidModelProposal("facts must have unique names")
        if type(self.read_requests) is not tuple or any(
            type(item) is not ReadRequest for item in self.read_requests
        ):
            raise InvalidModelProposal(
                "read_requests must contain exact ReadRequest values"
            )
        if type(self.effect_proposals) is not tuple or any(
            type(item) is not EffectProposal for item in self.effect_proposals
        ):
            raise InvalidModelProposal(
                "effect_proposals must contain exact EffectProposal values"
            )
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
            raise InvalidModelProposal(
                "confirm intent requires confirmed_summary_version"
            )
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
        if type(self.reply_chunks) is not tuple or any(
            type(item) is not str for item in self.reply_chunks
        ):
            raise TypeError("reply_chunks must be exact strings")
        if type(self.deduplicated) is not bool:
            raise TypeError("deduplicated must be an exact bool")


@dataclass(frozen=True, slots=True)
class AuditedTranscriptFrame:
    """Exact subprocess exchange; byte fields are deliberately excluded from repr."""

    stdin_bytes: bytes = dataclass_field(repr=False)
    stdout_bytes: bytes = dataclass_field(repr=False)
    response_bytes: bytes = dataclass_field(repr=False)
    request_hash: str
    stdout_hash: str
    frame_mac: str

    DOMAIN: ClassVar[str] = "v2-audited-transcript-frame-v1"

    def __post_init__(self) -> None:
        for name in ("stdin_bytes", "stdout_bytes", "response_bytes"):
            value = getattr(self, name)
            if type(value) is not bytes or not value:
                raise TypeError(f"{name} must be non-empty exact bytes")
        if self.response_bytes not in self.stdout_bytes:
            raise ValueError("response_bytes must be an exact stdout slice")
        expected_request = hashlib.sha256(self.stdin_bytes).hexdigest()
        expected_stdout = hashlib.sha256(self.stdout_bytes).hexdigest()
        if self.request_hash != expected_request or self.stdout_hash != expected_stdout:
            raise ValueError("audited transcript byte hash mismatch")
        for name in ("frame_mac",):
            value = getattr(self, name)
            if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be a lowercase SHA-256")

    @classmethod
    def create(
        cls,
        *,
        stdin_bytes: bytes,
        stdout_bytes: bytes,
        response_bytes: bytes,
        transcript_key: bytes,
    ) -> AuditedTranscriptFrame:
        if type(transcript_key) is not bytes or len(transcript_key) < 32:
            raise ValueError("transcript_key must contain at least 32 exact bytes")
        request_hash = hashlib.sha256(stdin_bytes).hexdigest()
        stdout_hash = hashlib.sha256(stdout_bytes).hexdigest()
        frame_mac = hmac.new(
            transcript_key,
            cls.DOMAIN.encode("ascii")
            + b"\x00"
            + request_hash.encode("ascii")
            + b"\x00"
            + stdout_hash.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return cls(
            stdin_bytes,
            stdout_bytes,
            response_bytes,
            request_hash,
            stdout_hash,
            frame_mac,
        )

    def commitment_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii")
            + b"\x00"
            + self.request_hash.encode("ascii")
            + b"\x00"
            + self.stdout_hash.encode("ascii")
            + b"\x00"
            + self.frame_mac.encode("ascii")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditedTranscriptClosure:
    final_seq: int
    final_frame_hash: str
    transcript_mac: str
    ephemeral_session_id: str
    zero_requests_in_flight: bool

    def __post_init__(self) -> None:
        if type(self.final_seq) is not int or self.final_seq < 1:
            raise ValueError("final_seq must be a positive exact integer")
        for name in ("final_frame_hash", "transcript_mac"):
            value = getattr(self, name)
            if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be a lowercase SHA-256")
        _text(self.ephemeral_session_id, "ephemeral_session_id", identifier=True)
        if self.zero_requests_in_flight is not True:
            raise ValueError("audited closure requires zero requests in flight")


@dataclass(frozen=True, slots=True)
class AuditedModelTurn:
    proposal: ModelProposal
    frames: tuple[AuditedTranscriptFrame, ...]
    closure: AuditedTranscriptClosure

    def __post_init__(self) -> None:
        if type(self.proposal) is not ModelProposal:
            raise TypeError("proposal must be an exact ModelProposal")
        if type(self.frames) is not tuple or not self.frames:
            raise ValueError("frames must be a non-empty exact tuple")
        if any(type(item) is not AuditedTranscriptFrame for item in self.frames):
            raise TypeError("frames must contain exact AuditedTranscriptFrame values")
        if type(self.closure) is not AuditedTranscriptClosure:
            raise TypeError("closure must be an exact AuditedTranscriptClosure")
        if self.closure.final_seq != len(self.frames):
            raise ValueError("closure final_seq must equal frame count")
        if self.closure.final_frame_hash != self.frames[-1].commitment_hash():
            raise ValueError("closure does not bind final audited frame")
        if self.closure.transcript_mac != self._transcript_mac(self.frames):
            raise ValueError("closure transcript MAC does not bind audited frames")

    @staticmethod
    def _transcript_mac(frames: tuple[AuditedTranscriptFrame, ...]) -> str:
        return hashlib.sha256(
            b"v2-audited-transcript-mac-v1\x00"
            + b"\x00".join(item.frame_mac.encode("ascii") for item in frames)
        ).hexdigest()

    @classmethod
    def from_exchange(
        cls,
        *,
        proposal: ModelProposal,
        stdin_bytes: bytes,
        stdout_bytes: bytes,
        response_bytes: bytes,
        transcript_key: bytes,
        ephemeral_session_id: str,
    ) -> AuditedModelTurn:
        frame = AuditedTranscriptFrame.create(
            stdin_bytes=stdin_bytes,
            stdout_bytes=stdout_bytes,
            response_bytes=response_bytes,
            transcript_key=transcript_key,
        )
        frames = (frame,)
        closure = AuditedTranscriptClosure(
            final_seq=1,
            final_frame_hash=frame.commitment_hash(),
            transcript_mac=cls._transcript_mac(frames),
            ephemeral_session_id=ephemeral_session_id,
            zero_requests_in_flight=True,
        )
        return cls(proposal, frames, closure)

    @classmethod
    def combine(cls, turns: tuple[AuditedModelTurn, ...]) -> AuditedModelTurn:
        if type(turns) is not tuple or not turns:
            raise ValueError("turns must be a non-empty exact tuple")
        if any(type(item) is not cls for item in turns):
            raise TypeError("turns must contain exact AuditedModelTurn values")
        frames = tuple(frame for turn in turns for frame in turn.frames)
        closure = AuditedTranscriptClosure(
            final_seq=len(frames),
            final_frame_hash=frames[-1].commitment_hash(),
            transcript_mac=cls._transcript_mac(frames),
            ephemeral_session_id=turns[-1].closure.ephemeral_session_id,
            zero_requests_in_flight=True,
        )
        return cls(turns[-1].proposal, frames, closure)


__all__ = [
    "AuditedModelTurn",
    "AuditedTranscriptClosure",
    "AuditedTranscriptFrame",
    "EffectProposal",
    "InvalidModelProposal",
    "ModelFact",
    "ModelProposal",
    "ModelRequest",
    "TurnResult",
]
