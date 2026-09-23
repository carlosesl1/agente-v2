"""Wire projection of authenticated operation facts; no generated prose."""

from v2_contracts.execution_context import ExecutionComponentContext
from v2_contracts.payments import StripePaymentLink


def component_wire(component: ExecutionComponentContext) -> dict:
    offer = component.offer
    outcome = component.outcome
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
                }
                for s in component.settlements
            ],
        },
    }
