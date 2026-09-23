"""Observe payment effects already performed by canonical owners. Never sends text."""

import json
from datetime import UTC, datetime

from reservation_boundary.completion import source_coverage
from reservation_boundary.public_dispatch import PublicAcceptanceReceipt
from reservation_followup.projection import PaymentEffectKind
from reservation_followup.serialization import semantic_hash
from reservation_followup.types import PaymentReceipt, PaymentStatus
from v2_contracts.channel import PublicChannelAcceptance


class StripePaymentEffectObserver:
    delivery_id = "v2:stripe-payment-effect-observer"
    delivery_version = 1

    def __init__(self, *, followup, boundary, lead_resolver, coordinator, clock=None):
        self.followup = followup
        self.boundary = boundary
        self.lead_resolver = lead_resolver
        self.coordinator = coordinator
        self.clock = clock or (lambda: datetime.now(UTC))

    def deliver(self, claim):
        state = self.followup.load_payment(claim.payment_id)
        finish = state.settlement_finish
        if (
            finish is None
            or finish.settlement_command_id != claim.settlement_command_id
            or finish.outcome != claim.message.outcome
        ):
            raise ValueError("payment effect has no exact durable result")
        command_id = state.subject.confirmed_reservation_anchor.reservation_command_id
        lead = self.lead_resolver.lead_id_for_command(command_id)
        if lead is None:
            raise ValueError("payment effect has no durable owner")
        token = semantic_hash(finish)
        if claim.message.kind is PaymentEffectKind.PAID_STATE_TRANSITION:
            if state.status is not PaymentStatus.PAID:
                raise ValueError("financial owner has not marked payment paid")
            reference = "paid-state:" + token[:40]
        elif claim.message.kind is PaymentEffectKind.MANUAL_REVIEW:
            from v2_application.recovery import _lead_hash

            active = self.followup.find_active_handoff_by_lead_hash(_lead_hash(lead))
            if active is None:
                raise ValueError("human handoff has not been persisted")
            reference = active.request.handoff_id
        elif claim.message.kind is PaymentEffectKind.CUSTOMER_PAYMENT_CONFIRMATION:
            coverage = source_coverage(
                self.boundary, lead, "completion:settlement:" + token[:40], token
            )
            if coverage is None:
                raise ValueError("Maya has not consumed settlement result")
            rows = self.boundary._connection.execute(
                "SELECT public_row_id,idempotency_key,status,delivery_receipt_json,delivery_receipt_hash "
                "FROM boundary_public_outbox WHERE lead_key=? AND aggregate_turn_id=? AND source_turn_receipt_hash=? ORDER BY chunk_index",
                (lead, *coverage),
            ).fetchall()
            if not rows or any(row[2] != "delivered" for row in rows):
                raise ValueError("settlement communication is not accepted by ManyChat")
            for row in rows:
                raw = json.loads(row[3])["data"]
                acceptance = PublicChannelAcceptance.from_canonical_bytes(
                    json.dumps(
                        raw["acceptance"], sort_keys=True, separators=(",", ":")
                    ).encode()
                )
                receipt = PublicAcceptanceReceipt(
                    raw["public_row_id"],
                    raw["idempotency_key"],
                    acceptance,
                    datetime.fromisoformat(raw["accepted_at"]),
                )
                if (
                    receipt.canonical_hash() != row[4]
                    or receipt.to_canonical_bytes().decode() != row[3]
                    or (
                        receipt.public_row_id,
                        receipt.idempotency_key,
                    )
                    != tuple(row[:2])
                ):
                    raise ValueError("settlement communication receipt diverged")
            reference = "manychat-accepted:" + token[:40]
        else:
            raise ValueError("payment effect has no configured owner")
        return PaymentReceipt.for_claim(
            claim,
            receipt_id="receipt:" + semantic_hash(claim)[:40],
            delivery_reference=reference,
            delivered_at=self.clock(),
        )
