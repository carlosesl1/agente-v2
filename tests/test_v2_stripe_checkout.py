from __future__ import annotations

from dataclasses import replace
from datetime import date
import re

import pytest

from v2_adapters.stripe_checkout import stripe_product_presentation
from v2_contracts.payments import (
    BusinessUnit,
    CheckoutService,
    PaymentDisplayDetails,
    StripeLinkRequest,
)


def _activity_request() -> StripeLinkRequest:
    return StripeLinkRequest(
        payment_id="payment:agency:checkout:001",
        reservation_anchor_id="anchor:bokun:checkout:001",
        account_profile_id="stripe-account:agency:test",
        amount_minor=6699,
        currency="BRL",
        economic_version=1,
        idempotency_key="stripe-link:payment:agency:checkout:001:v1",
        subscriber_fingerprint="a" * 64,
        payment_percentage=20,
        business_unit=BusinessUnit.AGENCY,
        display_details=PaymentDisplayDetails(
            service=CheckoutService.ACTIVITY,
            public_label="Roteiro dos 4Ps",
            start_date=date(2026, 12, 3),
            end_date=None,
            start_time="08:30",
            adults=1,
            children=0,
            reservation_total_minor=33495,
            package_component=True,
        ),
    )


def _lodging_request() -> StripeLinkRequest:
    return StripeLinkRequest(
        payment_id="payment:hostel:checkout:001",
        reservation_anchor_id="anchor:cloudbeds:checkout:001",
        account_profile_id="stripe-account:hostel:test",
        amount_minor=30000,
        currency="BRL",
        economic_version=1,
        idempotency_key="stripe-link:payment:hostel:checkout:001:v1",
        subscriber_fingerprint="a" * 64,
        payment_percentage=100,
        business_unit=BusinessUnit.HOSTEL,
        display_details=PaymentDisplayDetails(
            service=CheckoutService.LODGING,
            public_label="Suíte Casal",
            start_date=date(2026, 12, 2),
            end_date=date(2026, 12, 4),
            start_time=None,
            adults=1,
            children=0,
            reservation_total_minor=30000,
            package_component=False,
        ),
    )


def test_activity_presentation_details_package_deposit() -> None:
    presentation = stripe_product_presentation(_activity_request())

    assert presentation.name == (
        "Pacote / Package — Roteiro dos 4Ps — Sinal / Deposit 20%"
    )
    assert presentation.description == (
        "Passeio / Tour • 03/12/2026 às / at 08:30 • 1 adulto / adult • "
        "Total R$ 334,95 • Pagar agora / Pay now R$ 66,99 (20%)"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", presentation.details_sha256)
    assert presentation == stripe_product_presentation(_activity_request())
    assert "Bókun" not in presentation.description
    assert "provider:" not in presentation.description


def test_lodging_presentation_details_dates_guest_and_full_payment() -> None:
    presentation = stripe_product_presentation(_lodging_request())

    assert presentation.name == "Suíte Casal — Pagamento integral / Full payment"
    assert presentation.description == (
        "Hospedagem / Accommodation • Check-in 02/12/2026 • "
        "Check-out 04/12/2026 • 1 hóspede / guest • "
        "Total R$ 300,00 • Pagar agora / Pay now R$ 300,00 (100%)"
    )
    assert "Cloudbeds" not in presentation.description
    assert "provider:" not in presentation.description


def test_party_pluralization_and_missing_activity_time() -> None:
    details = replace(
        _activity_request().display_details,
        start_time=None,
        adults=2,
        children=1,
        package_component=False,
    )
    presentation = stripe_product_presentation(
        replace(_activity_request(), display_details=details)
    )

    assert (
        "03/12/2026 • 2 adultos / adults + 1 criança / child •"
        in presentation.description
    )
    assert " às / at " not in presentation.description


def test_product_name_truncates_label_but_preserves_payment_suffix() -> None:
    details = replace(
        _activity_request().display_details,
        public_label="Passeio muito detalhado " * 8,
    )
    presentation = stripe_product_presentation(
        replace(_activity_request(), display_details=details)
    )

    assert len(presentation.name) == 120
    assert "… — Sinal / Deposit 20%" in presentation.name
    assert presentation.name.startswith("Pacote / Package — Passeio muito detalhado")


def test_presentation_fails_closed_when_required_copy_exceeds_limit() -> None:
    huge_minor_units = int("9" * 400)
    details = replace(
        _activity_request().display_details,
        reservation_total_minor=huge_minor_units,
    )
    request = replace(
        _activity_request(),
        amount_minor=huge_minor_units,
        payment_percentage=100,
        display_details=details,
    )

    with pytest.raises(ValueError, match="description exceeds 500"):
        stripe_product_presentation(request)


def test_presentation_rejects_legacy_or_cross_unit_request() -> None:
    with pytest.raises(ValueError, match="display_details"):
        stripe_product_presentation(replace(_activity_request(), display_details=None))

    with pytest.raises(ValueError, match="service.*business unit"):
        stripe_product_presentation(
            replace(_activity_request(), business_unit=BusinessUnit.HOSTEL)
        )
