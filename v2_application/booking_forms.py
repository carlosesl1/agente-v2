"""Post-payment tour form projection. Reads finance; owns communication only."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from reservation_domain import ReservationOperation, loads_outcome
from reservation_followup.types import PaymentStatus, SettlementCertainty
from v2_application.completion import PublicClaim, PublicOutboxStore, PublicReply
from v2_contracts.booking_forms import BookingFormCatalog, BookingFormRoute
from v2_contracts.channel import PublicDeliveryRejected, PublicMessageAuthor
from v2_contracts.localization import CustomerLanguage, customer_language_from_phone


def _identity(prefix, *values):
    return prefix + ":" + hashlib.sha256("\0".join(values).encode()).hexdigest()[:40]


class BookingFormProjector:
    def __init__(
        self,
        *,
        execution,
        followup,
        public_store,
        boundary,
        lead_resolver,
        enabled_from: datetime | None,
        catalog=None,
    ):
        if enabled_from is not None and (
            type(enabled_from) is not datetime
            or enabled_from.tzinfo is None
            or enabled_from.utcoffset() != timedelta(0)
        ):
            raise ValueError("booking forms require an explicit UTC activation cutoff")
        if type(public_store) is not PublicOutboxStore:
            raise TypeError("booking forms require the canonical public outbox")
        self.execution = execution
        self.followup = followup
        self.public = public_store
        self.boundary = boundary
        self.leads = lead_resolver
        self.enabled_from = enabled_from
        self.catalog = catalog or BookingFormCatalog.load()

    def _candidates(self):
        if self.enabled_from is None or self.followup is None:
            return ()
        result = []
        for command, ledger in self.execution.list_outcome_projection_inputs():
            if command.operation is not ReservationOperation.BOOK_ACTIVITY:
                continue
            product = self.catalog.product_for_lookup(
                command.payload.components[0].lookup_id
            )
            if product is None:
                continue  # No operator-provided form for this product yet.
            lead = self.leads.lead_id_for_command(command.command_id)
            if lead is None:
                continue
            for state in self.followup.payments_for_reservation(command.command_id):
                finish = state.settlement_finish
                if (
                    state.status is not PaymentStatus.PAID
                    or finish is None
                    or finish.outcome.certainty is not SettlementCertainty.SETTLED
                    or finish.finished_at < self.enabled_from
                ):
                    continue
                anchor = state.subject.confirmed_reservation_anchor
                if (
                    anchor.reservation_command_id != command.command_id
                    or anchor.reservation_subject_signature != command.subject_signature
                    or anchor.reservation_outcome_hash != ledger.outcome_hash
                    or anchor.reservation_outcome != loads_outcome(ledger.outcome_json)
                    or anchor.business_unit.value != "agency"
                ):
                    raise ValueError(
                        "paid form anchor differs from its reservation ledger"
                    )
                result.append((command, state, lead, product))
        return tuple(
            sorted(
                result,
                key=lambda c: (
                    c[1].settlement_finish.finished_at,
                    c[1].subject.payment_id,
                ),
            )
        )

    def _language(self, command, lead):
        projection = self.boundary.load_latest_conversation_projection(lead)
        if projection is not None:
            language = projection.locale.split("-")[0]
            if language in {"pt", "en"}:
                return (
                    CustomerLanguage.PT_BR if language == "pt" else CustomerLanguage.EN
                )
        # Same typed localization fallback already used for payment instructions.
        return customer_language_from_phone(command.payload.customer.phone_e164)

    def _reply(self, candidate, language):
        _, state, lead, product = candidate
        route = self.catalog.route(product, language)
        return PublicReply(
            release_id=_identity("release:30-booking-form", lead),
            lead_id=lead,
            message_id=_identity(
                "message:booking-form", lead, state.subject.payment_id
            ),
            channel="manychat",
            chunks=(route.text,),
            author=PublicMessageAuthor.AUTHENTICATED_SYSTEM,
        )

    def run_once(self, *, now: datetime) -> int:
        inserted = 0
        for candidate in self._candidates():
            command, _, lead, _ = candidate
            release = _identity("release:30-booking-form", lead)
            existing = tuple(
                row
                for row in self.public.conversation_messages(lead)
                if row.release_id == release
            )
            if existing:
                from reservation_followup.handoff import HandoffReasonCode
                from v2_application.recovery import HandoffCoordinator

                reviewable = self.public.reviewable_outbox_ids(lead, now=now)
                for row in existing:
                    if row.outbox_id in reviewable:
                        HandoffCoordinator(store=self.followup).open_exception_once(
                            lead_id=lead,
                            workflow_id=release,
                            source_event_id=row.outbox_id,
                            reason_code=HandoffReasonCode.OPERATIONAL_REVIEW,
                            now=now,
                        )
                continue
            inserted += self.public.enqueue(
                self._reply(candidate, self._language(command, lead)),
                now=now,
                once_per_release=True,
            )
        return inserted

    def route_for_claim(self, claim: PublicClaim) -> BookingFormRoute:
        if (
            type(claim) is not PublicClaim
            or claim.author is not PublicMessageAuthor.AUTHENTICATED_SYSTEM
            or claim.chunk_index != 0
        ):
            raise PublicDeliveryRejected(
                "form requires an authenticated system outbox row"
            )
        persisted = tuple(
            row
            for row in self.public.conversation_messages(claim.lead_id)
            if row.outbox_id == claim.outbox_id
        )
        if len(persisted) != 1 or any(
            getattr(persisted[0], field) != getattr(claim, field)
            for field in (
                "release_id",
                "source_message_id",
                "chunk_index",
                "text",
                "author",
            )
        ):
            raise PublicDeliveryRejected(
                "form claim differs from its durable selection"
            )
        for candidate in self._candidates():
            if candidate[2] != claim.lead_id:
                continue
            for language in CustomerLanguage:
                reply = self._reply(candidate, language)
                if (reply.release_id, reply.message_id, reply.chunks[0]) == (
                    claim.release_id,
                    claim.source_message_id,
                    claim.text,
                ):
                    return self.catalog.route(candidate[3], language)
        raise PublicDeliveryRejected(
            "form lacks its exact paid reservation and configured route"
        )
