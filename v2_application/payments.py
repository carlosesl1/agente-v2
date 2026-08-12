"""Payment planning and method initiation without settlement authority."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from reservation_followup import PaymentEvidenceRecorded
from reservation_followup.payment import PixVisualEvidence
from reservation_followup.sqlite_store import (
    IdentityConflict as FollowupIdentityConflict,
    SQLiteFollowupUnitOfWork,
)
from v2_contracts.localization import CustomerLanguage
from v2_contracts.payments import (
    BusinessUnit,
    CheckoutService,
    DueKind,
    PaymentDisplayDetails,
    PaymentInstruction,
    PaymentMethod,
    PaymentMethodOffer,
    PaymentObligation,
    PaymentPlan,
    PaymentSelection,
    ReservationPaymentContext,
    StripeCreationStep,
    StripePaymentLink,
    StripeReconciliationResult,
    StripeStepReceipt,
    StripeStepStatus,
)


class PaymentService:
    def __init__(self, *, stripe, wise, pix) -> None:
        if not callable(getattr(stripe, "create_link", None)):
            raise TypeError("stripe must implement create_link")
        if not callable(getattr(wise, "instruction", None)):
            raise TypeError("wise must implement instruction")
        if not callable(getattr(pix, "instruction", None)):
            raise TypeError("pix must implement instruction")
        self._stripe = stripe
        self._wise = wise
        self._pix = pix

    def plan(self, context: ReservationPaymentContext) -> PaymentPlan:
        if type(context) is not ReservationPaymentContext:
            raise TypeError("context must be exact ReservationPaymentContext")
        due_kind = (
            DueKind.DUE_AT_CHECKIN
            if context.business_unit is BusinessUnit.HOSTEL
            and context.guest_country_code != "BR"
            else DueKind.PREPAYMENT
        )
        obligation = PaymentObligation(
            payment_id=context.payment_id,
            reservation_anchor_id=context.reservation_anchor_id,
            business_unit=context.business_unit,
            amount_minor=context.amount_minor,
            currency=context.currency,
            due_kind=due_kind,
            economic_version=context.economic_version,
            receiver_profile_id=context.receiver_profile_id,
            display_details=context.display_details,
        )
        effects = (
            ()
            if due_kind is DueKind.DUE_AT_CHECKIN
            else (PaymentMethod.STRIPE, PaymentMethod.WISE, PaymentMethod.PIX)
        )
        return PaymentPlan(obligation, effects)

    def initiate(
        self,
        obligation: PaymentObligation,
        method: PaymentMethod,
        *,
        initiation_id: str = "",
        journal_worker_id: str = "",
        journal_fencing_token: int = 0,
        subscriber_id: str = "",
    ) -> PaymentMethodOffer:
        if type(obligation) is not PaymentObligation:
            raise TypeError("obligation must be exact PaymentObligation")
        if type(method) is not PaymentMethod:
            raise TypeError("method must be exact PaymentMethod")
        if obligation.due_kind is DueKind.DUE_AT_CHECKIN:
            raise ValueError("due-at-checkin obligation has no initiation effect")
        if method is PaymentMethod.STRIPE:
            if initiation_id:
                journaled_create = getattr(
                    self._stripe,
                    "create_link_journaled",
                    None,
                )
                if callable(journaled_create):
                    journal_kwargs = {
                        "initiation_id": initiation_id,
                        "journal_worker_id": journal_worker_id,
                        "journal_fencing_token": journal_fencing_token,
                    }
                    if subscriber_id:
                        journal_kwargs["subscriber_id"] = subscriber_id
                    return journaled_create(obligation, **journal_kwargs)
            return self._stripe.create_link(obligation)
        if method is PaymentMethod.WISE:
            return self._wise.instruction(obligation)
        return self._pix.instruction(obligation)

    def change_method(
        self,
        selected: PaymentSelection,
        method: PaymentMethod,
    ) -> PaymentSelection:
        if type(selected) is not PaymentSelection or type(method) is not PaymentMethod:
            raise TypeError("change_method requires exact payment values")
        return PaymentSelection(selected.obligation, method)

    def change_amount(
        self,
        selected: PaymentSelection,
        *,
        amount_minor: int,
    ) -> PaymentSelection:
        if type(selected) is not PaymentSelection:
            raise TypeError("selected must be exact PaymentSelection")
        if type(amount_minor) is not int or amount_minor < 1:
            raise ValueError("amount_minor must be an exact positive integer")
        if amount_minor == selected.obligation.amount_minor:
            return selected
        display_details = selected.obligation.display_details
        if display_details is not None:
            display_details = replace(
                display_details,
                reservation_total_minor=amount_minor,
            )
        obligation = replace(
            selected.obligation,
            amount_minor=amount_minor,
            economic_version=selected.obligation.economic_version + 1,
            display_details=display_details,
        )
        return PaymentSelection(obligation, selected.method)


class EvidenceConflict(ValueError):
    """One global evidence identity was reused for a divergent payment target."""


class EvidenceDisposition(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class EvidenceAcceptance:
    payment_id: str
    claim_key: str
    disposition: EvidenceDisposition
    visual_evidence_accepted: bool
    bank_settlement_confirmed: bool

    def __post_init__(self) -> None:
        if type(self.disposition) is not EvidenceDisposition:
            raise TypeError("disposition must be exact EvidenceDisposition")
        if type(self.visual_evidence_accepted) is not bool:
            raise TypeError("visual_evidence_accepted must be exact bool")
        if self.bank_settlement_confirmed is not False:
            raise ValueError("evidence acceptance cannot claim bank settlement")


class V2PaymentEvidenceGateway:
    """Delegate verified evidence to the mature atomic global-claim ledger."""

    def __init__(self, store: SQLiteFollowupUnitOfWork) -> None:
        if type(store) is not SQLiteFollowupUnitOfWork:
            raise TypeError("store must be exact SQLiteFollowupUnitOfWork")
        self._store = store

    def accept(
        self,
        *,
        payment_id: str,
        expected_revision: int,
        event: PaymentEvidenceRecorded,
    ) -> EvidenceAcceptance:
        if type(event) is not PaymentEvidenceRecorded:
            raise TypeError("event must be exact PaymentEvidenceRecorded")
        if event.payment_id != payment_id:
            raise EvidenceConflict("evidence event targets another payment")
        try:
            transition = self._store.claim_payment_evidence(
                payment_id,
                expected_revision,
                event,
            )
        except FollowupIdentityConflict as exc:
            raise EvidenceConflict("global payment evidence identity conflict") from exc
        verified = transition.state.verified_evidence
        if verified is None:
            raise RuntimeError("claimed payment evidence is missing from resulting state")
        return EvidenceAcceptance(
            payment_id=payment_id,
            claim_key=verified.claim_key,
            disposition=(
                EvidenceDisposition.ACCEPTED
                if transition.commands
                else EvidenceDisposition.DUPLICATE
            ),
            visual_evidence_accepted=type(event.evidence) is PixVisualEvidence,
            bank_settlement_confirmed=False,
        )


class PaymentInitiationDisposition(str, Enum):
    IDLE = "idle"
    COMPLETED = "completed"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class PaymentInitiationClaim:
    initiation_id: str
    selection: PaymentSelection
    worker_id: str
    fencing_token: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class StripeReconciliationClaim:
    initiation_id: str
    selection: PaymentSelection = field(repr=False)
    worker_id: str
    fencing_token: int
    lease_expires_at: datetime
    already_completed: bool
    receipts: tuple[StripeStepReceipt, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class PaymentInitiationResult:
    disposition: PaymentInitiationDisposition
    offer: PaymentMethodOffer | None = None


_INITIATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_initiations (
  initiation_id TEXT PRIMARY KEY,
  selection_json BLOB NOT NULL,
  selection_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','fenced','completed','manual_review')),
  claim_owner TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TEXT,
  dispatch_slots INTEGER NOT NULL DEFAULT 0 CHECK(dispatch_slots IN (0,1)),
  result_json BLOB,
  result_hash TEXT,
  updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS stripe_step_receipts (
  initiation_id TEXT NOT NULL,
  step TEXT NOT NULL CHECK(step IN ('product','price','payment_link')),
  status TEXT NOT NULL CHECK(status IN ('intent','accepted')),
  journal_owner TEXT NOT NULL,
  journal_fencing_token INTEGER NOT NULL CHECK(journal_fencing_token>=1),
  receipt_json BLOB NOT NULL,
  receipt_hash TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(initiation_id,step),
  FOREIGN KEY(initiation_id) REFERENCES payment_initiations(initiation_id)
) STRICT;
CREATE TABLE IF NOT EXISTS stripe_reconciliations (
  initiation_id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK(status IN ('pending','claimed','matched','manual_review')),
  claim_owner TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts IN (0,1)),
  recovery_pending INTEGER NOT NULL DEFAULT 0 CHECK(recovery_pending IN (0,1)),
  updated_at TEXT NOT NULL,
  FOREIGN KEY(initiation_id) REFERENCES payment_initiations(initiation_id)
) STRICT;
"""


def _utc_text(value: object, name: str) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value.isoformat(timespec="microseconds")


_LEGACY_DISPLAY_DETAIL_FIELDS = frozenset(
    {
        "service",
        "public_label",
        "start_date",
        "end_date",
        "start_time",
        "adults",
        "children",
        "reservation_total_minor",
        "package_component",
    }
)
_DISPLAY_DETAIL_FIELDS = _LEGACY_DISPLAY_DETAIL_FIELDS | {"customer_language"}


def _display_details_value(details: PaymentDisplayDetails | None) -> dict | None:
    if details is None:
        return None
    return {
        "service": details.service.value,
        "public_label": details.public_label,
        "start_date": details.start_date.isoformat(),
        "end_date": details.end_date.isoformat() if details.end_date else None,
        "start_time": details.start_time,
        "adults": details.adults,
        "children": details.children,
        "reservation_total_minor": details.reservation_total_minor,
        "package_component": details.package_component,
        "customer_language": (
            details.customer_language.value
            if details.customer_language is not None
            else None
        ),
    }


def _display_details_from_value(value: object) -> PaymentDisplayDetails | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) not in (
        _DISPLAY_DETAIL_FIELDS,
        _LEGACY_DISPLAY_DETAIL_FIELDS,
    ):
        raise ValueError("payment display fields mismatch")
    return PaymentDisplayDetails(
        service=CheckoutService(value["service"]),
        public_label=value["public_label"],
        start_date=date.fromisoformat(value["start_date"]),
        end_date=(
            date.fromisoformat(value["end_date"])
            if value["end_date"] is not None
            else None
        ),
        start_time=value["start_time"],
        adults=value["adults"],
        children=value["children"],
        reservation_total_minor=value["reservation_total_minor"],
        package_component=value["package_component"],
        customer_language=(
            CustomerLanguage(value["customer_language"])
            if value.get("customer_language") is not None
            else None
        ),
    )


def _selection_bytes(selection: PaymentSelection) -> bytes:
    if type(selection) is not PaymentSelection:
        raise TypeError("selection must be exact PaymentSelection")
    item = selection.obligation
    return json.dumps(
        {
            "method": selection.method.value,
            "obligation": {
                "payment_id": item.payment_id,
                "reservation_anchor_id": item.reservation_anchor_id,
                "business_unit": item.business_unit.value,
                "amount_minor": item.amount_minor,
                "currency": item.currency,
                "due_kind": item.due_kind.value,
                "economic_version": item.economic_version,
                "receiver_profile_id": item.receiver_profile_id,
                "display_details": _display_details_value(item.display_details),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _selection_from_bytes(raw: object) -> PaymentSelection:
    if type(raw) is not bytes:
        raise RuntimeError("payment initiation selection has invalid SQLite type")
    try:
        value = json.loads(raw)
        obligation = value["obligation"]
        return PaymentSelection(
            PaymentObligation(
                payment_id=obligation["payment_id"],
                reservation_anchor_id=obligation["reservation_anchor_id"],
                business_unit=BusinessUnit(obligation["business_unit"]),
                amount_minor=obligation["amount_minor"],
                currency=obligation["currency"],
                due_kind=DueKind(obligation["due_kind"]),
                economic_version=obligation["economic_version"],
                receiver_profile_id=obligation["receiver_profile_id"],
                display_details=_display_details_from_value(
                    obligation.get("display_details")
                ),
            ),
            PaymentMethod(value["method"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("payment initiation selection is corrupt") from exc


def _initiation_id(selection: PaymentSelection) -> str:
    digest = hashlib.sha256(b"v2-payment-initiation-v1\0" + _selection_bytes(selection)).hexdigest()
    return "payment-init:" + digest[:32]


def _offer_bytes(offer: PaymentMethodOffer) -> bytes:
    if type(offer) is StripePaymentLink:
        value = {
            "type": "stripe_link",
            "payment_id": offer.payment_id,
            "reservation_anchor_id": offer.reservation_anchor_id,
            "account_profile_id": offer.account_profile_id,
            "economic_version": offer.economic_version,
            "public_url": offer.public_url,
            "provider_reference_fingerprint": offer.provider_reference_fingerprint,
            "receipt_hash": offer.receipt_hash,
            "customer_language": (
                offer.customer_language.value
                if offer.customer_language is not None
                else None
            ),
            "settled": offer.settled,
        }
    elif type(offer) is PaymentInstruction:
        value = {
            "type": "instruction",
            "payment_id": offer.payment_id,
            "reservation_anchor_id": offer.reservation_anchor_id,
            "method": offer.method.value,
            "receiver_profile_id": offer.receiver_profile_id,
            "economic_version": offer.economic_version,
            "public_text": offer.public_text,
            "settled": offer.settled,
        }
    else:
        raise TypeError("offer must be an exact payment initiation result")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _offer_from_bytes(raw: bytes) -> PaymentMethodOffer:
    if type(raw) is not bytes:
        raise RuntimeError("payment initiation result has invalid private bytes")
    try:
        value = json.loads(raw)
        if value["type"] == "stripe_link":
            legacy_fields = {
                "type",
                "payment_id",
                "reservation_anchor_id",
                "account_profile_id",
                "economic_version",
                "public_url",
                "provider_reference_fingerprint",
                "receipt_hash",
                "settled",
            }
            if set(value) not in (
                legacy_fields,
                legacy_fields | {"customer_language"},
            ):
                raise ValueError("Stripe result fields mismatch")
            return StripePaymentLink(
                payment_id=value["payment_id"],
                reservation_anchor_id=value["reservation_anchor_id"],
                account_profile_id=value["account_profile_id"],
                economic_version=value["economic_version"],
                public_url=value["public_url"],
                provider_reference_fingerprint=value[
                    "provider_reference_fingerprint"
                ],
                receipt_hash=value["receipt_hash"],
                customer_language=(
                    CustomerLanguage(value["customer_language"])
                    if value.get("customer_language") is not None
                    else None
                ),
                settled=value["settled"],
            )
        if value["type"] == "instruction":
            if set(value) != {
                "type",
                "payment_id",
                "reservation_anchor_id",
                "method",
                "receiver_profile_id",
                "economic_version",
                "public_text",
                "settled",
            }:
                raise ValueError("instruction result fields mismatch")
            return PaymentInstruction(
                payment_id=value["payment_id"],
                reservation_anchor_id=value["reservation_anchor_id"],
                method=PaymentMethod(value["method"]),
                receiver_profile_id=value["receiver_profile_id"],
                economic_version=value["economic_version"],
                public_text=value["public_text"],
                settled=value["settled"],
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("payment initiation result is corrupt") from exc
    raise RuntimeError("payment initiation result type is outside the closed catalog")


_RESULT_CIPHERTEXT_PREFIX = b"v2-payment-result-aesgcm-v1\0"
_STRIPE_RECEIPT_CIPHERTEXT_PREFIX = b"v2-stripe-step-aesgcm-v1\0"


def _stripe_receipt_bytes(receipt: StripeStepReceipt) -> bytes:
    if type(receipt) is not StripeStepReceipt:
        raise TypeError("receipt must be exact StripeStepReceipt")
    return json.dumps(
        {
            "step": receipt.step.value,
            "status": receipt.status.value,
            "account_profile_id": receipt.account_profile_id,
            "expected_metadata_hash": receipt.expected_metadata_hash,
            "idempotency_key": receipt.idempotency_key,
            "provider_object_id": receipt.provider_object_id,
            "canonical_url": receipt.canonical_url,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _stripe_receipt_from_bytes(raw: bytes) -> StripeStepReceipt:
    if type(raw) is not bytes:
        raise RuntimeError("Stripe receipt has invalid private bytes")
    try:
        value = json.loads(raw)
        if set(value) != {
            "step",
            "status",
            "account_profile_id",
            "expected_metadata_hash",
            "idempotency_key",
            "provider_object_id",
            "canonical_url",
        }:
            raise ValueError("Stripe receipt fields mismatch")
        return StripeStepReceipt(
            step=StripeCreationStep(value["step"]),
            status=StripeStepStatus(value["status"]),
            account_profile_id=value["account_profile_id"],
            expected_metadata_hash=value["expected_metadata_hash"],
            idempotency_key=value["idempotency_key"],
            provider_object_id=value["provider_object_id"],
            canonical_url=value["canonical_url"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Stripe step receipt is corrupt") from exc


class SQLitePaymentInitiationStore:
    def __init__(self, path: Path, *, result_encryption_key: bytes) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path must be an absolute pathlib.Path")
        if type(result_encryption_key) is not bytes or len(result_encryption_key) != 32:
            raise ValueError("result_encryption_key must be exact 32-byte key material")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._result_cipher = AESGCM(result_encryption_key)
        self._connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(_INITIATION_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _stripe_receipt_aad(initiation_id: str, step: StripeCreationStep) -> bytes:
        return f"{initiation_id}\0{step.value}".encode()

    def _encrypted_stripe_receipt(
        self,
        initiation_id: str,
        receipt: StripeStepReceipt,
    ) -> bytes:
        nonce = os.urandom(12)
        return (
            _STRIPE_RECEIPT_CIPHERTEXT_PREFIX
            + nonce
            + self._result_cipher.encrypt(
                nonce,
                _stripe_receipt_bytes(receipt),
                self._stripe_receipt_aad(initiation_id, receipt.step),
            )
        )

    def _decrypted_stripe_receipt(
        self,
        initiation_id: str,
        step: str,
        raw: object,
        digest: object,
    ) -> StripeStepReceipt:
        if (
            type(raw) is not bytes
            or not raw.startswith(_STRIPE_RECEIPT_CIPHERTEXT_PREFIX)
            or type(digest) is not str
            or hashlib.sha256(raw).hexdigest() != digest
        ):
            raise RuntimeError("Stripe step receipt ciphertext is invalid")
        encrypted = raw[len(_STRIPE_RECEIPT_CIPHERTEXT_PREFIX) :]
        if len(encrypted) <= 12:
            raise RuntimeError("Stripe step receipt ciphertext is truncated")
        nonce, ciphertext = encrypted[:12], encrypted[12:]
        try:
            plaintext = self._result_cipher.decrypt(
                nonce,
                ciphertext,
                self._stripe_receipt_aad(initiation_id, StripeCreationStep(step)),
            )
        except Exception:
            raise RuntimeError("Stripe step receipt ciphertext is invalid") from None
        receipt = _stripe_receipt_from_bytes(plaintext)
        if receipt.step.value != step:
            raise RuntimeError("Stripe step receipt identity diverged")
        return receipt

    def _stripe_receipts_for_id(
        self, initiation_id: str
    ) -> tuple[StripeStepReceipt, ...]:
        rows = self._connection.execute(
            "SELECT step,receipt_json,receipt_hash FROM stripe_step_receipts "
            "WHERE initiation_id=? ORDER BY CASE step "
            "WHEN 'product' THEN 1 WHEN 'price' THEN 2 ELSE 3 END",
            (initiation_id,),
        ).fetchall()
        return tuple(
            self._decrypted_stripe_receipt(initiation_id, step, raw, digest)
            for step, raw, digest in rows
        )

    def stripe_step_receipts(
        self, selection: PaymentSelection
    ) -> tuple[StripeStepReceipt, ...]:
        return self._stripe_receipts_for_id(_initiation_id(selection))

    def _record_stripe_step(
        self,
        initiation_id: str,
        receipt: StripeStepReceipt,
        *,
        expected_status: StripeStepStatus,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> None:
        if type(receipt) is not StripeStepReceipt or receipt.status is not expected_status:
            raise TypeError("Stripe journal received the wrong receipt status")
        if type(worker_id) is not str or not worker_id:
            raise ValueError("Stripe journal worker_id must be non-empty exact text")
        if type(fencing_token) is not int or fencing_token < 1:
            raise ValueError("Stripe journal fencing_token must be a positive exact integer")
        now_text = _utc_text(now, "now")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            initiation = self._connection.execute(
                "SELECT selection_json,status,claim_owner,fencing_token,lease_expires_at "
                "FROM payment_initiations WHERE initiation_id=?",
                (initiation_id,),
            ).fetchone()
            if initiation is None:
                raise RuntimeError("Stripe journal initiation is unknown")
            selection = _selection_from_bytes(initiation[0])
            if selection.method is not PaymentMethod.STRIPE:
                raise RuntimeError("Stripe journal initiation method diverged")
            row = self._connection.execute(
                "SELECT status,receipt_json,receipt_hash,journal_owner,"
                "journal_fencing_token FROM stripe_step_receipts "
                "WHERE initiation_id=? AND step=?",
                (initiation_id, receipt.step.value),
            ).fetchone()
            if expected_status is StripeStepStatus.INTENT:
                if initiation[1] != "fenced":
                    raise RuntimeError("Stripe journal requires a fenced Stripe initiation")
                if initiation[2:4] != (worker_id, fencing_token):
                    raise RuntimeError("stale Stripe journal authority")
                if initiation[4] <= now_text:
                    raise RuntimeError("expired Stripe journal authority")
                if row is not None:
                    raise RuntimeError(
                        "Stripe step was already dispatched; reconciliation is required"
                    )
                predecessor = {
                    StripeCreationStep.PRODUCT: None,
                    StripeCreationStep.PRICE: StripeCreationStep.PRODUCT,
                    StripeCreationStep.PAYMENT_LINK: StripeCreationStep.PRICE,
                }[receipt.step]
                if predecessor is not None:
                    previous = self._connection.execute(
                        "SELECT status FROM stripe_step_receipts "
                        "WHERE initiation_id=? AND step=?",
                        (initiation_id, predecessor.value),
                    ).fetchone()
                    if previous != (StripeStepStatus.ACCEPTED.value,):
                        raise RuntimeError("Stripe step predecessor is not accepted")
                raw = self._encrypted_stripe_receipt(initiation_id, receipt)
                self._connection.execute(
                    "INSERT INTO stripe_step_receipts "
                    "(initiation_id,step,status,journal_owner,journal_fencing_token,"
                    "receipt_json,receipt_hash,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                    (
                        initiation_id,
                        receipt.step.value,
                        receipt.status.value,
                        worker_id,
                        fencing_token,
                        raw,
                        hashlib.sha256(raw).hexdigest(),
                    ),
                )
                self._connection.execute(
                    "INSERT OR IGNORE INTO stripe_reconciliations "
                    "(initiation_id,status,updated_at) "
                    "VALUES (?,'pending',strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                    (initiation_id,),
                )
            else:
                if row is None:
                    raise RuntimeError("Stripe accepted receipt lacks matching intent")
                if row[3:5] != (worker_id, fencing_token):
                    raise RuntimeError("stale Stripe accepted receipt authority")
                if row[0] != StripeStepStatus.INTENT.value:
                    current = self._decrypted_stripe_receipt(
                        initiation_id,
                        receipt.step.value,
                        row[1],
                        row[2],
                    )
                    if current == receipt:
                        self._connection.execute("COMMIT")
                        return
                    raise RuntimeError("Stripe accepted receipt lacks matching intent")
                current = self._decrypted_stripe_receipt(
                    initiation_id,
                    receipt.step.value,
                    row[1],
                    row[2],
                )
                if (
                    current.expected_metadata_hash != receipt.expected_metadata_hash
                    or current.idempotency_key != receipt.idempotency_key
                ):
                    raise RuntimeError("Stripe accepted receipt diverges from intent")
                raw = self._encrypted_stripe_receipt(initiation_id, receipt)
                self._connection.execute(
                    "UPDATE stripe_step_receipts SET status=?,receipt_json=?,receipt_hash=?,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE initiation_id=? AND step=?",
                    (
                        receipt.status.value,
                        raw,
                        hashlib.sha256(raw).hexdigest(),
                        initiation_id,
                        receipt.step.value,
                    ),
                )
                if receipt.step is StripeCreationStep.PAYMENT_LINK:
                    reconciliation = self._connection.execute(
                        "SELECT status FROM stripe_reconciliations WHERE initiation_id=?",
                        (initiation_id,),
                    ).fetchone()
                    if reconciliation in {("manual_review",), ("matched",)}:
                        self._connection.execute(
                            "UPDATE stripe_reconciliations SET status='pending',"
                            "claim_owner=NULL,lease_expires_at=NULL,attempts=0,"
                            "recovery_pending=0,updated_at=? WHERE initiation_id=?",
                            (now_text, initiation_id),
                        )
                    elif reconciliation == ("claimed",):
                        self._connection.execute(
                            "UPDATE stripe_reconciliations SET recovery_pending=1,"
                            "updated_at=? WHERE initiation_id=?",
                            (now_text, initiation_id),
                        )
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def record_stripe_step_intent(
        self,
        initiation_id: str,
        receipt: StripeStepReceipt,
        *,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> None:
        self._record_stripe_step(
            initiation_id,
            receipt,
            expected_status=StripeStepStatus.INTENT,
            worker_id=worker_id,
            fencing_token=fencing_token,
            now=now,
        )

    def record_stripe_step_accepted(
        self,
        initiation_id: str,
        receipt: StripeStepReceipt,
        *,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> None:
        self._record_stripe_step(
            initiation_id,
            receipt,
            expected_status=StripeStepStatus.ACCEPTED,
            worker_id=worker_id,
            fencing_token=fencing_token,
            now=now,
        )

    def claim_stripe_reconciliation(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> StripeReconciliationClaim | None:
        if type(worker_id) is not str or not worker_id:
            raise ValueError("worker_id must be non-empty exact text")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive exact timedelta")
        now_text = _utc_text(now, "now")
        expires = now + lease_ttl
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "UPDATE payment_initiations SET status='manual_review',"
                "claim_owner=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE status='fenced' AND dispatch_slots=1 "
                "AND lease_expires_at<=? AND EXISTS (SELECT 1 FROM "
                "stripe_reconciliations AS r WHERE r.initiation_id="
                "payment_initiations.initiation_id AND r.status='pending')",
                (now_text, now_text),
            )
            self._connection.execute(
                "UPDATE stripe_reconciliations SET status='pending',claim_owner=NULL,"
                "lease_expires_at=NULL,attempts=0,recovery_pending=0,updated_at=? "
                "WHERE status='claimed' AND attempts>=1 AND lease_expires_at<=? "
                "AND recovery_pending=1",
                (now_text, now_text),
            )
            self._connection.execute(
                "UPDATE stripe_reconciliations SET status='manual_review',"
                "claim_owner=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE status='claimed' AND attempts>=1 AND lease_expires_at<=? "
                "AND recovery_pending=0",
                (now_text, now_text),
            )
            row = self._connection.execute(
                "SELECT r.initiation_id,p.selection_json,p.status,r.fencing_token "
                "FROM stripe_reconciliations AS r "
                "JOIN payment_initiations AS p USING(initiation_id) "
                "WHERE r.status='pending' AND r.attempts=0 "
                "AND (r.claim_owner IS NULL OR r.lease_expires_at<=?) "
                "AND p.status IN ('completed','manual_review') "
                "ORDER BY r.updated_at,r.initiation_id LIMIT 1",
                (now_text,),
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None
            token = row[3] + 1
            expires_text = _utc_text(expires, "lease_expires_at")
            self._connection.execute(
                "UPDATE stripe_reconciliations SET status='claimed',claim_owner=?,"
                "fencing_token=?,lease_expires_at=?,attempts=1,updated_at=? "
                "WHERE initiation_id=?",
                (worker_id, token, expires_text, now_text, row[0]),
            )
            receipts = self._stripe_receipts_for_id(row[0])
            self._connection.execute("COMMIT")
            return StripeReconciliationClaim(
                initiation_id=row[0],
                selection=_selection_from_bytes(row[1]),
                worker_id=worker_id,
                fencing_token=token,
                lease_expires_at=expires,
                already_completed=row[2] == "completed",
                receipts=receipts,
            )
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def finish_stripe_reconciliation(
        self,
        claim: StripeReconciliationClaim,
        result: StripeReconciliationResult,
        *,
        now: datetime,
    ) -> None:
        if type(claim) is not StripeReconciliationClaim:
            raise TypeError("claim must be exact StripeReconciliationClaim")
        if type(result) is not StripeReconciliationResult:
            raise TypeError("result must be exact StripeReconciliationResult")
        offer = result.offer
        now_text = _utc_text(now, "now")
        raw = None
        digest = None
        if offer is not None and not claim.already_completed:
            plaintext = _offer_bytes(offer)
            nonce = os.urandom(12)
            raw = (
                _RESULT_CIPHERTEXT_PREFIX
                + nonce
                + self._result_cipher.encrypt(
                    nonce,
                    plaintext,
                    claim.initiation_id.encode(),
                )
            )
            digest = hashlib.sha256(raw).hexdigest()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT status,claim_owner,fencing_token,attempts,recovery_pending FROM "
                "stripe_reconciliations WHERE initiation_id=?",
                (claim.initiation_id,),
            ).fetchone()
            if row is None or row[:4] != (
                "claimed",
                claim.worker_id,
                claim.fencing_token,
                1,
            ):
                raise RuntimeError("stale Stripe reconciliation claim")
            payment = self._connection.execute(
                "SELECT status,result_json,result_hash FROM payment_initiations "
                "WHERE initiation_id=?",
                (claim.initiation_id,),
            ).fetchone()
            expected_payment_status = "completed" if claim.already_completed else "manual_review"
            if payment is None or payment[0] != expected_payment_status:
                raise RuntimeError("Stripe reconciliation payment state diverged")
            offer = result.offer
            offer_diverged = False
            if claim.already_completed and offer is not None:
                result_blob, result_hash = payment[1], payment[2]
                if type(result_blob) is not bytes or not result_blob.startswith(
                    _RESULT_CIPHERTEXT_PREFIX
                ):
                    raise RuntimeError("Stripe completed result is not private ciphertext")
                if not hmac.compare_digest(
                    hashlib.sha256(result_blob).hexdigest(),
                    str(result_hash),
                ):
                    raise RuntimeError("Stripe completed result integrity check failed")
                encrypted = result_blob[len(_RESULT_CIPHERTEXT_PREFIX) :]
                if len(encrypted) <= 12:
                    raise RuntimeError("Stripe completed result is truncated")
                nonce, ciphertext = encrypted[:12], encrypted[12:]
                try:
                    current_offer = _offer_from_bytes(
                        self._result_cipher.decrypt(
                            nonce,
                            ciphertext,
                            claim.initiation_id.encode(),
                        )
                    )
                except Exception as exc:
                    raise RuntimeError("Stripe completed result is invalid") from exc
                offer_diverged = current_offer != offer
            if offer is not None and not claim.already_completed:
                self._connection.execute(
                    "UPDATE payment_initiations SET status='completed',result_json=?,"
                    "result_hash=?,updated_at=? WHERE initiation_id=?",
                    (raw, digest, now_text, claim.initiation_id),
                )
            if row[4] == 1:
                self._connection.execute(
                    "UPDATE stripe_reconciliations SET status='pending',"
                    "claim_owner=NULL,lease_expires_at=NULL,attempts=0,"
                    "recovery_pending=0,updated_at=? WHERE initiation_id=?",
                    (now_text, claim.initiation_id),
                )
            else:
                effective_manual_review = result.manual_review or offer_diverged
                self._connection.execute(
                    "UPDATE stripe_reconciliations SET status=?,claim_owner=NULL,"
                    "lease_expires_at=NULL,recovery_pending=0,updated_at=? "
                    "WHERE initiation_id=?",
                    (
                        "manual_review" if effective_manual_review else "matched",
                        now_text,
                        claim.initiation_id,
                    ),
                )
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def completed_offers(self) -> tuple[PaymentMethodOffer, ...]:
        rows = self._connection.execute(
            "SELECT initiation_id,result_json,result_hash FROM payment_initiations "
            "WHERE status='completed' ORDER BY initiation_id"
        ).fetchall()
        offers = []
        for initiation_id, result_blob, result_hash in rows:
            if type(result_blob) is not bytes or not result_blob.startswith(
                _RESULT_CIPHERTEXT_PREFIX
            ):
                raise RuntimeError("completed payment result is not private ciphertext")
            if hashlib.sha256(result_blob).hexdigest() != result_hash:
                raise RuntimeError("completed payment ciphertext hash diverged")
            encrypted = result_blob[len(_RESULT_CIPHERTEXT_PREFIX) :]
            if len(encrypted) <= 12:
                raise RuntimeError("completed payment ciphertext is truncated")
            nonce, ciphertext = encrypted[:12], encrypted[12:]
            try:
                raw = self._result_cipher.decrypt(
                    nonce,
                    ciphertext,
                    initiation_id.encode("utf-8"),
                )
            except Exception as exc:
                raise RuntimeError("completed payment ciphertext is invalid") from exc
            offers.append(_offer_from_bytes(raw))
        return tuple(offers)

    def enqueue(self, selection: PaymentSelection, *, now: datetime) -> bool:
        raw = _selection_bytes(selection)
        digest = hashlib.sha256(raw).hexdigest()
        initiation_id = _initiation_id(selection)
        now_text = _utc_text(now, "now")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT selection_hash FROM payment_initiations WHERE initiation_id=?",
                (initiation_id,),
            ).fetchone()
            if row is not None:
                if row != (digest,):
                    raise RuntimeError("payment initiation identity conflict")
                self._connection.execute("COMMIT")
                return False
            self._connection.execute(
                "INSERT INTO payment_initiations (initiation_id,selection_json,selection_hash,status,updated_at) VALUES (?,?,?,'queued',?)",
                (initiation_id, raw, digest, now_text),
            )
            self._connection.execute("COMMIT")
            return True
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> PaymentInitiationClaim | None:
        if type(worker_id) is not str or not worker_id:
            raise ValueError("worker_id must be non-empty exact text")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive exact timedelta")
        now_text = _utc_text(now, "now")
        expires = now + lease_ttl
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT initiation_id,selection_json,fencing_token FROM payment_initiations "
                "WHERE status='queued' AND dispatch_slots=0 AND (claim_owner IS NULL OR lease_expires_at<=?) "
                "ORDER BY updated_at,initiation_id LIMIT 1",
                (now_text,),
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None
            token = row[2] + 1
            expires_text = _utc_text(expires, "lease_expires_at")
            self._connection.execute(
                "UPDATE payment_initiations SET claim_owner=?,fencing_token=?,lease_expires_at=?,updated_at=? WHERE initiation_id=?",
                (worker_id, token, expires_text, now_text, row[0]),
            )
            self._connection.execute("COMMIT")
            return PaymentInitiationClaim(
                row[0], _selection_from_bytes(row[1]), worker_id, token, expires
            )
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def fence(self, claim: PaymentInitiationClaim, *, now: datetime) -> None:
        now_text = _utc_text(now, "now")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT status,claim_owner,fencing_token,lease_expires_at,dispatch_slots FROM payment_initiations WHERE initiation_id=?",
                (claim.initiation_id,),
            ).fetchone()
            if row is None or row[:3] != ("queued", claim.worker_id, claim.fencing_token):
                raise RuntimeError("stale payment initiation claim")
            if row[3] <= now_text or row[4] != 0:
                raise RuntimeError("expired or consumed payment initiation claim")
            self._connection.execute(
                "UPDATE payment_initiations SET status='fenced',dispatch_slots=1,updated_at=? WHERE initiation_id=?",
                (now_text, claim.initiation_id),
            )
            if claim.selection.method is PaymentMethod.STRIPE:
                self._connection.execute(
                    "INSERT INTO stripe_reconciliations "
                    "(initiation_id,status,updated_at) VALUES (?,'pending',?)",
                    (claim.initiation_id, now_text),
                )
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def _finish(
        self,
        claim: PaymentInitiationClaim,
        *,
        status: str,
        result: PaymentMethodOffer | None,
        now: datetime,
    ) -> None:
        now_text = _utc_text(now, "now")
        plaintext = None if result is None else _offer_bytes(result)
        if plaintext is None:
            raw = None
        else:
            nonce = os.urandom(12)
            raw = (
                _RESULT_CIPHERTEXT_PREFIX
                + nonce
                + self._result_cipher.encrypt(
                    nonce,
                    plaintext,
                    claim.initiation_id.encode(),
                )
            )
        digest = None if raw is None else hashlib.sha256(raw).hexdigest()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT status,claim_owner,fencing_token,dispatch_slots FROM payment_initiations WHERE initiation_id=?",
                (claim.initiation_id,),
            ).fetchone()
            if row != ("fenced", claim.worker_id, claim.fencing_token, 1):
                raise RuntimeError("payment initiation fence is stale")
            self._connection.execute(
                "UPDATE payment_initiations SET status=?,result_json=?,result_hash=?,claim_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE initiation_id=?",
                (status, raw, digest, now_text, claim.initiation_id),
            )
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def complete(
        self,
        claim: PaymentInitiationClaim,
        offer: PaymentMethodOffer,
        *,
        now: datetime,
    ) -> None:
        self._finish(claim, status="completed", result=offer, now=now)

    def mark_unknown(self, claim: PaymentInitiationClaim, *, now: datetime) -> None:
        self._finish(claim, status="manual_review", result=None, now=now)

    def dispatch_slots(self, selection: PaymentSelection) -> int:
        row = self._connection.execute(
            "SELECT dispatch_slots FROM payment_initiations WHERE initiation_id=?",
            (_initiation_id(selection),),
        ).fetchone()
        if row is None:
            raise KeyError("payment initiation is not queued")
        return row[0]


class PaymentInitiationWorker:
    def __init__(
        self,
        *,
        store: SQLitePaymentInitiationStore,
        payments: PaymentService,
        worker_id: str,
        lease_ttl: timedelta,
        effect_guard: object | None = None,
        stripe_reconciler: object | None = None,
        lead_resolver: object | None = None,
    ) -> None:
        if type(store) is not SQLitePaymentInitiationStore:
            raise TypeError("store must be exact SQLitePaymentInitiationStore")
        if type(payments) is not PaymentService:
            raise TypeError("payments must be exact PaymentService")
        self._store = store
        self._payments = payments
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl
        if effect_guard is not None and not callable(
            getattr(effect_guard, "allows_workflow", None)
        ):
            raise TypeError("effect_guard must expose allows_workflow")
        self._effect_guard = effect_guard
        if stripe_reconciler is not None and not callable(
            getattr(stripe_reconciler, "reconcile", None)
        ):
            raise TypeError("stripe_reconciler must expose reconcile")
        self._stripe_reconciler = stripe_reconciler
        if lead_resolver is not None and not callable(
            getattr(lead_resolver, "subscriber_id_for_payment", None)
        ):
            raise TypeError("lead_resolver must resolve payment owners")
        self._lead_resolver = lead_resolver

    def run_once(self, *, now: datetime) -> PaymentInitiationResult:
        if self._stripe_reconciler is not None:
            reconciliation = self._store.claim_stripe_reconciliation(
                worker_id=self._worker_id,
                now=now,
                lease_ttl=self._lease_ttl,
            )
            if reconciliation is not None:
                try:
                    subscriber_id = (
                        self._lead_resolver.subscriber_id_for_payment(
                            reconciliation.selection.obligation.payment_id
                        )
                        if self._lead_resolver is not None
                        else ""
                    )
                    reconciliation_kwargs = {
                        "initiation_id": reconciliation.initiation_id,
                    }
                    if subscriber_id:
                        reconciliation_kwargs["subscriber_id"] = subscriber_id
                    reconciled = self._stripe_reconciler.reconcile(
                        reconciliation.selection,
                        reconciliation.receipts,
                        **reconciliation_kwargs,
                    )
                    if type(reconciled) is not StripeReconciliationResult:
                        raise TypeError("Stripe reconciler returned the wrong contract")
                except Exception:
                    reconciled = StripeReconciliationResult(
                        offer=None,
                        manual_review=True,
                    )
                self._store.finish_stripe_reconciliation(
                    reconciliation,
                    reconciled,
                    now=now,
                )
                if reconciled.manual_review:
                    return PaymentInitiationResult(
                        PaymentInitiationDisposition.MANUAL_REVIEW
                    )
                return PaymentInitiationResult(
                    PaymentInitiationDisposition.COMPLETED,
                    reconciled.offer,
                )
        if self._effect_guard is not None and not self._effect_guard.allows_workflow(
            "stripe-payment-initiation"
        ):
            return PaymentInitiationResult(PaymentInitiationDisposition.IDLE)
        claim = self._store.claim(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return PaymentInitiationResult(PaymentInitiationDisposition.IDLE)
        self._store.fence(claim, now=now)
        try:
            subscriber_id = (
                self._lead_resolver.subscriber_id_for_payment(
                    claim.selection.obligation.payment_id
                )
                if self._lead_resolver is not None
                else ""
            )
            offer = self._payments.initiate(
                claim.selection.obligation,
                claim.selection.method,
                initiation_id=claim.initiation_id,
                journal_worker_id=claim.worker_id,
                journal_fencing_token=claim.fencing_token,
                subscriber_id=subscriber_id,
            )
        except Exception:
            self._store.mark_unknown(claim, now=now)
            return PaymentInitiationResult(PaymentInitiationDisposition.MANUAL_REVIEW)
        self._store.complete(claim, offer, now=now)
        return PaymentInitiationResult(PaymentInitiationDisposition.COMPLETED, offer)


__all__ = [
    "EvidenceAcceptance",
    "EvidenceConflict",
    "EvidenceDisposition",
    "PaymentInitiationDisposition",
    "PaymentInitiationResult",
    "PaymentInitiationWorker",
    "PaymentService",
    "SQLitePaymentInitiationStore",
    "V2PaymentEvidenceGateway",
]
