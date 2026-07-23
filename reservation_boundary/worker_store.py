"""Lease/fence APIs for draining the durable Phase 8 boundary queues."""

from __future__ import annotations

from datetime import datetime, timedelta

from reservation_boundary.conversation import PublicReplyChunk
from reservation_boundary.effects import (
    CommandRelayClaim,
    HandoffRelayBundle,
    InternalJobKind,
    InternalRelayClaim,
    ReservationRelayBundle,
    TargetOperationReceipt,
    target_operation_id,
)
from reservation_boundary.public_dispatch import (
    PublicDeliveryReceipt,
    PublicDispatchClaim,
)
from reservation_boundary.sqlite_store import (
    BoundaryStoreError,
    ConcurrencyConflict,
    DataCorruption,
    IdentityConflict,
    SQLiteBoundaryStore,
    _require_id,
    _utc_text,
)


class SQLiteBoundaryWorkerStore:
    """Operational queue surface kept separate from the atomic commit API."""

    def __init__(self, boundary: SQLiteBoundaryStore) -> None:
        if type(boundary) is not SQLiteBoundaryStore:
            raise TypeError("boundary must be exact SQLiteBoundaryStore")
        if boundary._schema_version != 8:
            raise BoundaryStoreError("worker queues require boundary schema v8")
        self._boundary = boundary

    @property
    def _connection(self):
        self._boundary._ensure_open()
        return self._boundary._connection

    def claim_command_relay(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> CommandRelayClaim | None:
        owner = _require_id(worker_id, "worker_id")
        now_text = _utc_text(now, "now")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive timedelta")
        expires = now + lease_ttl
        expires_text = _utc_text(expires, "lease_expires_at")
        with self._boundary._transaction():
            self._connection.execute(
                "UPDATE boundary_command_relays SET status='pending',owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE status='leased' AND lease_expires_at<=?",
                (now_text, now_text),
            )
            row = self._connection.execute(
                "SELECT relay_id,command_id,bundle_json,bundle_hash,"
                "source_turn_receipt_hash,fencing_token "
                "FROM boundary_command_relays WHERE status='pending' "
                "ORDER BY relay_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            relay_id, command_id, bundle_json, bundle_hash, source_hash, token = row
            next_token = int(token) + 1
            updated = self._connection.execute(
                "UPDATE boundary_command_relays SET status='leased',owner=?,"
                "fencing_token=?,claim_count=claim_count+1,lease_acquired_at=?,"
                "lease_expires_at=?,updated_at=? WHERE relay_id=? "
                "AND status='pending' AND fencing_token=?",
                (
                    owner,
                    next_token,
                    now_text,
                    expires_text,
                    now_text,
                    relay_id,
                    token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("command relay claim CAS lost")
            bundle_bytes = bundle_json.encode("utf-8")
            bundle = ReservationRelayBundle.from_canonical_bytes(bundle_bytes)
            if bundle.artifact_hash != bundle_hash:
                raise DataCorruption("command relay bundle hash diverged")
            operation_id = target_operation_id(
                InternalJobKind.HANDOFF, bundle_hash, source_hash
            )
            return CommandRelayClaim(
                relay_id=relay_id,
                command_id=command_id,
                bundle_bytes=bundle_bytes,
                bundle_hash=bundle_hash,
                source_turn_receipt_hash=source_hash,
                target_operation_id=operation_id,
                worker_id=owner,
                fencing_token=next_token,
                lease_expires_at=expires,
            )

    def complete_command_relay(
        self,
        claim: CommandRelayClaim,
        receipt: TargetOperationReceipt,
        *,
        now: datetime,
    ) -> None:
        if type(claim) is not CommandRelayClaim:
            raise TypeError("claim must be exact CommandRelayClaim")
        if type(receipt) is not TargetOperationReceipt:
            raise TypeError("receipt must be exact TargetOperationReceipt")
        now_text = _utc_text(now, "now")
        if now >= claim.lease_expires_at:
            raise ConcurrencyConflict("command relay lease expired before completion")
        if (
            receipt.operation_id != claim.target_operation_id
            or receipt.job_kind is not InternalJobKind.HANDOFF
            or receipt.artifact_hash != claim.bundle_hash
            or receipt.source_turn_receipt_hash != claim.source_turn_receipt_hash
        ):
            raise IdentityConflict("target receipt diverged from command relay claim")
        receipt_json = receipt.to_canonical_bytes().decode("utf-8")
        receipt_hash = receipt.canonical_hash()
        with self._boundary._transaction():
            updated = self._connection.execute(
                "UPDATE boundary_command_relays SET status='acked',owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,target_receipt_json=?,"
                "target_receipt_hash=?,acked_at=?,updated_at=? WHERE relay_id=? "
                "AND status='leased' AND owner=? AND fencing_token=?",
                (
                    receipt_json,
                    receipt_hash,
                    now_text,
                    now_text,
                    claim.relay_id,
                    claim.worker_id,
                    claim.fencing_token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("command relay completion CAS lost")

    def release_command_relay(
        self,
        claim: CommandRelayClaim,
        *,
        now: datetime,
    ) -> None:
        if type(claim) is not CommandRelayClaim:
            raise TypeError("claim must be exact CommandRelayClaim")
        now_text = _utc_text(now, "now")
        with self._boundary._transaction():
            row = self._connection.execute(
                "SELECT preparation_failures FROM boundary_command_relays "
                "WHERE relay_id=? AND status='leased' AND owner=? AND fencing_token=?",
                (claim.relay_id, claim.worker_id, claim.fencing_token),
            ).fetchone()
            if row is None:
                raise ConcurrencyConflict("command relay release CAS lost")
            failures = int(row[0]) + 1
            status = "manual_review" if failures >= 3 else "pending"
            updated = self._connection.execute(
                "UPDATE boundary_command_relays SET status=?,owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,preparation_failures=?,"
                "updated_at=? WHERE relay_id=? AND status='leased' AND owner=? "
                "AND fencing_token=?",
                (
                    status,
                    failures,
                    now_text,
                    claim.relay_id,
                    claim.worker_id,
                    claim.fencing_token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("command relay release CAS lost")

    def claim_internal_job(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> InternalRelayClaim | None:
        owner = _require_id(worker_id, "worker_id")
        now_text = _utc_text(now, "now")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive timedelta")
        expires = now + lease_ttl
        expires_text = _utc_text(expires, "lease_expires_at")
        with self._boundary._transaction():
            self._connection.execute(
                "UPDATE boundary_outbox SET status='pending',owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE status='leased' AND lease_expires_at<=?",
                (now_text, now_text),
            )
            row = self._connection.execute(
                "SELECT job_id,job_kind,artifact_json,artifact_hash,"
                "source_turn_receipt_hash,fencing_token FROM boundary_outbox "
                "WHERE status='pending' AND job_kind='handoff_relay' "
                "ORDER BY job_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            job_id, job_kind_text, artifact_json, artifact_hash, source_hash, token = row
            if job_kind_text != "handoff_relay":
                raise DataCorruption("unsupported internal relay job kind")
            next_token = int(token) + 1
            updated = self._connection.execute(
                "UPDATE boundary_outbox SET status='leased',owner=?,fencing_token=?,"
                "claim_count=claim_count+1,lease_acquired_at=?,lease_expires_at=?,"
                "updated_at=? WHERE job_id=? AND status='pending' AND fencing_token=?",
                (
                    owner,
                    next_token,
                    now_text,
                    expires_text,
                    now_text,
                    job_id,
                    token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("internal relay claim CAS lost")
            artifact_bytes = artifact_json.encode("utf-8")
            bundle = HandoffRelayBundle.from_canonical_bytes(artifact_bytes)
            if bundle.artifact_hash != artifact_hash:
                raise DataCorruption("internal handoff bundle hash diverged")
            operation_id = target_operation_id(
                InternalJobKind.HANDOFF, artifact_hash, source_hash
            )
            self._connection.execute(
                "UPDATE boundary_outbox SET target_operation_id=? WHERE job_id=? "
                "AND owner=? AND fencing_token=?",
                (operation_id, job_id, owner, next_token),
            )
            return InternalRelayClaim(
                job_id=job_id,
                job_kind=InternalJobKind.HANDOFF,
                artifact_bytes=artifact_bytes,
                artifact_hash=artifact_hash,
                source_turn_receipt_hash=source_hash,
                target_operation_id=operation_id,
                worker_id=owner,
                fencing_token=next_token,
                lease_expires_at=expires,
            )

    def complete_internal_job(
        self,
        claim: InternalRelayClaim,
        receipt: TargetOperationReceipt,
        *,
        now: datetime,
    ) -> None:
        if type(claim) is not InternalRelayClaim:
            raise TypeError("claim must be exact InternalRelayClaim")
        if type(receipt) is not TargetOperationReceipt:
            raise TypeError("receipt must be exact TargetOperationReceipt")
        now_text = _utc_text(now, "now")
        if now >= claim.lease_expires_at:
            raise ConcurrencyConflict("internal relay lease expired before completion")
        if (
            receipt.operation_id != claim.target_operation_id
            or receipt.job_kind is not claim.job_kind
            or receipt.artifact_hash != claim.artifact_hash
            or receipt.source_turn_receipt_hash != claim.source_turn_receipt_hash
        ):
            raise IdentityConflict("target receipt diverged from internal relay claim")
        receipt_json = receipt.to_canonical_bytes().decode("utf-8")
        receipt_hash = receipt.canonical_hash()
        with self._boundary._transaction():
            updated = self._connection.execute(
                "UPDATE boundary_outbox SET status='acked',owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,target_receipt_json=?,"
                "target_receipt_hash=?,acked_at=?,updated_at=? WHERE job_id=? "
                "AND status='leased' AND owner=? AND fencing_token=?",
                (
                    receipt_json,
                    receipt_hash,
                    now_text,
                    now_text,
                    claim.job_id,
                    claim.worker_id,
                    claim.fencing_token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("internal relay completion CAS lost")

    def release_internal_job(
        self,
        claim: InternalRelayClaim,
        *,
        now: datetime,
    ) -> None:
        if type(claim) is not InternalRelayClaim:
            raise TypeError("claim must be exact InternalRelayClaim")
        now_text = _utc_text(now, "now")
        with self._boundary._transaction():
            row = self._connection.execute(
                "SELECT preparation_failures FROM boundary_outbox WHERE job_id=? "
                "AND status='leased' AND owner=? AND fencing_token=?",
                (claim.job_id, claim.worker_id, claim.fencing_token),
            ).fetchone()
            if row is None:
                raise ConcurrencyConflict("internal relay release CAS lost")
            failures = int(row[0]) + 1
            status = "manual_review" if failures >= 3 else "pending"
            updated = self._connection.execute(
                "UPDATE boundary_outbox SET status=?,owner=NULL,lease_acquired_at=NULL,"
                "lease_expires_at=NULL,preparation_failures=?,updated_at=? WHERE job_id=? "
                "AND status='leased' AND owner=? AND fencing_token=?",
                (
                    status,
                    failures,
                    now_text,
                    claim.job_id,
                    claim.worker_id,
                    claim.fencing_token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("internal relay release CAS lost")

    def claim_public_delivery(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> PublicDispatchClaim | None:
        owner = _require_id(worker_id, "worker_id")
        now_text = _utc_text(now, "now")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive timedelta")
        expires = now + lease_ttl
        expires_text = _utc_text(expires, "lease_expires_at")
        with self._boundary._transaction():
            self._recover_expired_public(now_text)
            row = self._connection.execute(
                "SELECT p.public_row_id,p.lead_key,p.aggregate_turn_id,p.chunk_json,"
                "p.idempotency_key,p.target_binding_hash,p.channel_id,p.channel_scope,"
                "p.scope_subject_id,p.authorization_id,p.allocation_id,"
                "p.immutable_generation,p.source_turn_receipt_hash,p.deadline_at,"
                "p.fencing_token FROM boundary_public_outbox p "
                "WHERE p.status='pending' AND p.deadline_at>? "
                "AND (p.chunk_index=0 OR EXISTS (SELECT 1 FROM boundary_public_outbox q "
                "WHERE q.lead_key=p.lead_key AND q.aggregate_turn_id=p.aggregate_turn_id "
                "AND q.chunk_hash=p.predecessor_chunk_hash AND q.status='delivered')) "
                "ORDER BY p.lead_key,p.aggregate_turn_id,p.chunk_index LIMIT 1",
                (now_text,),
            ).fetchone()
            if row is None:
                return None
            next_token = int(row[14]) + 1
            updated = self._connection.execute(
                "UPDATE boundary_public_outbox SET status='leased',owner=?,fencing_token=?,"
                "claim_count=claim_count+1,lease_acquired_at=?,lease_expires_at=?,"
                "updated_at=? WHERE public_row_id=? AND status='pending' AND fencing_token=?",
                (
                    owner,
                    next_token,
                    now_text,
                    expires_text,
                    now_text,
                    row[0],
                    row[14],
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("public delivery claim CAS lost")
            return PublicDispatchClaim(
                public_row_id=row[0],
                lead_key=row[1],
                aggregate_turn_id=row[2],
                chunk=PublicReplyChunk.from_canonical_bytes(row[3].encode("utf-8")),
                idempotency_key=row[4],
                target_binding_hash=row[5],
                channel_id=row[6],
                channel_scope=row[7],
                scope_subject_id=row[8],
                authorization_id=row[9],
                allocation_id=row[10],
                immutable_generation=row[11],
                source_turn_receipt_hash=row[12],
                deadline_at=datetime.fromisoformat(row[13]),
                worker_id=owner,
                fencing_token=next_token,
                lease_expires_at=expires,
            )

    def _recover_expired_public(self, now_text: str) -> None:
        uncertain = self._connection.execute(
            "SELECT public_row_id FROM boundary_public_outbox "
            "WHERE status='dispatch_fenced' AND lease_expires_at<=?",
            (now_text,),
        ).fetchall()
        for (row_id,) in uncertain:
            self._connection.execute(
                "UPDATE boundary_dispatch_authority SET state='manual_review',"
                "fenced_at=NULL,updated_at=? WHERE public_row_id=? "
                "AND state='dispatch_fenced'",
                (now_text, row_id),
            )
            self._connection.execute(
                "UPDATE boundary_public_outbox SET status='manual_review',owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE public_row_id=? AND status='dispatch_fenced'",
                (now_text, row_id),
            )
        self._connection.execute(
            "UPDATE boundary_public_outbox SET status='pending',owner=NULL,"
            "lease_acquired_at=NULL,lease_expires_at=NULL,updated_at=? "
            "WHERE status='leased' AND lease_expires_at<=?",
            (now_text, now_text),
        )
        expired = self._connection.execute(
            "SELECT public_row_id FROM boundary_public_outbox "
            "WHERE status='pending' AND deadline_at<=?",
            (now_text,),
        ).fetchall()
        for (row_id,) in expired:
            self._connection.execute(
                "UPDATE boundary_dispatch_authority SET state='closed',updated_at=? "
                "WHERE public_row_id=? AND state='bound'",
                (now_text, row_id),
            )
            self._connection.execute(
                "UPDATE boundary_public_outbox SET status='cancelled',updated_at=? "
                "WHERE public_row_id=? AND status='pending'",
                (now_text, row_id),
            )

    def fence_public_delivery(
        self,
        claim: PublicDispatchClaim,
        *,
        now: datetime,
    ) -> None:
        if type(claim) is not PublicDispatchClaim:
            raise TypeError("claim must be exact PublicDispatchClaim")
        now_text = _utc_text(now, "now")
        if now >= claim.deadline_at or now >= claim.lease_expires_at:
            raise ConcurrencyConflict("public delivery claim expired before fence")
        with self._boundary._transaction():
            authority = self._connection.execute(
                "UPDATE boundary_dispatch_authority SET state='dispatch_fenced',"
                "fenced_at=?,cas_revision=cas_revision+1,updated_at=? "
                "WHERE authorization_id=? AND scope_subject_id=? AND channel_scope=? "
                "AND generation=? AND allocation_id=? AND public_row_id=? "
                "AND state='bound'",
                (
                    now_text,
                    now_text,
                    claim.authorization_id,
                    claim.scope_subject_id,
                    claim.channel_scope,
                    claim.immutable_generation,
                    claim.allocation_id,
                    claim.public_row_id,
                ),
            ).rowcount
            if authority != 1:
                raise ConcurrencyConflict("public dispatch authority fence CAS lost")
            updated = self._connection.execute(
                "UPDATE boundary_public_outbox SET status='dispatch_fenced',"
                "dispatch_slots_consumed=1,updated_at=? WHERE public_row_id=? "
                "AND status='leased' AND owner=? AND fencing_token=?",
                (
                    now_text,
                    claim.public_row_id,
                    claim.worker_id,
                    claim.fencing_token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("public outbox fence CAS lost")

    def complete_public_delivery(
        self,
        claim: PublicDispatchClaim,
        receipt: PublicDeliveryReceipt,
        *,
        now: datetime,
    ) -> None:
        if type(claim) is not PublicDispatchClaim:
            raise TypeError("claim must be exact PublicDispatchClaim")
        if type(receipt) is not PublicDeliveryReceipt:
            raise TypeError("receipt must be exact PublicDeliveryReceipt")
        now_text = _utc_text(now, "now")
        if now >= claim.lease_expires_at:
            raise ConcurrencyConflict("public delivery lease expired before receipt commit")
        if (
            receipt.public_row_id != claim.public_row_id
            or receipt.idempotency_key != claim.idempotency_key
        ):
            raise IdentityConflict("public delivery receipt diverged from claim")
        receipt_json = receipt.to_canonical_bytes().decode("utf-8")
        receipt_hash = receipt.canonical_hash()
        with self._boundary._transaction():
            updated = self._connection.execute(
                "UPDATE boundary_public_outbox SET status='delivered',owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,delivery_receipt_json=?,"
                "delivery_receipt_hash=?,updated_at=? WHERE public_row_id=? "
                "AND status='dispatch_fenced' AND owner=? AND fencing_token=?",
                (
                    receipt_json,
                    receipt_hash,
                    now_text,
                    claim.public_row_id,
                    claim.worker_id,
                    claim.fencing_token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("public delivery receipt CAS lost")
            authority = self._connection.execute(
                "UPDATE boundary_dispatch_authority SET state='terminal',"
                "cas_revision=cas_revision+1,updated_at=? WHERE public_row_id=? "
                "AND state='dispatch_fenced'",
                (now_text, claim.public_row_id),
            ).rowcount
            if authority != 1:
                raise ConcurrencyConflict("public delivery terminal CAS lost")

    def release_public_delivery_not_called(
        self,
        claim: PublicDispatchClaim,
        *,
        now: datetime,
    ) -> None:
        if type(claim) is not PublicDispatchClaim:
            raise TypeError("claim must be exact PublicDispatchClaim")
        now_text = _utc_text(now, "now")
        with self._boundary._transaction():
            authority = self._connection.execute(
                "UPDATE boundary_dispatch_authority SET state='bound',fenced_at=NULL,"
                "cas_revision=cas_revision+1,updated_at=? WHERE public_row_id=? "
                "AND state='dispatch_fenced'",
                (now_text, claim.public_row_id),
            ).rowcount
            if authority != 1:
                raise ConcurrencyConflict("public release authority CAS lost")
            updated = self._connection.execute(
                "UPDATE boundary_public_outbox SET status='pending',owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,dispatch_slots_consumed=0,"
                "updated_at=? WHERE public_row_id=? AND status='dispatch_fenced' "
                "AND owner=? AND fencing_token=?",
                (
                    now_text,
                    claim.public_row_id,
                    claim.worker_id,
                    claim.fencing_token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("public release outbox CAS lost")

    def mark_public_delivery_manual_review(
        self,
        claim: PublicDispatchClaim,
        *,
        now: datetime,
    ) -> None:
        if type(claim) is not PublicDispatchClaim:
            raise TypeError("claim must be exact PublicDispatchClaim")
        now_text = _utc_text(now, "now")
        with self._boundary._transaction():
            updated = self._connection.execute(
                "UPDATE boundary_public_outbox SET status='manual_review',owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE public_row_id=? AND status IN ('leased','dispatch_fenced') "
                "AND owner=? AND fencing_token=?",
                (
                    now_text,
                    claim.public_row_id,
                    claim.worker_id,
                    claim.fencing_token,
                ),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("public manual-review outbox CAS lost")
            authority = self._connection.execute(
                "UPDATE boundary_dispatch_authority SET state='manual_review',fenced_at=NULL,"
                "cas_revision=cas_revision+1,updated_at=? WHERE public_row_id=? "
                "AND state IN ('bound','dispatch_fenced')",
                (now_text, claim.public_row_id),
            ).rowcount
            if authority != 1:
                raise ConcurrencyConflict("public manual-review authority CAS lost")


__all__ = ["SQLiteBoundaryWorkerStore"]
