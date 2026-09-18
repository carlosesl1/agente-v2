"""Completion coverage and pre-dispatch consolidation on the existing v8 schema.

No new state owner: source identities authenticate coverage, receipts preserve
Maya's authorship, and cancellation joins the ordinary turn transaction.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from reservation_boundary.conversation import PublicReplyChunk, SourceEventIdentity


def consolidation_source(turn_id: str, receipt_hash: str) -> SourceEventIdentity:
    return SourceEventIdentity(
        "completion-merge:" + hashlib.sha256(turn_id.encode()).hexdigest()[:40],
        receipt_hash,
    )


def source_coverage(store, lead_id: str, event_id: str, payload_hash: str):
    row = store._connection.execute(
        "SELECT aggregate_turn_id,source_event_hash,source_turn_receipt_hash "
        "FROM boundary_event_sources WHERE lead_key=? AND source_event_id=?",
        (lead_id, event_id),
    ).fetchone()
    if row is None:
        return None
    if row[1] != payload_hash:
        raise ValueError("completion source content changed under the same identity")
    return row[0], row[2]


def is_unattempted_completion(store, lead_id: str, turn_id: str) -> bool:
    # This namespace is internal and is never accepted by the ManyChat inbox.
    if not turn_id.startswith("completion-turn:"):
        return False
    rows = store._connection.execute(
        "SELECT status,dispatch_slots_consumed FROM boundary_public_outbox "
        "WHERE lead_key=? AND aggregate_turn_id=?",
        (lead_id, turn_id),
    ).fetchall()
    return bool(rows) and all(
        s in {"pending", "leased"} and slots == 0 for s, slots in rows
    )


def cancel_unattempted_completions(store, *, lead_id, turn_ids, receipt, now):
    """Called only within commit_turn_v8's transaction; never cancels uncertain sends."""
    from reservation_boundary.sqlite_store import ConcurrencyConflict

    for turn_id in turn_ids:
        old = store.load_turn_receipt(turn_id)
        if (
            old is None
            or consolidation_source(turn_id, old.artifact_hash)
            not in receipt.source_events
        ):
            raise ValueError("consolidation must bind the previous receipt")
        if not is_unattempted_completion(store, lead_id, turn_id):
            raise ConcurrencyConflict(
                "completion delivery advanced before consolidation"
            )
        store._connection.execute(
            "UPDATE boundary_public_outbox SET status='cancelled',owner=NULL,"
            "lease_acquired_at=NULL,lease_expires_at=NULL,"
            "updated_at=? WHERE lead_key=? AND aggregate_turn_id=?",
            (now.isoformat(), lead_id, turn_id),
        )


def completion_messages(store, lead_id: str):
    rows = store._connection.execute(
        "SELECT public_row_id,aggregate_turn_id,chunk_json,status,updated_at "
        "FROM boundary_public_outbox WHERE lead_key=? ORDER BY created_at,aggregate_turn_id,chunk_index",
        (lead_id,),
    ).fetchall()
    result = []
    for identity, turn_id, payload, status, updated_at in rows:
        if not turn_id.startswith("completion-turn:"):
            continue
        chunk = PublicReplyChunk.from_canonical_bytes(payload.encode())
        result.append(
            (identity, turn_id, chunk, status, datetime.fromisoformat(updated_at))
        )
    return tuple(result)
