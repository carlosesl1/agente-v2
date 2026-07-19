"""Closed method-specific payment evidence contracts for Phase 6.

The module is capability-free: it validates already-structured evidence and never
contacts a provider, bank, transport, process, or persistence layer.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import re

from .types import (
    BusinessUnit,
    ConfirmedReservationAnchor,
    PaymentEffectPolicy,
    PaymentMethod,
    PaymentStatus,
    PaymentSubject,
    SettlementCertainty,
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


def _require_evidence_claim_key(value: str) -> str:
    if type(value) is not str:
        raise ValueError("evidence claim key must be canonical text")
    if value.startswith("pix:"):
        if not _is_canonical_e2e(value[4:]):
            raise ValueError("Pix evidence claim key is not canonical")
        return value
    if value.startswith("wise:"):
        if not _is_high_entropy_digest(value[5:]):
            raise ValueError("Wise evidence claim key is not canonical")
        return value
    if value.startswith("stripe:"):
        try:
            account_profile_id, event_id = value[7:].rsplit(":", 1)
        except ValueError as exc:
            raise ValueError("Stripe evidence claim key is not canonical") from exc
        _require_id(account_profile_id, "stripe claim account profile")
        if not _is_canonical_stripe_event_id(event_id):
            raise ValueError("Stripe evidence claim event is not canonical")
        return value
    raise ValueError("unknown evidence claim key method")


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


class SettlementOperation(str, Enum):
    REGISTER_AND_CONFIRM = "register_and_confirm"


class PaymentTransitionStatus(str, Enum):
    APPLIED = "applied"
    NOOP = "noop"
    REJECTED = "rejected"
    CONFLICT = "conflict"


class PaymentEventAction(str, Enum):
    HANDLE = "handle"
    REJECT = "reject"
    REPLAY_ONLY = "replay_only"
    TERMINAL_NOOP = "terminal_noop"


class PaymentTransitionReason(str, Enum):
    PAYMENT_OPENED = "payment_opened"
    METHOD_SELECTED = "method_selected"
    FINANCIAL_SUMMARY_RECORDED = "financial_summary_recorded"
    FINANCIAL_CONFIRMATION_RECORDED = "financial_confirmation_recorded"
    EVIDENCE_VERIFIED_AND_QUEUED = "evidence_verified_and_queued"
    SETTLEMENT_STARTED = "settlement_started"
    SETTLEMENT_FINISHED = "settlement_finished"
    PAYMENT_EXPIRED = "payment_expired"
    PAYMENT_CANCELLED = "payment_cancelled"
    IDENTICAL_REPLAY = "identical_replay"
    EVENT_NOT_APPLICABLE = "event_not_applicable"


def _require_canonical_utc(value: datetime, field_name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be a canonical UTC datetime")
    return value


def _stable_id(namespace: str, *parts: object) -> str:
    material = json.dumps(
        [namespace, *parts],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{namespace}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def financial_summary_hash(subject: PaymentSubject) -> str:
    clean = _revalidate_subject(subject)
    if clean.method is None:
        raise ValueError("financial summary requires a selected payment method")
    return _canonical_digest(
        {
            "amount_minor": clean.amount_minor,
            "business_unit": clean.business_unit.value,
            "currency": clean.currency,
            "economic_signature": clean.economic_signature,
            "method": clean.method.value,
            "payment_id": clean.payment_id,
            "payment_target_id": clean.payment_target_id,
            "payment_version": clean.payment_version,
            "receiver_profile_id": clean.receiver_profile_id,
            "type": "financial_summary",
        }
    )


@dataclass(frozen=True, slots=True)
class PaymentMethodSelected:
    event_id: str
    payment_id: str
    method: PaymentMethod
    selected_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.event_id, "payment_method_selected.event_id")
        _require_id(self.payment_id, "payment_method_selected.payment_id")
        if type(self.method) is not PaymentMethod:
            raise ValueError("payment method must be an exact PaymentMethod")
        _require_canonical_utc(self.selected_at, "payment_method_selected.selected_at")


@dataclass(frozen=True, slots=True)
class FinancialSummaryRecorded:
    event_id: str
    subject: PaymentSubject
    summary_hash: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.event_id, "financial_summary.event_id")
        clean_subject = _revalidate_subject(self.subject)
        if clean_subject.method is None:
            raise ValueError("financial summary requires a selected method")
        _require_hash(self.summary_hash, "financial_summary.summary_hash")
        if self.summary_hash != financial_summary_hash(clean_subject):
            raise ValueError("financial summary hash is not canonical")
        _require_canonical_utc(self.recorded_at, "financial_summary.recorded_at")
        if self.recorded_at < clean_subject.confirmed_reservation_anchor.confirmed_at:
            raise ValueError("financial summary predates the reservation anchor")


@dataclass(frozen=True, slots=True)
class FinancialConfirmationReceived:
    event_id: str
    payment_id: str
    payment_version: int
    economic_signature: str
    summary_hash: str
    confirmation_id: str
    confirmed_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.event_id, "financial_confirmation.event_id")
        _require_id(self.payment_id, "financial_confirmation.payment_id")
        _require_positive_int(self.payment_version, "financial_confirmation.payment_version")
        _require_hash(self.economic_signature, "financial_confirmation.economic_signature")
        _require_hash(self.summary_hash, "financial_confirmation.summary_hash")
        _require_id(self.confirmation_id, "financial_confirmation.confirmation_id")
        _require_canonical_utc(self.confirmed_at, "financial_confirmation.confirmed_at")


@dataclass(frozen=True, slots=True)
class PaymentEvidenceRecorded:
    event_id: str
    payment_id: str
    payment_version: int
    economic_signature: str
    evidence: PaymentEvidence
    trust: PaymentEvidenceTrust
    recorded_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.event_id, "payment_evidence_recorded.event_id")
        _require_id(self.payment_id, "payment_evidence_recorded.payment_id")
        _require_positive_int(self.payment_version, "payment_evidence_recorded.payment_version")
        _require_hash(self.economic_signature, "payment_evidence_recorded.economic_signature")
        clean_evidence = _revalidate_evidence(self.evidence)
        _revalidate_trust(self.trust)
        recorded_at = _require_canonical_utc(
            self.recorded_at,
            "payment_evidence_recorded.recorded_at",
        )
        observed_at = (
            clean_evidence.credited_at
            if type(clean_evidence) is VerifiedWiseCredit
            else clean_evidence.observed_at
        )
        if recorded_at < observed_at:
            raise ValueError("payment evidence record predates the evidence")


@dataclass(frozen=True, slots=True)
class SettlementOutcome:
    certainty: SettlementCertainty
    payment_registered: bool
    reservation_target_confirmed: bool
    provider_reference_fingerprint: str | None
    requires_reconciliation: bool
    claim_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.certainty) is not SettlementCertainty:
            raise ValueError("settlement outcome certainty must be exact")
        if type(self.payment_registered) is not bool:
            raise ValueError("payment_registered must be an exact bool")
        if type(self.reservation_target_confirmed) is not bool:
            raise ValueError("reservation_target_confirmed must be an exact bool")
        if self.provider_reference_fingerprint is not None:
            _require_hash(
                self.provider_reference_fingerprint,
                "settlement_outcome.provider_reference_fingerprint",
            )
        if type(self.requires_reconciliation) is not bool:
            raise ValueError("requires_reconciliation must be an exact bool")
        if type(self.claim_evidence) is not tuple:
            raise ValueError("claim_evidence must be an exact tuple")
        for item in self.claim_evidence:
            _require_hash(item, "settlement_outcome.claim_evidence")
        if len(set(self.claim_evidence)) != len(self.claim_evidence):
            raise ValueError("claim_evidence must not contain duplicates")

        if self.certainty is SettlementCertainty.NOT_DISPATCHED:
            if (
                self.payment_registered
                or self.reservation_target_confirmed
                or self.provider_reference_fingerprint is not None
                or self.requires_reconciliation
                or self.claim_evidence
            ):
                raise ValueError("not_dispatched must prove no financial dispatch/effect")
            return
        if not self.claim_evidence:
            raise ValueError("dispatched settlement outcome requires claim evidence")
        if self.certainty is SettlementCertainty.SETTLED:
            if not self.payment_registered or not self.reservation_target_confirmed:
                raise ValueError("settled requires payment and target confirmation")
            if self.provider_reference_fingerprint is None or self.requires_reconciliation:
                raise ValueError("settled requires provider proof without reconciliation")
            return
        if self.certainty is SettlementCertainty.PARTIAL_SETTLEMENT:
            if self.payment_registered == self.reservation_target_confirmed:
                raise ValueError("partial settlement requires exactly one confirmed effect")
            if self.provider_reference_fingerprint is None or not self.requires_reconciliation:
                raise ValueError("partial settlement requires provider proof and reconciliation")
            return
        if self.certainty is SettlementCertainty.DISPATCHED_UNKNOWN:
            if self.payment_registered and self.reservation_target_confirmed:
                raise ValueError("fully confirmed outcome must use settled certainty")
            if not self.requires_reconciliation:
                raise ValueError("dispatched_unknown requires reconciliation")
            return
        if self.certainty is SettlementCertainty.DISPATCHED_NO_EFFECT:
            if self.payment_registered or self.reservation_target_confirmed:
                raise ValueError("dispatched_no_effect cannot claim an applied effect")
            if self.provider_reference_fingerprint is None or not self.requires_reconciliation:
                raise ValueError("dispatched_no_effect requires dispatch proof and reconciliation")
            return
        raise ValueError("unsupported settlement certainty")  # pragma: no cover


def _revalidate_settlement_outcome(value: SettlementOutcome) -> SettlementOutcome:
    if type(value) is not SettlementOutcome:
        raise ValueError("settlement outcome must be exact")
    return SettlementOutcome(
        **{field.name: getattr(value, field.name) for field in fields(SettlementOutcome)}
    )


@dataclass(frozen=True, slots=True)
class PaymentSettlementCommand:
    settlement_command_id: str
    payment_id: str
    payment_version: int
    economic_signature: str
    evidence_claim_key: str
    operation: SettlementOperation
    idempotency_key: str
    canonical_payload: str

    def __post_init__(self) -> None:
        _require_id(self.settlement_command_id, "settlement_command.id")
        _require_id(self.payment_id, "settlement_command.payment_id")
        _require_positive_int(self.payment_version, "settlement_command.payment_version")
        _require_hash(self.economic_signature, "settlement_command.economic_signature")
        _require_evidence_claim_key(self.evidence_claim_key)
        if type(self.operation) is not SettlementOperation:
            raise ValueError("settlement command operation must be exact")
        _require_id(self.idempotency_key, "settlement_command.idempotency_key")
        if self.settlement_command_id != _stable_id(
            "settlement",
            self.payment_id,
            self.payment_version,
            self.economic_signature,
        ):
            raise ValueError("settlement command id is not canonical")
        if self.idempotency_key != _stable_id(
            "payment-idem",
            self.payment_id,
            self.payment_version,
            self.economic_signature,
        ):
            raise ValueError("settlement command idempotency key is not canonical")
        if type(self.canonical_payload) is not str:
            raise ValueError("settlement command payload must be canonical JSON text")
        try:
            payload = json.loads(self.canonical_payload)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("settlement command payload is invalid JSON") from exc
        if type(payload) is not dict or set(payload) != {
            "amount_minor",
            "business_unit",
            "currency",
            "economic_signature",
            "evidence_claim_key",
            "evidence_hash",
            "method",
            "operation",
            "payment_id",
            "payment_target_id",
            "payment_version",
            "receiver_profile_id",
            "schema_version",
        }:
            raise ValueError("settlement command payload fields are not closed")
        if json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) != self.canonical_payload:
            raise ValueError("settlement command payload is not canonical JSON")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("settlement command schema_version must be exact integer 1")
        if (
            type(payload["payment_version"]) is not int
            or payload["payment_version"] < 1
        ):
            raise ValueError("settlement command payload version must be a positive integer")
        if type(payload["amount_minor"]) is not int or payload["amount_minor"] < 1:
            raise ValueError("settlement command payload amount must be a positive integer")
        for field_name in (
            "business_unit",
            "currency",
            "economic_signature",
            "evidence_claim_key",
            "evidence_hash",
            "method",
            "operation",
            "payment_id",
            "payment_target_id",
            "receiver_profile_id",
        ):
            if type(payload[field_name]) is not str:
                raise ValueError(f"settlement command payload {field_name} must be text")
        try:
            business_unit = BusinessUnit(payload["business_unit"])
            method = PaymentMethod(payload["method"])
        except ValueError as exc:
            raise ValueError("settlement command payload enum is invalid") from exc
        if business_unit.value != payload["business_unit"] or method.value != payload["method"]:
            raise ValueError("settlement command payload enum is noncanonical")
        _require_currency(payload["currency"], "settlement command payload currency")
        _require_hash(
            payload["economic_signature"],
            "settlement command payload economic signature",
        )
        _require_hash(payload["evidence_hash"], "settlement command payload evidence hash")
        _require_evidence_claim_key(payload["evidence_claim_key"])
        if not payload["evidence_claim_key"].startswith(f"{method.value}:"):
            raise ValueError("settlement command method does not match evidence claim")
        _require_id(payload["payment_id"], "settlement command payload payment id")
        _require_id(
            payload["payment_target_id"],
            "settlement command payload target id",
        )
        _require_id(
            payload["receiver_profile_id"],
            "settlement command payload receiver id",
        )
        expected_economic_signature = _canonical_digest(
            {
                "amount_minor": payload["amount_minor"],
                "business_unit": payload["business_unit"],
                "currency": payload["currency"],
                "payment_target_id": payload["payment_target_id"],
                "receiver_profile_id": payload["receiver_profile_id"],
            }
        )
        if payload["economic_signature"] != expected_economic_signature:
            raise ValueError("settlement command payload economics are not canonical")
        if (
            payload["payment_id"] != self.payment_id
            or payload["payment_version"] != self.payment_version
            or payload["economic_signature"] != self.economic_signature
            or payload["evidence_claim_key"] != self.evidence_claim_key
            or payload["operation"] != self.operation.value
        ):
            raise ValueError("settlement command payload diverges from command identity")


@dataclass(frozen=True, slots=True)
class SettlementStarted:
    event_id: str
    payment_id: str
    payment_version: int
    economic_signature: str
    settlement_command_id: str
    idempotency_key: str
    started_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.event_id, "settlement_started.event_id")
        _require_id(self.payment_id, "settlement_started.payment_id")
        _require_positive_int(self.payment_version, "settlement_started.payment_version")
        _require_hash(self.economic_signature, "settlement_started.economic_signature")
        _require_id(self.settlement_command_id, "settlement_started.command_id")
        _require_id(self.idempotency_key, "settlement_started.idempotency_key")
        _require_canonical_utc(self.started_at, "settlement_started.started_at")


@dataclass(frozen=True, slots=True)
class SettlementFinished:
    event_id: str
    payment_id: str
    payment_version: int
    economic_signature: str
    settlement_command_id: str
    outcome: SettlementOutcome
    finished_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.event_id, "settlement_finished.event_id")
        _require_id(self.payment_id, "settlement_finished.payment_id")
        _require_positive_int(self.payment_version, "settlement_finished.payment_version")
        _require_hash(self.economic_signature, "settlement_finished.economic_signature")
        _require_id(self.settlement_command_id, "settlement_finished.command_id")
        if type(self.outcome) is not SettlementOutcome:
            raise ValueError("settlement finish requires exact SettlementOutcome")
        _revalidate_settlement_outcome(self.outcome)
        _require_canonical_utc(self.finished_at, "settlement_finished.finished_at")


@dataclass(frozen=True, slots=True)
class PaymentExpired:
    event_id: str
    payment_id: str
    payment_version: int
    economic_signature: str
    expired_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.event_id, "payment_expired.event_id")
        _require_id(self.payment_id, "payment_expired.payment_id")
        _require_positive_int(self.payment_version, "payment_expired.payment_version")
        _require_hash(self.economic_signature, "payment_expired.economic_signature")
        _require_canonical_utc(self.expired_at, "payment_expired.expired_at")


@dataclass(frozen=True, slots=True)
class PaymentCancelled:
    event_id: str
    payment_id: str
    payment_version: int
    economic_signature: str
    cancellation_id: str
    cancelled_at: datetime

    def __post_init__(self) -> None:
        _require_id(self.event_id, "payment_cancelled.event_id")
        _require_id(self.payment_id, "payment_cancelled.payment_id")
        _require_positive_int(self.payment_version, "payment_cancelled.payment_version")
        _require_hash(self.economic_signature, "payment_cancelled.economic_signature")
        _require_id(self.cancellation_id, "payment_cancelled.cancellation_id")
        _require_canonical_utc(self.cancelled_at, "payment_cancelled.cancelled_at")


PaymentEvent = (
    PaymentMethodSelected
    | FinancialSummaryRecorded
    | FinancialConfirmationReceived
    | PaymentEvidenceRecorded
    | SettlementStarted
    | SettlementFinished
    | PaymentExpired
    | PaymentCancelled
)


def _payment_event_time(event: PaymentEvent) -> datetime:
    field_name = {
        PaymentMethodSelected: "selected_at",
        FinancialSummaryRecorded: "recorded_at",
        FinancialConfirmationReceived: "confirmed_at",
        PaymentEvidenceRecorded: "recorded_at",
        SettlementStarted: "started_at",
        SettlementFinished: "finished_at",
        PaymentExpired: "expired_at",
        PaymentCancelled: "cancelled_at",
    }.get(type(event))
    if field_name is None:  # pragma: no cover - closed PaymentEvent universe
        raise TypeError("unknown payment event type")
    return getattr(event, field_name)


@dataclass(frozen=True, slots=True)
class PaymentWorkflow:
    subject: PaymentSubject
    policy: PaymentEffectPolicy
    status: PaymentStatus
    summary: FinancialSummaryRecorded | None
    confirmation: FinancialConfirmationReceived | None
    evidence_record: PaymentEvidenceRecorded | None
    verified_evidence: VerifiedPaymentEvidence | None
    settlement_command: PaymentSettlementCommand | None
    settlement_start: SettlementStarted | None
    settlement_finish: SettlementFinished | None
    expiration: PaymentExpired | None
    cancellation: PaymentCancelled | None
    history: tuple[PaymentEvent, ...]

    def __post_init__(self) -> None:
        clean_subject = _revalidate_subject(self.subject)
        if type(self.policy) is not PaymentEffectPolicy:
            raise ValueError("payment workflow policy must be exact")
        PaymentEffectPolicy(**{field.name: getattr(self.policy, field.name) for field in fields(self.policy)})
        if type(self.status) is not PaymentStatus:
            raise ValueError("payment workflow status must be exact")
        optional_types = (
            (self.summary, FinancialSummaryRecorded),
            (self.confirmation, FinancialConfirmationReceived),
            (self.evidence_record, PaymentEvidenceRecorded),
            (self.verified_evidence, VerifiedPaymentEvidence),
            (self.settlement_command, PaymentSettlementCommand),
            (self.settlement_start, SettlementStarted),
            (self.settlement_finish, SettlementFinished),
            (self.expiration, PaymentExpired),
            (self.cancellation, PaymentCancelled),
        )
        for value, expected in optional_types:
            if value is not None and type(value) is not expected:
                raise ValueError(f"payment workflow {expected.__name__} binding must be exact")
        if type(self.history) is not tuple:
            raise ValueError("payment workflow history must be an exact tuple")
        seen: dict[str, PaymentEvent] = {}
        event_types = get_payment_event_types()
        for event in self.history:
            if type(event) not in event_types:
                raise ValueError("payment workflow history contains unknown event")
            if event.event_id in seen:
                raise ValueError("payment workflow history contains duplicate event id")
            seen[event.event_id] = event
        event_times = tuple(_payment_event_time(event) for event in self.history)
        if any(later < earlier for earlier, later in zip(event_times, event_times[1:])):
            raise ValueError("payment workflow history is not chronological")
        if self.status is PaymentStatus.AWAITING_METHOD and clean_subject.method is not None:
            raise ValueError("awaiting_method workflow cannot have a selected method")
        if self.status not in (
            PaymentStatus.AWAITING_METHOD,
            PaymentStatus.EXPIRED,
            PaymentStatus.CANCELLED,
        ) and clean_subject.method is None:
            raise ValueError("active payment workflow requires a selected method")
        if self.confirmation is not None and self.summary is None:
            raise ValueError("financial confirmation requires a summary")
        if self.summary is not None and self.summary.subject != clean_subject:
            raise ValueError("financial summary does not match current subject")
        if self.confirmation is not None:
            if (
                self.confirmation.payment_id != clean_subject.payment_id
                or self.confirmation.payment_version != clean_subject.payment_version
                or self.confirmation.economic_signature != clean_subject.economic_signature
                or self.confirmation.summary_hash != self.summary.summary_hash
                or self.confirmation.confirmed_at < self.summary.recorded_at
            ):
                raise ValueError("financial confirmation is stale or noncanonical")
        if self.evidence_record is not None:
            if (
                self.evidence_record.payment_id != clean_subject.payment_id
                or self.evidence_record.payment_version != clean_subject.payment_version
                or self.evidence_record.economic_signature != clean_subject.economic_signature
                or self.confirmation is None
                or self.evidence_record.recorded_at < self.confirmation.confirmed_at
            ):
                raise ValueError("payment evidence record is stale or noncanonical")
        if self.verified_evidence is not None:
            if self.evidence_record is None:
                raise ValueError("verified evidence requires its raw record")
            expected_verified = validate_evidence(
                clean_subject,
                self.evidence_record.evidence,
                self.evidence_record.trust,
            )
            if expected_verified != self.verified_evidence:
                raise ValueError("verified evidence is not derived from workflow inputs")
        if self.settlement_command is not None:
            if self.verified_evidence is None:
                raise ValueError("settlement command requires verified evidence")
            expected_command = _settlement_command_for(clean_subject, self.verified_evidence)
            if expected_command != self.settlement_command:
                raise ValueError("settlement command is not canonical for the workflow")
        if self.settlement_start is not None and self.settlement_command is None:
            raise ValueError("settlement start requires a command")
        if self.settlement_start is not None:
            if (
                self.settlement_start.payment_id != clean_subject.payment_id
                or self.settlement_start.payment_version != clean_subject.payment_version
                or self.settlement_start.economic_signature != clean_subject.economic_signature
                or self.settlement_start.settlement_command_id
                != self.settlement_command.settlement_command_id
                or self.settlement_start.idempotency_key
                != self.settlement_command.idempotency_key
                or (
                    self.evidence_record is not None
                    and self.settlement_start.started_at < self.evidence_record.recorded_at
                )
            ):
                raise ValueError("settlement start does not match workflow command")
        if self.settlement_finish is not None and self.settlement_command is None:
            raise ValueError("settlement finish requires a command")
        if self.settlement_finish is not None:
            if (
                self.settlement_finish.outcome.certainty
                is not SettlementCertainty.NOT_DISPATCHED
                and self.settlement_start is None
            ):
                raise ValueError("dispatched settlement finish requires a dispatch fence")
            if (
                self.settlement_finish.payment_id != clean_subject.payment_id
                or self.settlement_finish.payment_version != clean_subject.payment_version
                or self.settlement_finish.economic_signature != clean_subject.economic_signature
                or self.settlement_finish.settlement_command_id
                != self.settlement_command.settlement_command_id
                or (
                    self.settlement_start is not None
                    and self.settlement_finish.finished_at < self.settlement_start.started_at
                )
            ):
                raise ValueError("settlement finish does not match workflow command")
            from .projection import project_settlement_outcome

            expected_status = project_settlement_outcome(
                self.settlement_finish.outcome,
                dispatch_fenced=self.settlement_start is not None,
            )
            if self.status is not expected_status:
                raise ValueError("payment status does not match settlement outcome")
        if self.status is PaymentStatus.PAID:
            if (
                self.settlement_finish is None
                or self.settlement_finish.outcome.certainty is not SettlementCertainty.SETTLED
            ):
                raise ValueError("paid workflow requires a settled outcome")
        if self.status is PaymentStatus.EVIDENCE_VERIFIED:
            raise ValueError("evidence_verified is not a reachable persisted status")
        if self.expiration is not None and self.cancellation is not None:
            raise ValueError("payment workflow cannot be expired and cancelled")
        if self.expiration is not None and (
            self.expiration.payment_id != clean_subject.payment_id
            or self.expiration.payment_version != clean_subject.payment_version
            or self.expiration.economic_signature != clean_subject.economic_signature
        ):
            raise ValueError("payment expiration is stale")
        if self.cancellation is not None and (
            self.cancellation.payment_id != clean_subject.payment_id
            or self.cancellation.payment_version != clean_subject.payment_version
            or self.cancellation.economic_signature != clean_subject.economic_signature
        ):
            raise ValueError("payment cancellation is stale")
        for bound_event in (
            self.summary,
            self.confirmation,
            self.evidence_record,
            self.settlement_start,
            self.settlement_finish,
            self.expiration,
            self.cancellation,
        ):
            if bound_event is not None and bound_event not in self.history:
                raise ValueError("payment workflow binding is absent from event history")

        command_statuses = (
            PaymentStatus.SETTLEMENT_QUEUED,
            PaymentStatus.SETTLING,
            PaymentStatus.PAID,
            PaymentStatus.RETRYABLE,
            PaymentStatus.MANUAL_REVIEW,
        )
        if self.status in command_statuses and (
            self.summary is None
            or self.confirmation is None
            or self.evidence_record is None
            or self.verified_evidence is None
            or self.settlement_command is None
        ):
            raise ValueError("post-evidence status requires complete financial provenance")
        if self.status is PaymentStatus.SETTLEMENT_QUEUED and (
            self.settlement_start is not None or self.settlement_finish is not None
        ):
            raise ValueError("settlement_queued cannot already contain dispatch outcome")
        if self.status is PaymentStatus.SETTLING and (
            self.settlement_start is None or self.settlement_finish is not None
        ):
            raise ValueError("settling requires a start and no finish")
        if self.status in (
            PaymentStatus.PAID,
            PaymentStatus.RETRYABLE,
            PaymentStatus.MANUAL_REVIEW,
        ) and self.settlement_finish is None:
            raise ValueError("terminal settlement projection requires an outcome")
        if self.status is PaymentStatus.AWAITING_EVIDENCE and (
            self.summary is None
            or self.confirmation is None
            or self.evidence_record is not None
            or self.settlement_command is not None
        ):
            raise ValueError("awaiting_evidence requires confirmation and no evidence command")
        if self.status is PaymentStatus.EXPIRED and self.expiration is None:
            raise ValueError("expired workflow requires expiration event")
        if self.status is PaymentStatus.CANCELLED and self.cancellation is None:
            raise ValueError("cancelled workflow requires cancellation event")
        expected_from_history = _replay_payment_history(clean_subject, self.history)
        actual = (
            clean_subject,
            self.status,
            self.summary,
            self.confirmation,
            self.evidence_record,
            self.verified_evidence,
            self.settlement_command,
            self.settlement_start,
            self.settlement_finish,
            self.expiration,
            self.cancellation,
        )
        if actual != expected_from_history:
            raise ValueError("payment workflow is not reachable by canonical reducer replay")


@dataclass(frozen=True, slots=True)
class PaymentTransition:
    state: PaymentWorkflow
    status: PaymentTransitionStatus
    reason: PaymentTransitionReason
    events: tuple[PaymentEvent, ...]
    commands: tuple[PaymentSettlementCommand, ...]

    def __post_init__(self) -> None:
        if type(self.state) is not PaymentWorkflow:
            raise ValueError("payment transition state must be exact")
        if type(self.status) is not PaymentTransitionStatus:
            raise ValueError("payment transition status must be exact")
        if type(self.reason) is not PaymentTransitionReason:
            raise ValueError("payment transition reason must be exact")
        if type(self.events) is not tuple or any(
            type(event) not in get_payment_event_types() for event in self.events
        ):
            raise ValueError("payment transition events must be exact")
        if type(self.commands) is not tuple or any(
            type(command) is not PaymentSettlementCommand for command in self.commands
        ):
            raise ValueError("payment transition commands must be exact")


def get_payment_event_types() -> tuple[type, ...]:
    return (
        PaymentMethodSelected,
        FinancialSummaryRecorded,
        FinancialConfirmationReceived,
        PaymentEvidenceRecorded,
        SettlementStarted,
        SettlementFinished,
        PaymentExpired,
        PaymentCancelled,
    )


_PAYMENT_EVENT_ACTIONS: dict[PaymentStatus, tuple[PaymentEventAction, ...]] = {
    PaymentStatus.AWAITING_METHOD: (
        PaymentEventAction.HANDLE,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.HANDLE,
        PaymentEventAction.HANDLE,
    ),
    PaymentStatus.AWAITING_FINANCIAL_CONFIRMATION: (
        PaymentEventAction.HANDLE,
        PaymentEventAction.HANDLE,
        PaymentEventAction.HANDLE,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.HANDLE,
        PaymentEventAction.HANDLE,
    ),
    PaymentStatus.AWAITING_EVIDENCE: (
        PaymentEventAction.HANDLE,
        PaymentEventAction.HANDLE,
        PaymentEventAction.REJECT,
        PaymentEventAction.HANDLE,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.HANDLE,
        PaymentEventAction.HANDLE,
    ),
    PaymentStatus.EVIDENCE_VERIFIED: (PaymentEventAction.REJECT,) * 8,
    PaymentStatus.SETTLEMENT_QUEUED: (
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.HANDLE,
        PaymentEventAction.HANDLE,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
    ),
    PaymentStatus.SETTLING: (
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
        PaymentEventAction.REPLAY_ONLY,
        PaymentEventAction.HANDLE,
        PaymentEventAction.REJECT,
        PaymentEventAction.REJECT,
    ),
    PaymentStatus.PAID: (PaymentEventAction.TERMINAL_NOOP,) * 8,
    PaymentStatus.RETRYABLE: (PaymentEventAction.REPLAY_ONLY,) * 8,
    PaymentStatus.MANUAL_REVIEW: (PaymentEventAction.TERMINAL_NOOP,) * 8,
    PaymentStatus.EXPIRED: (PaymentEventAction.TERMINAL_NOOP,) * 8,
    PaymentStatus.CANCELLED: (PaymentEventAction.TERMINAL_NOOP,) * 8,
}


def payment_transition_matrix(
) -> tuple[tuple[PaymentStatus, type, PaymentEventAction], ...]:
    return tuple(
        (status, event_type, action)
        for status in PaymentStatus
        for event_type, action in zip(
            get_payment_event_types(),
            _PAYMENT_EVENT_ACTIONS[status],
        )
    )


def _event_action(status: PaymentStatus, event_type: type) -> PaymentEventAction:
    try:
        index = get_payment_event_types().index(event_type)
    except ValueError as exc:  # pragma: no cover - exact input checked by reducer
        raise TypeError("unknown payment event type") from exc
    return _PAYMENT_EVENT_ACTIONS[status][index]


def _settlement_payload(
    subject: PaymentSubject,
    verified: VerifiedPaymentEvidence,
) -> str:
    return json.dumps(
        {
            "amount_minor": subject.amount_minor,
            "business_unit": subject.business_unit.value,
            "currency": subject.currency,
            "economic_signature": subject.economic_signature,
            "evidence_claim_key": verified.claim_key,
            "evidence_hash": verified.evidence_hash,
            "method": verified.method.value,
            "operation": SettlementOperation.REGISTER_AND_CONFIRM.value,
            "payment_id": subject.payment_id,
            "payment_target_id": subject.payment_target_id,
            "payment_version": subject.payment_version,
            "receiver_profile_id": subject.receiver_profile_id,
            "schema_version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _settlement_command_for(
    subject: PaymentSubject,
    verified: VerifiedPaymentEvidence,
) -> PaymentSettlementCommand:
    return PaymentSettlementCommand(
        settlement_command_id=_stable_id(
            "settlement",
            subject.payment_id,
            subject.payment_version,
            subject.economic_signature,
        ),
        payment_id=subject.payment_id,
        payment_version=subject.payment_version,
        economic_signature=subject.economic_signature,
        evidence_claim_key=verified.claim_key,
        operation=SettlementOperation.REGISTER_AND_CONFIRM,
        idempotency_key=_stable_id(
            "payment-idem",
            subject.payment_id,
            subject.payment_version,
            subject.economic_signature,
        ),
        canonical_payload=_settlement_payload(subject, verified),
    )


def _binding(event: PaymentEvent, subject: PaymentSubject) -> None:
    if type(event) is FinancialSummaryRecorded:
        candidate = _revalidate_subject(event.subject)
        if candidate.payment_id != subject.payment_id:
            raise ValueError("financial summary belongs to another payment")
        if candidate.confirmed_reservation_anchor != subject.confirmed_reservation_anchor:
            raise ValueError("financial summary changed the reservation anchor")
        if candidate.method is not subject.method:
            raise ValueError("financial summary method is stale")
        if candidate.payment_version < subject.payment_version:
            raise ValueError("financial summary uses a stale payment version")
        if (
            candidate.payment_version == subject.payment_version
            and candidate.economic_signature != subject.economic_signature
        ):
            raise ValueError("financial summary diverges within the current version")
        return
    if event.payment_id != subject.payment_id:
        raise ValueError("payment event belongs to another payment")
    if type(event) is PaymentMethodSelected:
        return
    if event.payment_version != subject.payment_version:
        raise ValueError("payment event uses a stale payment version")
    if event.economic_signature != subject.economic_signature:
        raise ValueError("payment event uses a stale economic signature")


def _replay_payment_history(
    final_subject: PaymentSubject,
    history: tuple[PaymentEvent, ...],
) -> tuple[object, ...]:
    subject = PaymentSubject.from_anchor(
        final_subject.confirmed_reservation_anchor,
        payment_id=final_subject.payment_id,
    )
    status = PaymentStatus.AWAITING_METHOD
    summary = None
    confirmation = None
    evidence_record = None
    verified_evidence = None
    settlement_command = None
    settlement_start = None
    settlement_finish = None
    expiration = None
    cancellation = None

    for raw_event in history:
        event_type = type(raw_event)
        if event_type not in get_payment_event_types():
            raise ValueError("payment history contains an unknown event")
        event = event_type(
            **{field.name: getattr(raw_event, field.name) for field in fields(event_type)}
        )
        _binding(event, subject)
        if _event_action(status, event_type) is not PaymentEventAction.HANDLE:
            raise ValueError("payment history contains an event the reducer would not apply")

        if event_type is PaymentMethodSelected:
            if event.selected_at < subject.confirmed_reservation_anchor.confirmed_at:
                raise ValueError("method selection predates reservation confirmation")
            if subject.method is event.method and summary is None:
                raise ValueError("payment history contains a method no-op")
            subject = replace(subject, method=event.method)
            status = PaymentStatus.AWAITING_FINANCIAL_CONFIRMATION
            summary = None
            confirmation = None
            evidence_record = None
            verified_evidence = None
            settlement_command = None
            settlement_start = None
            settlement_finish = None
            continue

        if event_type is FinancialSummaryRecorded:
            candidate = _revalidate_subject(event.subject)
            if candidate.economic_signature == subject.economic_signature:
                if candidate.payment_version != subject.payment_version:
                    raise ValueError("unchanged economics cannot change payment version")
            elif candidate.payment_version != subject.payment_version + 1:
                raise ValueError("economic changes require the next payment version")
            if summary is not None and summary.subject == candidate:
                raise ValueError("payment history contains a summary no-op")
            subject = candidate
            status = PaymentStatus.AWAITING_FINANCIAL_CONFIRMATION
            summary = event
            confirmation = None
            evidence_record = None
            verified_evidence = None
            settlement_command = None
            settlement_start = None
            settlement_finish = None
            continue

        if event_type is FinancialConfirmationReceived:
            if summary is None or event.summary_hash != summary.summary_hash:
                raise ValueError("payment history contains a stale financial confirmation")
            if event.confirmed_at < summary.recorded_at:
                raise ValueError("financial confirmation predates its summary")
            status = PaymentStatus.AWAITING_EVIDENCE
            confirmation = event
            continue

        if event_type is PaymentEvidenceRecorded:
            if confirmation is None or event.recorded_at < confirmation.confirmed_at:
                raise ValueError("payment history contains evidence before confirmation")
            verified_evidence = validate_evidence(subject, event.evidence, event.trust)
            settlement_command = _settlement_command_for(subject, verified_evidence)
            status = PaymentStatus.SETTLEMENT_QUEUED
            evidence_record = event
            continue

        if event_type is SettlementStarted:
            if settlement_command is None:
                raise ValueError("payment history starts settlement without a command")
            if (
                event.settlement_command_id != settlement_command.settlement_command_id
                or event.idempotency_key != settlement_command.idempotency_key
                or (
                    evidence_record is not None
                    and event.started_at < evidence_record.recorded_at
                )
            ):
                raise ValueError("payment history contains a divergent settlement start")
            status = PaymentStatus.SETTLING
            settlement_start = event
            continue

        if event_type is SettlementFinished:
            if (
                settlement_command is None
                or event.settlement_command_id
                != settlement_command.settlement_command_id
            ):
                raise ValueError("payment history finishes an unknown settlement command")
            if (
                event.outcome.certainty is not SettlementCertainty.NOT_DISPATCHED
                and settlement_start is None
            ):
                raise ValueError("dispatched settlement history requires a dispatch fence")
            boundary_time = (
                settlement_start.started_at
                if settlement_start is not None
                else evidence_record.recorded_at if evidence_record is not None else None
            )
            if boundary_time is None or event.finished_at < boundary_time:
                raise ValueError("settlement finish predates its boundary event")
            from .projection import project_settlement_outcome

            status = project_settlement_outcome(
                event.outcome,
                dispatch_fenced=settlement_start is not None,
            )
            settlement_finish = event
            continue

        if event_type is PaymentExpired:
            deadline = subject.confirmed_reservation_anchor.payment_deadline
            if deadline is None or event.expired_at < deadline:
                raise ValueError("payment history expires before its configured deadline")
            status = PaymentStatus.EXPIRED
            expiration = event
            continue

        if event_type is PaymentCancelled:
            if event.cancelled_at < subject.confirmed_reservation_anchor.confirmed_at:
                raise ValueError("payment history cancellation predates confirmation")
            status = PaymentStatus.CANCELLED
            cancellation = event
            continue

        raise TypeError("unsupported payment history event")  # pragma: no cover

    return (
        subject,
        status,
        summary,
        confirmation,
        evidence_record,
        verified_evidence,
        settlement_command,
        settlement_start,
        settlement_finish,
        expiration,
        cancellation,
    )


def _noop(state: PaymentWorkflow) -> PaymentTransition:
    return PaymentTransition(
        state=state,
        status=PaymentTransitionStatus.NOOP,
        reason=PaymentTransitionReason.IDENTICAL_REPLAY,
        events=(),
        commands=(),
    )


def _not_applicable(state: PaymentWorkflow) -> PaymentTransition:
    return PaymentTransition(
        state=state,
        status=PaymentTransitionStatus.NOOP,
        reason=PaymentTransitionReason.EVENT_NOT_APPLICABLE,
        events=(),
        commands=(),
    )


def _applied(
    state: PaymentWorkflow,
    event: PaymentEvent,
    reason: PaymentTransitionReason,
    *,
    commands: tuple[PaymentSettlementCommand, ...] = (),
    **changes: object,
) -> PaymentTransition:
    next_state = replace(
        state,
        history=(*state.history, event),
        **changes,
    )
    return PaymentTransition(
        state=next_state,
        status=PaymentTransitionStatus.APPLIED,
        reason=reason,
        events=(event,),
        commands=commands,
    )


def new_payment(
    anchor: ConfirmedReservationAnchor,
    policy: PaymentEffectPolicy,
) -> PaymentTransition:
    if type(anchor) is not ConfirmedReservationAnchor:
        raise TypeError("payment bootstrap requires an exact ConfirmedReservationAnchor")
    if type(policy) is not PaymentEffectPolicy:
        raise TypeError("payment bootstrap requires an exact PaymentEffectPolicy")
    clean_policy = PaymentEffectPolicy(
        **{field.name: getattr(policy, field.name) for field in fields(policy)}
    )
    payment_id = _stable_id(
        "payment",
        anchor.reservation_workflow_id,
        anchor.payment_target_id,
    )
    subject = PaymentSubject.from_anchor(anchor, payment_id=payment_id)
    state = PaymentWorkflow(
        subject=subject,
        policy=clean_policy,
        status=PaymentStatus.AWAITING_METHOD,
        summary=None,
        confirmation=None,
        evidence_record=None,
        verified_evidence=None,
        settlement_command=None,
        settlement_start=None,
        settlement_finish=None,
        expiration=None,
        cancellation=None,
        history=(),
    )
    return PaymentTransition(
        state=state,
        status=PaymentTransitionStatus.APPLIED,
        reason=PaymentTransitionReason.PAYMENT_OPENED,
        events=(),
        commands=(),
    )


def reduce_payment(state: PaymentWorkflow, event: PaymentEvent) -> PaymentTransition:
    if type(state) is not PaymentWorkflow:
        raise TypeError("state must be an exact PaymentWorkflow")
    if type(event) not in get_payment_event_types():
        raise TypeError("event must be an exact PaymentEvent")

    # Binding is checked before replay so an event from an older economic
    # version cannot become a no-op certificate for the current subject.
    _binding(event, state.subject)

    for previous in state.history:
        if previous.event_id == event.event_id:
            if previous == event:
                return _noop(state)
            raise ValueError("payment event id replay has divergent payload")

    if type(event) is PaymentEvidenceRecorded and state.evidence_record is not None:
        canonical_replay = replace(event, event_id=state.evidence_record.event_id)
        if canonical_replay == state.evidence_record:
            expected = validate_evidence(state.subject, event.evidence, event.trust)
            if expected != state.verified_evidence:
                raise ValueError("payment evidence replay no longer validates")
            return _noop(state)
        raise ValueError("payment evidence replay has divergent payload")

    action = _event_action(state.status, type(event))
    if action is PaymentEventAction.TERMINAL_NOOP:
        return _not_applicable(state)
    if action is PaymentEventAction.REPLAY_ONLY:
        raise ValueError("payment status accepts only an identical replay")
    if action is PaymentEventAction.REJECT:
        raise ValueError("payment event is not applicable to current status")

    if type(event) is PaymentMethodSelected:
        if event.selected_at < state.subject.confirmed_reservation_anchor.confirmed_at:
            raise ValueError("method selection predates reservation confirmation")
        if state.subject.method is event.method and state.summary is None:
            return _not_applicable(state)
        subject = replace(state.subject, method=event.method)
        return _applied(
            state,
            event,
            PaymentTransitionReason.METHOD_SELECTED,
            subject=subject,
            status=PaymentStatus.AWAITING_FINANCIAL_CONFIRMATION,
            summary=None,
            confirmation=None,
            evidence_record=None,
            verified_evidence=None,
            settlement_command=None,
            settlement_start=None,
            settlement_finish=None,
        )

    if type(event) is FinancialSummaryRecorded:
        candidate = _revalidate_subject(event.subject)
        current = state.subject
        if candidate.payment_id != current.payment_id:
            raise ValueError("financial summary belongs to another payment")
        if candidate.confirmed_reservation_anchor != current.confirmed_reservation_anchor:
            raise ValueError("financial summary changed the reservation anchor")
        if candidate.method is not current.method:
            raise ValueError("method changes require PaymentMethodSelected")
        if candidate.economic_signature == current.economic_signature:
            if candidate.payment_version != current.payment_version:
                raise ValueError("unchanged economics cannot change payment version")
        elif candidate.payment_version != current.payment_version + 1:
            raise ValueError("economic changes require the next payment version")
        if state.summary is not None and state.summary.subject == candidate:
            return _not_applicable(state)
        return _applied(
            state,
            event,
            PaymentTransitionReason.FINANCIAL_SUMMARY_RECORDED,
            subject=candidate,
            status=PaymentStatus.AWAITING_FINANCIAL_CONFIRMATION,
            summary=event,
            confirmation=None,
            evidence_record=None,
            verified_evidence=None,
            settlement_command=None,
            settlement_start=None,
            settlement_finish=None,
        )

    if type(event) is FinancialConfirmationReceived:
        if state.summary is None:
            raise ValueError("financial confirmation requires a recorded summary")
        if event.summary_hash != state.summary.summary_hash:
            raise ValueError("financial confirmation references a stale summary")
        if event.confirmed_at < state.summary.recorded_at:
            raise ValueError("financial confirmation predates its summary")
        return _applied(
            state,
            event,
            PaymentTransitionReason.FINANCIAL_CONFIRMATION_RECORDED,
            status=PaymentStatus.AWAITING_EVIDENCE,
            confirmation=event,
        )

    if type(event) is PaymentEvidenceRecorded:
        if state.confirmation is None:
            raise ValueError("payment evidence requires financial confirmation")
        if event.recorded_at < state.confirmation.confirmed_at:
            raise ValueError("payment evidence record predates financial confirmation")
        if state.evidence_record is not None:
            if state.evidence_record.evidence == event.evidence:
                return _not_applicable(state)
            raise ValueError("payment evidence replay diverges from the claimed evidence")
        verified = validate_evidence(state.subject, event.evidence, event.trust)
        command = _settlement_command_for(state.subject, verified)
        return _applied(
            state,
            event,
            PaymentTransitionReason.EVIDENCE_VERIFIED_AND_QUEUED,
            commands=(command,),
            status=PaymentStatus.SETTLEMENT_QUEUED,
            evidence_record=event,
            verified_evidence=verified,
            settlement_command=command,
        )

    if type(event) is SettlementStarted:
        command = state.settlement_command
        if command is None:
            raise ValueError("settlement start requires a queued command")
        if (
            event.settlement_command_id != command.settlement_command_id
            or event.idempotency_key != command.idempotency_key
        ):
            raise ValueError("settlement start does not match queued command")
        if state.evidence_record is not None and event.started_at < state.evidence_record.recorded_at:
            raise ValueError("settlement start predates evidence recording")
        return _applied(
            state,
            event,
            PaymentTransitionReason.SETTLEMENT_STARTED,
            status=PaymentStatus.SETTLING,
            settlement_start=event,
        )

    if type(event) is SettlementFinished:
        command = state.settlement_command
        if command is None or event.settlement_command_id != command.settlement_command_id:
            raise ValueError("settlement finish does not match queued command")
        if event.outcome.certainty is not SettlementCertainty.NOT_DISPATCHED:
            if state.settlement_start is None:
                raise ValueError("dispatched settlement outcome requires a dispatch fence")
        boundary_time = (
            state.settlement_start.started_at
            if state.settlement_start is not None
            else state.evidence_record.recorded_at if state.evidence_record is not None else None
        )
        if boundary_time is not None and event.finished_at < boundary_time:
            raise ValueError("settlement finish predates its boundary event")
        from .projection import project_settlement_outcome

        projected_status = project_settlement_outcome(
            event.outcome,
            dispatch_fenced=state.settlement_start is not None,
        )
        return _applied(
            state,
            event,
            PaymentTransitionReason.SETTLEMENT_FINISHED,
            status=projected_status,
            settlement_finish=event,
        )

    if type(event) is PaymentExpired:
        deadline = state.subject.confirmed_reservation_anchor.payment_deadline
        if deadline is None or event.expired_at < deadline:
            raise ValueError("payment cannot expire before its configured deadline")
        return _applied(
            state,
            event,
            PaymentTransitionReason.PAYMENT_EXPIRED,
            status=PaymentStatus.EXPIRED,
            expiration=event,
        )

    if type(event) is PaymentCancelled:
        if event.cancelled_at < state.subject.confirmed_reservation_anchor.confirmed_at:
            raise ValueError("payment cancellation predates reservation confirmation")
        return _applied(
            state,
            event,
            PaymentTransitionReason.PAYMENT_CANCELLED,
            status=PaymentStatus.CANCELLED,
            cancellation=event,
        )

    raise TypeError("unsupported payment event")  # pragma: no cover


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
    "SettlementOperation",
    "PaymentEventAction",
    "PaymentTransitionStatus",
    "PaymentTransitionReason",
    "PaymentMethodSelected",
    "FinancialSummaryRecorded",
    "FinancialConfirmationReceived",
    "PaymentEvidenceRecorded",
    "SettlementStarted",
    "SettlementFinished",
    "PaymentExpired",
    "PaymentCancelled",
    "PaymentEvent",
    "PaymentSettlementCommand",
    "SettlementOutcome",
    "PaymentWorkflow",
    "PaymentTransition",
    "financial_summary_hash",
    "payment_transition_matrix",
    "new_payment",
    "reduce_payment",
]
