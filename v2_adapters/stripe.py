"""Stripe payment-link adapter with unit-specific account routing."""

from __future__ import annotations

import hashlib
import json

from v2_contracts.payments import (
    BusinessUnit,
    PaymentObligation,
    StripeLinkRequest,
    StripePaymentLink,
)


class StripeLinkAdapter:
    def __init__(self, *, transport, account_profiles: dict[BusinessUnit, str], enabled: bool) -> None:
        if not callable(transport):
            raise TypeError("transport must be callable")
        if type(account_profiles) is not dict or set(account_profiles) != set(BusinessUnit):
            raise ValueError("account_profiles must bind every business unit exactly once")
        if any(type(value) is not str or not value for value in account_profiles.values()):
            raise ValueError("account profile ids must be exact non-empty strings")
        if type(enabled) is not bool:
            raise TypeError("enabled must be an exact boolean")
        self._transport = transport
        self._profiles = dict(account_profiles)
        self._enabled = enabled

    def create_link(self, obligation: PaymentObligation) -> StripePaymentLink:
        if type(obligation) is not PaymentObligation:
            raise TypeError("obligation must be exact PaymentObligation")
        if not self._enabled:
            raise RuntimeError("stripe_link_gate_closed")
        request = StripeLinkRequest(
            payment_id=obligation.payment_id,
            reservation_anchor_id=obligation.reservation_anchor_id,
            account_profile_id=self._profiles[obligation.business_unit],
            amount_minor=obligation.amount_minor,
            currency=obligation.currency,
            economic_version=obligation.economic_version,
            idempotency_key=(
                f"stripe-link:{obligation.payment_id}:v{obligation.economic_version}"
            ),
        )
        response = self._transport(request)
        if type(response) is not dict or set(response) != {"link_id", "url"}:
            raise ValueError("Stripe link response fields mismatch")
        link_id = response["link_id"]
        url = response["url"]
        if type(link_id) is not str or not link_id:
            raise ValueError("Stripe link response lacks link_id")
        if type(url) is not str:
            raise ValueError("Stripe link response lacks URL")
        receipt = json.dumps(
            {
                "account_profile_id": request.account_profile_id,
                "economic_version": request.economic_version,
                "idempotency_key": request.idempotency_key,
                "link_id": link_id,
                "payment_id": request.payment_id,
                "url": url,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return StripePaymentLink(
            payment_id=obligation.payment_id,
            reservation_anchor_id=obligation.reservation_anchor_id,
            account_profile_id=request.account_profile_id,
            economic_version=obligation.economic_version,
            public_url=url,
            provider_reference_fingerprint=hashlib.sha256(link_id.encode()).hexdigest(),
            receipt_hash=hashlib.sha256(b"v2-stripe-link-receipt-v1\0" + receipt).hexdigest(),
        )


__all__ = ["StripeLinkAdapter"]
