"""Closed immutable qualification contracts for the Phase 8 boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import ClassVar, Final


_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def _require_identifier(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must use the closed identifier alphabet")
    return value


def _require_exact_int(value: object, name: str, *, minimum: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_sha256(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _canonical_envelope(*, schema: str, version: int, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": schema, "version": version, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class BehaviorStateSnapshot:
    """Canonical identity of the dynamic memory state admitted for a turn."""

    schema: str
    version: int
    memory_snapshot_hash: str

    SCHEMA: ClassVar[str] = "phase8-behavior-state-snapshot"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-behavior-state-snapshot-v1"

    def __post_init__(self) -> None:
        _require_identifier(self.schema, "BehaviorStateSnapshot.schema")
        _require_exact_int(
            self.version,
            "BehaviorStateSnapshot.version",
            minimum=1,
        )
        _require_sha256(
            self.memory_snapshot_hash,
            "BehaviorStateSnapshot.memory_snapshot_hash",
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "schema": self.schema,
                "version": self.version,
                "memory_snapshot_hash": self.memory_snapshot_hash,
            },
        )

    def canonical_hash(self) -> str:
        preimage = self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        return hashlib.sha256(preimage).hexdigest()


__all__ = ("BehaviorStateSnapshot",)
