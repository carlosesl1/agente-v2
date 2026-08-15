"""Stripe payment-link adapter with unit-specific account routing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable, Mapping
from urllib.parse import quote, urlparse

import httpx

from v2_adapters.stripe_checkout import stripe_product_presentation
from v2_contracts.payments import (
    BusinessUnit,
    PaymentMethod,
    PaymentObligation,
    PaymentSelection,
    StripeCreationStep,
    StripeLinkRequest,
    StripePaymentLink,
    StripeReconciliationResult,
    StripeStepReceipt,
    StripeStepStatus,
)


def _canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"v2-stripe-step-binding-v1\0" + raw).hexdigest()


def _is_canonical_stripe_link_url(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        port = parsed.port
    except (UnicodeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and hostname == "buy.stripe.com"
        and username is None
        and password is None
        and port in (None, 443)
        and parsed.path.startswith("/")
        and parsed.path != "/"
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _is_canonical_stripe_api_origin(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        port = parsed.port
    except (UnicodeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and hostname in {"api.stripe.com", "api.stripe.invalid"}
        and username is None
        and password is None
        and port in (None, 443)
        and parsed.path in ("", "/")
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _is_canonical_wise_api_origin(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = urlparse(value)
    except (UnicodeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.wise.com"
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and parsed.query == ""
        and parsed.fragment == ""
    )


@dataclass(frozen=True, slots=True)
class WiseBRLRates:
    usd_brl: Decimal
    eur_brl: Decimal

    def __post_init__(self) -> None:
        for value in (self.usd_brl, self.eur_brl):
            if type(value) is not Decimal or not value.is_finite() or value <= 0:
                raise ValueError("Wise BRL rates must be positive finite Decimals")


class WiseExchangeRateReader:
    """Read current BRL-per-foreign-unit rates using the V1 adjustment."""

    _MARGIN_BRL = Decimal("0.20")
    _RATE_QUANTUM = Decimal("0.0001")

    def __init__(
        self,
        *,
        api_token: str,
        base_url: str = "https://api.wise.com",
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if type(api_token) is not str or not api_token or "\x00" in api_token:
            raise ValueError("Wise API token is required")
        if not _is_canonical_wise_api_origin(base_url):
            raise ValueError("Wise base URL must be the canonical API origin")
        if type(timeout_seconds) not in {int, float} or timeout_seconds <= 0:
            raise ValueError("Wise timeout must be positive")
        self._token = api_token
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._client = client or httpx.Client()

    def __call__(self) -> WiseBRLRates:
        return WiseBRLRates(
            usd_brl=self._read_adjusted_rate("USD"),
            eur_brl=self._read_adjusted_rate("EUR"),
        )

    def _read_adjusted_rate(self, target: str) -> Decimal:
        try:
            response = self._client.get(
                f"{self._base_url}/v1/rates",
                params={"source": "BRL", "target": target},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if (
                type(payload) is not list
                or len(payload) != 1
                or type(payload[0]) is not dict
            ):
                raise ValueError("unexpected Wise rate payload")
            item = payload[0]
            if item.get("source") != "BRL" or item.get("target") != target:
                raise ValueError("unexpected Wise currency route")
            raw_rate = Decimal(str(item["rate"]))
            if not raw_rate.is_finite() or raw_rate <= 0:
                raise ValueError("invalid Wise rate")
            adjusted = ((Decimal("1") / raw_rate) - self._MARGIN_BRL).quantize(
                self._RATE_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            if adjusted <= 0:
                raise ValueError("invalid adjusted Wise rate")
            return adjusted
        except (
            httpx.HTTPError,
            KeyError,
            TypeError,
            ValueError,
            InvalidOperation,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeError("Wise exchange-rate read failed") from exc


_STRIPE_OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$")


def _is_stripe_object_id(value: object, prefix: str) -> bool:
    return bool(
        type(value) is str
        and value.startswith(prefix + "_")
        and _STRIPE_OBJECT_ID_RE.fullmatch(value)
    )


def _journal_binding(request: StripeLinkRequest, expected: object) -> object:
    return {
        "account_profile_id_sha256": hashlib.sha256(
            request.account_profile_id.encode()
        ).hexdigest(),
        "provider_expected": expected,
    }


def _stripe_link_request(
    obligation: PaymentObligation,
    *,
    account_profiles: Mapping[BusinessUnit, str],
    subscriber_fingerprint: str,
    payment_percentages: Mapping[BusinessUnit, int],
    initiation_id: str = "",
    journal_worker_id: str = "",
    journal_fencing_token: int = 0,
) -> StripeLinkRequest:
    payment_percentage = payment_percentages[obligation.business_unit]
    amount_minor = int(
        (
            Decimal(obligation.amount_minor)
            * Decimal(payment_percentage)
            / Decimal(100)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if amount_minor < 1:
        raise ValueError("payment percentage produced no payable minor units")
    return StripeLinkRequest(
        payment_id=obligation.payment_id,
        reservation_anchor_id=obligation.reservation_anchor_id,
        account_profile_id=account_profiles[obligation.business_unit],
        amount_minor=amount_minor,
        currency=obligation.currency,
        economic_version=obligation.economic_version,
        idempotency_key=(
            f"stripe-link:{obligation.payment_id}:v{obligation.economic_version}"
        ),
        subscriber_fingerprint=subscriber_fingerprint,
        payment_percentage=payment_percentage,
        business_unit=obligation.business_unit,
        display_details=obligation.display_details,
        initiation_id=initiation_id,
        journal_worker_id=journal_worker_id,
        journal_fencing_token=journal_fencing_token,
    )


def _stripe_payment_link(
    request: StripeLinkRequest,
    *,
    link_id: str,
    url: str,
) -> StripePaymentLink:
    details = request.display_details
    if details is None or details.customer_language is None:
        raise ValueError("Stripe link creation requires customer_language")
    receipt = json.dumps(
        {
            "account_profile_id": request.account_profile_id,
            "economic_version": request.economic_version,
            "idempotency_key": request.idempotency_key,
            "link_id": link_id,
            "customer_language": details.customer_language.value,
            "payment_id": request.payment_id,
            "url": url,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return StripePaymentLink(
        payment_id=request.payment_id,
        reservation_anchor_id=request.reservation_anchor_id,
        account_profile_id=request.account_profile_id,
        economic_version=request.economic_version,
        public_url=url,
        provider_reference_fingerprint=hashlib.sha256(link_id.encode()).hexdigest(),
        receipt_hash=hashlib.sha256(
            b"v2-stripe-link-receipt-v1\0" + receipt
        ).hexdigest(),
        customer_language=details.customer_language,
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
        wise_rates: Callable[[], WiseBRLRates] | None = None,
        journal: object | None = None,
        clock: Callable[[], datetime] | None = None,
        effect_guard: object | None = None,
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
        if not _is_canonical_stripe_api_origin(base_url):
            raise ValueError("Stripe base URL must be the canonical Stripe API")
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("Stripe timeout must be positive")
        self._keys = dict(secret_keys)
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._client = client or httpx.Client()
        if wise_rates is not None and not callable(wise_rates):
            raise TypeError("wise_rates must be callable")
        self._wise_rates = wise_rates
        if journal is not None and (
            not callable(getattr(journal, "record_stripe_step_intent", None))
            or not callable(getattr(journal, "record_stripe_step_accepted", None))
        ):
            raise TypeError("Stripe journal must expose the closed step receipt API")
        self._journal = journal
        if clock is not None and not callable(clock):
            raise TypeError("Stripe transport clock must be callable")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if effect_guard is not None and not callable(
            getattr(effect_guard, "allows_workflow", None)
        ):
            raise TypeError("Stripe effect guard must expose allows_workflow")
        self._effect_guard = effect_guard

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
        if self._effect_guard is not None and not self._effect_guard.allows_workflow(
            "stripe-payment-initiation"
        ):
            raise RuntimeError("Stripe write authority is closed")
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
        except httpx.HTTPError:
            raise RuntimeError("Stripe creation outcome is ambiguous") from None
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise RuntimeError("Stripe creation outcome is ambiguous") from None
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError("Stripe creation outcome is ambiguous")
        if not isinstance(payload, dict):
            raise RuntimeError("Stripe creation response fields mismatch")
        return payload

    def _record_step(
        self,
        request: StripeLinkRequest,
        *,
        step: StripeCreationStep,
        status: StripeStepStatus,
        expected: object,
        idempotency_key: str,
        provider_object_id: str | None = None,
        canonical_url: str | None = None,
    ) -> None:
        if self._journal is None:
            return
        if not request.initiation_id:
            raise ValueError("journaled Stripe request requires initiation_id")
        receipt = StripeStepReceipt(
            step=step,
            status=status,
            account_profile_id=request.account_profile_id,
            expected_metadata_hash=_canonical_hash(
                _journal_binding(request, expected)
            ),
            idempotency_key=idempotency_key,
            provider_object_id=provider_object_id,
            canonical_url=canonical_url,
        )
        method_name = (
            "record_stripe_step_intent"
            if status is StripeStepStatus.INTENT
            else "record_stripe_step_accepted"
        )
        getattr(self._journal, method_name)(
            request.initiation_id,
            receipt,
            worker_id=request.journal_worker_id,
            fencing_token=request.journal_fencing_token,
            now=self._clock(),
        )

    @staticmethod
    def _provider_id(payload: dict[str, object], kind: str) -> str:
        value = payload.get("id")
        prefix = {
            "product": "prod",
            "price": "price",
            "payment link": "plink",
        }[kind]
        if not _is_stripe_object_id(value, prefix):
            raise RuntimeError(f"Stripe {kind} response lacks a canonical id")
        assert type(value) is str
        return value

    @staticmethod
    def _require_test_mode(payload: dict[str, object], kind: str) -> None:
        if payload.get("livemode") is not False:
            raise RuntimeError(f"Stripe {kind} was not confirmed in test mode")

    @staticmethod
    def _canonical_link_url(payload: dict[str, object]) -> str:
        url = payload.get("url")
        if not _is_canonical_stripe_link_url(url):
            raise RuntimeError("Stripe payment link URL is not canonical")
        assert type(url) is str
        return url

    def __call__(self, request: StripeLinkRequest) -> dict[str, str]:
        if type(request) is not StripeLinkRequest:
            raise TypeError("Stripe transport requires exact StripeLinkRequest")
        if not request.subscriber_fingerprint:
            raise ValueError("Stripe request requires allowlisted subscriber fingerprint")
        if request.currency != "BRL":
            raise ValueError("Stripe multi-currency Price requires a BRL obligation")
        if self._wise_rates is None:
            raise RuntimeError("Wise exchange-rate reader is required")
        rates = self._wise_rates()
        if type(rates) is not WiseBRLRates:
            raise TypeError("Wise exchange-rate reader returned an invalid value")
        usd_minor = int(
            (Decimal(request.amount_minor) / rates.usd_brl).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        eur_minor = int(
            (Decimal(request.amount_minor) / rates.eur_brl).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        presentation = stripe_product_presentation(request)
        expected_product_metadata = {
            "payment_id_sha256": hashlib.sha256(
                request.payment_id.encode()
            ).hexdigest(),
            "economic_version": str(request.economic_version),
            "display_details_sha256": presentation.details_sha256,
        }
        product_expected = {
            "name": presentation.name,
            "description": presentation.description,
            "metadata": expected_product_metadata,
        }
        product_key = request.idempotency_key + ":product"
        self._record_step(
            request,
            step=StripeCreationStep.PRODUCT,
            status=StripeStepStatus.INTENT,
            expected=product_expected,
            idempotency_key=product_key,
        )
        product = self._post(
            profile=request.account_profile_id,
            path="/v1/products",
            form={
                "name": presentation.name,
                "description": presentation.description,
                **{
                    f"metadata[{name}]": value
                    for name, value in expected_product_metadata.items()
                },
            },
            idempotency_key=product_key,
        )
        self._require_test_mode(product, "product")
        product_metadata = product.get("metadata")
        if (
            product.get("name") != presentation.name
            or product.get("description") != presentation.description
            or not isinstance(product_metadata, dict)
            or any(
                product_metadata.get(name) != value
                for name, value in expected_product_metadata.items()
            )
        ):
            raise RuntimeError("Stripe product display did not match")
        product_id = self._provider_id(product, "product")
        self._record_step(
            request,
            step=StripeCreationStep.PRODUCT,
            status=StripeStepStatus.ACCEPTED,
            expected=product_expected,
            idempotency_key=product_key,
            provider_object_id=product_id,
        )
        price_expected = {
            "product": product_id,
            "currency": request.currency.lower(),
            "unit_amount": str(request.amount_minor),
        }
        price_form = {
            **price_expected,
            "currency_options[usd][unit_amount]": str(usd_minor),
            "currency_options[eur][unit_amount]": str(eur_minor),
        }
        price_key = request.idempotency_key + ":price"
        self._record_step(
            request,
            step=StripeCreationStep.PRICE,
            status=StripeStepStatus.INTENT,
            expected=price_expected,
            idempotency_key=price_key,
        )
        price = self._post(
            profile=request.account_profile_id,
            path="/v1/prices",
            form=price_form,
            idempotency_key=price_key,
        )
        self._require_test_mode(price, "price")
        price_id = self._provider_id(price, "price")
        self._record_step(
            request,
            step=StripeCreationStep.PRICE,
            status=StripeStepStatus.ACCEPTED,
            expected=price_expected,
            idempotency_key=price_key,
            provider_object_id=price_id,
        )
        expected_metadata = {
            "reservation_anchor_sha256": hashlib.sha256(
                request.reservation_anchor_id.encode()
            ).hexdigest(),
            "subscriber_sha256": request.subscriber_fingerprint,
            "business_unit": request.business_unit.value,
            "economic_version": str(request.economic_version),
            "payment_percentage": str(request.payment_percentage),
            "display_details_sha256": presentation.details_sha256,
        }
        link_expected = {
            "price_id": price_id,
            "metadata": expected_metadata,
        }
        link_key = request.idempotency_key + ":payment_link"
        self._record_step(
            request,
            step=StripeCreationStep.PAYMENT_LINK,
            status=StripeStepStatus.INTENT,
            expected=link_expected,
            idempotency_key=link_key,
        )
        link = self._post(
            profile=request.account_profile_id,
            path="/v1/payment_links",
            form={
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                **{
                    f"metadata[{name}]": value
                    for name, value in expected_metadata.items()
                },
            },
            idempotency_key=link_key,
        )
        self._require_test_mode(link, "payment link")
        link_id = self._provider_id(link, "payment link")
        url = self._canonical_link_url(link)
        if link.get("active") is not True:
            raise RuntimeError("Stripe payment link is not active")
        self._record_step(
            request,
            step=StripeCreationStep.PAYMENT_LINK,
            status=StripeStepStatus.ACCEPTED,
            expected=link_expected,
            idempotency_key=link_key,
            provider_object_id=link_id,
            canonical_url=url,
        )
        return {"link_id": link_id, "url": url}


class StripeTestReconciliationTransport:
    """Private Stripe test-mode reader with no create surface."""

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
            raise ValueError("Stripe reconciliation accepts only mapped test keys")
        if not _is_canonical_stripe_api_origin(base_url):
            raise ValueError("Stripe base URL must be the canonical Stripe API")
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("Stripe timeout must be positive")
        self._keys = dict(secret_keys)
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._client = client or httpx.Client()

    def __repr__(self) -> str:
        return f"StripeTestReconciliationTransport(accounts={len(self._keys)},mode=test,method=GET)"

    def get(
        self,
        *,
        profile: str,
        path: str,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        key = self._keys.get(profile)
        if key is None:
            raise ValueError("Stripe account profile is outside the closed map")
        if type(path) is not str or not path.startswith("/v1/") or "\x00" in path:
            raise ValueError("Stripe reconciliation path is outside the closed API")
        if params is not None and (
            type(params) is not dict
            or any(type(k) is not str or type(v) is not str for k, v in params.items())
        ):
            raise TypeError("Stripe reconciliation params must be exact text map")
        try:
            response = self._client.get(
                self._base_url + path,
                headers={"Authorization": f"Bearer {key}"},
                params=params,
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            raise RuntimeError("Stripe reconciliation read is ambiguous") from None
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise RuntimeError("Stripe reconciliation read is ambiguous") from None
        if not 200 <= response.status_code < 300 or not isinstance(payload, dict):
            raise RuntimeError("Stripe reconciliation read is ambiguous")
        return payload


class StripeLinkReconciliationAdapter:
    """Reconcile a journaled Stripe chain through GET/list/search only."""

    def __init__(
        self,
        *,
        transport: StripeTestReconciliationTransport,
        account_profiles: dict[BusinessUnit, str],
        subscriber_id: str = "",
        payment_percentages: dict[BusinessUnit, int],
    ) -> None:
        if type(transport) is not StripeTestReconciliationTransport:
            raise TypeError("reconciliation transport must be exact GET-only transport")
        if type(account_profiles) is not dict or set(account_profiles) != set(BusinessUnit):
            raise ValueError("account_profiles must bind every business unit exactly once")
        if any(type(value) is not str or not value for value in account_profiles.values()):
            raise ValueError("account profile ids must be exact non-empty strings")
        if type(subscriber_id) is not str or "\x00" in subscriber_id:
            raise ValueError("subscriber_id must be exact NUL-free text")
        if type(payment_percentages) is not dict or set(payment_percentages) != set(
            BusinessUnit
        ):
            raise ValueError("payment_percentages must bind every business unit")
        self._transport = transport
        self._profiles = dict(account_profiles)
        self._subscriber_fingerprint = hashlib.sha256(subscriber_id.encode()).hexdigest()
        self._percentages = dict(payment_percentages)

    @staticmethod
    def _single_list(payload: dict[str, object]) -> tuple[dict[str, object], ...]:
        data = payload.get("data")
        if (
            payload.get("object") != "list"
            or type(data) is not list
            or payload.get("has_more") is not False
            or any(type(item) is not dict for item in data)
        ):
            raise RuntimeError("Stripe reconciliation list envelope is invalid")
        return tuple(data)

    @staticmethod
    def _product_matches(
        payload: dict[str, object],
        *,
        product_id: str | None,
        presentation,
        metadata: dict[str, str],
    ) -> bool:
        return (
            payload.get("livemode") is False
            and (product_id is None or payload.get("id") == product_id)
            and _is_stripe_object_id(payload.get("id"), "prod")
            and payload.get("name") == presentation.name
            and payload.get("description") == presentation.description
            and type(payload.get("metadata")) is dict
            and all(payload["metadata"].get(k) == v for k, v in metadata.items())
        )

    @staticmethod
    def _price_matches(
        payload: dict[str, object],
        *,
        price_id: str | None,
        product_id: str,
        request: StripeLinkRequest,
    ) -> bool:
        return (
            payload.get("livemode") is False
            and payload.get("active") is True
            and (price_id is None or payload.get("id") == price_id)
            and _is_stripe_object_id(payload.get("id"), "price")
            and payload.get("product") == product_id
            and payload.get("currency") == request.currency.lower()
            and payload.get("unit_amount") == request.amount_minor
        )

    @staticmethod
    def _link_values(
        payload: dict[str, object],
        *,
        link_id: str | None,
        price_id: str,
        metadata: dict[str, str],
    ) -> tuple[str, str] | None:
        candidate_id = payload.get("id")
        url = payload.get("url")
        line_items = payload.get("line_items")
        lines = line_items.get("data") if type(line_items) is dict else None
        if (
            payload.get("livemode") is not False
            or payload.get("active") is not True
            or not _is_stripe_object_id(candidate_id, "plink")
            or (link_id is not None and candidate_id != link_id)
            or not _is_canonical_stripe_link_url(url)
            or type(payload.get("metadata")) is not dict
            or any(payload["metadata"].get(k) != v for k, v in metadata.items())
            or type(lines) is not list
            or len(lines) != 1
            or type(lines[0]) is not dict
            or (
                lines[0].get("price") != price_id
                and not (
                    type(lines[0].get("price")) is dict
                    and lines[0]["price"].get("id") == price_id
                )
            )
        ):
            return None
        return candidate_id, url

    @staticmethod
    def _link_metadata(
        request: StripeLinkRequest,
        details_sha256: str,
    ) -> dict[str, str]:
        return {
            "reservation_anchor_sha256": hashlib.sha256(
                request.reservation_anchor_id.encode()
            ).hexdigest(),
            "subscriber_sha256": request.subscriber_fingerprint,
            "business_unit": request.business_unit.value,
            "economic_version": str(request.economic_version),
            "payment_percentage": str(request.payment_percentage),
            "display_details_sha256": details_sha256,
        }

    def reconcile(
        self,
        selection: PaymentSelection,
        receipts: tuple[StripeStepReceipt, ...],
        *,
        initiation_id: str,
        subscriber_id: str = "",
    ) -> StripeReconciliationResult:
        if (
            type(selection) is not PaymentSelection
            or selection.method is not PaymentMethod.STRIPE
        ):
            raise TypeError("Stripe reconciliation requires exact Stripe selection")
        if type(receipts) is not tuple or not receipts:
            raise ValueError("Stripe reconciliation requires durable step receipts")
        effective_fingerprint = self._subscriber_fingerprint
        if subscriber_id:
            if type(subscriber_id) is not str or not subscriber_id.isdecimal():
                raise ValueError("subscriber_id must be exact decimal text")
            effective_fingerprint = hashlib.sha256(subscriber_id.encode()).hexdigest()
        if not effective_fingerprint:
            raise ValueError("Stripe reconciliation requires a subscriber binding")
        request = _stripe_link_request(
            selection.obligation,
            account_profiles=self._profiles,
            subscriber_fingerprint=effective_fingerprint,
            payment_percentages=self._percentages,
            initiation_id=initiation_id,
        )
        if any(
            receipt.account_profile_id != request.account_profile_id
            for receipt in receipts
        ):
            return StripeReconciliationResult(offer=None, manual_review=True)
        presentation = stripe_product_presentation(request)
        by_step = {receipt.step: receipt for receipt in receipts}
        if len(by_step) != len(receipts):
            raise RuntimeError("Stripe reconciliation receipts contain duplicate steps")

        product_metadata = {
            "payment_id_sha256": hashlib.sha256(request.payment_id.encode()).hexdigest(),
            "economic_version": str(request.economic_version),
            "display_details_sha256": presentation.details_sha256,
        }
        product_expected = {
            "name": presentation.name,
            "description": presentation.description,
            "metadata": product_metadata,
        }
        product_receipt = by_step.get(StripeCreationStep.PRODUCT)
        if (
            product_receipt is None
            or product_receipt.expected_metadata_hash != _canonical_hash(_journal_binding(request, product_expected))
            or product_receipt.idempotency_key != request.idempotency_key + ":product"
        ):
            raise RuntimeError("Stripe Product receipt binding diverged")

        price_receipt = by_step.get(StripeCreationStep.PRICE)
        link_receipt = by_step.get(StripeCreationStep.PAYMENT_LINK)
        if (
            link_receipt is not None
            and link_receipt.status is StripeStepStatus.ACCEPTED
        ):
            if (
                product_receipt.status is not StripeStepStatus.ACCEPTED
                or price_receipt is None
                or price_receipt.status is not StripeStepStatus.ACCEPTED
            ):
                raise RuntimeError("accepted Payment Link lacks accepted predecessors")
            assert product_receipt.provider_object_id is not None
            assert price_receipt.provider_object_id is not None
            assert link_receipt.provider_object_id is not None
            assert link_receipt.canonical_url is not None
            product_id = product_receipt.provider_object_id
            price_id = price_receipt.provider_object_id
            price_expected = {
                "product": product_id,
                "currency": request.currency.lower(),
                "unit_amount": str(request.amount_minor),
            }
            if (
                price_receipt.expected_metadata_hash != _canonical_hash(_journal_binding(request, price_expected))
                or price_receipt.idempotency_key
                != request.idempotency_key + ":price"
            ):
                raise RuntimeError("Stripe Price receipt binding diverged")
            link_metadata = self._link_metadata(
                request,
                presentation.details_sha256,
            )
            link_expected = {"price_id": price_id, "metadata": link_metadata}
            if (
                link_receipt.expected_metadata_hash != _canonical_hash(_journal_binding(request, link_expected))
                or link_receipt.idempotency_key
                != request.idempotency_key + ":payment_link"
            ):
                raise RuntimeError("Stripe Payment Link receipt binding diverged")
            accepted_offer = _stripe_payment_link(
                request,
                link_id=link_receipt.provider_object_id,
                url=link_receipt.canonical_url,
            )
            try:
                product_payload = self._transport.get(
                    profile=request.account_profile_id,
                    path="/v1/products/" + quote(product_id, safe=""),
                )
                product_matches = self._product_matches(
                    product_payload,
                    product_id=product_id,
                    presentation=presentation,
                    metadata=product_metadata,
                )
                price_payload = self._transport.get(
                    profile=request.account_profile_id,
                    path="/v1/prices/" + quote(price_id, safe=""),
                )
                price_matches = self._price_matches(
                    price_payload,
                    price_id=price_id,
                    product_id=product_id,
                    request=request,
                )
                link_payload = self._transport.get(
                    profile=request.account_profile_id,
                    path=(
                        "/v1/payment_links/"
                        + quote(link_receipt.provider_object_id, safe="")
                    ),
                    params={"expand[]": "line_items"},
                )
                link_values = self._link_values(
                    link_payload,
                    link_id=link_receipt.provider_object_id,
                    price_id=price_id,
                    metadata=link_metadata,
                )
                audit_matches = (
                    product_matches
                    and price_matches
                    and link_values is not None
                    and link_values[1] == link_receipt.canonical_url
                )
            except Exception:
                audit_matches = False
            return StripeReconciliationResult(
                offer=accepted_offer,
                manual_review=not audit_matches,
            )

        if product_receipt.status is StripeStepStatus.ACCEPTED:
            assert product_receipt.provider_object_id is not None
            product_id = product_receipt.provider_object_id
            product_payload = self._transport.get(
                profile=request.account_profile_id,
                path="/v1/products/" + quote(product_id, safe=""),
            )
            if not self._product_matches(
                product_payload,
                product_id=product_id,
                presentation=presentation,
                metadata=product_metadata,
            ):
                return StripeReconciliationResult(offer=None, manual_review=True)
        else:
            product_list = self._transport.get(
                profile=request.account_profile_id,
                path="/v1/products/search",
                params={
                    "query": (
                        "metadata['payment_id_sha256']:'"
                        + product_metadata["payment_id_sha256"]
                        + "'"
                    ),
                    "limit": "100",
                },
            )
            matches = tuple(
                item
                for item in self._single_list(product_list)
                if self._product_matches(
                    item,
                    product_id=None,
                    presentation=presentation,
                    metadata=product_metadata,
                )
            )
            if len(matches) != 1:
                return StripeReconciliationResult(offer=None, manual_review=True)
            product_id = matches[0]["id"]
            assert type(product_id) is str

        price_receipt = by_step.get(StripeCreationStep.PRICE)
        if price_receipt is None:
            return StripeReconciliationResult(offer=None, manual_review=True)
        price_expected = {
            "product": product_id,
            "currency": request.currency.lower(),
            "unit_amount": str(request.amount_minor),
        }
        if (
            price_receipt.expected_metadata_hash != _canonical_hash(_journal_binding(request, price_expected))
            or price_receipt.idempotency_key != request.idempotency_key + ":price"
        ):
            raise RuntimeError("Stripe Price receipt binding diverged")
        if price_receipt.status is StripeStepStatus.ACCEPTED:
            assert price_receipt.provider_object_id is not None
            price_id = price_receipt.provider_object_id
            price_payload = self._transport.get(
                profile=request.account_profile_id,
                path="/v1/prices/" + quote(price_id, safe=""),
            )
            if not self._price_matches(
                price_payload,
                price_id=price_id,
                product_id=product_id,
                request=request,
            ):
                return StripeReconciliationResult(offer=None, manual_review=True)
        else:
            price_list = self._transport.get(
                profile=request.account_profile_id,
                path="/v1/prices",
                params={"product": product_id, "active": "true", "limit": "100"},
            )
            price_matches = tuple(
                item
                for item in self._single_list(price_list)
                if self._price_matches(
                    item,
                    price_id=None,
                    product_id=product_id,
                    request=request,
                )
            )
            if len(price_matches) != 1:
                return StripeReconciliationResult(offer=None, manual_review=True)
            price_id = price_matches[0]["id"]
            assert type(price_id) is str

        link_receipt = by_step.get(StripeCreationStep.PAYMENT_LINK)
        if link_receipt is None:
            return StripeReconciliationResult(offer=None, manual_review=True)
        link_metadata = self._link_metadata(request, presentation.details_sha256)
        link_expected = {"price_id": price_id, "metadata": link_metadata}
        if (
            link_receipt.expected_metadata_hash != _canonical_hash(_journal_binding(request, link_expected))
            or link_receipt.idempotency_key
            != request.idempotency_key + ":payment_link"
        ):
            raise RuntimeError("Stripe Payment Link receipt binding diverged")
        if link_receipt.status is StripeStepStatus.ACCEPTED:
            assert link_receipt.provider_object_id is not None
            payload = self._transport.get(
                profile=request.account_profile_id,
                path=(
                    "/v1/payment_links/"
                    + quote(link_receipt.provider_object_id, safe="")
                ),
                params={"expand[]": "line_items"},
            )
            values = self._link_values(
                payload,
                link_id=link_receipt.provider_object_id,
                price_id=price_id,
                metadata=link_metadata,
            )
            if values is None or values[1] != link_receipt.canonical_url:
                return StripeReconciliationResult(offer=None, manual_review=True)
        else:
            payload = self._transport.get(
                profile=request.account_profile_id,
                path="/v1/payment_links",
                params={"active": "true", "limit": "100"},
            )
            matches = tuple(
                values
                for item in self._single_list(payload)
                if (
                    values := self._link_values(
                        item,
                        link_id=None,
                        price_id=price_id,
                        metadata=link_metadata,
                    )
                )
            )
            if len(matches) != 1:
                return StripeReconciliationResult(offer=None, manual_review=True)
            values = matches[0]
        return StripeReconciliationResult(
            offer=_stripe_payment_link(request, link_id=values[0], url=values[1]),
            manual_review=False,
        )


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

    def create_link_journaled(
        self,
        obligation: PaymentObligation,
        *,
        initiation_id: str,
        journal_worker_id: str,
        journal_fencing_token: int,
        subscriber_id: str = "",
    ) -> StripePaymentLink:
        if type(initiation_id) is not str or not initiation_id:
            raise ValueError("journaled Stripe create requires initiation_id")
        return self.create_link(
            obligation,
            initiation_id=initiation_id,
            journal_worker_id=journal_worker_id,
            journal_fencing_token=journal_fencing_token,
            subscriber_id=subscriber_id,
        )

    def create_link(
        self,
        obligation: PaymentObligation,
        *,
        initiation_id: str = "",
        journal_worker_id: str = "",
        journal_fencing_token: int = 0,
        subscriber_id: str = "",
    ) -> StripePaymentLink:
        if type(obligation) is not PaymentObligation:
            raise TypeError("obligation must be exact PaymentObligation")
        if not self._enabled:
            raise RuntimeError("stripe_link_gate_closed")
        if obligation.display_details is None:
            raise ValueError("Stripe link creation requires display_details")
        if obligation.display_details.customer_language is None:
            raise ValueError("Stripe link creation requires customer_language")
        effective_fingerprint = self._subscriber_fingerprint
        if subscriber_id:
            if type(subscriber_id) is not str or not subscriber_id.isdecimal():
                raise ValueError("subscriber_id must be exact decimal text")
            effective_fingerprint = hashlib.sha256(subscriber_id.encode()).hexdigest()
        request = _stripe_link_request(
            obligation,
            account_profiles=self._profiles,
            subscriber_fingerprint=effective_fingerprint,
            payment_percentages=self._percentages,
            initiation_id=initiation_id,
            journal_worker_id=journal_worker_id,
            journal_fencing_token=journal_fencing_token,
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
        return _stripe_payment_link(request, link_id=link_id, url=url)


__all__ = [
    "StripeLinkAdapter",
    "StripeLinkReconciliationAdapter",
    "StripeTestHTTPTransport",
    "StripeTestReconciliationTransport",
    "WiseBRLRates",
    "WiseExchangeRateReader",
]
