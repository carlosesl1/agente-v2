"""Derive completion events from canonical ledgers; Maya alone authors replies."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from reservation_boundary.completion import (
    consolidation_source,
    is_unattempted_completion,
    source_coverage,
)
from reservation_boundary.conversation import SourceEventIdentity
from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_domain import (
    ExecutionCertainty,
    ReservationCommand,
    ReservationOperation,
    loads_outcome,
)
from reservation_execution import LedgerStatus
from reservation_execution.projection import LedgerSnapshot
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from v2_application.completion import PublicClaim, PublicOutboxStore, PublicReply
from v2_application.payments import SQLitePaymentInitiationStore
from v2_contracts.channel import PublicDeliveryRejected, PublicMessageAuthor
from v2_contracts.completion import CompletionEvent, CompletionTurn
from v2_contracts.execution_context import PaymentInitiationContext
from v2_contracts.payments import (
    BusinessUnit,
    CustomerLanguage,
    PaymentMethod,
    StripePaymentLink,
)


@dataclass(frozen=True, slots=True)
class CompletionProjectionResult:
    inserted: int
    attempted_releases: int


@dataclass(frozen=True, slots=True)
class CompletionContext:
    events: tuple[CompletionEvent, ...] = ()
    sources: tuple[SourceEventIdentity, ...] = ()
    superseded_turns: tuple[str, ...] = ()


class CompletionSuperseded(RuntimeError):
    """A customer turn consumed this event while continuation work was in flight."""


class CompletionProjector:
    def __init__(
        self,
        *,
        execution: SQLiteUnitOfWork,
        payment_store: SQLitePaymentInitiationStore | None,
        public_store: PublicOutboxStore,
        boundary: SQLiteBoundaryStore,
        lead_resolver: object,
        include_payment_offers: bool = True,
        executor=None,
        inbox=None,
        followup=None,
    ) -> None:
        if (
            type(execution) is not SQLiteUnitOfWork
            or type(boundary) is not SQLiteBoundaryStore
        ):
            raise TypeError("completion requires exact execution and boundary stores")
        if (
            payment_store is not None
            and type(payment_store) is not SQLitePaymentInitiationStore
        ):
            raise TypeError("payment_store must be exact or None")
        if type(public_store) is not PublicOutboxStore:
            raise TypeError("public_store must be exact PublicOutboxStore")
        if not all(
            callable(getattr(lead_resolver, name, None))
            for name in ("lead_id_for_command", "lead_id_for_payment")
        ):
            raise TypeError("completion requires durable lead identity")
        if type(include_payment_offers) is not bool or (
            include_payment_offers and payment_store is None
        ):
            raise ValueError("payment offers require their store capability")
        self._followup = followup
        self._execution = execution
        self._payment_store = payment_store
        self._public_store = public_store
        self._boundary = boundary
        self._lead_resolver = lead_resolver
        self._include_payment_offers = include_payment_offers
        self.executor = executor
        self._inbox = inbox

    def _legacy_exists(self, lead_id, release_id):
        return any(
            m.release_id == release_id
            for m in self._public_store.conversation_messages(lead_id)
        )

    def events(self) -> tuple[CompletionEvent, ...]:
        grouped = defaultdict(list)
        for command, ledger in self._execution.list_outcome_projection_inputs():
            grouped[
                (
                    command.payload.customer.customer_ref,
                    command.draft_id,
                    command.draft_version,
                )
            ].append((command, ledger))
        result = []
        for (customer, draft, version), members in sorted(grouped.items()):
            if not _terminal_group(members):
                continue
            owners = {
                self._lead_resolver.lead_id_for_command(c.command_id)
                for c, _ in members
            }
            if len(owners) != 1 or None in owners:
                raise RuntimeError(
                    "terminal group does not have one durable lead owner"
                )
            lead_id = owners.pop()
            legacy_id = _opaque(
                "release:00-reservation",
                customer,
                draft,
                str(version),
                *(
                    (_terminal_outcome_key(members),)
                    if not _confirmed_group(members)
                    else ()
                ),
            )
            if self._legacy_exists(lead_id, legacy_id):
                continue
            value = "|".join(
                sorted(
                    c.command_id + ":" + ledger.outcome_hash for c, ledger in members
                )
            )
            digest = hashlib.sha256(value.encode()).hexdigest()
            result.append(
                CompletionEvent(
                    "completion:reservation:" + digest[:40],
                    lead_id,
                    "reservation_result",
                    tuple(sorted(c.command_id for c, _ in members)),
                    None,
                    max(ledger.updated_at for _, ledger in members),
                    digest,
                )
            )
        if self._include_payment_offers:
            for offer, digest, updated in self._payment_store.completed_offer_events():
                lead_id = self._lead_resolver.lead_id_for_payment(offer.payment_id)
                if lead_id is None:
                    raise RuntimeError(
                        "payment offer does not have a durable lead owner"
                    )
                if self._legacy_exists(
                    lead_id, _opaque("release:10-payment", offer.payment_id)
                ):
                    continue
                result.append(
                    CompletionEvent(
                        "completion:payment:" + digest[:40],
                        lead_id,
                        "payment_offer",
                        (),
                        offer.payment_id,
                        updated,
                        digest,
                    )
                )
        if self._followup is not None:
            from reservation_followup.serialization import semantic_hash
            for command, _ in self._execution.list_outcome_projection_inputs():
                lead_id = self._lead_resolver.lead_id_for_command(command.command_id)
                if lead_id is None:
                    continue
                for workflow in self._followup.payments_for_reservation(command.command_id):
                    finish = workflow.settlement_finish
                    if finish is None:
                        continue
                    fact_hash = semantic_hash(finish)
                    result.append(CompletionEvent(
                        "completion:settlement:" + fact_hash[:40], lead_id,
                        "payment_settlement", (command.command_id,),
                        workflow.subject.payment_id, finish.finished_at, fact_hash,
                    ))
        return tuple(sorted(result, key=lambda e: (e.occurred_at, e.event_id)))

    def context(self, lead_id: str) -> CompletionContext:
        events, sources, superseded = [], [], {}
        for event in self.events():
            if event.lead_id != lead_id:
                continue
            covered = source_coverage(
                self._boundary, lead_id, event.event_id, event.payload_hash
            )
            if covered is None:
                events.append(event)
                sources.append(SourceEventIdentity(event.event_id, event.payload_hash))
            elif is_unattempted_completion(self._boundary, lead_id, covered[0]):
                events.append(event)
                superseded[covered[0]] = covered[1]
        for turn_id, receipt_hash in sorted(superseded.items()):
            sources.append(consolidation_source(turn_id, receipt_hash))
        return CompletionContext(
            tuple(events), tuple(sources), tuple(sorted(superseded))
        )

    def run_once(self, *, now: datetime) -> CompletionProjectionResult:
        _utc(now)
        if self.executor is None:
            raise RuntimeError("completion continuation executor is not bound")
        events_snapshot = self.events()
        grouped = defaultdict(list)
        for event in events_snapshot:
            if (
                source_coverage(
                    self._boundary, event.lead_id, event.event_id, event.payload_hash
                )
                is None
            ):
                grouped[event.lead_id].append(event)
        inserted = attempted = 0
        for lead_id, events in sorted(grouped.items()):
            if self._inbox is not None and self._inbox.has_waiting_lead(lead_id):
                continue
            attempted += 1
            try:
                result = self.executor.execute(CompletionTurn(lead_id, tuple(events)))
            except CompletionSuperseded:
                continue
            inserted += int(not result.replayed)
        self._enqueue_payment_buttons(events_snapshot, now=now)
        return CompletionProjectionResult(inserted, attempted)

    def payment_context_for_claim(self, claim: PublicClaim) -> PaymentInitiationContext:
        """Recover routing from the payment owner, keeping persisted IDs unchanged."""
        if type(claim) is not PublicClaim:
            raise PublicDeliveryRejected("payment routing requires an exact outbox claim")
        if self._payment_store is None:
            raise PublicDeliveryRejected("payment routing owner is unavailable")
        source = getattr(claim, "source_message_id", None)
        matches = tuple(
            offer for offer in self._payment_store.completed_offers()
            if type(offer) is StripePaymentLink
            and _opaque("message:payment-link", offer.payment_id) == source
        )
        if len(matches) != 1:
            raise PublicDeliveryRejected("payment row lacks one authenticated offer")
        offer = matches[0]
        lead = self._lead_resolver.lead_id_for_payment(offer.payment_id)
        if lead is None or getattr(claim, "lead_id", None) != lead:
            raise PublicDeliveryRejected("payment row belongs to another lead")
        records = tuple(r for r in self._payment_store.context_for_payment(offer.payment_id) if r.offer == offer)
        if len(records) != 1:
            raise PublicDeliveryRejected("payment row lacks one authenticated selection")
        record = records[0]
        obligation = record.selection.obligation
        if record.selection.method is not PaymentMethod.STRIPE:
            raise PublicDeliveryRejected("payment method differs from Stripe offer")
        if offer.account_profile_id != obligation.receiver_profile_id:
            raise PublicDeliveryRejected("payment account differs from obligation")
        if type(offer.customer_language) is not CustomerLanguage:
            raise PublicDeliveryRejected("payment route lacks an authenticated language")
        if (
            getattr(claim, "author", None) is not PublicMessageAuthor.AUTHENTICATED_SYSTEM
            or getattr(claim, "chunk_index", None) != 0
            or getattr(claim, "text", None) != _payment_button_text(obligation.business_unit, offer)
        ):
            raise PublicDeliveryRejected("payment row differs from its authenticated offer")
        return record

    def _enqueue_payment_buttons(self, events, *, now: datetime) -> None:
        """Project committed Stripe offers, independently of Maya's prose.

        Scan covered events too: a customer turn may consume the event, or the
        process may stop after the turn commit but before this outbox commit.
        Stable release identities make either recovery idempotent. A separate
        release namespace preserves pre-dispatch completion consolidation;
        historical release:10-payment rows remain excluded by events().
        """
        for event in events:
            if (
                event.kind != "payment_offer"
                or source_coverage(
                    self._boundary, event.lead_id, event.event_id, event.payload_hash
                )
                is None
            ):
                continue
            records = tuple(
                record
                for record in self._payment_store.context_for_payment(event.payment_id)
                if type(record.offer) is StripePaymentLink
            )
            if not records:
                continue  # Pix/Wise instructions stay in Maya's existing context.
            if len(records) != 1:
                raise RuntimeError("payment button requires one authenticated offer")
            record = records[0]
            offer = record.offer
            text = _payment_button_text(
                record.selection.obligation.business_unit, offer
            )
            self._public_store.enqueue(
                PublicReply(
                    release_id=_opaque("release:20-payment-button", offer.payment_id),
                    lead_id=event.lead_id,
                    message_id=_opaque("message:payment-link", offer.payment_id),
                    channel="manychat",
                    chunks=(text,),
                    author=PublicMessageAuthor.AUTHENTICATED_SYSTEM,
                ),
                now=now,
            )


def _payment_button_text(unit: BusinessUnit, offer: StripePaymentLink) -> str:
    """Existing channel payload: a factual field label and provider-issued URL."""
    if type(offer.customer_language) is not CustomerLanguage:
        raise ValueError("payment button requires exact customer_language")
    label = {
        CustomerLanguage.PT_BR: {
            BusinessUnit.HOSTEL: "Link de pagamento da hospedagem",
            BusinessUnit.AGENCY: "Link de pagamento do passeio",
        },
        CustomerLanguage.EN: {
            BusinessUnit.HOSTEL: "Accommodation payment link",
            BusinessUnit.AGENCY: "Tour payment link",
        },
    }[offer.customer_language][unit]
    return f"{label}: {offer.public_url}"


def _confirmed_group(
    members: list[tuple[ReservationCommand, LedgerSnapshot]],
) -> bool:
    return _terminal_group(members) and all(
        loads_outcome(ledger.outcome_json).certainty
        is ExecutionCertainty.EFFECT_CONFIRMED
        for _, ledger in members
        if ledger.outcome_json is not None
    )


def _terminal_group(
    members: list[tuple[ReservationCommand, LedgerSnapshot]],
) -> bool:
    commands = tuple(command for command, _ in members)
    if len(commands) == 1:
        shape = commands[0].operation in (
            ReservationOperation.RESERVE_LODGING,
            ReservationOperation.BOOK_ACTIVITY,
        )
    else:
        shape = len(commands) == 2 and {command.operation for command in commands} == {
            ReservationOperation.RESERVE_LODGING,
            ReservationOperation.BOOK_ACTIVITY,
        }
    if not shape:
        return False
    for _, ledger in members:
        if ledger.outcome_json is None:
            return False
        certainty = loads_outcome(ledger.outcome_json).certainty
        expected_status = (
            LedgerStatus.MANUAL_REVIEW
            if certainty is ExecutionCertainty.CALLED_UNKNOWN
            else LedgerStatus.OUTCOME_RECORDED
        )
        if ledger.status is not expected_status:
            return False
    return True


def _terminal_outcome_key(
    members: list[tuple[ReservationCommand, LedgerSnapshot]],
) -> str:
    if not _terminal_group(members):
        raise RuntimeError("terminal outcome key requires a terminal group")
    return "|".join(
        sorted(
            f"{command.operation.value}:{loads_outcome(ledger.outcome_json).certainty.value}"
            for command, ledger in members
            if ledger.outcome_json is not None
        )
    )


def _opaque(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()[:32]


def _utc(value: datetime) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("now must be an exact UTC datetime")
    return value.astimezone(timezone.utc)
