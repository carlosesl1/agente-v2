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
from v2_contracts.channel import PublicMessageAuthor
from v2_contracts.localization import (
    CustomerLanguage,
    customer_language_from_phone,
)
from v2_contracts.payments import BusinessUnit, PaymentInstruction, StripePaymentLink


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
        payment_store: SQLitePaymentInitiationStore | None,
        public_store: PublicOutboxStore,
        account_profiles: dict[BusinessUnit, str] | None,
        subscriber_id: str = "",
        lead_resolver: object | None = None,
        include_payment_offers: bool = True,
    ) -> None:
        if type(execution) is not SQLiteUnitOfWork:
            raise TypeError("execution must be exact SQLiteUnitOfWork")
        if payment_store is not None and type(payment_store) is not SQLitePaymentInitiationStore:
            raise TypeError("payment_store must be exact SQLitePaymentInitiationStore or None")
        if type(public_store) is not PublicOutboxStore:
            raise TypeError("public_store must be exact PublicOutboxStore")
        if type(subscriber_id) is not str or (
            subscriber_id and not subscriber_id.isdecimal()
        ):
            raise ValueError("subscriber_id must be empty or exact decimal text")
        if lead_resolver is not None and (
            not callable(getattr(lead_resolver, "lead_id_for_command", None))
            or not callable(getattr(lead_resolver, "lead_id_for_payment", None))
        ):
            raise TypeError("lead_resolver must resolve command and payment owners")
        if bool(subscriber_id) == (lead_resolver is not None):
            raise ValueError("completion requires exactly one fixed or durable lead source")
        if type(include_payment_offers) is not bool:
            raise TypeError("include_payment_offers must be an exact bool")
        profiles = {} if account_profiles is None else account_profiles
        if type(profiles) is not dict:
            raise TypeError("account_profiles must be an exact dict or None")
        if profiles and set(profiles) != set(BusinessUnit):
            raise ValueError("account_profiles must cover both business units")
        if include_payment_offers and set(profiles) != set(BusinessUnit):
            raise ValueError("payment offers require both business unit profiles")
        if include_payment_offers and payment_store is None:
            raise ValueError("payment offers require the payment store capability")
        if len(set(profiles.values())) != len(profiles):
            raise ValueError("business units must have distinct account profiles")
        self._execution = execution
        self._payment_store = payment_store
        self._public_store = public_store
        self._lead_id = f"manychat:{subscriber_id}" if subscriber_id else None
        self._lead_resolver = lead_resolver
        self._include_payment_offers = include_payment_offers
        self._unit_by_profile = {
            profile: unit for unit, profile in profiles.items()
        }

    def run_once(self, *, now: datetime) -> CompletionProjectionResult:
        instant = _utc(now)
        inserted = 0
        attempted = 0
        grouped: dict[
            tuple[str, str, int],
            list[tuple[ReservationCommand, LedgerSnapshot]],
        ] = defaultdict(list)
        for command, ledger in self._execution.list_outcome_projection_inputs():
            grouped[(
                command.payload.customer.customer_ref,
                command.draft_id,
                command.draft_version,
            )].append(
                (command, ledger)
            )
        for (customer_ref, draft_id, draft_version), members in grouped.items():
            if not _confirmed_group(members):
                continue
            attempted += 1
            release_id = _opaque(
                "release:00-reservation",
                customer_ref,
                draft_id,
                str(draft_version),
            )
            lead_ids = {
                self._lead_resolver.lead_id_for_command(command.command_id)
                if self._lead_resolver is not None
                else self._lead_id
                for command, _ in members
            }
            if len(lead_ids) != 1 or None in lead_ids:
                raise RuntimeError("confirmed group does not have one durable lead owner")
            lead_id = next(iter(lead_ids))
            inserted += self._public_store.enqueue(
                PublicReply(
                    release_id=release_id,
                    lead_id=lead_id,
                    message_id=_opaque(
                        "message:reservation-confirmed",
                        customer_ref,
                        draft_id,
                        str(draft_version),
                    ),
                    channel="manychat",
                    chunks=(_confirmation_text(tuple(item[0] for item in members)),),
                    author=PublicMessageAuthor.AUTHENTICATED_SYSTEM,
                ),
                now=instant,
            )
        if not self._include_payment_offers:
            return CompletionProjectionResult(inserted, attempted)
        assert self._payment_store is not None
        for offer in self._payment_store.completed_offers():
            if type(offer) not in (StripePaymentLink, PaymentInstruction):
                continue
            profile_id = (
                offer.account_profile_id
                if type(offer) is StripePaymentLink
                else offer.receiver_profile_id
            )
            unit = self._unit_by_profile.get(profile_id)
            if unit is None:
                raise RuntimeError("completed payment offer has an unknown receiver profile")
            if type(offer) is StripePaymentLink:
                message_id = _opaque("message:payment-link", offer.payment_id)
                text = _payment_text(
                    unit,
                    offer.public_url,
                    offer.customer_language,
                )
            else:
                message_id = _opaque(
                    f"message:payment-{offer.method.value}",
                    offer.payment_id,
                )
                text = offer.public_text
            lead_id = (
                self._lead_resolver.lead_id_for_payment(offer.payment_id)
                if self._lead_resolver is not None
                else self._lead_id
            )
            if lead_id is None:
                raise RuntimeError("payment offer does not have a durable lead owner")
            attempted += 1
            inserted += self._public_store.enqueue(
                PublicReply(
                    release_id=_opaque("release:10-payment", offer.payment_id),
                    lead_id=lead_id,
                    message_id=message_id,
                    channel="manychat",
                    chunks=(text,),
                    author=PublicMessageAuthor.AUTHENTICATED_SYSTEM,
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
    languages = {
        customer_language_from_phone(command.payload.customer.phone_e164)
        for command in commands
    }
    if len(languages) != 1:
        raise RuntimeError("confirmed public group has mixed customer language")
    language = languages.pop()
    services = {
        command.payload.components[0].service for command in commands
    }
    copy = {
        CustomerLanguage.PT_BR: {
            frozenset((ServiceKind.LODGING, ServiceKind.ACTIVITY)): (
                "Sua hospedagem e seu passeio foram confirmados."
            ),
            frozenset((ServiceKind.LODGING,)): "Sua hospedagem foi confirmada.",
            frozenset((ServiceKind.ACTIVITY,)): "Seu passeio foi confirmado.",
        },
        CustomerLanguage.EN: {
            frozenset((ServiceKind.LODGING, ServiceKind.ACTIVITY)): (
                "Your accommodation and tour have been confirmed."
            ),
            frozenset((ServiceKind.LODGING,)): (
                "Your accommodation has been confirmed."
            ),
            frozenset((ServiceKind.ACTIVITY,)): "Your tour has been confirmed.",
        },
    }
    try:
        return copy[language][frozenset(services)]
    except KeyError as exc:
        raise RuntimeError(
            "confirmed public group has an unsupported service shape"
        ) from exc


def _payment_text(
    unit: BusinessUnit,
    url: str,
    customer_language: CustomerLanguage | None,
) -> str:
    if type(customer_language) is not CustomerLanguage:
        raise ValueError("payment message requires exact customer_language")
    label = {
        CustomerLanguage.PT_BR: {
            BusinessUnit.HOSTEL: "Link de pagamento da hospedagem",
            BusinessUnit.AGENCY: "Link de pagamento do passeio",
        },
        CustomerLanguage.EN: {
            BusinessUnit.HOSTEL: "Accommodation payment link",
            BusinessUnit.AGENCY: "Tour payment link",
        },
    }[customer_language][unit]
    return f"{label}: {url}"


def _opaque(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()[:32]


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("now must be an exact UTC datetime")
    return value.astimezone(timezone.utc)


__all__ = ["CompletionProjectionResult", "CompletionProjector"]
