"""Pure completion policy and a durable local public-delivery outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import hashlib
from pathlib import Path
import re
import sqlite3

from v2_contracts.channel import PublicDeliveryNotCalled, PublicDeliveryUnknown


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


class CompletionStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    MANUAL_REVIEW = "manual_review"


class PublicServiceKind(str, Enum):
    LODGING = "lodging"
    ACTIVITY = "activity"
    PACKAGE = "package"


@dataclass(frozen=True, slots=True)
class CompletionContext:
    workflow_id: str
    service_kind: PublicServiceKind
    requires_payment: bool
    manual_review: bool
    receipts: frozenset[str]

    def __post_init__(self) -> None:
        if type(self.workflow_id) is not str or _ID_RE.fullmatch(self.workflow_id) is None:
            raise ValueError("workflow_id must be a canonical identifier")
        if type(self.service_kind) is not PublicServiceKind:
            raise TypeError("service_kind must be exact PublicServiceKind")
        if type(self.requires_payment) is not bool or type(self.manual_review) is not bool:
            raise TypeError("completion flags must be exact booleans")
        if type(self.receipts) is not frozenset or any(
            type(item) is not str or not item for item in self.receipts
        ):
            raise TypeError("receipts must be an exact string frozenset")


class CompletionPolicy:
    def required_receipts(self, context: CompletionContext) -> frozenset[str]:
        if type(context) is not CompletionContext:
            raise TypeError("context must be exact CompletionContext")
        required = {"reservation", "public_delivery"}
        if context.requires_payment:
            required.add("settlement")
        if context.service_kind in (PublicServiceKind.ACTIVITY, PublicServiceKind.PACKAGE):
            required.add("bokun_form")
        return frozenset(required)

    def evaluate(self, context: CompletionContext) -> CompletionStatus:
        if context.manual_review:
            return CompletionStatus.MANUAL_REVIEW
        if self.required_receipts(context).issubset(context.receipts):
            return CompletionStatus.COMPLETED
        return CompletionStatus.PENDING


@dataclass(frozen=True, slots=True)
class PublicReply:
    release_id: str
    lead_id: str
    message_id: str
    channel: str
    chunks: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("release_id", "lead_id", "message_id", "channel"):
            value = getattr(self, name)
            if type(value) is not str or _ID_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be a canonical identifier")
        if self.channel != "manychat":
            raise ValueError("public channel is outside the current allowlist")
        if type(self.chunks) is not tuple or not self.chunks or any(
            type(chunk) is not str or not chunk.strip() for chunk in self.chunks
        ):
            raise ValueError("chunks must be a non-empty exact text tuple")


@dataclass(frozen=True, slots=True)
class PublicClaim:
    outbox_id: str
    release_id: str
    lead_id: str
    source_message_id: str
    chunk_index: int
    text: str
    worker_id: str
    fencing_token: int
    lease_expires_at: datetime

    @property
    def message_id(self) -> str:
        return self.outbox_id


class PublicDeliveryDisposition(str, Enum):
    IDLE = "idle"
    DELIVERED = "delivered"
    RETRYABLE_FAILURE = "retryable_failure"
    MANUAL_REVIEW = "manual_review"


_CREATE_PUBLIC_OUTBOX = """
CREATE TABLE public_outbox (
  outbox_id TEXT PRIMARY KEY,
  release_id TEXT NOT NULL,
  lead_id TEXT NOT NULL,
  source_message_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','leased','delivered','manual_review')),
  claim_owner TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TEXT,
  receipt_id TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(release_id, chunk_index)
) STRICT
"""
_SCHEMA = _CREATE_PUBLIC_OUTBOX.replace(
    "CREATE TABLE public_outbox",
    "CREATE TABLE IF NOT EXISTS public_outbox",
    1,
) + ";"
_PUBLIC_OUTBOX_COLUMNS = (
    "outbox_id",
    "release_id",
    "lead_id",
    "source_message_id",
    "chunk_index",
    "text",
    "status",
    "claim_owner",
    "fencing_token",
    "lease_expires_at",
    "receipt_id",
    "updated_at",
)


def _utc(value: object, name: str) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value.isoformat(timespec="microseconds")


def _outbox_id(reply: PublicReply, index: int) -> str:
    material = "\0".join(
        (reply.release_id, reply.lead_id, reply.message_id, reply.channel, str(index))
    ).encode()
    return "public:" + hashlib.sha256(material).hexdigest()[:32]


def _migrate_checkpoint_schema(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='public_outbox'"
    ).fetchone()
    if row is None or "'manual_review'" in row[0]:
        return
    actual_columns = tuple(
        item[1] for item in connection.execute("PRAGMA table_info(public_outbox)")
    )
    if actual_columns != _PUBLIC_OUTBOX_COLUMNS:
        raise RuntimeError("public outbox checkpoint schema is not migratable")
    columns = ",".join(_PUBLIC_OUTBOX_COLUMNS)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "ALTER TABLE public_outbox RENAME TO public_outbox_checkpoint"
        )
        connection.execute(_CREATE_PUBLIC_OUTBOX)
        connection.execute(
            f"INSERT INTO public_outbox ({columns}) "
            f"SELECT {columns} FROM public_outbox_checkpoint"
        )
        connection.execute("DROP TABLE public_outbox_checkpoint")
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


class PublicOutboxStore:
    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path must be an absolute pathlib.Path")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(_SCHEMA)
        _migrate_checkpoint_schema(self._connection)

    def close(self) -> None:
        self._connection.close()

    def enqueue(self, reply: PublicReply, *, now: datetime) -> int:
        if type(reply) is not PublicReply:
            raise TypeError("reply must be exact PublicReply")
        now_text = _utc(now, "now")
        inserted = 0
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for index, text in enumerate(reply.chunks):
                outbox_id = _outbox_id(reply, index)
                row = self._connection.execute(
                    "SELECT release_id,lead_id,source_message_id,chunk_index,text FROM public_outbox WHERE outbox_id=?",
                    (outbox_id,),
                ).fetchone()
                expected = (reply.release_id, reply.lead_id, reply.message_id, index, text)
                if row is not None:
                    if row != expected:
                        raise RuntimeError("public outbox identity conflict")
                    continue
                self._connection.execute(
                    "INSERT INTO public_outbox (outbox_id,release_id,lead_id,source_message_id,chunk_index,text,status,updated_at) VALUES (?,?,?,?,?,?,'pending',?)",
                    (outbox_id, *expected, now_text),
                )
                inserted += 1
            self._connection.execute("COMMIT")
            return inserted
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
    ) -> PublicClaim | None:
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        now_text = _utc(now, "now")
        expires = now + lease_ttl
        expires_text = _utc(expires, "lease_expires_at")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT outbox_id,release_id,lead_id,source_message_id,chunk_index,text,fencing_token FROM public_outbox "
                "WHERE status='pending' OR (status='leased' AND lease_expires_at<=?) "
                "ORDER BY release_id,chunk_index LIMIT 1",
                (now_text,),
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None
            token = row[6] + 1
            self._connection.execute(
                "UPDATE public_outbox SET status='leased',claim_owner=?,fencing_token=?,lease_expires_at=?,updated_at=? WHERE outbox_id=?",
                (worker_id, token, expires_text, now_text, row[0]),
            )
            self._connection.execute("COMMIT")
            return PublicClaim(*row[:6], worker_id, token, expires)
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def complete(self, claim: PublicClaim, receipt_id: str, *, now: datetime) -> None:
        now_text = _utc(now, "now")
        if type(receipt_id) is not str or _ID_RE.fullmatch(receipt_id) is None:
            raise ValueError("receipt_id must be canonical")
        cursor = self._connection.execute(
            "UPDATE public_outbox SET status='delivered',receipt_id=?,claim_owner=NULL,lease_expires_at=NULL,updated_at=? "
            "WHERE outbox_id=? AND status='leased' AND claim_owner=? AND fencing_token=?",
            (receipt_id, now_text, claim.outbox_id, claim.worker_id, claim.fencing_token),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("public delivery claim is stale")

    def release(self, claim: PublicClaim, *, now: datetime) -> None:
        now_text = _utc(now, "now")
        cursor = self._connection.execute(
            "UPDATE public_outbox SET status='pending',claim_owner=NULL,lease_expires_at=NULL,updated_at=? "
            "WHERE outbox_id=? AND status='leased' AND claim_owner=? AND fencing_token=?",
            (now_text, claim.outbox_id, claim.worker_id, claim.fencing_token),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("public delivery claim is stale")

    def mark_manual_review(self, claim: PublicClaim, *, now: datetime) -> None:
        now_text = _utc(now, "now")
        cursor = self._connection.execute(
            "UPDATE public_outbox SET status='manual_review',claim_owner=NULL,lease_expires_at=NULL,updated_at=? "
            "WHERE outbox_id=? AND status='leased' AND claim_owner=? AND fencing_token=?",
            (now_text, claim.outbox_id, claim.worker_id, claim.fencing_token),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("public delivery claim is stale")

    def delivered_count(self, release_id: str) -> int:
        return self._connection.execute(
            "SELECT count(*) FROM public_outbox WHERE release_id=? AND status='delivered'",
            (release_id,),
        ).fetchone()[0]

    def pending_count(self) -> int:
        return self._connection.execute(
            "SELECT count(*) FROM public_outbox WHERE status='pending'"
        ).fetchone()[0]

    def manual_review_count(self) -> int:
        return self._connection.execute(
            "SELECT count(*) FROM public_outbox WHERE status='manual_review'"
        ).fetchone()[0]


class PublicDeliveryWorker:
    def __init__(self, *, store, delivery, worker_id: str, lease_ttl: timedelta) -> None:
        if type(store) is not PublicOutboxStore:
            raise TypeError("store must be exact PublicOutboxStore")
        if not callable(getattr(delivery, "send", None)):
            raise TypeError("delivery must implement send")
        self._store = store
        self._delivery = delivery
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> PublicDeliveryDisposition:
        claim = self._store.claim(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return PublicDeliveryDisposition.IDLE
        try:
            receipt = self._delivery.send(claim)
        except PublicDeliveryNotCalled:
            self._store.release(claim, now=now)
            return PublicDeliveryDisposition.RETRYABLE_FAILURE
        except PublicDeliveryUnknown:
            self._store.mark_manual_review(claim, now=now)
            return PublicDeliveryDisposition.MANUAL_REVIEW
        except Exception:
            self._store.mark_manual_review(claim, now=now)
            return PublicDeliveryDisposition.MANUAL_REVIEW
        if type(receipt) is not str or _ID_RE.fullmatch(receipt) is None:
            self._store.mark_manual_review(claim, now=now)
            return PublicDeliveryDisposition.MANUAL_REVIEW
        self._store.complete(claim, receipt, now=now)
        return PublicDeliveryDisposition.DELIVERED


__all__ = [
    "CompletionContext",
    "CompletionPolicy",
    "CompletionStatus",
    "PublicDeliveryNotCalled",
    "PublicDeliveryUnknown",
    "PublicDeliveryWorker",
    "PublicOutboxStore",
    "PublicReply",
    "PublicServiceKind",
]
