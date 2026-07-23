"""Payment planning and method initiation without settlement authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3

from reservation_followup import PaymentEvidenceRecorded
from reservation_followup.payment import PixVisualEvidence
from reservation_followup.sqlite_store import (
    IdentityConflict as FollowupIdentityConflict,
    SQLiteFollowupUnitOfWork,
)
from v2_contracts.payments import (
    BusinessUnit,
    DueKind,
    PaymentInstruction,
    PaymentMethod,
    PaymentMethodOffer,
    PaymentObligation,
    PaymentPlan,
    PaymentSelection,
    ReservationPaymentContext,
    StripePaymentLink,
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
    ) -> PaymentMethodOffer:
        if type(obligation) is not PaymentObligation:
            raise TypeError("obligation must be exact PaymentObligation")
        if type(method) is not PaymentMethod:
            raise TypeError("method must be exact PaymentMethod")
        if obligation.due_kind is DueKind.DUE_AT_CHECKIN:
            raise ValueError("due-at-checkin obligation has no initiation effect")
        if method is PaymentMethod.STRIPE:
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
        obligation = replace(
            selected.obligation,
            amount_minor=amount_minor,
            economic_version=selected.obligation.economic_version + 1,
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
"""


def _utc_text(value: object, name: str) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value.isoformat(timespec="microseconds")


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


class SQLitePaymentInitiationStore:
    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path must be an absolute pathlib.Path")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(_INITIATION_SCHEMA)

    def close(self) -> None:
        self._connection.close()

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
        raw = None if result is None else _offer_bytes(result)
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
    ) -> None:
        if type(store) is not SQLitePaymentInitiationStore:
            raise TypeError("store must be exact SQLitePaymentInitiationStore")
        if type(payments) is not PaymentService:
            raise TypeError("payments must be exact PaymentService")
        self._store = store
        self._payments = payments
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> PaymentInitiationResult:
        claim = self._store.claim(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return PaymentInitiationResult(PaymentInitiationDisposition.IDLE)
        self._store.fence(claim, now=now)
        try:
            offer = self._payments.initiate(
                claim.selection.obligation,
                claim.selection.method,
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
