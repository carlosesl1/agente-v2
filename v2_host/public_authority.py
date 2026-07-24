"""Authenticated finite public-delivery authority manifest for shadow turns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import json
from pathlib import Path

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from v2_application.turn_executor import PublicTurnAuthority
from v2_contracts.channel import InboundBatch


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("authority manifest has duplicate keys")
        result[key] = value
    return result


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class _ManifestAuthority:
    authorization_id: str
    subscriber_id: str
    target_binding_hash: str
    channel_id: str
    channel_scope: str
    generation: int
    capability_policy_digest: str
    effect_authorization_binding_digest: str
    contract_digest: str
    deadline_at: datetime
    allocations: tuple[tuple[str, int], ...]
    allocation_manifest_hash: str


class ManifestPublicAuthorityResolver:
    """Resolve only pre-signed, pre-installed and still-unspent allocations."""

    def __init__(
        self,
        *,
        store: SQLiteBoundaryStore,
        manifest_path: Path,
        hmac_key: bytes,
        now: datetime,
    ) -> None:
        if type(store) is not SQLiteBoundaryStore:
            raise TypeError("authority resolver requires exact SQLiteBoundaryStore")
        if not isinstance(manifest_path, Path) or not manifest_path.is_absolute():
            raise ValueError("authority manifest path must be absolute")
        if type(hmac_key) is not bytes or len(hmac_key) < 32:
            raise ValueError("authority manifest HMAC key must contain at least 32 bytes")
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("authority installer now must be exact UTC")
        self._store = store
        self._entries = self._load(manifest_path, hmac_key)
        self._by_subscriber = {entry.subscriber_id: entry for entry in self._entries}
        if len(self._by_subscriber) != len(self._entries):
            raise ValueError("authority manifest subscriber identities must be unique")
        self._install(now)

    @staticmethod
    def _load(path: Path, key: bytes) -> tuple[_ManifestAuthority, ...]:
        try:
            raw = path.read_bytes()
            payload = json.loads(raw, object_pairs_hook=_unique_object)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("authority manifest is unreadable") from exc
        if type(payload) is not dict or set(payload) != {
            "schema",
            "authorities",
            "hmac_sha256",
        }:
            raise ValueError("authority manifest fields mismatch")
        if payload["schema"] != "v2-public-authority-manifest-v1":
            raise ValueError("authority manifest schema mismatch")
        signature = payload["hmac_sha256"]
        if type(signature) is not str or len(signature) != 64:
            raise ValueError("authority manifest signature is not SHA-256 hex")
        signed = _canonical(
            {"schema": payload["schema"], "authorities": payload["authorities"]}
        )
        expected = hmac.new(key, signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("authority manifest authentication failed")
        if type(payload["authorities"]) is not list or not payload["authorities"]:
            raise ValueError("authority manifest must contain finite authorities")
        return tuple(
            ManifestPublicAuthorityResolver._entry(item)
            for item in payload["authorities"]
        )

    @staticmethod
    def _entry(value: object) -> _ManifestAuthority:
        fields = {
            "authorization_id",
            "subscriber_id",
            "target_binding_hash",
            "channel_id",
            "channel_scope",
            "generation",
            "capability_policy_digest",
            "effect_authorization_binding_digest",
            "contract_digest",
            "deadline_at",
            "allocations",
        }
        if type(value) is not dict or set(value) != fields:
            raise ValueError("authority entry fields mismatch")
        raw_allocations = value["allocations"]
        if type(raw_allocations) is not list or not raw_allocations:
            raise ValueError("authority allocations must be a finite list")
        allocations: list[tuple[str, int]] = []
        for item in raw_allocations:
            if type(item) is not dict or set(item) != {"allocation_id", "ordinal"}:
                raise ValueError("authority allocation fields mismatch")
            allocation_id = item["allocation_id"]
            ordinal = item["ordinal"]
            if type(allocation_id) is not str or not allocation_id:
                raise ValueError("authority allocation ID is invalid")
            if type(ordinal) is not int or ordinal < 0 or ordinal > 31:
                raise ValueError("authority allocation ordinal is invalid")
            allocations.append((allocation_id, ordinal))
        if len({item[0] for item in allocations}) != len(allocations):
            raise ValueError("authority allocation IDs must be unique")
        try:
            deadline = datetime.fromisoformat(value["deadline_at"])
        except (TypeError, ValueError) as exc:
            raise ValueError("authority deadline is invalid") from exc
        if deadline.tzinfo is None or deadline.utcoffset() != timedelta(0):
            raise ValueError("authority deadline must be exact UTC")
        manifest_hash = hashlib.sha256(_canonical(raw_allocations)).hexdigest()
        candidate = PublicTurnAuthority(
            authorization_kind="conversation_test",
            authorization_id=value["authorization_id"],
            scope_subject_id=value["subscriber_id"],
            target_binding_hash=value["target_binding_hash"],
            channel_id=value["channel_id"],
            channel_scope=value["channel_scope"],
            immutable_generation=value["generation"],
            allocation_ids=(allocations[0][0],),
            capability_policy_digest=value["capability_policy_digest"],
            effect_authorization_binding_digest=value[
                "effect_authorization_binding_digest"
            ],
            contract_digest=value["contract_digest"],
            allocation_manifest_hash=manifest_hash,
            deadline_at=deadline,
        )
        return _ManifestAuthority(
            authorization_id=candidate.authorization_id,
            subscriber_id=candidate.scope_subject_id,
            target_binding_hash=candidate.target_binding_hash,
            channel_id=candidate.channel_id,
            channel_scope=candidate.channel_scope,
            generation=candidate.immutable_generation,
            capability_policy_digest=candidate.capability_policy_digest,
            effect_authorization_binding_digest=candidate.effect_authorization_binding_digest,
            contract_digest=candidate.contract_digest,
            deadline_at=candidate.deadline_at,
            allocations=tuple(allocations),
            allocation_manifest_hash=manifest_hash,
        )

    def _install(self, now: datetime) -> None:
        for entry in self._entries:
            common = (
                entry.authorization_id,
                entry.subscriber_id,
                entry.channel_scope,
                entry.generation,
                "conversation_test",
                None,
                None,
                entry.contract_digest,
                entry.effect_authorization_binding_digest,
                entry.capability_policy_digest,
                entry.target_binding_hash,
                entry.allocation_manifest_hash,
            )
            with self._store._transaction():
                existing = self._store._connection.execute(
                    "SELECT contract_digest,allocation_manifest_hash FROM "
                    "boundary_dispatch_authority WHERE authorization_id=? AND "
                    "scope_subject_id=? AND channel_scope=? AND generation=? AND "
                    "allocation_id='__header__'",
                    common[:4],
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != (entry.contract_digest, entry.allocation_manifest_hash):
                        raise ValueError("installed authority conflicts with signed manifest")
                    continue
                self._store._connection.execute(
                    "INSERT INTO boundary_dispatch_authority "
                    "(authorization_id,scope_subject_id,channel_scope,generation,allocation_id,"
                    "row_kind,authorization_kind,qualification_id,scenario_id,contract_digest,"
                    "effect_authorization_binding_digest,capability_policy_digest,target_binding_hash,"
                    "allowed_chunk_ordinal,allocation_manifest_hash,state,public_row_id,cas_revision,"
                    "closure_receipt_hash,created_at,updated_at,fenced_at) "
                    "VALUES (?,?,?,?,?,'generation_header',?,?,?,?,?,?,?,?,?,'open',NULL,0,NULL,?,?,NULL)",
                    common[:4]
                    + ("__header__",)
                    + common[4:11]
                    + (None, common[11], now.isoformat(), now.isoformat()),
                )
                for allocation_id, ordinal in entry.allocations:
                    self._store._connection.execute(
                        "INSERT INTO boundary_dispatch_authority "
                        "(authorization_id,scope_subject_id,channel_scope,generation,allocation_id,"
                        "row_kind,authorization_kind,qualification_id,scenario_id,contract_digest,"
                        "effect_authorization_binding_digest,capability_policy_digest,target_binding_hash,"
                        "allowed_chunk_ordinal,allocation_manifest_hash,state,public_row_id,cas_revision,"
                        "closure_receipt_hash,created_at,updated_at,fenced_at) "
                        "VALUES (?,?,?,?,?,'allocation',?,?,?,?,?,?,?,?,?,'available',NULL,0,NULL,?,?,NULL)",
                        common[:4]
                        + (allocation_id,)
                        + common[4:11]
                        + (ordinal, common[11], now.isoformat(), now.isoformat()),
                    )

    def resolve(
        self,
        batch: InboundBatch,
        *,
        chunk_count: int,
        now: datetime,
    ) -> PublicTurnAuthority:
        if type(batch) is not InboundBatch:
            raise TypeError("authority resolver requires exact InboundBatch")
        if type(chunk_count) is not int or chunk_count < 1 or chunk_count > 32:
            raise ValueError("public chunk count is outside the finite authority range")
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("authority resolution now must be exact UTC")
        entry = self._by_subscriber.get(batch.subscriber_id)
        if entry is None or now >= entry.deadline_at:
            raise ValueError("no active signed shadow authority for subscriber")
        selected: list[str] = []
        for ordinal in range(chunk_count):
            row = self._store._connection.execute(
                "SELECT allocation_id FROM boundary_dispatch_authority WHERE "
                "authorization_id=? AND scope_subject_id=? AND channel_scope=? AND generation=? "
                "AND row_kind='allocation' AND allowed_chunk_ordinal=? AND state='available' "
                "ORDER BY allocation_id LIMIT 1",
                (
                    entry.authorization_id,
                    entry.subscriber_id,
                    entry.channel_scope,
                    entry.generation,
                    ordinal,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("signed shadow public allocation is exhausted")
            selected.append(row[0])
        return PublicTurnAuthority(
            authorization_kind="conversation_test",
            authorization_id=entry.authorization_id,
            scope_subject_id=entry.subscriber_id,
            target_binding_hash=entry.target_binding_hash,
            channel_id=entry.channel_id,
            channel_scope=entry.channel_scope,
            immutable_generation=entry.generation,
            allocation_ids=tuple(selected),
            capability_policy_digest=entry.capability_policy_digest,
            effect_authorization_binding_digest=entry.effect_authorization_binding_digest,
            contract_digest=entry.contract_digest,
            allocation_manifest_hash=entry.allocation_manifest_hash,
            deadline_at=entry.deadline_at,
        )


__all__ = ["ManifestPublicAuthorityResolver"]
