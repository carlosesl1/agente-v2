"""Non-PII payment display fixtures shared by V2 qualification harnesses."""

from __future__ import annotations

from datetime import date

from v2_contracts.localization import CustomerLanguage
from v2_contracts.payments import (
    BusinessUnit,
    CheckoutService,
    PaymentDisplayDetails,
)


def synthetic_payment_display_details(
    *,
    business_unit: BusinessUnit,
    amount_minor: int,
    customer_language: CustomerLanguage,
    package_component: bool = False,
) -> PaymentDisplayDetails:
    if business_unit is BusinessUnit.HOSTEL:
        return PaymentDisplayDetails(
            service=CheckoutService.LODGING,
            public_label="Hospedagem qualificada",
            start_date=date(2027, 2, 10),
            end_date=date(2027, 2, 12),
            start_time=None,
            adults=1,
            children=0,
            reservation_total_minor=amount_minor,
            package_component=package_component,
            customer_language=customer_language,
        )
    if business_unit is BusinessUnit.AGENCY:
        return PaymentDisplayDetails(
            service=CheckoutService.ACTIVITY,
            public_label="Passeio qualificado",
            start_date=date(2027, 2, 11),
            end_date=None,
            start_time="08:30",
            adults=1,
            children=0,
            reservation_total_minor=amount_minor,
            package_component=package_component,
            customer_language=customer_language,
        )
    raise TypeError("business_unit must be an exact BusinessUnit")


__all__ = ["synthetic_payment_display_details"]
