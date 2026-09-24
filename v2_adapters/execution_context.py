"""Wire projection of authenticated operation facts; no generated prose."""

from decimal import Decimal, InvalidOperation

from v2_contracts.execution_context import (
    ExecutionComponentContext,
    ProviderReservationStatus,
)
from v2_contracts.payments import StripePaymentLink


class ReservationStatusReader:
    """GET-only lifecycle observation of a reference owned by a durable command.

    Never replays a write, mutates a ledger, or replaces creation certainty with
    today's reservation/payment status. Legacy Bókun hashes are not booking IDs.
    """

    def __init__(self, *, cloudbeds, bokun, clock) -> None:
        self._cloudbeds = cloudbeds
        self._bokun = bokun
        self._clock = clock

    def read(
        self, *, service: str, provider_reference: str | None
    ) -> ProviderReservationStatus:
        try:
            reference = provider_reference or ""
            if service == "lodging":
                prefix, port, method = (
                    "provider:cloudbeds:",
                    self._cloudbeds,
                    "get_reservation",
                )
            elif service == "activity":
                prefix, port, method = "provider:bokun:id:", self._bokun, "get_booking"
            else:
                raise ValueError("unknown reservation service")
            if not reference.startswith(prefix) or port is None:
                raise ValueError("native reference unavailable")
            native_id = reference.removeprefix(prefix)
            # Native references returned by these endpoints are decimal IDs.
            if not native_id.isascii() or not native_id.isdecimal():
                raise ValueError("invalid native reference")
            payload = getattr(port, method)(native_id)
            if type(payload) is not dict or payload.get("success") is False:
                raise ValueError("unsuccessful provider read")
            row = payload.get("data", payload)
            if type(row) is not dict:
                raise ValueError("missing native reservation")
            identity_fields = (
                ("reservationID", "reservationId")
                if service == "lodging"
                else ("bookingId", "id")
            )
            if _consistent_text(row, identity_fields) != native_id:
                raise ValueError("native identity mismatch")
            status = _consistent_text(row, ("status", "bookingStatus"))
            if status is None:
                raise ValueError("missing reservation status")
            currency = _consistent_text(row, ("currency",))
            if service == "activity":
                payment = _consistent_text(row, ("paymentType",))
                paid = _money(row.get("totalPaid"))
                due = _money(row.get("totalDue"))
            else:
                detail = row.get("balanceDetailed", {})
                if type(detail) is not dict:
                    raise ValueError("invalid financial facts")
                paid = _money(detail.get("paid"))
                due = _money(row.get("balance"))
                payment = None
                if paid is not None and due is not None:
                    p, d = Decimal(paid), Decimal(due)
                    if p == 0 and d > 0:
                        payment = "NOT_PAID"
                    elif p > 0 and d > 0:
                        payment = "PARTIALLY_PAID"
                    elif p > 0 and d <= 0:
                        payment = "PAID"
            return ProviderReservationStatus(
                "observed", self._clock.now(), status, payment, paid, due, currency
            )
        except (RuntimeError, ValueError, TypeError, InvalidOperation):
            return ProviderReservationStatus("unavailable", self._clock.now())


def _consistent_text(row: dict, names: tuple[str, ...]) -> str | None:
    values = []
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        if type(value) not in (str, int) or not str(value).strip():
            raise ValueError("invalid native field")
        values.append(str(value).strip())
    if len(set(values)) > 1:
        raise ValueError("conflicting native aliases")
    return values[0] if values else None


def _money(value) -> str | None:
    if value is None:
        return None
    if type(value) not in (str, int, float):
        raise ValueError("invalid native amount")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
        raise ValueError("invalid amount precision")
    return f"{amount:.2f}"


def component_wire(component: ExecutionComponentContext) -> dict:
    offer = component.offer
    outcome = component.outcome
    reservation = component.reservation_status
    return {
        "command_id": component.command_id,
        "draft_id": component.draft_id,
        "draft_version": component.draft_version,
        "offer_id": offer.offer_id,
        "service": offer.service,
        "provider_ref": offer.provider_ref,
        "label": offer.public_label,
        "start_date": offer.start_date.isoformat(),
        "end_date": offer.end_date.isoformat() if offer.end_date else None,
        "start_time": offer.start_time,
        "adults": offer.adults,
        "children": offer.children,
        "total": offer.amount,
        "currency": offer.currency,
        "ledger_status": component.ledger_status,
        "certainty": outcome.certainty if outcome else None,
        "normalized_status": outcome.normalized_status if outcome else None,
        "execution_status_scope": "creation_result",
        "reservation": {
            "source_status": reservation.source_status
            if reservation
            else "unavailable",
            "observed_at": reservation.observed_at.isoformat() if reservation else None,
            "status": reservation.reservation_status if reservation else None,
            "payment_status": reservation.payment_status if reservation else None,
            "paid_amount": reservation.paid_amount if reservation else None,
            "balance_due": reservation.balance_due if reservation else None,
            "currency": reservation.currency if reservation else None,
        },
        "provider_reference": outcome.provider_reference if outcome else None,
        "payment": {
            "payment_id": component.payment_id,
            "initiation_status": component.payment_initiation_status,
            "initiations": [
                {
                    "initiation_id": payment.initiation_id,
                    "status": payment.status,
                    "dispatch_started": payment.dispatch_started,
                    "method": payment.selection.method.value,
                    "business_unit": payment.selection.obligation.business_unit.value,
                    "due_kind": payment.selection.obligation.due_kind.value,
                    "amount_minor": payment.selection.obligation.amount_minor,
                    "currency": payment.selection.obligation.currency,
                    "economic_version": payment.selection.obligation.economic_version,
                    # URLs belong to the authenticated channel action, not prose.
                    "delivery": "manychat_button"
                    if type(payment.offer) is StripePaymentLink
                    else None,
                    "instructions": payment.offer.public_text
                    if payment.offer is not None
                    and type(payment.offer) is not StripePaymentLink
                    else None,
                }
                for payment in component.payments
            ],
            "settlement_status": component.settlement_status,
            "settlements": [
                {
                    "payment_id": s.payment_id,
                    "economic_version": s.economic_version,
                    "amount_minor": s.amount_minor,
                    "currency": s.currency,
                    "method": s.method,
                    "status": s.status,
                    "certainty": s.certainty,
                    "verified_payment": {
                        "source": "stripe",
                        "status": "captured",
                        "amount_minor": s.amount_minor,
                        "currency": s.currency,
                        "observed_at": s.stripe_capture_observed_at.isoformat(),
                    } if s.stripe_capture_observed_at is not None else None,
                }
                for s in component.settlements
            ],
        },
    }
