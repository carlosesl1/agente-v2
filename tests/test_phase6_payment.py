from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
import hashlib
import json
import unittest

from reservation_domain import ExecutionCertainty, ReservationCommand
from reservation_followup import (
    BusinessUnit,
    PaymentMethod,
    PaymentSubject,
    from_wire_json,
    to_wire_json,
)
from reservation_followup.payment import (
    PaymentEvidence,
    PixProofStatus,
    PixVisualEvidence,
    StripeEventType,
    VerifiedPaymentEvidence,
    VerifiedStripeEvent,
    VerifiedWiseCredit,
    evidence_claim_key,
    stripe_target_fingerprint,
    validate_evidence,
)
from tests.phase6_helpers import T0, confirmed_anchor, outcome


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _pix_hash(**changes: object) -> str:
    payload: dict[str, object] = {
        "type": "pix_visual_evidence",
        "proof_amount_minor": 12500,
        "proof_currency": "BRL",
        "proof_receiver_profile_id": "receiver:profile:synthetic:1",
        "proof_status": "paid",
        "normalized_e2e": "E2E20270201ABCDEF123456789",
        "observed_at": T0.isoformat(),
        "extractor_id": "extractor:synthetic:pix:1",
        "extractor_version": "extractor-version:synthetic:1",
    }
    payload.update(changes)
    return _digest(payload)


def pix_evidence(**changes: object) -> PixVisualEvidence:
    values: dict[str, object] = {
        "proof_amount_minor": 12500,
        "proof_currency": "BRL",
        "proof_receiver_profile_id": "receiver:profile:synthetic:1",
        "proof_status": PixProofStatus.PAID,
        "normalized_e2e": "E2E20270201ABCDEF123456789",
        "observed_at": T0,
        "extractor_id": "extractor:synthetic:pix:1",
        "extractor_version": "extractor-version:synthetic:1",
    }
    values.update(changes)
    if "evidence_hash" not in values:
        hash_values = {
            key: (value.value if hasattr(value, "value") else value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in values.items()
        }
        values["evidence_hash"] = _pix_hash(**hash_values)
    return PixVisualEvidence(**values)


def _wise_hash(**changes: object) -> str:
    payload: dict[str, object] = {
        "type": "verified_wise_credit",
        "signer_profile_id": "receiver:profile:synthetic:1",
        "account_profile_id": "receiver:profile:synthetic:1",
        "amount_minor": 12500,
        "currency": "BRL",
        "credited_at": T0.isoformat(),
        "transaction_fingerprint": _digest({"wise_transaction": "synthetic:1"}),
        "payer_fingerprint": _digest({"wise_payer": "synthetic:1"}),
        "reference_fingerprint": _digest({"wise_reference": "synthetic:1"}),
        "signature_verified": True,
    }
    payload.update(changes)
    return _digest(payload)


def wise_credit(**changes: object) -> VerifiedWiseCredit:
    values: dict[str, object] = {
        "signer_profile_id": "receiver:profile:synthetic:1",
        "account_profile_id": "receiver:profile:synthetic:1",
        "amount_minor": 12500,
        "currency": "BRL",
        "credited_at": T0,
        "transaction_fingerprint": _digest({"wise_transaction": "synthetic:1"}),
        "payer_fingerprint": _digest({"wise_payer": "synthetic:1"}),
        "reference_fingerprint": _digest({"wise_reference": "synthetic:1"}),
        "signature_verified": True,
    }
    values.update(changes)
    if "verification_hash" not in values:
        hash_values = {
            key: (value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in values.items()
        }
        values["verification_hash"] = _wise_hash(**hash_values)
    return VerifiedWiseCredit(**values)


def _stripe_hash(**changes: object) -> str:
    payload: dict[str, object] = {
        "type": "verified_stripe_event",
        "stripe_account_profile_id": "receiver:profile:synthetic:1",
        "event_id": "stripe-event:synthetic:1",
        "payment_intent_fingerprint": stripe_target_fingerprint(
            "target:reservation:synthetic:1"
        ),
        "amount_minor": 12500,
        "currency": "BRL",
        "event_type": "payment_intent.succeeded",
        "signature_verified": True,
        "observed_at": T0.isoformat(),
    }
    payload.update(changes)
    return _digest(payload)


def stripe_event(**changes: object) -> VerifiedStripeEvent:
    values: dict[str, object] = {
        "stripe_account_profile_id": "receiver:profile:synthetic:1",
        "event_id": "stripe-event:synthetic:1",
        "payment_intent_fingerprint": stripe_target_fingerprint(
            "target:reservation:synthetic:1"
        ),
        "amount_minor": 12500,
        "currency": "BRL",
        "event_type": StripeEventType.PAYMENT_INTENT_SUCCEEDED,
        "signature_verified": True,
        "observed_at": T0,
    }
    values.update(changes)
    if "verification_hash" not in values:
        hash_values = {
            key: (value.value if hasattr(value, "value") else value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in values.items()
        }
        values["verification_hash"] = _stripe_hash(**hash_values)
    return VerifiedStripeEvent(**values)


class Phase6PaymentEvidenceTests(unittest.TestCase):
    def subject(self, method: PaymentMethod) -> PaymentSubject:
        return PaymentSubject.from_anchor(
            confirmed_anchor(),
            payment_id="payment:synthetic:1",
            method=method,
        )

    def test_only_effect_confirmed_anchor_can_bootstrap_payment(self) -> None:
        for certainty in ExecutionCertainty:
            if certainty is ExecutionCertainty.EFFECT_CONFIRMED:
                continue
            anchor = confirmed_anchor()
            object.__setattr__(anchor.reservation_outcome, "certainty", certainty)
            with self.subTest(certainty=certainty), self.assertRaises(ValueError):
                PaymentSubject.from_anchor(
                    anchor,
                    payment_id="payment:synthetic:1",
                    method=PaymentMethod.PIX,
                )

    def test_payment_bootstrap_rejects_mutated_anchor_instead_of_normalizing_it(self) -> None:
        wrong_nested_type = confirmed_anchor()
        object.__setattr__(wrong_nested_type, "reservation_outcome", object())
        noncanonical_id = confirmed_anchor()
        object.__setattr__(
            noncanonical_id,
            "reservation_workflow_id",
            " workflow:reservation:synthetic:1 ",
        )
        for anchor in (wrong_nested_type, noncanonical_id):
            with self.subTest(anchor=anchor), self.assertRaises(ValueError):
                PaymentSubject.from_anchor(
                    anchor,
                    payment_id="payment:synthetic:1",
                    method=PaymentMethod.PIX,
                )

    def test_payment_subject_factory_distinguishes_method_and_economic_changes(self) -> None:
        anchor = confirmed_anchor()
        pix = PaymentSubject.from_anchor(
            anchor,
            payment_id="payment:synthetic:1",
            method=PaymentMethod.PIX,
        )
        wise = PaymentSubject.from_anchor(
            anchor,
            payment_id="payment:synthetic:1",
            method=PaymentMethod.WISE,
        )
        changed_amount = PaymentSubject.from_anchor(
            anchor,
            payment_id="payment:synthetic:1",
            method=PaymentMethod.PIX,
            amount_minor=anchor.amount_minor + 1,
        )
        changed_receiver = PaymentSubject.from_anchor(
            anchor,
            payment_id="payment:synthetic:1",
            method=PaymentMethod.PIX,
            receiver_profile_id="receiver:profile:synthetic:2",
        )
        self.assertEqual(pix.economic_signature, wise.economic_signature)
        self.assertEqual((pix.payment_version, wise.payment_version), (1, 1))
        self.assertEqual((changed_amount.payment_version, changed_receiver.payment_version), (2, 2))
        self.assertNotEqual(pix.economic_signature, changed_amount.economic_signature)
        self.assertNotEqual(pix.economic_signature, changed_receiver.economic_signature)
        self.assertFalse(any(isinstance(item, ReservationCommand) for item in (pix, wise, changed_amount, changed_receiver)))

    def test_payment_subject_factory_and_wire_close_economic_version(self) -> None:
        anchor = confirmed_anchor()
        for invalid_version in (True, 1.0, "1"):
            with self.subTest(invalid_version=invalid_version), self.assertRaises(ValueError):
                PaymentSubject.from_anchor(
                    anchor,
                    payment_id="payment:synthetic:1",
                    method=PaymentMethod.PIX,
                    payment_version=invalid_version,
                )

        baseline = PaymentSubject.from_anchor(
            anchor,
            payment_id="payment:synthetic:1",
            method=PaymentMethod.PIX,
        )
        changed = PaymentSubject.from_anchor(
            anchor,
            payment_id="payment:synthetic:1",
            method=PaymentMethod.PIX,
            amount_minor=anchor.amount_minor + 1,
        )
        mutations = []
        baseline_as_version_two = json.loads(to_wire_json(baseline))
        baseline_as_version_two["data"]["payment_version"] = 2
        mutations.append(baseline_as_version_two)
        changed_as_version_one = json.loads(to_wire_json(changed))
        changed_as_version_one["data"]["payment_version"] = 1
        mutations.append(changed_as_version_one)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                from_wire_json(json.dumps(mutation), PaymentSubject)

        later_revision = replace(changed, payment_version=3)
        self.assertEqual(later_revision.payment_version, 3)
        self.assertEqual(later_revision.economic_signature, changed.economic_signature)

    def test_pix_accepts_exact_visual_evidence_without_bank_confirmation_claim(self) -> None:
        evidence = pix_evidence()
        verified = validate_evidence(self.subject(PaymentMethod.PIX), evidence)
        self.assertIs(type(verified), VerifiedPaymentEvidence)
        self.assertIs(verified.method, PaymentMethod.PIX)
        self.assertEqual(verified.claim_key, f"pix:{evidence.normalized_e2e}")
        self.assertEqual(verified.evidence, evidence)
        self.assertNotIn("bank", repr(verified).casefold())
        self.assertNotIn("banc", repr(verified).casefold())

    def test_pix_rejects_mismatch_pending_placeholder_entropy_and_hash(self) -> None:
        subject = self.subject(PaymentMethod.PIX)
        invalid = (
            pix_evidence(proof_amount_minor=12501),
            pix_evidence(proof_currency="USD"),
            pix_evidence(proof_receiver_profile_id="receiver:profile:synthetic:2"),
            pix_evidence(proof_status=PixProofStatus.PENDING),
            pix_evidence(normalized_e2e="0000000000000000"),
            pix_evidence(normalized_e2e="E2EPLACEHOLDER123"),
            pix_evidence(evidence_hash="0" * 64),
            pix_evidence(observed_at=T0 - timedelta(microseconds=1)),
        )
        for evidence in invalid:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                validate_evidence(subject, evidence)

    def test_wise_requires_trusted_profiles_window_signature_and_exact_economics(self) -> None:
        subject = self.subject(PaymentMethod.WISE)
        self.assertIs(validate_evidence(subject, wise_credit()).method, PaymentMethod.WISE)
        non_boolean_signature = wise_credit()
        object.__setattr__(non_boolean_signature, "signature_verified", 1)
        invalid = (
            wise_credit(signer_profile_id="signer:synthetic:other"),
            wise_credit(account_profile_id="account:synthetic:other"),
            wise_credit(amount_minor=12501),
            wise_credit(currency="USD"),
            wise_credit(signature_verified=False),
            non_boolean_signature,
            wise_credit(credited_at=T0 - timedelta(microseconds=1)),
            wise_credit(credited_at=T0 + timedelta(days=3)),
            wise_credit(transaction_fingerprint="0" * 64),
            wise_credit(verification_hash="0" * 64),
        )
        for evidence in invalid:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                validate_evidence(subject, evidence)

    def test_stripe_requires_account_target_event_signature_window_and_economics(self) -> None:
        subject = self.subject(PaymentMethod.STRIPE)
        self.assertIs(validate_evidence(subject, stripe_event()).method, PaymentMethod.STRIPE)
        non_boolean_signature = stripe_event()
        object.__setattr__(non_boolean_signature, "signature_verified", 1)
        invalid = (
            stripe_event(stripe_account_profile_id="stripe-account:synthetic:other"),
            stripe_event(payment_intent_fingerprint="4" * 64),
            stripe_event(amount_minor=12501),
            stripe_event(currency="USD"),
            stripe_event(event_type=StripeEventType.PAYMENT_INTENT_FAILED),
            stripe_event(signature_verified=False),
            non_boolean_signature,
            stripe_event(observed_at=T0 - timedelta(microseconds=1)),
            stripe_event(observed_at=T0 + timedelta(days=3)),
            stripe_event(verification_hash="0" * 64),
        )
        for evidence in invalid:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                validate_evidence(subject, evidence)

    def test_cross_method_evidence_is_rejected(self) -> None:
        matrix = {
            PaymentMethod.PIX: (wise_credit(), stripe_event()),
            PaymentMethod.WISE: (pix_evidence(), stripe_event()),
            PaymentMethod.STRIPE: (pix_evidence(), wise_credit()),
        }
        for method, evidence_items in matrix.items():
            for evidence in evidence_items:
                with self.subTest(method=method, evidence=type(evidence).__name__):
                    with self.assertRaises(ValueError):
                        validate_evidence(self.subject(method), evidence)

    def test_claim_keys_are_global_and_do_not_include_target_or_caller_key(self) -> None:
        pix = pix_evidence()
        wise = wise_credit()
        stripe = stripe_event()
        self.assertEqual(evidence_claim_key(pix), f"pix:{pix.normalized_e2e}")
        self.assertEqual(evidence_claim_key(wise), f"wise:{wise.transaction_fingerprint}")
        self.assertEqual(
            evidence_claim_key(stripe),
            f"stripe:{stripe.stripe_account_profile_id}:{stripe.event_id}",
        )
        for evidence in (pix, wise, stripe):
            claim = evidence_claim_key(evidence)
            self.assertNotIn("target:reservation", claim)
            self.assertNotIn("idempot", claim.casefold())
        with self.assertRaises(TypeError):
            evidence_claim_key(object())

        mutated = pix_evidence()
        object.__setattr__(mutated, "normalized_e2e", "E2EPLACEHOLDER123")
        with self.assertRaises(ValueError):
            evidence_claim_key(mutated)

        other_target = PaymentSubject.from_anchor(
            confirmed_anchor(),
            payment_id="payment:synthetic:2",
            method=PaymentMethod.PIX,
            payment_target_id="target:reservation:synthetic:2",
        )
        first = validate_evidence(self.subject(PaymentMethod.PIX), pix)
        second = validate_evidence(other_target, pix)
        self.assertEqual(first.claim_key, second.claim_key)

    def test_economic_signature_has_independent_known_answer_vector(self) -> None:
        subject = self.subject(PaymentMethod.PIX)
        self.assertEqual(
            subject.economic_signature,
            "9474c681909529cdc58ee743860dcaf0d5a4b1a14b74f30948104b2b815feefb",
        )

    def test_evidence_fields_are_closed_and_contain_no_raw_proof_or_pii(self) -> None:
        expected = {
            PixVisualEvidence: (
                "proof_amount_minor", "proof_currency", "proof_receiver_profile_id",
                "proof_status", "normalized_e2e", "observed_at", "extractor_id",
                "extractor_version", "evidence_hash",
            ),
            VerifiedWiseCredit: (
                "signer_profile_id", "account_profile_id", "amount_minor", "currency",
                "credited_at", "transaction_fingerprint", "payer_fingerprint",
                "reference_fingerprint", "signature_verified", "verification_hash",
            ),
            VerifiedStripeEvent: (
                "stripe_account_profile_id", "event_id", "payment_intent_fingerprint",
                "amount_minor", "currency", "event_type", "signature_verified",
                "observed_at", "verification_hash",
            ),
            VerifiedPaymentEvidence: (
                "payment_id", "payment_version", "economic_signature", "method",
                "claim_key", "evidence_hash", "evidence",
            ),
        }
        for cls, names in expected.items():
            self.assertEqual(tuple(field.name for field in fields(cls)), names)
            lowered = " ".join(names).casefold()
            self.assertNotIn("raw", lowered)
            self.assertNotIn("email", lowered)
            self.assertNotIn("phone", lowered)
            self.assertNotIn("name", lowered)

    def test_evidence_and_verified_wrapper_are_frozen_slotted_and_exact(self) -> None:
        values = (
            pix_evidence(), wise_credit(), stripe_event(),
            validate_evidence(self.subject(PaymentMethod.PIX), pix_evidence()),
        )
        for value in values:
            self.assertFalse(hasattr(value, "__dict__"))
            with self.assertRaises(FrozenInstanceError):
                setattr(value, fields(value)[0].name, "changed")
        with self.assertRaises(TypeError):
            validate_evidence(self.subject(PaymentMethod.PIX), object())

    def test_payment_evidence_union_and_wire_round_trip_are_closed(self) -> None:
        values: tuple[PaymentEvidence, ...] = (
            pix_evidence(), wise_credit(), stripe_event(),
        )
        for value in values:
            wire = to_wire_json(value)
            decoded = from_wire_json(wire, type(value))
            self.assertEqual(decoded, value)
            self.assertIs(type(decoded), type(value))
        mutated = json.loads(to_wire_json(pix_evidence()))
        mutated["data"]["raw_proof"] = "synthetic forbidden payload"
        with self.assertRaises(ValueError):
            from_wire_json(json.dumps(mutated), PixVisualEvidence)

        verified = validate_evidence(
            self.subject(PaymentMethod.STRIPE),
            stripe_event(),
        )
        with self.assertRaises(TypeError):
            to_wire_json(verified)


if __name__ == "__main__":
    unittest.main()
