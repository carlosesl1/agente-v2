"""Closed immutable effect and relay contracts for the Phase 8 boundary."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import re
from typing import ClassVar, Final


RESERVATION_RELAY_DOMAIN: Final = "phase8-reservation-relay-bundle-v1"

_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def _require_bytes(value: object, name: str) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{name} must be exact bytes")
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _require_bytes_tuple(value: object, name: str) -> tuple[bytes, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be an exact tuple")
    for item in value:
        _require_bytes(item, f"{name} item")
    return value


def _require_identifier(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must use the closed identifier alphabet")
    return value


def _require_sha256(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _require_generation(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    if value < 1:
        raise ValueError(f"{name} must be >= 1")
    return value


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _canonical_envelope(*, schema: str, version: int, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": schema, "version": version, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ReservationRelayBundle:
    """Canonical Phase 5 full-replay bundle with backlink-independent identity."""

    genesis_state: bytes
    phase5_events: tuple[bytes, ...]
    summary_outboxes: tuple[bytes, ...]
    expected_final_state: bytes
    expected_final_state_hash: str
    command_ledger_seed: bytes
    qualification_id: str | None
    scenario_id: str | None
    immutable_generation: int | None
    allocation_id: str | None
    artifact_hash: str

    SCHEMA: ClassVar[str] = "phase8-reservation-relay-bundle"
    PREIMAGE_SCHEMA: ClassVar[str] = "phase8-reservation-relay-bundle-preimage"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = RESERVATION_RELAY_DOMAIN

    def __post_init__(self) -> None:
        _require_bytes(self.genesis_state, "ReservationRelayBundle.genesis_state")
        _require_bytes_tuple(
            self.phase5_events,
            "ReservationRelayBundle.phase5_events",
        )
        _require_bytes_tuple(
            self.summary_outboxes,
            "ReservationRelayBundle.summary_outboxes",
        )
        _require_bytes(
            self.expected_final_state,
            "ReservationRelayBundle.expected_final_state",
        )
        _require_sha256(
            self.expected_final_state_hash,
            "ReservationRelayBundle.expected_final_state_hash",
        )
        if hashlib.sha256(self.expected_final_state).hexdigest() != self.expected_final_state_hash:
            raise ValueError("expected_final_state_hash does not authenticate expected_final_state")
        _require_bytes(
            self.command_ledger_seed,
            "ReservationRelayBundle.command_ledger_seed",
        )

        e2e_values = (
            self.qualification_id,
            self.scenario_id,
            self.immutable_generation,
            self.allocation_id,
        )
        present = tuple(value is not None for value in e2e_values)
        if any(present) and not all(present):
            raise ValueError("E2E relay fields must be all-null or all-present")
        if all(present):
            _require_identifier(
                self.qualification_id,
                "ReservationRelayBundle.qualification_id",
            )
            _require_identifier(
                self.scenario_id,
                "ReservationRelayBundle.scenario_id",
            )
            _require_generation(
                self.immutable_generation,
                "ReservationRelayBundle.immutable_generation",
            )
            _require_identifier(
                self.allocation_id,
                "ReservationRelayBundle.allocation_id",
            )

        _require_sha256(self.artifact_hash, "ReservationRelayBundle.artifact_hash")
        expected_artifact_hash = hashlib.sha256(
            self.DOMAIN.encode("ascii")
            + b"\x00"
            + self.artifact_preimage_bytes()
        ).hexdigest()
        if self.artifact_hash != expected_artifact_hash:
            raise ValueError("artifact_hash does not authenticate relay bundle preimage")

    def _preimage_data(self) -> dict[str, object]:
        return {
            "genesis_state": _b64(self.genesis_state),
            "phase5_events": [_b64(value) for value in self.phase5_events],
            "summary_outboxes": [_b64(value) for value in self.summary_outboxes],
            "expected_final_state": _b64(self.expected_final_state),
            "expected_final_state_hash": self.expected_final_state_hash,
            "command_ledger_seed": _b64(self.command_ledger_seed),
            "qualification_id": self.qualification_id,
            "scenario_id": self.scenario_id,
            "immutable_generation": self.immutable_generation,
            "allocation_id": self.allocation_id,
        }

    def artifact_preimage_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.PREIMAGE_SCHEMA,
            version=self.VERSION,
            data=self._preimage_data(),
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data=self._preimage_data() | {"artifact_hash": self.artifact_hash},
        )


__all__ = (
    "RESERVATION_RELAY_DOMAIN",
    "ReservationRelayBundle",
)
