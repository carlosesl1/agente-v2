from __future__ import annotations

from datetime import date
import hashlib

from v2_adapters.stripe import StripeLinkAdapter
from v2_contracts.localization import CustomerLanguage
from v2_contracts.payments import (
    BusinessUnit,
    CheckoutService,
    DueKind,
    PaymentDisplayDetails,
    PaymentObligation,
)


def _obligation() -> PaymentObligation:
    return PaymentObligation(
        payment_id="payment:ga:stripe:001",
        reservation_anchor_id="anchor:ga:001",
        business_unit=BusinessUnit.HOSTEL,
        amount_minor=15000,
        currency="BRL",
        due_kind=DueKind.PREPAYMENT,
        economic_version=1,
        receiver_profile_id="stripe-account:hostel:test",
        display_details=PaymentDisplayDetails(
            service=CheckoutService.LODGING,
            public_label="Quarto Compartilhado",
            start_date=date(2026, 12, 20),
            end_date=date(2026, 12, 22),
            start_time=None,
            adults=1,
            children=0,
            reservation_total_minor=15000,
            package_component=False,
            customer_language=CustomerLanguage.PT_BR,
        ),
    )


def test_general_availability_stripe_binding_is_per_subscriber() -> None:
    requests = []

    def transport(request):
        requests.append(request)
        return {
            "link_id": f"plink_test_{len(requests)}",
            "url": f"https://buy.stripe.com/test_ga_{len(requests)}",
        }

    adapter = StripeLinkAdapter(
        transport=transport,
        account_profiles={
            BusinessUnit.HOSTEL: "stripe-account:hostel:test",
            BusinessUnit.AGENCY: "stripe-account:agency:test",
        },
        enabled=True,
        subscriber_id="",
    )

    adapter.create_link(_obligation())
    adapter.create_link(_obligation(), subscriber_id="111111")
    adapter.create_link(_obligation(), subscriber_id="222222")

    assert [request.subscriber_fingerprint for request in requests] == [
        "",
        hashlib.sha256(b"111111").hexdigest(),
        hashlib.sha256(b"222222").hexdigest(),
    ]
