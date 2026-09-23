"""Account-scoped native Stripe TEST ingress into the existing financial owner.

No provider POSTs. Hosted checkout acceptance is the financial confirmation;
canonical payment_intent.succeeded IDs (retrieved from Stripe) own global claims.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime

import httpx

from reservation_domain import ExecutionCertainty, loads_outcome
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup import (
    FinancialConfirmationReceived,
    FinancialSummaryRecorded,
    PaymentEvidenceRecorded,
    PaymentEvidenceTrust,
    PaymentMethodSelected,
    StripeEventType,
    VerifiedStripeEvent,
)
from reservation_followup.payment import (
    _stripe_verification_hash,
    financial_summary_hash,
    stripe_target_fingerprint,
)
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from reservation_followup.types import (
    BusinessUnit,
    ConfirmedReservationAnchor,
    EffectRequirement,
    PaymentEffectPolicy,
    PaymentMethod,
    PaymentSubject,
)
from v2_application.financial_webhooks import (
    FinancialWebhookInvalid,
    FinancialWebhookUnauthorized,
    _unique_object,
)
from v2_application.lead_identity import payment_id_for_command
from v2_application.outcome_projector import _minor_units, _opaque
from v2_application.payments import (
    EvidenceConflict,
    SQLitePaymentInitiationStore,
    V2PaymentEvidenceGateway,
)
from v2_contracts.payments import (
    StripeCreationStep,
    StripePaymentLink,
    StripeStepStatus,
)


class NativeStripeUnresolved(RuntimeError):
    """Authenticated event cannot yet be correlated; Stripe should retry."""


class NativeStripeIgnored(RuntimeError):
    """Authentic notification outside this endpoint's explicitly supported scope."""


def validate_native_accounts(accounts, result_key):
    if type(accounts) is not dict or set(accounts) - {"hostel", "agency"}:
        raise ValueError("native Stripe accounts must use business-unit keys")
    if type(result_key) is not bytes or bool(accounts) != bool(result_key):
        raise ValueError("native Stripe accounts and result key are all-or-none")
    if accounts and len(result_key) != 32:
        raise ValueError("native Stripe result key must contain 32 bytes")
    for config in accounts.values():
        if type(config) is not dict or set(config) != {
            "profile_id",
            "account_id",
            "api_key",
            "webhook_secret",
        }:
            raise ValueError("native Stripe account fields mismatch")
        if any(type(v) is not str or not v or "\x00" in v for v in config.values()):
            raise ValueError("native Stripe account requires non-empty text")
        if (
            not config["api_key"].startswith(("rk_test_", "sk_test_"))
            or not config["account_id"].startswith("acct_")
            or not config["webhook_secret"].startswith("whsec_")
        ):
            raise ValueError(
                "native Stripe activation is TEST-only with exact account secret"
            )
    if len({c["account_id"] for c in accounts.values()}) != len(accounts):
        raise ValueError("native Stripe units require distinct accounts")


def _hash(value):
    return hashlib.sha256(
        b"v2-stripe-step-binding-v1\0"
        + json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _require(condition):
    if not condition:
        raise FinancialWebhookInvalid("native Stripe payment contract mismatch")


def _object_id(value, prefix):
    _require(
        type(value) is str
        and value.startswith(prefix + "_")
        and all(c.isalnum() or c == "_" for c in value)
    )
    return value


def verify_checkout(body, headers, *, secret, received_at):
    signature = next(
        (v for k, v in headers.items() if k.lower() == "stripe-signature"), ""
    )
    try:
        parts = [p.split("=", 1) for p in signature.split(",")]
        stamps = [v for k, v in parts if k == "t"]
        if len(stamps) != 1 or not stamps[0].isdecimal():
            raise ValueError()
        stamp = stamps[0]
        expected = hmac.new(
            secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        if abs(received_at.timestamp() - int(stamp)) > 300 or not any(
            k == "v1" and hmac.compare_digest(v, expected) for k, v in parts
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise FinancialWebhookUnauthorized("native Stripe signature mismatch") from None
    try:
        event = json.loads(
            body,
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, UnicodeError):
        raise FinancialWebhookInvalid(
            "native Stripe event must be strict JSON"
        ) from None
    _require(
        type(event) is dict
        and event.get("object") == "event"
        and event.get("livemode") is False
    )
    if event.get("type") not in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }:
        raise NativeStripeIgnored()
    _require(event.get("account") is None)  # no implicit Connect delegation
    _object_id(event.get("id"), "evt")
    _require(
        type(event.get("created")) is int
        and 0 < event["created"] <= received_at.timestamp()
    )
    session = event.get("data", {}).get("object", {})
    _require(type(session) is dict and session.get("object") == "checkout.session")
    _object_id(session.get("id"), "cs")
    if session.get("payment_status") != "paid":
        raise NativeStripeIgnored()
    return event


class NativeStripeIngress:
    def __init__(
        self, *, paths, accounts, result_key, allowed_subscribers=(), client=None
    ):
        validate_native_accounts(accounts, result_key)
        self.paths = paths
        self.accounts = accounts
        self.result_key = result_key
        self.allowed_subscribers = allowed_subscribers
        self.client = client

    def accept(self, unit, body, headers, *, received_at):
        config = self.accounts[unit]
        event = verify_checkout(
            body, headers, secret=config["webhook_secret"], received_at=received_at
        )
        # All blocking I/O runs in a request worker, with short-lived SQLite owners.
        with ExitStack() as stack:
            client = self.client or stack.enter_context(
                httpx.Client(timeout=20, follow_redirects=False)
            )

            def get(path, params=None):
                try:
                    response = client.get(
                        "https://api.stripe.com/v1/" + path,
                        params=params,
                        headers={"Authorization": "Bearer " + config["api_key"]},
                        follow_redirects=False,
                    )
                    if response.status_code != 200:
                        raise NativeStripeUnresolved(
                            "Stripe correlation read unavailable"
                        )
                    value = response.json()
                    _require(type(value) is dict)
                    return value
                except (httpx.HTTPError, ValueError):
                    raise NativeStripeUnresolved(
                        "Stripe correlation read unavailable"
                    ) from None

            _require(get("account").get("id") == config["account_id"])
            session_id = event["data"]["object"]["id"]
            session = get("checkout/sessions/" + session_id)
            _require(
                session.get("id") == session_id
                and session.get("livemode") is False
                and session.get("status") == "complete"
                and session.get("mode") == "payment"
                and session.get("payment_status") == "paid"
            )
            for key in ("payment_link", "payment_intent", "currency", "amount_total"):
                _require(session.get(key) == event["data"]["object"].get(key))
            link_id = _object_id(session.get("payment_link"), "plink")
            pi_id = _object_id(session.get("payment_intent"), "pi")
            intent = get("payment_intents/" + pi_id)
            _require(
                intent.get("id") == pi_id
                and intent.get("livemode") is False
                and intent.get("status") == "succeeded"
            )
            _require(
                type(session.get("amount_total")) is int
                and session["amount_total"] > 0
                and intent.get("amount_received") == session["amount_total"]
                and intent.get("currency") == session.get("currency")
            )
            # Never create missing owners from a foreign charge.
            if any(
                not self.paths[k].is_file() for k in ("execution", "payment_initiation")
            ):
                raise NativeStripeUnresolved("local issuance owner unavailable")
            payments = SQLitePaymentInitiationStore(
                self.paths["payment_initiation"], result_encryption_key=self.result_key
            )
            stack.callback(payments.close)
            matches = [
                o
                for o in payments.completed_offers()
                if type(o) is StripePaymentLink
                and o.account_profile_id == config["profile_id"]
                and o.provider_reference_fingerprint
                == hashlib.sha256(link_id.encode()).hexdigest()
            ]
            if not matches:
                # A successful checkout can beat final local publication of the
                # offer. An account-authenticated V2/allowlisted notification must
                # retry instead of being acknowledged and permanently dropped.
                metadata = get("payment_links/" + link_id).get("metadata", {})
                with SQLiteUnitOfWork.open_v6(self.paths["execution"]) as owner:
                    local_anchor = any(
                        metadata.get("reservation_anchor_sha256")
                        == hashlib.sha256(
                            _opaque("reservation-anchor", c.command_id).encode()
                        ).hexdigest()
                        for c, _ in owner.list_outcome_projection_inputs()
                    )
                subscriber_allowed = not self.allowed_subscribers or metadata.get(
                    "subscriber_sha256"
                ) in {
                    hashlib.sha256(s.encode()).hexdigest()
                    for s in self.allowed_subscribers
                }
                if (
                    metadata.get("business_unit") == unit
                    and local_anchor
                    and subscriber_allowed
                ):
                    raise NativeStripeUnresolved("issuance publication not complete")
                raise NativeStripeIgnored()
            _require(len(matches) == 1)
            offer = matches[0]
            records = [
                r
                for r in payments.context_for_payment(offer.payment_id)
                if r.offer == offer
            ]
            _require(len(records) == 1)
            record = records[0]
            obligation = record.selection.obligation
            _require(
                obligation.business_unit.value == unit
                and obligation.receiver_profile_id == config["profile_id"]
            )
            receipts = {
                r.step: r
                for r in payments._stripe_receipts_for_id(record.initiation_id)
            }
            _require(
                all(
                    s in receipts
                    and receipts[s].status is StripeStepStatus.ACCEPTED
                    and receipts[s].account_profile_id == config["profile_id"]
                    for s in StripeCreationStep
                )
            )
            _require(
                receipts[StripeCreationStep.PAYMENT_LINK].provider_object_id == link_id
            )
            price_id = receipts[StripeCreationStep.PRICE].provider_object_id
            link = get("payment_links/" + link_id)
            price = get(
                "prices/" + _object_id(price_id, "price"),
                {"expand[]": "currency_options"},
            )
            _require(
                link.get("id") == link_id
                and link.get("livemode") is False
                and link.get("url") == offer.public_url
                and price.get("id") == price_id
                and price.get("livemode") is False
            )

            def receipt_matches(step, expected):
                _require(
                    receipts[step].expected_metadata_hash
                    == _hash(
                        {
                            "account_profile_id_sha256": hashlib.sha256(
                                config["profile_id"].encode()
                            ).hexdigest(),
                            "provider_expected": expected,
                        }
                    )
                )

            receipt_matches(
                StripeCreationStep.PAYMENT_LINK,
                {"price_id": price_id, "metadata": link.get("metadata")},
            )
            receipt_matches(
                StripeCreationStep.PRICE,
                {
                    "product": price.get("product"),
                    "currency": price.get("currency"),
                    "unit_amount": str(price.get("unit_amount")),
                },
            )
            metadata = link["metadata"]
            _require(
                metadata.get("business_unit") == unit
                and metadata.get("reservation_anchor_sha256")
                == hashlib.sha256(obligation.reservation_anchor_id.encode()).hexdigest()
            )
            if self.allowed_subscribers:
                _require(
                    metadata.get("subscriber_sha256")
                    in {
                        hashlib.sha256(s.encode()).hexdigest()
                        for s in self.allowed_subscribers
                    }
                )
            items = get(
                "checkout/sessions/" + session_id + "/line_items", {"limit": "2"}
            )
            _require(items.get("has_more") is False and len(items.get("data", [])) == 1)
            item = items["data"][0]
            _require(
                type(item.get("quantity")) is int
                and item["quantity"] == 1
                and item.get("price", {}).get("id") == price_id
            )
            _require(
                price.get("currency") == "brl"
                and type(price.get("unit_amount")) is int
                and price["unit_amount"] > 0
            )
            paid_currency = session["currency"]
            expected_amount = (
                price["unit_amount"]
                if paid_currency == "brl"
                else price.get("currency_options", {})
                .get(paid_currency, {})
                .get("unit_amount")
            )
            _require(
                type(expected_amount) is int
                and expected_amount == session["amount_total"]
            )
            canonical_amount = price["unit_amount"]
            execution = SQLiteUnitOfWork.open_v6(self.paths["execution"])
            stack.callback(execution.close)
            owners = [
                (c, l)
                for c, l in execution.list_outcome_projection_inputs()
                if payment_id_for_command(c) == offer.payment_id
            ]
            _require(len(owners) == 1)
            command, ledger = owners[0]
            outcome = loads_outcome(ledger.outcome_json)
            _require(
                outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED
                and command.payload.terms.payment_method == "stripe"
                and ledger.outcome_hash
                == hashlib.sha256(ledger.outcome_json.encode()).hexdigest()
            )
            _require(
                obligation.reservation_anchor_id
                == _opaque("reservation-anchor", command.command_id)
                and obligation.economic_version == command.draft_version
                and obligation.currency == "BRL"
            )
            _require(
                obligation.amount_minor
                == _minor_units(command.payload.components[0].total.amount)
                and canonical_amount <= obligation.amount_minor
            )
            # Find Stripe's canonical success event, not the delivery event ID. Its
            # stable identity deduplicates completed/async notifications of one PI.
            created = session.get("created")
            _require(
                type(created) is int
                and ledger.updated_at.timestamp() <= created <= event["created"]
            )
            events = get(
                "events",
                {
                    "type": "payment_intent.succeeded",
                    "created[gte]": created,
                    "created[lte]": event["created"],
                    "limit": "100",
                },
            )
            canonical = [
                e
                for e in events.get("data", [])
                if e.get("data", {}).get("object", {}).get("id") == pi_id
            ]
            if events.get("has_more") is not False or len(canonical) != 1:
                raise NativeStripeUnresolved(
                    "canonical Stripe transaction event unresolved"
                )
            transaction = canonical[0]
            _require(
                transaction.get("type") == "payment_intent.succeeded"
                and transaction.get("livemode") is False
                and transaction.get("account") is None
            )
            _object_id(transaction.get("id"), "evt")
            for key in ("id", "status", "amount_received", "currency", "livemode"):
                _require(transaction["data"]["object"].get(key) == intent.get(key))
            _require(
                type(transaction.get("created")) is int
                and created <= transaction["created"] <= event["created"]
            )
            observed = datetime.fromtimestamp(transaction["created"], UTC)
            expires = session.get("expires_at")
            _require(type(expires) is int and expires >= transaction["created"])
            deadline = datetime.fromtimestamp(expires, UTC)
            anchor = ConfirmedReservationAnchor(
                reservation_workflow_id=command.workflow_id,
                reservation_command_id=command.command_id,
                reservation_subject_signature=command.subject_signature,
                reservation_outcome_hash=ledger.outcome_hash,
                reservation_outcome=outcome,
                provider_reference=outcome.provider_reference,
                service=command.payload.components[0].service,
                business_unit=BusinessUnit(unit),
                payment_target_id=_opaque("payment-target", command.command_id),
                amount_minor=obligation.amount_minor,
                currency="BRL",
                receiver_profile_id=config["profile_id"],
                confirmed_at=ledger.updated_at,
                payment_deadline=deadline,
            )
            followup = SQLiteFollowupUnitOfWork.open_v2(
                self.paths["followup"], migrate_v1=True
            )
            stack.callback(followup.close)
            return self._record(
                followup, anchor, canonical_amount, observed, transaction["id"], pi_id
            )

    @staticmethod
    def _record(store, anchor, amount, observed, event_id, pi_id):
        # Maya's completion path owns public wording; effect receipts observe
        # its actual delivery instead of sending a second canned message.
        policy = PaymentEffectPolicy(
            EffectRequirement.REQUIRED,
            EffectRequirement.REQUIRED,
            EffectRequirement.DISABLED,
            EffectRequirement.DISABLED,
        )
        state = store.open_payment(anchor, policy).state
        payment_id = state.subject.payment_id
        if state.evidence_record is not None:
            old = state.evidence_record
            if old.evidence.event_id != event_id or old.evidence.amount_minor != amount:
                raise EvidenceConflict("reservation already owns another payment")
            return V2PaymentEvidenceGateway(store).accept(
                payment_id=payment_id,
                expected_revision=store._load_payment(payment_id)[1],
                event=old,
            )

        def apply(event):
            store.apply_payment(payment_id, store._load_payment(payment_id)[1], event)

        # Replays use immutable source times/IDs; a crash between commits resumes.
        apply(
            PaymentMethodSelected(
                _opaque("stripe-method", payment_id),
                payment_id,
                PaymentMethod.STRIPE,
                anchor.confirmed_at,
            )
        )
        subject = PaymentSubject.from_anchor(
            anchor,
            payment_id=payment_id,
            method=PaymentMethod.STRIPE,
            amount_minor=amount,
        )
        summary = financial_summary_hash(subject)
        apply(
            FinancialSummaryRecorded(
                _opaque("stripe-summary", payment_id),
                subject,
                summary,
                anchor.confirmed_at,
            )
        )
        apply(
            FinancialConfirmationReceived(
                _opaque("stripe-confirm", payment_id),
                payment_id,
                subject.payment_version,
                subject.economic_signature,
                summary,
                pi_id,
                observed,
            )
        )
        evidence = VerifiedStripeEvent(
            anchor.receiver_profile_id,
            event_id,
            stripe_target_fingerprint(subject.payment_target_id),
            amount,
            "BRL",
            StripeEventType.PAYMENT_INTENT_SUCCEEDED,
            True,
            observed,
            "0" * 64,
        )
        evidence = replace(
            evidence, verification_hash=_stripe_verification_hash(evidence)
        )
        trust = PaymentEvidenceTrust(
            "closed:pix",
            "closed:wise",
            "closed:wise-account",
            anchor.receiver_profile_id,
        )
        recorded = PaymentEvidenceRecorded(
            _opaque("stripe-evidence", event_id),
            payment_id,
            subject.payment_version,
            subject.economic_signature,
            evidence,
            trust,
            observed,
        )
        return V2PaymentEvidenceGateway(store).accept(
            payment_id=payment_id,
            expected_revision=store._load_payment(payment_id)[1],
            event=recorded,
        )
