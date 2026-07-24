"""Replay-safe projection of reservation receipts and private payment links."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib

from reservation_domain import (
    ExecutionCertainty,
    ReservationCommand,
    ReservationOperation,
    ServiceKind,
    loads_outcome,
)
from reservation_execution import LedgerStatus
from reservation_execution.projection import LedgerSnapshot
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from v2_application.completion import PublicOutboxStore, PublicReply
from v2_application.payments import SQLitePaymentInitiationStore
from v2_contracts.payments import BusinessUnit, StripePaymentLink


@dataclass(frozen=True, slots=True)
class CompletionProjectionResult:
    inserted: int
    attempted_releases: int

    def __post_init__(self) -> None:
        if (
            type(self.inserted) is not int
            or self.inserted < 0
            or type(self.attempted_releases) is not int
            or self.attempted_releases < 0
        ):
            raise ValueError("completion projection counters must be non-negative")


class CompletionProjector:
    """Copy durable private outcomes into the local send outbox by replay."""

    def __init__(
        self,
        *,
        execution: SQLiteUnitOfWork,
        payment_store: SQLitePaymentInitiationStore,
        public_store: PublicOutboxStore,
        subscriber_id: str,
        account_profiles: dict[BusinessUnit, str],
    ) -> None:
        if type(execution) is not SQLiteUnitOfWork:
            raise TypeError("execution must be exact SQLiteUnitOfWork")
        if type(payment_store) is not SQLitePaymentInitiationStore:
            raise TypeError("payment_store must be exact SQLitePaymentInitiationStore")
        if type(public_store) is not PublicOutboxStore:
            raise TypeError("public_store must be exact PublicOutboxStore")
        if type(subscriber_id) is not str or not subscriber_id.isdecimal():
            raise ValueError("subscriber_id must be exact decimal text")
        if type(account_profiles) is not dict or set(account_profiles) != set(
            BusinessUnit
        ):
            raise ValueError("account_profiles must cover both business units")
        if len(set(account_profiles.values())) != len(account_profiles):
            raise ValueError("business units must have distinct account profiles")
        self._execution = execution
        self._payment_store = payment_store
        self._public_store = public_store
        self._lead_id = f"manychat:{subscriber_id}"
        self._unit_by_profile = {
            profile: unit for unit, profile in account_profiles.items()
        }

    def run_once(self, *, now: datetime) -> CompletionProjectionResult:
        instant = _utc(now)
        inserted = 0
        attempted = 0
        grouped: dict[
            tuple[str, int],
            list[tuple[ReservationCommand, LedgerSnapshot]],
        ] = defaultdict(list)
        for command, ledger in self._execution.list_outcome_projection_inputs():
            grouped[(command.draft_id, command.draft_version)].append(
                (command, ledger)
            )
        for (draft_id, draft_version), members in grouped.items():
            if not _confirmed_group(members):
                continue
            attempted += 1
            release_id = _opaque(
                "release:00-reservation",
                draft_id,
                str(draft_version),
            )
            inserted += self._public_store.enqueue(
                PublicReply(
                    release_id=release_id,
                    lead_id=self._lead_id,
                    message_id=_opaque(
                        "message:reservation-confirmed",
                        draft_id,
                        str(draft_version),
                    ),
                    channel="manychat",
                    chunks=(_confirmation_text(tuple(item[0] for item in members)),),
                ),
                now=instant,
            )
        for offer in self._payment_store.completed_offers():
            if type(offer) is not StripePaymentLink:
                continue
            unit = self._unit_by_profile.get(offer.account_profile_id)
            if unit is None:
                raise RuntimeError("completed Stripe link has an unknown account profile")
            attempted += 1
            inserted += self._public_store.enqueue(
                PublicReply(
                    release_id=_opaque("release:10-payment", offer.payment_id),
                    lead_id=self._lead_id,
                    message_id=_opaque("message:payment-link", offer.payment_id),
                    channel="manychat",
                    chunks=(_payment_text(unit, offer.public_url),),
                ),
                now=instant,
            )
        return CompletionProjectionResult(inserted, attempted)


def _confirmed_group(
    members: list[tuple[ReservationCommand, LedgerSnapshot]],
) -> bool:
    commands = tuple(command for command, _ in members)
    if len(commands) == 1:
        shape = commands[0].operation in (
            ReservationOperation.RESERVE_LODGING,
            ReservationOperation.BOOK_ACTIVITY,
        )
    else:
        shape = len(commands) == 2 and {
            command.operation for command in commands
        } == {
            ReservationOperation.RESERVE_LODGING,
            ReservationOperation.BOOK_ACTIVITY,
        }
    if not shape:
        return False
    for _, ledger in members:
        if (
            ledger.status is not LedgerStatus.OUTCOME_RECORDED
            or ledger.outcome_json is None
            or loads_outcome(ledger.outcome_json).certainty
            is not ExecutionCertainty.EFFECT_CONFIRMED
        ):
            return False
    return True


def _confirmation_text(commands: tuple[ReservationCommand, ...]) -> str:
    services = {
        command.payload.components[0].service for command in commands
    }
    if services == {ServiceKind.LODGING, ServiceKind.ACTIVITY}:
        return "Sua hospedagem e seu passeio foram confirmados."
    if services == {ServiceKind.LODGING}:
        return "Sua hospedagem foi confirmada."
    if services == {ServiceKind.ACTIVITY}:
        return "Seu passeio foi confirmado."
    raise RuntimeError("confirmed public group has an unsupported service shape")


def _payment_text(unit: BusinessUnit, url: str) -> str:
    label = "hospedagem" if unit is BusinessUnit.HOSTEL else "passeio"
    return f"Link de pagamento da {label}: {url}"


def _opaque(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()[:32]


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("now must be an exact UTC datetime")
    return value.astimezone(timezone.utc)


__all__ = ["CompletionProjectionResult", "CompletionProjector"]
