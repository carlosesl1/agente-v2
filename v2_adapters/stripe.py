"""Stripe payment-link adapter with unit-specific account routing."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping
from urllib.parse import urlparse

import httpx

from v2_contracts.payments import (
    BusinessUnit,
    PaymentObligation,
    StripeLinkRequest,
    StripePaymentLink,
)


class StripeTestHTTPTransport:
    """Closed Product → Price → Payment Link transport for Stripe test accounts."""

    def __init__(
        self,
        *,
        secret_keys: Mapping[str, str],
        base_url: str = "https://api.stripe.com",
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if type(secret_keys) is not dict or not secret_keys:
            raise ValueError("Stripe test secret keys must be a non-empty exact map")
        if any(
            type(profile) is not str
            or not profile
            or type(key) is not str
            or not key.startswith(("sk_test_", "rk_test_"))
            for profile, key in secret_keys.items()
        ):
            raise ValueError("Stripe transport accepts only mapped test keys")
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or parsed.path not in ("", "/")
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.hostname not in {"api.stripe.com", "api.stripe.invalid"}
        ):
            raise ValueError("Stripe base URL must be the canonical Stripe API")
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("Stripe timeout must be positive")
        self._keys = dict(secret_keys)
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._client = client or httpx.Client()

    def __repr__(self) -> str:
        return f"StripeTestHTTPTransport(accounts={len(self._keys)},mode=test)"

    def _post(
        self,
        *,
        profile: str,
        path: str,
        form: dict[str, str],
        idempotency_key: str,
    ) -> dict[str, object]:
        key = self._keys.get(profile)
        if key is None:
            raise ValueError("Stripe account profile is outside the closed map")
        try:
            response = self._client.post(
                self._base_url + path,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Idempotency-Key": idempotency_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=form,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("Stripe creation outcome is ambiguous") from exc
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Stripe creation outcome is ambiguous") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError("Stripe creation outcome is ambiguous")
        if not isinstance(payload, dict):
            raise RuntimeError("Stripe creation response fields mismatch")
        return payload

    @staticmethod
    def _provider_id(payload: dict[str, object], kind: str) -> str:
        value = payload.get("id")
        if type(value) is not str or not value:
            raise RuntimeError(f"Stripe {kind} response lacks an id")
        return value

    def __call__(self, request: StripeLinkRequest) -> dict[str, str]:
        if type(request) is not StripeLinkRequest:
            raise TypeError("Stripe transport requires exact StripeLinkRequest")
        if not request.subscriber_fingerprint:
            raise ValueError("Stripe request requires allowlisted subscriber fingerprint")
        product = self._post(
            profile=request.account_profile_id,
            path="/v1/products",
            form={
                "name": f"V2 {request.business_unit.value} reservation payment",
                "metadata[payment_id_sha256]": hashlib.sha256(
                    request.payment_id.encode()
                ).hexdigest(),
                "metadata[economic_version]": str(request.economic_version),
            },
            idempotency_key=request.idempotency_key + ":product",
        )
        product_id = self._provider_id(product, "product")
        price = self._post(
            profile=request.account_profile_id,
            path="/v1/prices",
            form={
                "product": product_id,
                "currency": request.currency.lower(),
                "unit_amount": str(request.amount_minor),
            },
            idempotency_key=request.idempotency_key + ":price",
        )
        price_id = self._provider_id(price, "price")
        link = self._post(
            profile=request.account_profile_id,
            path="/v1/payment_links",
            form={
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "metadata[reservation_anchor_sha256]": hashlib.sha256(
                    request.reservation_anchor_id.encode()
                ).hexdigest(),
                "metadata[subscriber_sha256]": request.subscriber_fingerprint,
                "metadata[business_unit]": request.business_unit.value,
                "metadata[economic_version]": str(request.economic_version),
                "metadata[payment_percentage]": str(request.payment_percentage),
            },
            idempotency_key=request.idempotency_key + ":payment_link",
        )
        link_id = self._provider_id(link, "payment link")
        url = link.get("url")
        parsed = urlparse(url) if type(url) is str else None
        if (
            parsed is None
            or parsed.scheme != "https"
            or parsed.hostname != "buy.stripe.com"
        ):
            raise RuntimeError("Stripe payment link URL is not canonical")
        return {"link_id": link_id, "url": url}


class StripeLinkAdapter:
    def __init__(
        self,
        *,
        transport,
        account_profiles: dict[BusinessUnit, str],
        enabled: bool,
        subscriber_id: str = "",
        payment_percentages: dict[BusinessUnit, int] | None = None,
    ) -> None:
        if not callable(transport):
            raise TypeError("transport must be callable")
        if type(account_profiles) is not dict or set(account_profiles) != set(BusinessUnit):
            raise ValueError("account_profiles must bind every business unit exactly once")
        if any(type(value) is not str or not value for value in account_profiles.values()):
            raise ValueError("account profile ids must be exact non-empty strings")
        if type(enabled) is not bool:
            raise TypeError("enabled must be an exact boolean")
        if type(subscriber_id) is not str or "\x00" in subscriber_id:
            raise ValueError("subscriber_id must be exact NUL-free text")
        percentages = payment_percentages or {
            BusinessUnit.HOSTEL: 100,
            BusinessUnit.AGENCY: 100,
        }
        if type(percentages) is not dict or set(percentages) != set(BusinessUnit):
            raise ValueError("payment_percentages must bind every business unit")
        if any(type(value) is not int or not 1 <= value <= 100 for value in percentages.values()):
            raise ValueError("payment percentages must be exact integers from 1 to 100")
        self._transport = transport
        self._profiles = dict(account_profiles)
        self._enabled = enabled
        self._subscriber_fingerprint = (
            hashlib.sha256(subscriber_id.encode()).hexdigest() if subscriber_id else ""
        )
        self._percentages = dict(percentages)

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
            subscriber_fingerprint=self._subscriber_fingerprint,
            payment_percentage=self._percentages[obligation.business_unit],
            business_unit=obligation.business_unit,
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


__all__ = ["StripeLinkAdapter", "StripeTestHTTPTransport"]
