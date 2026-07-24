"""Idempotent reservation-outcome to payment-obligation projection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib

from reservation_domain import (
    ExecutionCertainty,
    ReservationCommand,
    ReservationOperation,
    ServiceKind,
    dumps_outcome,
    loads_outcome,
)
from reservation_execution import LedgerStatus
from reservation_execution.projection import LedgerSnapshot
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.types import (
    BusinessUnit as FollowupBusinessUnit,
    ConfirmedReservationAnchor,
)
from v2_application.payments import SQLitePaymentInitiationStore
from v2_contracts.payments import (
    BusinessUnit,
    DueKind,
    PaymentObligation,
    PaymentMethod,
    PaymentSelection,
    ReservationPaymentContext,
)


@dataclass(frozen=True, slots=True)
class OutcomeProjectionResult:
    inserted: int
    replayed: int
    pending_groups: int
    suppressed_groups: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (
                self.inserted,
                self.replayed,
                self.pending_groups,
                self.suppressed_groups,
            )
        ):
            raise ValueError("projection counters must be exact non-negative integers")


class ReservationOutcomeProjector:
    """Project complete successful draft groups without owning provider effects."""

    def __init__(
        self,
        *,
        execution: SQLiteUnitOfWork,
        payment_store: SQLitePaymentInitiationStore,
        receiver_profiles: dict[BusinessUnit, str],
    ) -> None:
        if type(execution) is not SQLiteUnitOfWork:
            raise TypeError("execution must be exact SQLiteUnitOfWork")
        if type(payment_store) is not SQLitePaymentInitiationStore:
            raise TypeError("payment_store must be exact SQLitePaymentInitiationStore")
        if type(receiver_profiles) is not dict or set(receiver_profiles) != set(
            BusinessUnit
        ):
            raise ValueError("receiver_profiles must cover the closed business-unit catalog")
        if any(type(value) is not str or not value for value in receiver_profiles.values()):
            raise ValueError("receiver profile ids must be non-empty exact text")
        if len(set(receiver_profiles.values())) != len(receiver_profiles):
            raise ValueError("business units must use distinct receiver profiles")
        self._execution = execution
        self._payment_store = payment_store
        self._receiver_profiles = dict(receiver_profiles)

    def run_once(self, *, now: datetime) -> OutcomeProjectionResult:
        instant = _utc(now)
        grouped: dict[
            tuple[str, int],
            list[tuple[ReservationCommand, LedgerSnapshot]],
        ] = defaultdict(list)
        for command, ledger in self._execution.list_outcome_projection_inputs():
            grouped[(command.draft_id, command.draft_version)].append(
                (command, ledger)
            )

        inserted = 0
        replayed = 0
        pending = 0
        suppressed = 0
        for members in grouped.values():
            terminal = all(
                ledger.status
                in (LedgerStatus.OUTCOME_RECORDED, LedgerStatus.MANUAL_REVIEW)
                for _, ledger in members
            )
            if not terminal:
                pending += 1
                continue
            outcomes = tuple(
                None
                if ledger.outcome_json is None
                else loads_outcome(ledger.outcome_json)
                for _, ledger in members
            )
            if any(
                outcome is None
                or outcome.certainty is not ExecutionCertainty.EFFECT_CONFIRMED
                for outcome in outcomes
            ):
                suppressed += 1
                continue
            if not _closed_group_shape(tuple(command for command, _ in members)):
                suppressed += 1
                continue
            if any(command.payload.terms.payment_method != "stripe" for command, _ in members):
                suppressed += 1
                continue

            selections = tuple(
                self._selection(command, ledger, outcome)
                for (command, ledger), outcome in zip(members, outcomes, strict=True)
            )
            actionable = tuple(
                selection for selection in selections if selection is not None
            )
            for selection in actionable:
                if self._payment_store.enqueue(selection, now=instant):
                    inserted += 1
                else:
                    replayed += 1
        return OutcomeProjectionResult(inserted, replayed, pending, suppressed)

    def _selection(
        self,
        command: ReservationCommand,
        ledger: LedgerSnapshot,
        outcome,
    ) -> PaymentSelection | None:
        component = command.payload.components[0]
        unit = {
            ServiceKind.LODGING: BusinessUnit.HOSTEL,
            ServiceKind.ACTIVITY: BusinessUnit.AGENCY,
        }[component.service]
        followup_unit = FollowupBusinessUnit(unit.value)
        receiver = self._receiver_profiles[unit]
        amount_minor = _minor_units(component.total.amount)
        anchor_id = _opaque("reservation-anchor", command.command_id)
        payment_id = _opaque("payment", command.command_id, unit.value)
        payment_target_id = _opaque("payment-target", command.command_id)
        if ledger.outcome_hash != hashlib.sha256(
            dumps_outcome(outcome).encode("utf-8")
        ).hexdigest():
            raise RuntimeError("reservation outcome hash diverged during projection")
        ConfirmedReservationAnchor(
            reservation_workflow_id=command.workflow_id,
            reservation_command_id=command.command_id,
            reservation_subject_signature=command.subject_signature,
            reservation_outcome_hash=ledger.outcome_hash,
            reservation_outcome=outcome,
            provider_reference=outcome.provider_reference,
            service=component.service,
            business_unit=followup_unit,
            payment_target_id=payment_target_id,
            amount_minor=amount_minor,
            currency=component.total.currency,
            receiver_profile_id=receiver,
            confirmed_at=ledger.updated_at,
            payment_deadline=ledger.updated_at + timedelta(hours=24),
        )
        context = ReservationPaymentContext(
            payment_id=payment_id,
            reservation_anchor_id=anchor_id,
            business_unit=unit,
            amount_minor=amount_minor,
            currency=component.total.currency,
            receiver_profile_id=receiver,
            guest_country_code=command.payload.customer.country_code,
            economic_version=command.draft_version,
        )
        due_kind = (
            DueKind.DUE_AT_CHECKIN
            if context.business_unit is BusinessUnit.HOSTEL
            and context.guest_country_code != "BR"
            else DueKind.PREPAYMENT
        )
        if due_kind is DueKind.DUE_AT_CHECKIN:
            return None
        obligation = PaymentObligation(
            payment_id=context.payment_id,
            reservation_anchor_id=context.reservation_anchor_id,
            business_unit=context.business_unit,
            amount_minor=context.amount_minor,
            currency=context.currency,
            due_kind=due_kind,
            economic_version=context.economic_version,
            receiver_profile_id=context.receiver_profile_id,
        )
        return PaymentSelection(obligation, PaymentMethod.STRIPE)


def _closed_group_shape(commands: tuple[ReservationCommand, ...]) -> bool:
    if len(commands) == 1:
        return commands[0].operation in (
            ReservationOperation.RESERVE_LODGING,
            ReservationOperation.BOOK_ACTIVITY,
        )
    return len(commands) == 2 and {
        command.operation for command in commands
    } == {
        ReservationOperation.RESERVE_LODGING,
        ReservationOperation.BOOK_ACTIVITY,
    }


def _minor_units(amount: Decimal) -> int:
    minor = amount * Decimal("100")
    integral = minor.to_integral_value()
    if minor != integral or integral < 1:
        raise ValueError("reservation amount is not exact positive minor units")
    return int(integral)


def _opaque(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()[:32]


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("now must be an exact UTC datetime")
    return value.astimezone(timezone.utc)


__all__ = ["OutcomeProjectionResult", "ReservationOutcomeProjector"]
