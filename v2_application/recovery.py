"""Derived package recovery and single-owner handoff admission for V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import re

from reservation_domain import ExecutionCertainty, ServiceKind
from reservation_followup.handoff import (
    HandoffReasonCode,
    HandoffRequested,
    HandoffTransitionStatus,
    HandoffWorkflow,
)
from reservation_followup.sqlite_store import (
    IdentityConflict,
    SQLiteFollowupUnitOfWork,
)
from reservation_followup.types import HandoffEffectPolicy
from v2_contracts.payments import BusinessUnit, PaymentObligation


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_EXPECTED_UNIT = {
    ServiceKind.LODGING: BusinessUnit.HOSTEL,
    ServiceKind.ACTIVITY: BusinessUnit.AGENCY,
}
_REVIEW_CERTAINTIES = {
    ExecutionCertainty.CALLED_NO_EFFECT,
    ExecutionCertainty.CALLED_UNKNOWN,
}


def _id(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical opaque identifier")
    return value


def _lead_hash(lead_id: str) -> str:
    canonical = _id(lead_id, "lead_id")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _workflow_incident_key(workflow_id: str) -> str:
    canonical = _id(workflow_id, "workflow_id")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"incident:v2:{digest}"


def _id_set(value: object, name: str) -> frozenset[str]:
    if type(value) is not frozenset:
        raise TypeError(f"{name} must be an exact frozenset")
    for item in value:
        _id(item, name)
    return value


class PackageProgressStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class PackageComponent:
    command_id: str
    service: ServiceKind
    business_unit: BusinessUnit
    certainty: ExecutionCertainty

    def __post_init__(self) -> None:
        _id(self.command_id, "package_component.command_id")
        if type(self.service) is not ServiceKind:
            raise TypeError("service must be exact ServiceKind")
        if type(self.business_unit) is not BusinessUnit:
            raise TypeError("business_unit must be exact BusinessUnit")
        if self.business_unit is not _EXPECTED_UNIT[self.service]:
            raise ValueError("component service and business unit disagree")
        if type(self.certainty) is not ExecutionCertainty:
            raise TypeError("certainty must be exact ExecutionCertainty")


@dataclass(frozen=True, slots=True)
class PackageProgress:
    status: PackageProgressStatus
    confirmed_command_ids: frozenset[str]
    dispatchable_command_ids: frozenset[str]
    uncertain_command_ids: frozenset[str]
    unsettled_payment_ids: frozenset[str]
    missing_receipts: frozenset[str]
    payment_claim_namespaces: tuple[str, str]

    def __post_init__(self) -> None:
        if type(self.status) is not PackageProgressStatus:
            raise TypeError("status must be exact PackageProgressStatus")
        for field_name in (
            "confirmed_command_ids",
            "dispatchable_command_ids",
            "uncertain_command_ids",
            "unsettled_payment_ids",
            "missing_receipts",
        ):
            _id_set(getattr(self, field_name), field_name)
        if (
            type(self.payment_claim_namespaces) is not tuple
            or len(self.payment_claim_namespaces) != 2
        ):
            raise ValueError("package requires exactly two payment claim namespaces")
        for namespace in self.payment_claim_namespaces:
            _id(namespace, "payment_claim_namespace")
        command_partitions = (
            self.confirmed_command_ids,
            self.dispatchable_command_ids,
            self.uncertain_command_ids,
        )
        if any(
            left & right
            for index, left in enumerate(command_partitions)
            for right in command_partitions[index + 1 :]
        ):
            raise ValueError("package command projections must be disjoint")
        if self.status is PackageProgressStatus.COMPLETED and (
            self.dispatchable_command_ids
            or self.uncertain_command_ids
            or self.unsettled_payment_ids
            or self.missing_receipts
        ):
            raise ValueError("completed package cannot retain unresolved work")
        if (
            self.status is PackageProgressStatus.MANUAL_REVIEW
            and not self.uncertain_command_ids
        ):
            raise ValueError("manual-review package requires a terminal component issue")


class PackageRecoveryPolicy:
    """Derive package progress from canonical ledgers and receipts only."""

    def derive(
        self,
        *,
        components: tuple[PackageComponent, PackageComponent],
        obligations: tuple[PaymentObligation, PaymentObligation],
        settled_payment_ids: frozenset[str],
        required_receipts: frozenset[str],
        observed_receipts: frozenset[str],
    ) -> PackageProgress:
        if (
            type(components) is not tuple
            or len(components) != 2
            or any(type(item) is not PackageComponent for item in components)
        ):
            raise ValueError("package requires exactly two canonical components")
        by_service = {item.service: item for item in components}
        if set(by_service) != {ServiceKind.LODGING, ServiceKind.ACTIVITY}:
            raise ValueError("package requires one lodging and one activity component")
        if len({item.command_id for item in components}) != 2:
            raise ValueError("package component command ids must be distinct")

        if (
            type(obligations) is not tuple
            or len(obligations) != 2
            or any(type(item) is not PaymentObligation for item in obligations)
        ):
            raise ValueError("package requires exactly two payment obligations")
        by_unit = {item.business_unit: item for item in obligations}
        if set(by_unit) != {BusinessUnit.HOSTEL, BusinessUnit.AGENCY}:
            raise ValueError("package obligations must remain separated by business unit")
        if len({item.payment_id for item in obligations}) != 2:
            raise ValueError("package payment ids must be distinct")
        if len({item.receiver_profile_id for item in obligations}) != 2:
            raise ValueError("package receiver profiles must be distinct")

        settled = _id_set(settled_payment_ids, "settled_payment_ids")
        payment_ids = frozenset(item.payment_id for item in obligations)
        if not settled <= payment_ids:
            raise ValueError("settled payment id is outside the package obligations")
        required = _id_set(required_receipts, "required_receipts")
        observed = _id_set(observed_receipts, "observed_receipts")

        confirmed = frozenset(
            item.command_id
            for item in components
            if item.certainty is ExecutionCertainty.EFFECT_CONFIRMED
        )
        dispatchable = frozenset(
            item.command_id
            for item in components
            if item.certainty is ExecutionCertainty.NOT_CALLED
        )
        uncertain = frozenset(
            item.command_id for item in components if item.certainty in _REVIEW_CERTAINTIES
        )
        unsettled = payment_ids - settled
        missing = required - observed
        if uncertain:
            status = PackageProgressStatus.MANUAL_REVIEW
        elif (
            len(confirmed) == 2
            and not dispatchable
            and not unsettled
            and not missing
        ):
            status = PackageProgressStatus.COMPLETED
        else:
            status = PackageProgressStatus.PENDING

        namespaces = tuple(
            f"payment-claim:{unit.value}:{by_unit[unit].payment_id}"
            for unit in (BusinessUnit.HOSTEL, BusinessUnit.AGENCY)
        )
        return PackageProgress(
            status=status,
            confirmed_command_ids=confirmed,
            dispatchable_command_ids=dispatchable,
            uncertain_command_ids=uncertain,
            unsettled_payment_ids=unsettled,
            missing_receipts=missing,
            payment_claim_namespaces=namespaces,
        )


@dataclass(frozen=True, slots=True)
class HandoffOpenResult:
    workflow: HandoffWorkflow
    created: bool

    def __post_init__(self) -> None:
        if type(self.workflow) is not HandoffWorkflow:
            raise TypeError("workflow must be exact HandoffWorkflow")
        if type(self.created) is not bool:
            raise TypeError("created must be exact bool")


class CommercialEffectBlocked(RuntimeError):
    def __init__(self, handoff_id: str) -> None:
        self.handoff_id = _id(handoff_id, "handoff_id")
        super().__init__("commercial effect blocked by active handoff")


class HandoffEffectGuard:
    """Deny commercial admission while the mature handoff queue is active."""

    def __init__(self, *, store: SQLiteFollowupUnitOfWork) -> None:
        if type(store) is not SQLiteFollowupUnitOfWork:
            raise TypeError("store must be exact SQLiteFollowupUnitOfWork")
        self._store = store

    def assert_allowed(self, *, lead_id: str) -> None:
        active = self._store.find_active_handoff_by_lead_hash(_lead_hash(lead_id))
        if active is not None:
            raise CommercialEffectBlocked(active.request.handoff_id)

    def allows_workflow(self, workflow_id: str) -> bool:
        active = self._store.find_active_handoff_by_incident_key(
            _workflow_incident_key(workflow_id)
        )
        return active is None

    def assert_workflow_allowed(self, *, workflow_id: str) -> None:
        active = self._store.find_active_handoff_by_incident_key(
            _workflow_incident_key(workflow_id)
        )
        if active is not None:
            raise CommercialEffectBlocked(active.request.handoff_id)


class HandoffCoordinator:
    """Open one deterministic exception workflow without provider capabilities."""

    def __init__(self, *, store: SQLiteFollowupUnitOfWork) -> None:
        if type(store) is not SQLiteFollowupUnitOfWork:
            raise TypeError("store must be exact SQLiteFollowupUnitOfWork")
        self._store = store

    def open_exception_once(
        self,
        *,
        lead_id: str,
        workflow_id: str,
        source_event_id: str,
        reason_code: HandoffReasonCode,
        now: datetime,
    ) -> HandoffOpenResult:
        lead_key_hash = _lead_hash(lead_id)
        workflow_id = _id(workflow_id, "workflow_id")
        source_event_id = _id(source_event_id, "source_event_id")
        if type(reason_code) is not HandoffReasonCode:
            raise TypeError("reason_code must be exact HandoffReasonCode")

        active = self._store.find_active_handoff_by_lead_hash(lead_key_hash)
        if active is not None:
            return HandoffOpenResult(active, False)

        material = "\0".join((lead_key_hash, workflow_id, reason_code.value))
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        request = HandoffRequested(
            handoff_id=f"handoff:v2:{digest}",
            lead_key_hash=lead_key_hash,
            incident_key=_workflow_incident_key(workflow_id),
            reason_code=reason_code,
            source_event_id=source_event_id,
            reservation_anchor=None,
            requested_at=now,
        )
        try:
            transition = self._store.open_handoff(
                request,
                HandoffEffectPolicy.default_email_disabled(),
            )
        except IdentityConflict:
            active = self._store.find_active_handoff_by_lead_hash(lead_key_hash)
            if active is None:
                raise
            return HandoffOpenResult(active, False)
        if transition.status is not HandoffTransitionStatus.APPLIED:
            raise RuntimeError("new deterministic handoff was not applied")
        return HandoffOpenResult(transition.state, True)


__all__ = [
    "CommercialEffectBlocked",
    "HandoffCoordinator",
    "HandoffEffectGuard",
    "HandoffOpenResult",
    "PackageComponent",
    "PackageProgress",
    "PackageProgressStatus",
    "PackageRecoveryPolicy",
]
