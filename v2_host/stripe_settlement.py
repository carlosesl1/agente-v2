"""Stripe-only provider settlement with fresh reads and one permanent dispatch.

Cloudbeds PMS v1.3 postPayment and Bókun REST-v1 confirm-with-payment contracts.
No reservation creation, reactivation, retries, currency conversion or discounts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import httpx

from reservation_domain import ExecutionCertainty, loads_outcome
from reservation_followup.payment import SettlementOutcome, VerifiedStripeEvent
from reservation_followup.types import PaymentMethod, SettlementCertainty
from reservation_followup.workers import (
    RetryableSettlementPreparationError,
    TerminalSettlementPreparationError,
)


def _require(value):
    if not value:
        raise TerminalSettlementPreparationError(
            "Stripe settlement target contract mismatch"
        )


def _money(value):
    if type(value) not in (str, int, float):
        raise TerminalSettlementPreparationError("provider amount unavailable")
    amount = Decimal(str(value))
    _require(
        amount.is_finite()
        and amount >= 0
        and amount * 100 == (amount * 100).to_integral_value()
    )
    return int(amount * 100)


class StripeSettlementAdapter:
    settlement_id = "v2:stripe-provider-settlement"
    settlement_version = 1

    def __init__(
        self,
        *,
        store,
        execution,
        cloudbeds_api_key,
        cloudbeds_property_id,
        bokun_access_key,
        bokun_secret_key,
        effect_guard,
        lead_resolver,
        allowed_subscribers=(),
        clock=None,
        client=None,
        cloudbeds_base_url="https://api.cloudbeds.com",
        bokun_base_url="https://api.bokun.io",
        cloudbeds_auth_mode="bearer",
    ):
        self.store = store
        self.execution = execution
        self.cloudbeds_key = cloudbeds_api_key
        self.property_id = cloudbeds_property_id
        self.bokun_access = bokun_access_key
        self.bokun_secret = bokun_secret_key.encode()
        self.effect_guard = effect_guard
        self.lead_resolver = lead_resolver
        self.allowed_subscribers = allowed_subscribers
        self.clock = clock or (lambda: datetime.now(UTC))
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        cloudbeds = urlsplit(cloudbeds_base_url.rstrip("/"))
        _require(
            cloudbeds.scheme == "https"
            and bool(cloudbeds.netloc)
            and not cloudbeds.username
            and not cloudbeds.password
            and not cloudbeds.query
            and not cloudbeds.fragment
        )
        _require(cloudbeds.path in {"", "/api/v1.1", "/api/v1.2", "/api/v1.3"})
        # Settlement is qualified against PMS v1.3 even where reservation reads
        # retain an older, version-prefixed base URL. Never append /api twice.
        self.cloudbeds_url = f"{cloudbeds.scheme}://{cloudbeds.netloc}/api/v1.3"
        self.bokun_url = bokun_base_url.rstrip("/")
        self.cloudbeds_auth_mode = cloudbeds_auth_mode
        _require(cloudbeds_auth_mode in {"bearer", "api_key"})
        self._prepared = {}
        self._dispatched = set()

    def _cloudbeds_headers(self):
        return (
            {"Authorization": "Bearer " + self.cloudbeds_key}
            if self.cloudbeds_auth_mode == "bearer"
            else {"x-api-key": self.cloudbeds_key}
        )

    def _bokun_headers(self, method, path):
        stamp = self.clock().astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        signature = base64.b64encode(
            hmac.new(
                self.bokun_secret,
                (stamp + self.bokun_access + method + path).encode(),
                hashlib.sha1,
            ).digest()
        ).decode()
        return {
            "X-Bokun-AccessKey": self.bokun_access,
            "X-Bokun-Date": stamp,
            "X-Bokun-Signature": signature,
        }

    def _get(self, url, *, headers, params=None):
        try:
            response = self.client.get(
                url, headers=headers, params=params, follow_redirects=False
            )
            if response.status_code != 200:
                raise RetryableSettlementPreparationError("provider read unavailable")
            row = response.json()
            _require(type(row) is dict)
            return row
        except (httpx.HTTPError, ValueError):
            raise RetryableSettlementPreparationError(
                "provider read unavailable"
            ) from None

    def _bound_state(self, request):
        state = self.store.load_payment(request.payment_id)
        subject = state.subject
        _require(
            subject.method is PaymentMethod.STRIPE
            and type(state.evidence_record.evidence) is VerifiedStripeEvent
        )
        _require(
            subject.payment_version == request.payment_version
            and subject.economic_signature == request.economic_signature
        )
        _require(subject.currency == "BRL")
        anchor = subject.confirmed_reservation_anchor
        matches = [
            (c, l)
            for c, l in self.execution.list_outcome_projection_inputs()
            if c.command_id == anchor.reservation_command_id
        ]
        _require(len(matches) == 1)
        command, ledger = matches[0]
        _require(
            ledger.outcome_hash == anchor.reservation_outcome_hash
            and loads_outcome(ledger.outcome_json) == anchor.reservation_outcome
            and anchor.reservation_outcome.certainty
            is ExecutionCertainty.EFFECT_CONFIRMED
            and command.subject_signature == anchor.reservation_subject_signature
        )
        lead = self.lead_resolver.lead_id_for_command(command.command_id)
        _require(type(lead) is str and lead.startswith("manychat:"))
        if self.allowed_subscribers:
            _require(lead.removeprefix("manychat:") in self.allowed_subscribers)
        from v2_application.recovery import HandoffEffectGuard

        HandoffEffectGuard(store=self.store).assert_allowed(lead_id=lead)
        if not self.effect_guard.allows_workflow(command.workflow_id):
            raise RetryableSettlementPreparationError("provider write window closed")
        prefix = (
            "provider:cloudbeds:"
            if anchor.business_unit.value == "hostel"
            else "provider:bokun:id:"
        )
        _require(anchor.provider_reference.startswith(prefix))
        native = anchor.provider_reference.removeprefix(prefix)
        _require(native.isascii() and native.isdecimal())
        return state, command, native

    def prepare(self, request):
        state, command, native = self._bound_state(request)
        subject = state.subject
        if subject.business_unit.value == "hostel":
            _require(bool(self.cloudbeds_key and self.property_id))
            response = self._get(
                self.cloudbeds_url + "/getReservation",
                headers=self._cloudbeds_headers(),
                params={"propertyID": self.property_id, "reservationID": native},
            )
            _require(response.get("success") is True)
            row = response.get("data", {})
            _require(
                str(row.get("reservationID")) == native
                and str(row.get("propertyID", self.property_id)) == self.property_id
                and row.get("status") in {"confirmed", "checked_in"}
            )
            currency = row.get("currency")
            if currency is None:
                settings = self._get(
                    self.cloudbeds_url + "/getCurrencySettings",
                    headers=self._cloudbeds_headers(),
                    params={"propertyID": self.property_id},
                )
                _require(settings.get("success") is True)
                currency = settings.get("data", {}).get("default")
            _require(
                currency == "BRL"
                and _money(row.get("balanceDetailed", {}).get("paid")) == 0
                and _money(row.get("balance")) >= subject.amount_minor
            )
            methods = self._get(
                self.cloudbeds_url + "/getPaymentMethods",
                headers=self._cloudbeds_headers(),
                params={"propertyID": self.property_id},
            )
            _require(
                methods.get("success") is True
                and str(methods.get("data", {}).get("propertyID")) == self.property_id
            )
            _require(
                any(
                    m.get("method") == "Cartãodecrédito"
                    for m in methods["data"].get("methods", [])
                )
            )
        else:
            _require(bool(self.bokun_access and self.bokun_secret))
            path = f"/booking.json/booking/{native}?lang=en&currency=BRL"
            row = self._get(
                self.bokun_url + path, headers=self._bokun_headers("GET", path)
            )
            _require(
                str(row.get("bookingId")) == native
                and row.get("status") in {"RESERVED", "CONFIRMED"}
                and row.get("currency") == "BRL"
            )
            _require(
                _money(row.get("totalPaid")) == 0
                and _money(row.get("totalDue")) >= subject.amount_minor
            )
        self._prepared[request.settlement_command_id] = (state, command, native)
        return request.canonical_payload

    def dispatch(self, permit):
        command_id = permit.command.settlement_command_id
        _require(command_id not in self._dispatched and command_id in self._prepared)
        state, command, native = self._prepared.pop(command_id)
        # A permanent fence and an active lease are both necessary, never merely
        # a process-local prepared flag. Re-read business state before each POST.
        current, live_command, live_native = self._bound_state(permit.command)
        _require(
            current.subject == state.subject
            and live_command == command
            and live_native == native
        )
        self.store.assert_live_settlement_permit(permit, now=self.clock())
        _require(self.effect_guard.allows_workflow(command.workflow_id))
        self._dispatched.add(command_id)
        amount = Decimal(state.subject.amount_minor) / 100
        reference = state.confirmation.confirmation_id
        if state.subject.business_unit.value == "hostel":
            response = self.client.post(
                self.cloudbeds_url + "/postPayment",
                headers=self._cloudbeds_headers(),
                data={
                    "propertyID": self.property_id,
                    "reservationID": native,
                    "type": "Cartãodecrédito",
                    "amount": format(amount, ".2f"),
                    "description": "Stripe " + reference,
                },
                follow_redirects=False,
            )
        else:
            path = f"/booking.json/{native}/confirm?currency=BRL&lang=pt&sendCustomerNotification=false"
            response = self.client.post(
                self.bokun_url + path,
                headers=self._bokun_headers("POST", path),
                json={
                    "payment": {
                        "amount": float(amount),
                        "currency": "BRL",
                        "paymentType": "WEB_PAYMENT",
                        "paymentReferenceId": reference,
                        "comment": "Stripe " + reference,
                    }
                },
                follow_redirects=False,
            )
        evidence = (permit.request_hash, hashlib.sha256(response.content).hexdigest())
        if response.status_code != 200:
            return SettlementOutcome(
                SettlementCertainty.DISPATCHED_UNKNOWN,
                False,
                False,
                None,
                True,
                evidence,
            )
        try:
            row = response.json()
            _require(type(row) is dict)
            return self._receipt_outcome(row, state, native, reference, evidence)
        except (ValueError, TypeError, KeyError, AttributeError, TerminalSettlementPreparationError):
            # A received response must not lose its evidence just because its
            # contract is incomplete. Do not retry, infer from aggregate balance,
            # or report no payment. Persist unknown with the response fingerprint.
            diagnostic = {
                "http_status": response.status_code,
                "response_sha256": evidence[1],
                "provider_request_id": response.headers.get("x-request-id"),
                "receipt_shape": {
                    k: type(row.get(k)).__name__
                    for k in ("success", "paymentID", "transactionID", "bookingId")
                } if isinstance(locals().get("row"), dict) else "non_object",
            }
            # Plain production formatters discard extra fields. Keep the shape
            # and fingerprint in the actual message without logging raw receipts.
            logging.getLogger(__name__).warning(
                "stripe_settlement_receipt_unrecognized %s",
                json.dumps(diagnostic, sort_keys=True, separators=(",", ":")),
                extra=diagnostic,
            )
            return SettlementOutcome(
                SettlementCertainty.DISPATCHED_UNKNOWN,
                False, False, None, True, evidence,
            )

    @staticmethod
    def _receipt_outcome(row, state, native, reference, evidence):
        if state.subject.business_unit.value == "hostel":
            if row.get("success") is False:
                return SettlementOutcome(
                    SettlementCertainty.DISPATCHED_NO_EFFECT,
                    False,
                    False,
                    None,
                    False,
                    evidence,
                )
            _require(
                row.get("success") is True
                and all(
                    type(row.get(key)) is str and bool(row[key].strip())
                    for key in ("paymentID", "transactionID")
                )
            )
            receipt = str(row["paymentID"]) + ":" + str(row["transactionID"])
        else:
            _require(
                str(row.get("bookingId")) == native
                and row.get("status") == "CONFIRMED"
                and row.get("currency") == "BRL"
                and _money(row.get("totalPaid")) == state.subject.amount_minor
            )
            matches = [
                p
                for p in row.get("customerPayments", [])
                if p.get("paymentReferenceId") == reference
                and p.get("currency") == "BRL"
                and _money(p.get("amount")) == state.subject.amount_minor
            ]
            _require(
                len(matches) == 1
                and bool(matches[0].get("id") or matches[0].get("paymentId"))
            )
            receipt = str(matches[0].get("id") or matches[0]["paymentId"])
        return SettlementOutcome(
            SettlementCertainty.SETTLED,
            True,
            True,
            hashlib.sha256(receipt.encode()).hexdigest(),
            False,
            evidence,
        )
