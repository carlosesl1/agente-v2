"""Closed immutable qualification contracts for the Phase 8 boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import ClassVar, Final


_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")

SCENARIO_TERMINAL_VERIFICATION_DOMAIN: Final = (
    "phase8-scenario-terminal-verification-v1"
)


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


@dataclass(frozen=True, slots=True)
class ScenarioTerminalVerificationReceipt:
    """Terminal owner-derived verification receipt for one E2E scenario."""

    qualification_id: str
    epoch: int
    scenario_id: str
    scenario_contract_hash: str
    cutoff_sequence: int
    admitted_set_hash: str
    admitted_turn_receipt_aggregate_hash: str
    target_ingress_receipt_aggregate_hash: str
    provider_effect_outcome_aggregate_hash: str
    followup_delivery_receipt_aggregate_hash: str
    public_delivery_receipt_aggregate_hash: str
    compensation_receipt_aggregate_hash: str
    final_state_hash: str
    final_economic_hash: str
    allocation_manifest_hash: str
    exact_effect_budget_hash: str
    previous_qualification_artifact_hash: str

    SCHEMA: ClassVar[str] = "phase8-scenario-terminal-verification-receipt"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = SCENARIO_TERMINAL_VERIFICATION_DOMAIN

    def __post_init__(self) -> None:
        _require_identifier(
            self.qualification_id,
            "ScenarioTerminalVerificationReceipt.qualification_id",
        )
        _require_exact_int(
            self.epoch,
            "ScenarioTerminalVerificationReceipt.epoch",
            minimum=1,
        )
        _require_identifier(
            self.scenario_id,
            "ScenarioTerminalVerificationReceipt.scenario_id",
        )
        _require_exact_int(
            self.cutoff_sequence,
            "ScenarioTerminalVerificationReceipt.cutoff_sequence",
            minimum=1,
        )
        for name in (
            "scenario_contract_hash",
            "admitted_set_hash",
            "admitted_turn_receipt_aggregate_hash",
            "target_ingress_receipt_aggregate_hash",
            "provider_effect_outcome_aggregate_hash",
            "followup_delivery_receipt_aggregate_hash",
            "public_delivery_receipt_aggregate_hash",
            "compensation_receipt_aggregate_hash",
            "final_state_hash",
            "final_economic_hash",
            "allocation_manifest_hash",
            "exact_effect_budget_hash",
            "previous_qualification_artifact_hash",
        ):
            _require_sha256(
                getattr(self, name),
                f"ScenarioTerminalVerificationReceipt.{name}",
            )

    def _data(self) -> dict[str, object]:
        return {
            "qualification_id": self.qualification_id,
            "epoch": self.epoch,
            "scenario_id": self.scenario_id,
            "scenario_contract_hash": self.scenario_contract_hash,
            "cutoff_sequence": self.cutoff_sequence,
            "admitted_set_hash": self.admitted_set_hash,
            "admitted_turn_receipt_aggregate_hash": (
                self.admitted_turn_receipt_aggregate_hash
            ),
            "target_ingress_receipt_aggregate_hash": (
                self.target_ingress_receipt_aggregate_hash
            ),
            "provider_effect_outcome_aggregate_hash": (
                self.provider_effect_outcome_aggregate_hash
            ),
            "followup_delivery_receipt_aggregate_hash": (
                self.followup_delivery_receipt_aggregate_hash
            ),
            "public_delivery_receipt_aggregate_hash": (
                self.public_delivery_receipt_aggregate_hash
            ),
            "compensation_receipt_aggregate_hash": (
                self.compensation_receipt_aggregate_hash
            ),
            "final_state_hash": self.final_state_hash,
            "final_economic_hash": self.final_economic_hash,
            "allocation_manifest_hash": self.allocation_manifest_hash,
            "exact_effect_budget_hash": self.exact_effect_budget_hash,
            "previous_qualification_artifact_hash": (
                self.previous_qualification_artifact_hash
            ),
        }

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data=self._data(),
        )

    def canonical_hash(self) -> str:
        preimage = self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        return hashlib.sha256(preimage).hexdigest()


__all__ = (
    "BehaviorStateSnapshot",
    "SCENARIO_TERMINAL_VERIFICATION_DOMAIN",
    "ScenarioTerminalVerificationReceipt",
)
