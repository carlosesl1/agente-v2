"""Closed method-specific payment evidence contracts for Phase 6.

The module is capability-free: it validates already-structured evidence and never
contacts a provider, bank, transport, process, or persistence layer.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import re

from .types import (
    ConfirmedReservationAnchor,
    PaymentMethod,
    PaymentSubject,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_E2E_RE = re.compile(r"^E[0-9]{8}[0-9]{8}[A-Z0-9]{11}$")
_STRIPE_EVENT_RE = re.compile(r"^evt_[A-Za-z0-9]{16,64}$")
_PLACEHOLDER_PARTS = (
    "PLACEHOLDER",
    "EXAMPLE",
    "SAMPLE",
    "DUMMY",
    "FAKE",
    "DEMO",
    "UNKNOWN",
    "TEST",
)


class PixProofStatus(str, Enum):
    PAID = "paid"
    COMPLETED = "completed"
    PENDING = "pending"
    SCHEDULED = "scheduled"


class StripeEventType(str, Enum):
    PAYMENT_INTENT_SUCCEEDED = "payment_intent.succeeded"
    PAYMENT_INTENT_FAILED = "payment_intent.failed"
    PAYMENT_INTENT_PROCESSING = "payment_intent.processing"


def _require_id(value: str, field_name: str) -> str:
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a canonical opaque identifier")
    return value


def _require_hash(value: str, field_name: str) -> str:
    if type(value) is not str or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _require_currency(value: str, field_name: str) -> str:
    if type(value) is not str or not _CURRENCY_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be an uppercase three-letter currency")
    return value


def _require_positive_int(value: int, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be an integer >= 1")
    return value


def _require_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise ValueError(f"{field_name} must be an exact datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _is_high_entropy_digest(value: str) -> bool:
    if type(value) is not str or not _HASH_RE.fullmatch(value):
        return False
    if len(set(value)) < 10:
        return False
    max_period = len(value) // 2
    return not any(
        value == (value[:period] * math.ceil(len(value) / period))[: len(value)]
        for period in range(1, max_period + 1)
    )


def _is_canonical_e2e(value: str) -> bool:
    if type(value) is not str or not _E2E_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value[9:17], "%Y%m%d")
    except ValueError:
        return False
    if value[1:9] == "00000000":
        return False
    variable = value[17:]
    return len(set(variable)) >= 6 and not any(
        marker in variable for marker in _PLACEHOLDER_PARTS
    )


def _is_canonical_stripe_event_id(value: str) -> bool:
    if type(value) is not str or not _STRIPE_EVENT_RE.fullmatch(value):
        return False
    suffix = value[4:]
    return len(set(suffix)) >= 8 and not any(
        marker in suffix.upper() for marker in _PLACEHOLDER_PARTS
    )


def stripe_target_fingerprint(payment_target_id: str) -> str:
    """Return the domain-separated opaque Stripe target binding."""

    target = _require_id(payment_target_id, "payment_target_id")
    return hashlib.sha256(f"stripe-payment-target:{target}".encode("utf-8")).hexdigest()


def wise_target_fingerprint(payment_target_id: str) -> str:
    """Return the domain-separated Wise reference binding for one target."""

    target = _require_id(payment_target_id, "payment_target_id")
    return hashlib.sha256(f"wise-payment-target:{target}".encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PaymentEvidenceTrust:
    pix_receiver_profile_id: str
    wise_signer_profile_id: str
    wise_account_profile_id: str
    stripe_account_profile_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "pix_receiver_profile_id",
            "wise_signer_profile_id",
            "wise_account_profile_id",
            "stripe_account_profile_id",
        ):
            _require_id(getattr(self, field_name), f"payment_evidence_trust.{field_name}")


@dataclass(frozen=True, slots=True)
class PixVisualEvidence:
    proof_amount_minor: int
    proof_currency: str
    proof_receiver_profile_id: str
    proof_status: PixProofStatus
    normalized_e2e: str
    observed_at: datetime
    extractor_id: str
    extractor_version: str
    evidence_hash: str

    def __post_init__(self) -> None:
        _require_positive_int(self.proof_amount_minor, "pix.proof_amount_minor")
        _require_currency(self.proof_currency, "pix.proof_currency")
        _require_id(
            self.proof_receiver_profile_id,
            "pix.proof_receiver_profile_id",
        )
        if type(self.proof_status) is not PixProofStatus:
            raise ValueError("pix.proof_status must be an exact PixProofStatus")
        if not _is_canonical_e2e(self.normalized_e2e):
            raise ValueError("pix.normalized_e2e must use the closed canonical format")
        object.__setattr__(
            self,
            "observed_at",
            _require_utc(self.observed_at, "pix.observed_at"),
        )
        _require_id(self.extractor_id, "pix.extractor_id")
        _require_id(self.extractor_version, "pix.extractor_version")
        _require_hash(self.evidence_hash, "pix.evidence_hash")


@dataclass(frozen=True, slots=True)
class VerifiedWiseCredit:
    signer_profile_id: str
    account_profile_id: str
    amount_minor: int
    currency: str
    credited_at: datetime
    transaction_fingerprint: str
    payer_fingerprint: str | None
    reference_fingerprint: str | None
    signature_verified: bool
    verification_hash: str

    def __post_init__(self) -> None:
        _require_id(self.signer_profile_id, "wise.signer_profile_id")
        _require_id(self.account_profile_id, "wise.account_profile_id")
        _require_positive_int(self.amount_minor, "wise.amount_minor")
        _require_currency(self.currency, "wise.currency")
        object.__setattr__(
            self,
            "credited_at",
            _require_utc(self.credited_at, "wise.credited_at"),
        )
        _require_hash(
            self.transaction_fingerprint,
            "wise.transaction_fingerprint",
        )
        if self.payer_fingerprint is not None:
            _require_hash(self.payer_fingerprint, "wise.payer_fingerprint")
        if self.reference_fingerprint is not None:
            _require_hash(self.reference_fingerprint, "wise.reference_fingerprint")
        if type(self.signature_verified) is not bool:
            raise ValueError("wise.signature_verified must be an exact bool")
        _require_hash(self.verification_hash, "wise.verification_hash")


@dataclass(frozen=True, slots=True)
class VerifiedStripeEvent:
    stripe_account_profile_id: str
    event_id: str
    payment_intent_fingerprint: str
    amount_minor: int
    currency: str
    event_type: StripeEventType
    signature_verified: bool
    observed_at: datetime
    verification_hash: str

    def __post_init__(self) -> None:
        _require_id(
            self.stripe_account_profile_id,
            "stripe.stripe_account_profile_id",
        )
        if not _is_canonical_stripe_event_id(self.event_id):
            raise ValueError("stripe.event_id must use the canonical provider format")
        _require_hash(
            self.payment_intent_fingerprint,
            "stripe.payment_intent_fingerprint",
        )
        _require_positive_int(self.amount_minor, "stripe.amount_minor")
        _require_currency(self.currency, "stripe.currency")
        if type(self.event_type) is not StripeEventType:
            raise ValueError("stripe.event_type must be an exact StripeEventType")
        if type(self.signature_verified) is not bool:
            raise ValueError("stripe.signature_verified must be an exact bool")
        object.__setattr__(
            self,
            "observed_at",
            _require_utc(self.observed_at, "stripe.observed_at"),
        )
        _require_hash(self.verification_hash, "stripe.verification_hash")


PaymentEvidence = PixVisualEvidence | VerifiedWiseCredit | VerifiedStripeEvent


def _pix_evidence_hash(evidence: PixVisualEvidence) -> str:
    return _canonical_digest(
        {
            "type": "pix_visual_evidence",
            "proof_amount_minor": evidence.proof_amount_minor,
            "proof_currency": evidence.proof_currency,
            "proof_receiver_profile_id": evidence.proof_receiver_profile_id,
            "proof_status": evidence.proof_status.value,
            "normalized_e2e": evidence.normalized_e2e,
            "observed_at": evidence.observed_at.isoformat(),
            "extractor_id": evidence.extractor_id,
            "extractor_version": evidence.extractor_version,
        }
    )


def _wise_verification_hash(evidence: VerifiedWiseCredit) -> str:
    return _canonical_digest(
        {
            "type": "verified_wise_credit",
            "signer_profile_id": evidence.signer_profile_id,
            "account_profile_id": evidence.account_profile_id,
            "amount_minor": evidence.amount_minor,
            "currency": evidence.currency,
            "credited_at": evidence.credited_at.isoformat(),
            "transaction_fingerprint": evidence.transaction_fingerprint,
            "payer_fingerprint": evidence.payer_fingerprint,
            "reference_fingerprint": evidence.reference_fingerprint,
            "signature_verified": evidence.signature_verified,
        }
    )


def _stripe_verification_hash(evidence: VerifiedStripeEvent) -> str:
    return _canonical_digest(
        {
            "type": "verified_stripe_event",
            "stripe_account_profile_id": evidence.stripe_account_profile_id,
            "event_id": evidence.event_id,
            "payment_intent_fingerprint": evidence.payment_intent_fingerprint,
            "amount_minor": evidence.amount_minor,
            "currency": evidence.currency,
            "event_type": evidence.event_type.value,
            "signature_verified": evidence.signature_verified,
            "observed_at": evidence.observed_at.isoformat(),
        }
    )


def evidence_claim_key(evidence: PaymentEvidence) -> str:
    """Return a global claim identity independent of target/caller keys."""

    clean = _revalidate_evidence(evidence)
    _require_intrinsic_integrity(clean)
    if type(clean) is PixVisualEvidence:
        return f"pix:{clean.normalized_e2e}"
    if type(clean) is VerifiedWiseCredit:
        return f"wise:{clean.transaction_fingerprint}"
    if type(clean) is VerifiedStripeEvent:
        return f"stripe:{clean.stripe_account_profile_id}:{clean.event_id}"
    raise TypeError("unsupported payment evidence type")  # pragma: no cover


def _require_intrinsic_integrity(evidence: PaymentEvidence) -> None:
    if type(evidence) is PixVisualEvidence:
        if evidence.proof_status not in (PixProofStatus.PAID, PixProofStatus.COMPLETED):
            raise ValueError("Pix claim requires completed/paid evidence")
        if not _is_canonical_e2e(evidence.normalized_e2e):
            raise ValueError("Pix claim requires canonical E2E identity")
        if evidence.evidence_hash != _pix_evidence_hash(evidence):
            raise ValueError("Pix claim evidence hash is not canonical")
        return
    if type(evidence) is VerifiedWiseCredit:
        if evidence.signature_verified is not True:
            raise ValueError("Wise claim requires a verified signature")
        if not _is_high_entropy_digest(evidence.transaction_fingerprint):
            raise ValueError("Wise claim requires a high-entropy transaction digest")
        if evidence.verification_hash != _wise_verification_hash(evidence):
            raise ValueError("Wise claim verification hash is not canonical")
        return
    if type(evidence) is VerifiedStripeEvent:
        if evidence.signature_verified is not True:
            raise ValueError("Stripe claim requires a verified signature")
        if evidence.event_type is not StripeEventType.PAYMENT_INTENT_SUCCEEDED:
            raise ValueError("Stripe claim requires a successful event")
        if not _is_canonical_stripe_event_id(evidence.event_id):
            raise ValueError("Stripe claim requires canonical event identity")
        if evidence.verification_hash != _stripe_verification_hash(evidence):
            raise ValueError("Stripe claim verification hash is not canonical")
        return
    raise TypeError("unsupported payment evidence type")


def _revalidate_subject(subject: PaymentSubject) -> PaymentSubject:
    if type(subject) is not PaymentSubject:
        raise TypeError("subject must be the exact PaymentSubject type")
    anchor = subject.confirmed_reservation_anchor
    if type(anchor) is not ConfirmedReservationAnchor:
        raise ValueError("subject anchor must be exact")
    if (
        type(anchor.confirmed_at) is not datetime
        or anchor.confirmed_at.utcoffset() != timedelta(0)
        or (
            anchor.payment_deadline is not None
            and (
                type(anchor.payment_deadline) is not datetime
                or anchor.payment_deadline.utcoffset() != timedelta(0)
            )
        )
    ):
        raise ValueError("subject anchor timestamps must be canonical UTC")
    clean_anchor = ConfirmedReservationAnchor(
        **{field.name: getattr(anchor, field.name) for field in fields(anchor)}
    )
    clean = PaymentSubject(
        payment_id=subject.payment_id,
        payment_version=subject.payment_version,
        confirmed_reservation_anchor=clean_anchor,
        amount_minor=subject.amount_minor,
        currency=subject.currency,
        receiver_profile_id=subject.receiver_profile_id,
        business_unit=subject.business_unit,
        payment_target_id=subject.payment_target_id,
        method=subject.method,
        economic_signature=subject.economic_signature,
    )
    if clean != subject:
        raise ValueError("payment subject contains noncanonical values")
    return clean


def _revalidate_evidence(evidence: PaymentEvidence) -> PaymentEvidence:
    evidence_type = type(evidence)
    if evidence_type not in (PixVisualEvidence, VerifiedWiseCredit, VerifiedStripeEvent):
        raise TypeError("evidence must be an exact PaymentEvidence type")
    timestamp = (
        evidence.credited_at
        if evidence_type is VerifiedWiseCredit
        else evidence.observed_at
    )
    if type(timestamp) is not datetime or timestamp.utcoffset() != timedelta(0):
        raise ValueError("payment evidence timestamp must be canonical UTC")
    clean = evidence_type(
        **{field.name: getattr(evidence, field.name) for field in fields(evidence)}
    )
    if clean != evidence:
        raise ValueError("payment evidence contains noncanonical values")
    return clean


def _revalidate_trust(trust: PaymentEvidenceTrust) -> PaymentEvidenceTrust:
    if type(trust) is not PaymentEvidenceTrust:
        raise TypeError("trust must be the exact PaymentEvidenceTrust type")
    clean = PaymentEvidenceTrust(
        **{field.name: getattr(trust, field.name) for field in fields(trust)}
    )
    if clean != trust:
        raise ValueError("payment evidence trust contains noncanonical values")
    return clean


def _validate_window(
    subject: PaymentSubject,
    observed_at: datetime,
    *,
    require_deadline: bool,
) -> None:
    anchor = subject.confirmed_reservation_anchor
    if observed_at < anchor.confirmed_at:
        raise ValueError("payment evidence predates the confirmed reservation")
    if require_deadline and anchor.payment_deadline is None:
        raise ValueError("payment evidence requires a configured verification window")
    if anchor.payment_deadline is not None and observed_at > anchor.payment_deadline:
        raise ValueError("payment evidence is outside the configured window")


def _validate_pix(
    subject: PaymentSubject,
    evidence: PixVisualEvidence,
    trust: PaymentEvidenceTrust,
) -> str:
    if evidence.proof_amount_minor != subject.amount_minor:
        raise ValueError("Pix proof amount does not match payment subject")
    if evidence.proof_currency != subject.currency:
        raise ValueError("Pix proof currency does not match payment subject")
    if subject.receiver_profile_id != trust.pix_receiver_profile_id:
        raise ValueError("payment receiver does not match trusted Pix configuration")
    if evidence.proof_receiver_profile_id != trust.pix_receiver_profile_id:
        raise ValueError("Pix receiver profile does not match trusted configuration")
    if evidence.proof_status not in (PixProofStatus.PAID, PixProofStatus.COMPLETED):
        raise ValueError("Pix proof status is not completed/paid")
    if not _is_canonical_e2e(evidence.normalized_e2e):
        raise ValueError("Pix E2E is not canonical")
    if evidence.evidence_hash != _pix_evidence_hash(evidence):
        raise ValueError("Pix evidence hash does not match canonical evidence")
    _validate_window(subject, evidence.observed_at, require_deadline=False)
    return evidence.evidence_hash


def _validate_wise(
    subject: PaymentSubject,
    evidence: VerifiedWiseCredit,
    trust: PaymentEvidenceTrust,
) -> str:
    if evidence.signer_profile_id != trust.wise_signer_profile_id:
        raise ValueError("Wise signer profile does not match trusted configuration")
    if evidence.account_profile_id != trust.wise_account_profile_id:
        raise ValueError("Wise account profile does not match trusted configuration")
    if evidence.amount_minor != subject.amount_minor or evidence.currency != subject.currency:
        raise ValueError("Wise economics do not match payment subject")
    if evidence.signature_verified is not True:
        raise ValueError("Wise evidence requires verified signature")
    if not _is_high_entropy_digest(evidence.transaction_fingerprint):
        raise ValueError("Wise transaction fingerprint has insufficient entropy")
    if evidence.reference_fingerprint != wise_target_fingerprint(
        subject.payment_target_id
    ):
        raise ValueError("Wise credit is ambiguous for the payment target")
    if evidence.verification_hash != _wise_verification_hash(evidence):
        raise ValueError("Wise verification hash does not match canonical evidence")
    _validate_window(subject, evidence.credited_at, require_deadline=True)
    return evidence.verification_hash


def _validate_stripe(
    subject: PaymentSubject,
    evidence: VerifiedStripeEvent,
    trust: PaymentEvidenceTrust,
) -> str:
    if evidence.stripe_account_profile_id != trust.stripe_account_profile_id:
        raise ValueError("Stripe account profile does not match trusted configuration")
    if evidence.payment_intent_fingerprint != stripe_target_fingerprint(
        subject.payment_target_id
    ):
        raise ValueError("Stripe event does not bind the expected payment target")
    if evidence.amount_minor != subject.amount_minor or evidence.currency != subject.currency:
        raise ValueError("Stripe economics do not match payment subject")
    if evidence.event_type is not StripeEventType.PAYMENT_INTENT_SUCCEEDED:
        raise ValueError("Stripe event type is not a successful payment event")
    if evidence.signature_verified is not True:
        raise ValueError("Stripe evidence requires verified signature")
    if evidence.verification_hash != _stripe_verification_hash(evidence):
        raise ValueError("Stripe verification hash does not match canonical evidence")
    _validate_window(subject, evidence.observed_at, require_deadline=True)
    return evidence.verification_hash


@dataclass(frozen=True, slots=True)
class VerifiedPaymentEvidence:
    payment_id: str
    payment_version: int
    economic_signature: str
    method: PaymentMethod
    claim_key: str
    evidence_hash: str
    evidence: PaymentEvidence

    def __post_init__(self) -> None:
        _require_id(self.payment_id, "verified_evidence.payment_id")
        _require_positive_int(
            self.payment_version,
            "verified_evidence.payment_version",
        )
        _require_hash(
            self.economic_signature,
            "verified_evidence.economic_signature",
        )
        if type(self.method) is not PaymentMethod:
            raise ValueError("verified_evidence.method must be an exact PaymentMethod")
        if type(self.evidence) not in (
            PixVisualEvidence,
            VerifiedWiseCredit,
            VerifiedStripeEvent,
        ):
            raise ValueError("verified_evidence.evidence must be an exact evidence type")
        expected_method = {
            PixVisualEvidence: PaymentMethod.PIX,
            VerifiedWiseCredit: PaymentMethod.WISE,
            VerifiedStripeEvent: PaymentMethod.STRIPE,
        }[type(self.evidence)]
        if self.method is not expected_method:
            raise ValueError("verified evidence method does not match evidence type")
        expected_claim = evidence_claim_key(self.evidence)
        if type(self.claim_key) is not str or self.claim_key != expected_claim:
            raise ValueError("verified evidence claim key is not canonical")
        _require_hash(self.evidence_hash, "verified_evidence.evidence_hash")
        expected_hash = {
            PixVisualEvidence: _pix_evidence_hash,
            VerifiedWiseCredit: _wise_verification_hash,
            VerifiedStripeEvent: _stripe_verification_hash,
        }[type(self.evidence)](self.evidence)
        if self.evidence_hash != expected_hash:
            raise ValueError("verified evidence hash does not match its evidence")


def validate_evidence(
    subject: PaymentSubject,
    evidence: PaymentEvidence,
    trust: PaymentEvidenceTrust,
) -> VerifiedPaymentEvidence:
    """Validate exact method evidence against one immutable economic subject."""

    clean_subject = _revalidate_subject(subject)
    clean_evidence = _revalidate_evidence(evidence)
    clean_trust = _revalidate_trust(trust)
    if clean_subject.method is None:
        raise ValueError("payment method must be selected before evidence validation")
    expected_evidence_type = {
        PaymentMethod.PIX: PixVisualEvidence,
        PaymentMethod.WISE: VerifiedWiseCredit,
        PaymentMethod.STRIPE: VerifiedStripeEvent,
    }[clean_subject.method]
    if type(clean_evidence) is not expected_evidence_type:
        raise ValueError("payment evidence type does not match selected method")
    if type(clean_evidence) is PixVisualEvidence:
        evidence_hash = _validate_pix(clean_subject, clean_evidence, clean_trust)
    elif type(clean_evidence) is VerifiedWiseCredit:
        evidence_hash = _validate_wise(clean_subject, clean_evidence, clean_trust)
    elif type(clean_evidence) is VerifiedStripeEvent:
        evidence_hash = _validate_stripe(clean_subject, clean_evidence, clean_trust)
    else:  # pragma: no cover - exact map above is the closed universe
        raise TypeError("unsupported payment evidence type")
    return VerifiedPaymentEvidence(
        payment_id=clean_subject.payment_id,
        payment_version=clean_subject.payment_version,
        economic_signature=clean_subject.economic_signature,
        method=clean_subject.method,
        claim_key=evidence_claim_key(clean_evidence),
        evidence_hash=evidence_hash,
        evidence=clean_evidence,
    )


__all__ = [
    "PaymentEvidenceTrust",
    "PixProofStatus",
    "StripeEventType",
    "PixVisualEvidence",
    "VerifiedWiseCredit",
    "VerifiedStripeEvent",
    "PaymentEvidence",
    "VerifiedPaymentEvidence",
    "stripe_target_fingerprint",
    "wise_target_fingerprint",
    "evidence_claim_key",
    "validate_evidence",
]
