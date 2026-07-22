"""Closed Phase 8 effect-allocation authority wire contracts.

These DTOs carry only immutable installation material. Mutable target-local authority
state belongs to the Phase 5/6 SQLite owners.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import re
from typing import ClassVar, Final

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class AllocationRowKind(str, Enum):
    ALLOCATION = "allocation"


class InstallationTarget(str, Enum):
    BOUNDARY_DISPATCH_AUTHORITY = "boundary_dispatch_authority"
    RESERVATION_E2E_EFFECT_AUTHORITY = "reservation_e2e_effect_authority"
    FOLLOWUP_E2E_EFFECT_AUTHORITY = "followup_e2e_effect_authority"


class EffectFamily(str, Enum):
    RESERVATION = "reservation"
    PAYMENT = "payment"
    HANDOFF_DELIVERY = "handoff_delivery"
    PAYMENT_DELIVERY = "payment_delivery"
    PUBLIC_DELIVERY = "public_delivery"


class EffectKind(str, Enum):
    PROVIDER_PRIMARY = "provider_primary"
    PROVIDER_COMPENSATION = "provider_compensation"
    EXTERNAL_MESSAGE = "external_message"
    PUBLIC_CHUNK = "public_chunk"


class AllocationEffectRole(str, Enum):
    PRIMARY = "primary"
    COMPENSATION = "compensation"
    NONE = "none"


class ActivationParentKind(str, Enum):
    NONE = "none"
    PROVIDER_ALLOCATION = "provider_allocation"
    INTERNAL_TARGET_OPERATION = "internal_target_operation"


class AllocationInitialState(str, Enum):
    AVAILABLE = "available"


class InstallationHeaderState(str, Enum):
    OPEN = "open"


class InstallationStatus(str, Enum):
    INSTALLED = "installed"


def _require_exact_enum(value: object, expected: type[Enum], name: str) -> None:
    if type(value) is not expected:
        raise TypeError(f"{name} must be exact {expected.__name__}")


def _require_id(value: object, name: str) -> None:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be an ID token")


def _require_hash(value: object, name: str) -> None:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")


def _require_ordinal(value: object, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative exact integer")


def _require_positive(value: object, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive exact integer")


def _utc_text(value: object, name: str) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise TypeError(f"{name} must be an exact UTC datetime")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_envelope(schema: str, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": schema, "version": 1, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _domain_hash(domain: str, payload: bytes) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + payload).hexdigest()


@dataclass(frozen=True, slots=True)
class EffectAllocationRow:
    row_kind: AllocationRowKind
    installation_target: InstallationTarget
    qualification_id: str
    epoch: int
    scenario_id: str
    contract_hash: str
    effect_authorization_binding_hash: str
    generation_id: str
    allocation_id: str
    allocation_ordinal: int
    effect_family: EffectFamily
    effect_kind: EffectKind
    effect_role: AllocationEffectRole
    effect_scope_hash: str
    workflow_scope_hash: str | None
    channel_scope_hash: str | None
    target_binding_hash: str
    message_ordinal: int | None
    activation_parent_kind: ActivationParentKind
    activation_parent_id: str | None
    activation_parent_hash: str | None
    initial_state: AllocationInitialState

    SCHEMA: ClassVar[str] = "phase8-effect-allocation-row"
    DOMAIN: ClassVar[str] = "phase8-effect-allocation-row-v1"

    def __post_init__(self) -> None:
        _require_exact_enum(self.row_kind, AllocationRowKind, "row_kind")
        _require_exact_enum(self.installation_target, InstallationTarget, "installation_target")
        _require_id(self.qualification_id, "qualification_id")
        _require_positive(self.epoch, "epoch")
        _require_id(self.scenario_id, "scenario_id")
        _require_hash(self.contract_hash, "contract_hash")
        _require_hash(
            self.effect_authorization_binding_hash,
            "effect_authorization_binding_hash",
        )
        _require_id(self.generation_id, "generation_id")
        _require_id(self.allocation_id, "allocation_id")
        _require_ordinal(self.allocation_ordinal, "allocation_ordinal")
        _require_exact_enum(self.effect_family, EffectFamily, "effect_family")
        _require_exact_enum(self.effect_kind, EffectKind, "effect_kind")
        _require_exact_enum(self.effect_role, AllocationEffectRole, "effect_role")
        _require_hash(self.effect_scope_hash, "effect_scope_hash")
        if self.workflow_scope_hash is not None:
            _require_hash(self.workflow_scope_hash, "workflow_scope_hash")
        if self.channel_scope_hash is not None:
            _require_hash(self.channel_scope_hash, "channel_scope_hash")
        _require_hash(self.target_binding_hash, "target_binding_hash")
        if self.message_ordinal is not None:
            _require_ordinal(self.message_ordinal, "message_ordinal")
        _require_exact_enum(
            self.activation_parent_kind,
            ActivationParentKind,
            "activation_parent_kind",
        )
        if self.activation_parent_kind is ActivationParentKind.NONE:
            if self.activation_parent_id is not None or self.activation_parent_hash is not None:
                raise ValueError("root allocation cannot carry activation parent")
        else:
            _require_id(self.activation_parent_id, "activation_parent_id")
            _require_hash(self.activation_parent_hash, "activation_parent_hash")
        _require_exact_enum(self.initial_state, AllocationInitialState, "initial_state")

        target_families = {
            InstallationTarget.BOUNDARY_DISPATCH_AUTHORITY: {EffectFamily.PUBLIC_DELIVERY},
            InstallationTarget.RESERVATION_E2E_EFFECT_AUTHORITY: {EffectFamily.RESERVATION},
            InstallationTarget.FOLLOWUP_E2E_EFFECT_AUTHORITY: {
                EffectFamily.PAYMENT,
                EffectFamily.HANDOFF_DELIVERY,
                EffectFamily.PAYMENT_DELIVERY,
            },
        }
        if self.effect_family not in target_families[self.installation_target]:
            raise ValueError("effect family does not belong to installation target")
        if self.effect_family in (EffectFamily.RESERVATION, EffectFamily.PAYMENT):
            if (
                self.workflow_scope_hash is None
                or self.channel_scope_hash is not None
                or self.message_ordinal is not None
            ):
                raise ValueError("provider allocation scope matrix is invalid")
            expected = {
                AllocationEffectRole.PRIMARY: EffectKind.PROVIDER_PRIMARY,
                AllocationEffectRole.COMPENSATION: EffectKind.PROVIDER_COMPENSATION,
            }
            if self.effect_role not in expected or self.effect_kind is not expected[self.effect_role]:
                raise ValueError("provider effect role/kind matrix is invalid")
            if self.effect_role is AllocationEffectRole.PRIMARY:
                if self.activation_parent_kind is not ActivationParentKind.NONE:
                    raise ValueError("primary provider allocation cannot have a parent")
            elif self.activation_parent_kind is not ActivationParentKind.PROVIDER_ALLOCATION:
                raise ValueError("compensation must reference its provider allocation")
        elif self.effect_family is EffectFamily.PUBLIC_DELIVERY:
            if (
                self.effect_kind is not EffectKind.PUBLIC_CHUNK
                or self.effect_role is not AllocationEffectRole.NONE
                or self.workflow_scope_hash is not None
                or self.channel_scope_hash is None
                or self.message_ordinal is None
                or self.activation_parent_kind is not ActivationParentKind.NONE
            ):
                raise ValueError("public delivery allocation matrix is invalid")
        else:
            if (
                self.effect_kind is not EffectKind.EXTERNAL_MESSAGE
                or self.effect_role is not AllocationEffectRole.NONE
                or self.workflow_scope_hash is None
                or self.channel_scope_hash is None
                or self.message_ordinal is not None
            ):
                raise ValueError("follow-up delivery allocation matrix is invalid")
            expected_parent = (
                ActivationParentKind.INTERNAL_TARGET_OPERATION
                if self.effect_family is EffectFamily.HANDOFF_DELIVERY
                else ActivationParentKind.PROVIDER_ALLOCATION
            )
            if self.activation_parent_kind is not expected_parent:
                raise ValueError("delivery allocation has the wrong causal parent")

    def _data(self) -> dict[str, object]:
        return {
            field.name: (
                value.value if isinstance(value, Enum) else value
            )
            for field in fields(self)
            for value in (getattr(self, field.name),)
        }

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(self.SCHEMA, self._data())

    def canonical_hash(self) -> str:
        return _domain_hash(self.DOMAIN, self.to_canonical_bytes())


@dataclass(frozen=True, slots=True)
class ExactEffectAllocationManifest:
    qualification_id: str
    epoch: int
    contract_hash: str
    effect_authorization_binding_hash: str
    rows: tuple[EffectAllocationRow, ...]
    allocation_count: int

    SCHEMA: ClassVar[str] = "phase8-exact-effect-allocation-manifest"
    DOMAIN: ClassVar[str] = "phase8-exact-effect-allocation-manifest-v1"

    def __post_init__(self) -> None:
        _require_id(self.qualification_id, "qualification_id")
        _require_positive(self.epoch, "epoch")
        _require_hash(self.contract_hash, "contract_hash")
        _require_hash(
            self.effect_authorization_binding_hash,
            "effect_authorization_binding_hash",
        )
        if type(self.rows) is not tuple or not self.rows:
            raise ValueError("manifest rows must be a non-empty exact tuple")
        if type(self.allocation_count) is not int or self.allocation_count != len(self.rows):
            raise ValueError("allocation_count must equal manifest rows")
        for row in self.rows:
            if type(row) is not EffectAllocationRow:
                raise TypeError("manifest rows must be exact EffectAllocationRow values")
            if (
                row.qualification_id != self.qualification_id
                or row.epoch != self.epoch
                or row.contract_hash != self.contract_hash
                or row.effect_authorization_binding_hash
                != self.effect_authorization_binding_hash
            ):
                raise ValueError("manifest row authority tuple diverges")
        ordering = tuple(
            (
                row.installation_target.value,
                row.scenario_id,
                row.generation_id,
                row.allocation_ordinal,
                row.allocation_id,
            )
            for row in self.rows
        )
        if ordering != tuple(sorted(ordering)):
            raise ValueError("manifest rows are not canonically ordered")
        if len({row.allocation_id for row in self.rows}) != len(self.rows):
            raise ValueError("manifest allocation IDs must be globally unique")
        positions = {row.allocation_id: index for index, row in enumerate(self.rows)}
        for index, row in enumerate(self.rows):
            if row.activation_parent_kind is not ActivationParentKind.NONE:
                if row.activation_parent_id not in positions or positions[row.activation_parent_id] >= index:
                    raise ValueError("manifest parent must precede its child")

    def _data(self) -> dict[str, object]:
        return {
            "qualification_id": self.qualification_id,
            "epoch": self.epoch,
            "contract_hash": self.contract_hash,
            "effect_authorization_binding_hash": self.effect_authorization_binding_hash,
            "rows": [json.loads(row.to_canonical_bytes()) for row in self.rows],
            "allocation_count": self.allocation_count,
        }

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(self.SCHEMA, self._data())

    def canonical_hash(self) -> str:
        return _domain_hash(self.DOMAIN, self.to_canonical_bytes())


@dataclass(frozen=True, slots=True)
class AllocationInstallationReceipt:
    operation_id: str
    installation_target: InstallationTarget
    qualification_id: str
    epoch: int
    contract_hash: str
    effect_authorization_binding_hash: str
    manifest_hash: str
    generation_ids: tuple[str, ...]
    installed_row_hashes: tuple[str, ...]
    allocation_count: int
    installed_allocation_aggregate_hash: str
    header_state: InstallationHeaderState
    status: InstallationStatus
    installed_at: datetime

    SCHEMA: ClassVar[str] = "phase8-allocation-installation-receipt"
    DOMAIN: ClassVar[str] = "phase8-allocation-installation-receipt-v1"

    def __post_init__(self) -> None:
        _require_hash(self.operation_id, "operation_id")
        _require_exact_enum(self.installation_target, InstallationTarget, "installation_target")
        _require_id(self.qualification_id, "qualification_id")
        _require_positive(self.epoch, "epoch")
        _require_hash(self.contract_hash, "contract_hash")
        _require_hash(
            self.effect_authorization_binding_hash,
            "effect_authorization_binding_hash",
        )
        _require_hash(self.manifest_hash, "manifest_hash")
        if type(self.generation_ids) is not tuple or not self.generation_ids:
            raise ValueError("generation_ids must be a non-empty exact tuple")
        for value in self.generation_ids:
            _require_id(value, "generation_id")
        if self.generation_ids != tuple(sorted(set(self.generation_ids))):
            raise ValueError("generation_ids must be unique and canonical")
        if type(self.installed_row_hashes) is not tuple or not self.installed_row_hashes:
            raise ValueError("installed_row_hashes must be a non-empty exact tuple")
        for value in self.installed_row_hashes:
            _require_hash(value, "installed_row_hash")
        if type(self.allocation_count) is not int or self.allocation_count != len(
            self.installed_row_hashes
        ):
            raise ValueError("receipt allocation_count must equal row hashes")
        _require_hash(
            self.installed_allocation_aggregate_hash,
            "installed_allocation_aggregate_hash",
        )
        _require_exact_enum(self.header_state, InstallationHeaderState, "header_state")
        _require_exact_enum(self.status, InstallationStatus, "status")
        _utc_text(self.installed_at, "installed_at")

    def _data(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "installation_target": self.installation_target.value,
            "qualification_id": self.qualification_id,
            "epoch": self.epoch,
            "contract_hash": self.contract_hash,
            "effect_authorization_binding_hash": self.effect_authorization_binding_hash,
            "manifest_hash": self.manifest_hash,
            "generation_ids": list(self.generation_ids),
            "installed_row_hashes": list(self.installed_row_hashes),
            "allocation_count": self.allocation_count,
            "installed_allocation_aggregate_hash": self.installed_allocation_aggregate_hash,
            "header_state": self.header_state.value,
            "status": self.status.value,
            "installed_at": _utc_text(self.installed_at, "installed_at"),
        }

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(self.SCHEMA, self._data())

    def canonical_hash(self) -> str:
        return _domain_hash(self.DOMAIN, self.to_canonical_bytes())


__all__: Final = (
    "ActivationParentKind",
    "AllocationEffectRole",
    "AllocationInitialState",
    "AllocationInstallationReceipt",
    "AllocationRowKind",
    "EffectAllocationRow",
    "EffectFamily",
    "EffectKind",
    "ExactEffectAllocationManifest",
    "InstallationHeaderState",
    "InstallationStatus",
    "InstallationTarget",
)
