"""Durable, idempotent SQLite inbox for V2 channel events."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from v2_contracts.channel import AcceptDisposition, InboundBatch, InboundEvent


_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_events (
  event_id TEXT PRIMARY KEY,
  lead_id TEXT NOT NULL,
  subscriber_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  payload BLOB NOT NULL,
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','claimed','processed','manual_review')),
  claim_token TEXT,
  claim_expires_at TEXT
) STRICT;
CREATE INDEX IF NOT EXISTS inbound_events_lead_status
ON inbound_events(lead_id,status,occurred_at,event_id);
"""
_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "lead_id",
        "subscriber_id",
        "conversation_id",
        "text",
        "media_url",
        "media_type",
        "occurred_at",
        "payload_hash",
    }
)


def _utc_text(value: object, field_name: str) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be an exact UTC datetime")
    return value.isoformat(timespec="microseconds")


def _positive_delta(value: object, field_name: str, *, allow_zero: bool) -> timedelta:
    if type(value) is not timedelta:
        raise TypeError(f"{field_name} must be an exact timedelta")
    minimum = timedelta(0)
    if value < minimum or (not allow_zero and value == minimum):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field_name} must be {qualifier}")
    return value


def _event_bytes(event: InboundEvent) -> bytes:
    if type(event) is not InboundEvent:
        raise TypeError("event must be an exact InboundEvent")
    return json.dumps(
        {
            "event_id": event.event_id,
            "lead_id": event.lead_id,
            "subscriber_id": event.subscriber_id,
            "conversation_id": event.conversation_id,
            "text": event.text,
            "media_url": event.media_url,
            "media_type": event.media_type,
            "occurred_at": _utc_text(event.occurred_at, "occurred_at"),
            "payload_hash": event.payload_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _batch_id(events: tuple[InboundEvent, ...]) -> str:
    identity = json.dumps(
        [[event.event_id, event.payload_hash] for event in events],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(b"v2-inbound-batch-v1\0" + identity).hexdigest()
    return "batch:" + digest


def _event_from_bytes(payload: object) -> InboundEvent:
    if type(payload) is not bytes or not payload:
        raise ValueError("persisted event payload must be non-empty bytes")
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("persisted event payload must be strict JSON") from exc
    if type(value) is not dict or set(value) != _EVENT_FIELDS:
        raise ValueError("persisted event payload fields mismatch")
    try:
        occurred_at = datetime.fromisoformat(value["occurred_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("persisted occurred_at is invalid") from exc
    return InboundEvent(
        event_id=value["event_id"],
        lead_id=value["lead_id"],
        subscriber_id=value["subscriber_id"],
        conversation_id=value["conversation_id"],
        text=value["text"],
        media_url=value["media_url"],
        media_type=value["media_type"],
        occurred_at=occurred_at,
        payload_hash=value["payload_hash"],
    )


class SQLiteInbox:
    """Single-writer inbox with exact duplicate/conflict classification."""

    def __init__(self, path: Path, *, timeout_seconds: float = 5.0) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path must be an absolute pathlib.Path")
        if type(timeout_seconds) not in {int, float} or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.path = path
        self._timeout_seconds = float(timeout_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self._timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def accept(self, event: InboundEvent) -> AcceptDisposition:
        payload = _event_bytes(event)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_hash FROM inbound_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO inbound_events (
                      event_id, lead_id, subscriber_id, conversation_id,
                      occurred_at, payload, payload_hash, status,
                      claim_token, claim_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL)
                    """,
                    (
                        event.event_id,
                        event.lead_id,
                        event.subscriber_id,
                        event.conversation_id,
                        _utc_text(event.occurred_at, "occurred_at"),
                        payload,
                        event.payload_hash,
                    ),
                )
                disposition = AcceptDisposition.ACCEPTED
            elif existing["payload_hash"] == event.payload_hash:
                disposition = AcceptDisposition.DUPLICATE
            else:
                disposition = AcceptDisposition.CONFLICT
            connection.commit()
            return disposition
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def pending_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM inbound_events WHERE status = 'pending'"
            ).fetchone()
        return int(row["count"])

    def claim_ready(
        self,
        *,
        now: datetime,
        quiet_window: timedelta,
        lease_for: timedelta,
    ) -> InboundBatch | None:
        now_text = _utc_text(now, "now")
        quiet = _positive_delta(quiet_window, "quiet_window", allow_zero=True)
        lease = _positive_delta(lease_for, "lease_for", allow_zero=False)
        cutoff_text = _utc_text(now - quiet, "cutoff")
        expires_text = _utc_text(now + lease, "claim_expires_at")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE inbound_events
                SET status = 'pending', claim_token = NULL, claim_expires_at = NULL
                WHERE status = 'claimed' AND claim_expires_at <= ?
                """,
                (now_text,),
            )
            candidate = connection.execute(
                """
                SELECT lead_id, MIN(occurred_at) AS first_at
                FROM inbound_events
                WHERE status = 'pending'
                GROUP BY lead_id
                HAVING MAX(occurred_at) <= ?
                ORDER BY first_at, lead_id
                LIMIT 1
                """,
                (cutoff_text,),
            ).fetchone()
            if candidate is None:
                connection.commit()
                return None
            rows = connection.execute(
                """
                SELECT event_id, payload
                FROM inbound_events
                WHERE lead_id = ? AND status = 'pending'
                ORDER BY occurred_at, event_id
                """,
                (candidate["lead_id"],),
            ).fetchall()
            events = tuple(_event_from_bytes(row["payload"]) for row in rows)
            claim_token = uuid.uuid4().hex
            event_ids = tuple(row["event_id"] for row in rows)
            placeholders = ",".join("?" for _ in event_ids)
            cursor = connection.execute(
                f"""
                UPDATE inbound_events
                SET status = 'claimed', claim_token = ?, claim_expires_at = ?
                WHERE status = 'pending' AND event_id IN ({placeholders})
                """,
                (claim_token, expires_text, *event_ids),
            )
            if cursor.rowcount != len(event_ids):
                raise RuntimeError("claim cardinality changed inside write transaction")
            batch = InboundBatch(
                batch_id=_batch_id(events),
                lead_id=events[0].lead_id,
                subscriber_id=events[0].subscriber_id,
                events=events,
                combined_text="\n".join(
                    event.text for event in events if event.text.strip()
                ),
            )
            connection.commit()
            return batch
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
