"""Literal six-table Phase 7 boundary schema for SQLite/PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Final, Literal


SCHEMA_VERSION: Final = 7
TABLE_NAMES: Final = (
    "boundary_state",
    "boundary_events",
    "boundary_commands",
    "boundary_outbox",
    "legacy_import_claims",
    "decision_comparisons",
)
SCHEMA_VERSION_V8: Final = 8
BOUNDARY_V8_TABLES: Final = (
    "boundary_state",
    "boundary_events",
    "boundary_event_sources",
    "boundary_turn_artifacts",
    "boundary_commands",
    "boundary_command_relays",
    "boundary_outbox",
    "boundary_public_outbox",
    "boundary_dispatch_authority",
    "legacy_import_claims",
    "decision_comparisons",
)
Dialect = Literal["sqlite", "postgresql"]


def _sqlite_hash(name: str) -> str:
    return (
        f"length({name}) = 64 AND {name} = lower({name}) "
        f"AND {name} NOT GLOB '*[^0-9a-f]*'"
    )


def _sqlite_id(name: str) -> str:
    return f"length({name}) BETWEEN 1 AND 256 AND instr({name}, char(0)) = 0"


def _sqlite_timestamp(name: str) -> str:
    return (
        f"length({name}) BETWEEN 25 AND 32 AND substr({name}, 11, 1) = 'T' "
        f"AND substr({name}, -6) = '+00:00' AND instr({name}, char(0)) = 0"
    )


def render_sqlite() -> str:
    """Return the deterministic SQLite 3 STRICT DDL."""

    ident = _sqlite_id
    digest = _sqlite_hash
    stamp = _sqlite_timestamp
    return f"""CREATE TABLE boundary_state (
    lead_key TEXT NOT NULL CONSTRAINT pk_boundary_state PRIMARY KEY CHECK ({ident('lead_key')}),
    version INTEGER NOT NULL CHECK (version >= 0),
    state_json TEXT NOT NULL CHECK (json_valid(state_json)),
    state_hash TEXT NOT NULL CHECK ({digest('state_hash')}),
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
    created_at TEXT NOT NULL CHECK ({stamp('created_at')}),
    updated_at TEXT NOT NULL CHECK ({stamp('updated_at')})
) STRICT;

CREATE TABLE boundary_events (
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    event_id TEXT NOT NULL CHECK ({ident('event_id')}),
    event_hash TEXT NOT NULL CHECK ({digest('event_hash')}),
    commit_hash TEXT NOT NULL CHECK ({digest('commit_hash')}),
    state_version INTEGER NOT NULL CHECK (state_version >= 1),
    occurred_at TEXT NOT NULL CHECK ({stamp('occurred_at')}),
    CONSTRAINT pk_boundary_events PRIMARY KEY (lead_key, event_id),
    CONSTRAINT uq_boundary_events_version UNIQUE (lead_key, state_version),
    CONSTRAINT fk_boundary_events_state FOREIGN KEY (lead_key)
        REFERENCES boundary_state (lead_key)
) STRICT;

CREATE TABLE boundary_commands (
    command_id TEXT NOT NULL CONSTRAINT pk_boundary_commands PRIMARY KEY CHECK ({ident('command_id')}),
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    event_id TEXT NOT NULL CHECK ({ident('event_id')}),
    command_type TEXT NOT NULL CHECK (command_type IN ('reservation', 'payment_settlement')),
    command_json TEXT NOT NULL CHECK (json_valid(command_json)),
    command_hash TEXT NOT NULL CHECK ({digest('command_hash')}),
    created_at TEXT NOT NULL CHECK ({stamp('created_at')}),
    CONSTRAINT uq_boundary_commands_event UNIQUE (lead_key, event_id, command_id),
    CONSTRAINT fk_boundary_commands_event FOREIGN KEY (lead_key, event_id)
        REFERENCES boundary_events (lead_key, event_id)
) STRICT;

CREATE TABLE boundary_outbox (
    message_id TEXT NOT NULL CONSTRAINT pk_boundary_outbox PRIMARY KEY CHECK ({ident('message_id')}),
    idempotency_key TEXT NOT NULL CONSTRAINT uq_boundary_outbox_idempotency UNIQUE CHECK ({ident('idempotency_key')}),
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    event_id TEXT NOT NULL CHECK ({ident('event_id')}),
    workflow_id TEXT NOT NULL CHECK ({ident('workflow_id')}),
    command_id TEXT CHECK (command_id IS NULL OR {ident('command_id')}),
    kind TEXT NOT NULL CHECK ({ident('kind')}),
    template_id TEXT NOT NULL CHECK ({ident('template_id')}),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    payload_hash TEXT NOT NULL CHECK ({digest('payload_hash')}),
    created_at TEXT NOT NULL CHECK ({stamp('created_at')}),
    CONSTRAINT fk_boundary_outbox_command FOREIGN KEY (lead_key, event_id, command_id)
        REFERENCES boundary_commands (lead_key, event_id, command_id),
    CONSTRAINT fk_boundary_outbox_event FOREIGN KEY (lead_key, event_id)
        REFERENCES boundary_events (lead_key, event_id)
) STRICT;

CREATE TABLE legacy_import_claims (
    lead_key TEXT NOT NULL CONSTRAINT pk_legacy_import_claims PRIMARY KEY CHECK ({ident('lead_key')}),
    snapshot_hash TEXT NOT NULL CHECK ({digest('snapshot_hash')}),
    disposition TEXT NOT NULL CHECK (disposition IN ('migrated', 'manual_review', 'rejected')),
    state_hash TEXT NOT NULL CHECK ({digest('state_hash')}),
    claimed_at TEXT NOT NULL CHECK ({stamp('claimed_at')}),
    CONSTRAINT fk_legacy_import_claims_state FOREIGN KEY (lead_key)
        REFERENCES boundary_state (lead_key)
) STRICT;

CREATE TABLE decision_comparisons (
    comparison_id TEXT NOT NULL CONSTRAINT pk_decision_comparisons PRIMARY KEY CHECK ({ident('comparison_id')}),
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    event_id TEXT NOT NULL CHECK ({ident('event_id')}),
    old_hash TEXT NOT NULL CHECK ({digest('old_hash')}),
    new_hash TEXT NOT NULL CHECK ({digest('new_hash')}),
    severity TEXT NOT NULL CHECK (severity IN ('equivalent', 'noncritical', 'critical')),
    changed_fields_json TEXT NOT NULL CHECK (json_valid(changed_fields_json)),
    created_at TEXT NOT NULL CHECK ({stamp('created_at')}),
    CONSTRAINT fk_decision_comparisons_state FOREIGN KEY (lead_key)
        REFERENCES boundary_state (lead_key)
) STRICT;
"""


def render_sqlite_v8() -> str:
    """Return the deterministic, additive Phase 8 SQLite boundary DDL."""

    ident = _sqlite_id
    digest = _sqlite_hash
    stamp = _sqlite_timestamp
    artifact_kinds = (
        "'frame_commitment','read_observation','typed_fact',"
        "'normalized_tool_proposal','learning_proposal','maya_closure',"
        "'maya_proposal','kernel_decision'"
    )
    return f"""CREATE TABLE boundary_state (
    lead_key TEXT NOT NULL CONSTRAINT pk_boundary_state PRIMARY KEY CHECK ({ident('lead_key')}),
    version INTEGER NOT NULL CHECK (version >= 0),
    state_json TEXT NOT NULL CHECK (json_valid(state_json)),
    state_hash TEXT NOT NULL CHECK ({digest('state_hash')}),
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
    created_at TEXT NOT NULL CHECK ({stamp('created_at')}),
    updated_at TEXT NOT NULL CHECK ({stamp('updated_at')})
) STRICT;

CREATE TABLE boundary_events (
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    aggregate_turn_id TEXT NOT NULL CHECK ({ident('aggregate_turn_id')}),
    event_hash TEXT NOT NULL CHECK ({digest('event_hash')}),
    commit_hash TEXT NOT NULL CHECK ({digest('commit_hash')}),
    turn_receipt_json TEXT NOT NULL CHECK (json_valid(turn_receipt_json)),
    turn_receipt_hash TEXT NOT NULL CHECK ({digest('turn_receipt_hash')}),
    state_version INTEGER NOT NULL CHECK (state_version >= 1),
    occurred_at TEXT NOT NULL CHECK ({stamp('occurred_at')}),
    CONSTRAINT pk_boundary_events PRIMARY KEY (lead_key, aggregate_turn_id),
    CONSTRAINT uq_boundary_events_version UNIQUE (lead_key, state_version),
    CONSTRAINT uq_boundary_events_receipt UNIQUE (turn_receipt_hash),
    CONSTRAINT fk_boundary_events_state FOREIGN KEY (lead_key)
        REFERENCES boundary_state (lead_key)
) STRICT;

CREATE TABLE boundary_event_sources (
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    aggregate_turn_id TEXT NOT NULL CHECK ({ident('aggregate_turn_id')}),
    source_index INTEGER NOT NULL CHECK (source_index >= 0),
    source_event_id TEXT NOT NULL CHECK ({ident('source_event_id')}),
    source_event_hash TEXT NOT NULL CHECK ({digest('source_event_hash')}),
    source_event_json TEXT NOT NULL CHECK (json_valid(source_event_json)),
    source_turn_receipt_hash TEXT NOT NULL CHECK ({digest('source_turn_receipt_hash')}),
    CONSTRAINT pk_boundary_event_sources PRIMARY KEY
        (lead_key, aggregate_turn_id, source_index),
    CONSTRAINT uq_boundary_event_sources_identity UNIQUE (lead_key, source_event_id),
    CONSTRAINT uq_boundary_event_sources_turn_identity UNIQUE
        (lead_key, aggregate_turn_id, source_event_id),
    CONSTRAINT fk_boundary_event_sources_event FOREIGN KEY (lead_key, aggregate_turn_id)
        REFERENCES boundary_events (lead_key, aggregate_turn_id)
) STRICT;

CREATE TABLE boundary_turn_artifacts (
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    aggregate_turn_id TEXT NOT NULL CHECK ({ident('aggregate_turn_id')}),
    artifact_index INTEGER NOT NULL CHECK (artifact_index >= 0),
    artifact_id TEXT NOT NULL CHECK ({ident('artifact_id')}),
    artifact_kind TEXT NOT NULL CHECK (artifact_kind IN ({artifact_kinds})),
    frame_sequence INTEGER CHECK (frame_sequence IS NULL OR frame_sequence >= 1),
    frame_reference TEXT CHECK (frame_reference IS NULL OR ({digest('frame_reference')})),
    artifact_json TEXT NOT NULL CHECK (json_valid(artifact_json)),
    artifact_hash TEXT NOT NULL CHECK ({digest('artifact_hash')}),
    source_turn_receipt_hash TEXT NOT NULL CHECK ({digest('source_turn_receipt_hash')}),
    CONSTRAINT pk_boundary_turn_artifacts PRIMARY KEY
        (lead_key, aggregate_turn_id, artifact_index),
    CONSTRAINT uq_boundary_turn_artifacts_id UNIQUE (lead_key, artifact_id),
    CONSTRAINT uq_boundary_turn_artifacts_frame UNIQUE
        (lead_key, aggregate_turn_id, frame_sequence),
    CONSTRAINT fk_boundary_turn_artifacts_event FOREIGN KEY (lead_key, aggregate_turn_id)
        REFERENCES boundary_events (lead_key, aggregate_turn_id)
) STRICT;

CREATE INDEX idx_boundary_turn_artifacts_frame
    ON boundary_turn_artifacts (lead_key, aggregate_turn_id, frame_reference);

CREATE TABLE boundary_commands (
    command_id TEXT NOT NULL CONSTRAINT pk_boundary_commands PRIMARY KEY CHECK ({ident('command_id')}),
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    aggregate_turn_id TEXT NOT NULL CHECK ({ident('aggregate_turn_id')}),
    command_type TEXT NOT NULL CHECK (command_type IN ('reservation', 'payment_settlement')),
    command_json TEXT NOT NULL CHECK (json_valid(command_json)),
    command_hash TEXT NOT NULL CHECK ({digest('command_hash')}),
    source_turn_receipt_hash TEXT NOT NULL CHECK ({digest('source_turn_receipt_hash')}),
    created_at TEXT NOT NULL CHECK ({stamp('created_at')}),
    CONSTRAINT uq_boundary_commands_event UNIQUE
        (lead_key, aggregate_turn_id, command_id),
    CONSTRAINT fk_boundary_commands_event FOREIGN KEY (lead_key, aggregate_turn_id)
        REFERENCES boundary_events (lead_key, aggregate_turn_id)
) STRICT;

CREATE TABLE boundary_command_relays (
    relay_id TEXT NOT NULL CONSTRAINT pk_boundary_command_relays PRIMARY KEY CHECK ({ident('relay_id')}),
    command_id TEXT NOT NULL CONSTRAINT uq_boundary_command_relays_command UNIQUE CHECK ({ident('command_id')}),
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    aggregate_turn_id TEXT NOT NULL CHECK ({ident('aggregate_turn_id')}),
    bundle_json TEXT NOT NULL CHECK (json_valid(bundle_json)),
    bundle_hash TEXT NOT NULL CHECK ({digest('bundle_hash')}),
    source_turn_receipt_hash TEXT NOT NULL CHECK ({digest('source_turn_receipt_hash')}),
    status TEXT NOT NULL CHECK (status IN ('pending','leased','acked','cancelled','manual_review')),
    owner TEXT CHECK (owner IS NULL OR {ident('owner')}),
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
    lease_acquired_at TEXT CHECK (lease_acquired_at IS NULL OR ({stamp('lease_acquired_at')})),
    lease_expires_at TEXT CHECK (lease_expires_at IS NULL OR ({stamp('lease_expires_at')})),
    claim_count INTEGER NOT NULL CHECK (claim_count >= 0),
    preparation_failures INTEGER NOT NULL CHECK (preparation_failures BETWEEN 0 AND 3),
    target_receipt_json TEXT CHECK (target_receipt_json IS NULL OR json_valid(target_receipt_json)),
    target_receipt_hash TEXT CHECK (target_receipt_hash IS NULL OR ({digest('target_receipt_hash')})),
    acked_at TEXT CHECK (acked_at IS NULL OR ({stamp('acked_at')})),
    updated_at TEXT NOT NULL CHECK ({stamp('updated_at')}),
    CONSTRAINT ck_boundary_command_relays_fence CHECK (fencing_token = claim_count),
    CONSTRAINT ck_boundary_command_relays_lease CHECK (
        (status = 'leased' AND owner IS NOT NULL AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (status <> 'leased' AND owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT ck_boundary_command_relays_receipt CHECK (
        (status = 'acked' AND target_receipt_json IS NOT NULL AND target_receipt_hash IS NOT NULL AND acked_at IS NOT NULL)
        OR (status <> 'acked' AND target_receipt_json IS NULL AND target_receipt_hash IS NULL AND acked_at IS NULL)
    ),
    CONSTRAINT fk_boundary_command_relays_command
        FOREIGN KEY (lead_key, aggregate_turn_id, command_id)
        REFERENCES boundary_commands (lead_key, aggregate_turn_id, command_id)
) STRICT;

CREATE INDEX idx_boundary_command_relays_claim
    ON boundary_command_relays (status, lease_expires_at, relay_id);

CREATE TABLE boundary_outbox (
    job_id TEXT NOT NULL CONSTRAINT pk_boundary_outbox PRIMARY KEY CHECK ({ident('job_id')}),
    job_kind TEXT NOT NULL CHECK (job_kind IN ('handoff_relay','learning_proposal')),
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    aggregate_turn_id TEXT NOT NULL CHECK ({ident('aggregate_turn_id')}),
    artifact_json TEXT NOT NULL CHECK (json_valid(artifact_json)),
    artifact_hash TEXT NOT NULL CHECK ({digest('artifact_hash')}),
    source_turn_receipt_hash TEXT NOT NULL CHECK ({digest('source_turn_receipt_hash')}),
    qualification_id TEXT CHECK (qualification_id IS NULL OR {ident('qualification_id')}),
    epoch INTEGER CHECK (epoch IS NULL OR epoch >= 1),
    target_operation_id TEXT NOT NULL CONSTRAINT uq_boundary_outbox_target UNIQUE CHECK ({ident('target_operation_id')}),
    status TEXT NOT NULL CHECK (status IN ('pending','leased','acked','cancelled','manual_review')),
    owner TEXT CHECK (owner IS NULL OR {ident('owner')}),
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
    lease_acquired_at TEXT CHECK (lease_acquired_at IS NULL OR ({stamp('lease_acquired_at')})),
    lease_expires_at TEXT CHECK (lease_expires_at IS NULL OR ({stamp('lease_expires_at')})),
    claim_count INTEGER NOT NULL CHECK (claim_count >= 0),
    preparation_failures INTEGER NOT NULL CHECK (preparation_failures BETWEEN 0 AND 3),
    target_receipt_json TEXT CHECK (target_receipt_json IS NULL OR json_valid(target_receipt_json)),
    target_receipt_hash TEXT CHECK (target_receipt_hash IS NULL OR ({digest('target_receipt_hash')})),
    acked_at TEXT CHECK (acked_at IS NULL OR ({stamp('acked_at')})),
    updated_at TEXT NOT NULL CHECK ({stamp('updated_at')}),
    CONSTRAINT ck_boundary_outbox_qualification CHECK
        ((qualification_id IS NULL) = (epoch IS NULL)),
    CONSTRAINT ck_boundary_outbox_fence CHECK (fencing_token = claim_count),
    CONSTRAINT ck_boundary_outbox_lease CHECK (
        (status = 'leased' AND owner IS NOT NULL AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (status <> 'leased' AND owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT ck_boundary_outbox_receipt CHECK (
        (status = 'acked' AND target_receipt_json IS NOT NULL AND target_receipt_hash IS NOT NULL AND acked_at IS NOT NULL)
        OR (status <> 'acked' AND target_receipt_json IS NULL AND target_receipt_hash IS NULL AND acked_at IS NULL)
    ),
    CONSTRAINT fk_boundary_outbox_event FOREIGN KEY (lead_key, aggregate_turn_id)
        REFERENCES boundary_events (lead_key, aggregate_turn_id)
) STRICT;

CREATE INDEX idx_boundary_outbox_claim
    ON boundary_outbox (status, lease_expires_at, job_id);

CREATE TABLE boundary_dispatch_authority (
    authorization_id TEXT NOT NULL CHECK ({ident('authorization_id')}),
    scope_subject_id TEXT NOT NULL CHECK ({ident('scope_subject_id')}),
    channel_scope TEXT NOT NULL CHECK ({ident('channel_scope')}),
    generation INTEGER NOT NULL CHECK (generation >= 1),
    allocation_id TEXT NOT NULL CHECK ({ident('allocation_id')}),
    row_kind TEXT NOT NULL CHECK (row_kind IN ('generation_header','allocation')),
    authorization_kind TEXT NOT NULL CHECK (authorization_kind IN ('conversation_test','e2e')),
    qualification_id TEXT CHECK (qualification_id IS NULL OR {ident('qualification_id')}),
    scenario_id TEXT CHECK (scenario_id IS NULL OR {ident('scenario_id')}),
    contract_digest TEXT NOT NULL CHECK ({digest('contract_digest')}),
    effect_authorization_binding_digest TEXT NOT NULL CHECK ({digest('effect_authorization_binding_digest')}),
    capability_policy_digest TEXT NOT NULL CHECK ({digest('capability_policy_digest')}),
    target_binding_hash TEXT NOT NULL CHECK ({digest('target_binding_hash')}),
    allowed_chunk_ordinal INTEGER CHECK (allowed_chunk_ordinal IS NULL OR allowed_chunk_ordinal >= 0),
    allocation_manifest_hash TEXT NOT NULL CHECK ({digest('allocation_manifest_hash')}),
    state TEXT NOT NULL CHECK (state IN ('open','available','bound','dispatch_fenced','terminal','closed','manual_review')),
    public_row_id TEXT CHECK (public_row_id IS NULL OR {ident('public_row_id')}),
    cas_revision INTEGER NOT NULL CHECK (cas_revision >= 0),
    closure_receipt_hash TEXT CHECK (closure_receipt_hash IS NULL OR ({digest('closure_receipt_hash')})),
    created_at TEXT NOT NULL CHECK ({stamp('created_at')}),
    updated_at TEXT NOT NULL CHECK ({stamp('updated_at')}),
    fenced_at TEXT CHECK (fenced_at IS NULL OR ({stamp('fenced_at')})),
    CONSTRAINT pk_boundary_dispatch_authority PRIMARY KEY
        (authorization_id, scope_subject_id, channel_scope, generation, allocation_id),
    CONSTRAINT uq_boundary_dispatch_authority_public_row UNIQUE (public_row_id),
    CONSTRAINT ck_boundary_dispatch_authority_e2e CHECK (
        (authorization_kind = 'conversation_test' AND qualification_id IS NULL AND scenario_id IS NULL)
        OR (authorization_kind = 'e2e' AND qualification_id IS NOT NULL AND scenario_id IS NOT NULL)
    ),
    CONSTRAINT ck_boundary_dispatch_authority_row CHECK (
        (row_kind = 'generation_header' AND allocation_id = '__header__' AND allowed_chunk_ordinal IS NULL
            AND public_row_id IS NULL AND state IN ('open','closed','manual_review'))
        OR (row_kind = 'allocation' AND allocation_id <> '__header__' AND allowed_chunk_ordinal IS NOT NULL
            AND state IN ('available','bound','dispatch_fenced','terminal','closed','manual_review'))
    ),
    CONSTRAINT ck_boundary_dispatch_authority_binding CHECK (
        (state = 'available' AND public_row_id IS NULL)
        OR state IN ('open','closed','manual_review')
        OR (state IN ('bound','dispatch_fenced','terminal') AND public_row_id IS NOT NULL)
    ),
    CONSTRAINT ck_boundary_dispatch_authority_fenced CHECK (
        (state IN ('dispatch_fenced','terminal') AND fenced_at IS NOT NULL)
        OR (state NOT IN ('dispatch_fenced','terminal') AND fenced_at IS NULL)
    )
) STRICT;

CREATE INDEX idx_boundary_dispatch_authority_state
    ON boundary_dispatch_authority
        (authorization_id, scope_subject_id, channel_scope, state, generation);

CREATE TRIGGER trg_boundary_dispatch_authority_single_open_insert
BEFORE INSERT ON boundary_dispatch_authority
WHEN NEW.row_kind = 'generation_header' AND NEW.state <> 'closed'
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM boundary_dispatch_authority
        WHERE authorization_id = NEW.authorization_id
          AND scope_subject_id = NEW.scope_subject_id
          AND channel_scope = NEW.channel_scope
          AND row_kind = 'generation_header'
          AND state <> 'closed'
    ) THEN RAISE(ABORT, 'open dispatch authority generation already exists') END;
END;

CREATE TRIGGER trg_boundary_dispatch_authority_single_open_update
BEFORE UPDATE OF state ON boundary_dispatch_authority
WHEN NEW.row_kind = 'generation_header' AND NEW.state <> 'closed'
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM boundary_dispatch_authority
        WHERE authorization_id = NEW.authorization_id
          AND scope_subject_id = NEW.scope_subject_id
          AND channel_scope = NEW.channel_scope
          AND row_kind = 'generation_header'
          AND generation <> NEW.generation
          AND state <> 'closed'
    ) THEN RAISE(ABORT, 'open dispatch authority generation already exists') END;
END;

CREATE TABLE boundary_public_outbox (
    public_row_id TEXT NOT NULL CONSTRAINT pk_boundary_public_outbox PRIMARY KEY CHECK ({ident('public_row_id')}),
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    aggregate_turn_id TEXT NOT NULL CHECK ({ident('aggregate_turn_id')}),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    idempotency_key TEXT NOT NULL CONSTRAINT uq_boundary_public_outbox_idempotency UNIQUE CHECK ({ident('idempotency_key')}),
    target_binding_hash TEXT NOT NULL CHECK ({digest('target_binding_hash')}),
    channel_id TEXT NOT NULL CHECK ({ident('channel_id')}),
    channel_scope TEXT NOT NULL CHECK ({ident('channel_scope')}),
    chunk_json TEXT NOT NULL CHECK (json_valid(chunk_json)),
    chunk_hash TEXT NOT NULL CHECK ({digest('chunk_hash')}),
    predecessor_chunk_hash TEXT CHECK (predecessor_chunk_hash IS NULL OR ({digest('predecessor_chunk_hash')})),
    status TEXT NOT NULL CHECK (status IN ('pending','leased','dispatch_fenced','delivered','cancelled','manual_review')),
    owner TEXT CHECK (owner IS NULL OR {ident('owner')}),
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
    lease_acquired_at TEXT CHECK (lease_acquired_at IS NULL OR ({stamp('lease_acquired_at')})),
    lease_expires_at TEXT CHECK (lease_expires_at IS NULL OR ({stamp('lease_expires_at')})),
    claim_count INTEGER NOT NULL CHECK (claim_count >= 0),
    preparation_failures INTEGER NOT NULL CHECK (preparation_failures BETWEEN 0 AND 3),
    dispatch_slots_consumed INTEGER NOT NULL CHECK (dispatch_slots_consumed IN (0,1)),
    authorization_kind TEXT NOT NULL CHECK (authorization_kind IN ('conversation_test','e2e')),
    authorization_id TEXT NOT NULL CHECK ({ident('authorization_id')}),
    scope_subject_id TEXT NOT NULL CHECK ({ident('scope_subject_id')}),
    allocation_id TEXT NOT NULL CHECK ({ident('allocation_id')}),
    immutable_generation INTEGER NOT NULL CHECK (immutable_generation >= 1),
    qualification_id TEXT CHECK (qualification_id IS NULL OR {ident('qualification_id')}),
    scenario_id TEXT CHECK (scenario_id IS NULL OR {ident('scenario_id')}),
    capability_policy_digest TEXT NOT NULL CHECK ({digest('capability_policy_digest')}),
    effect_authorization_binding_digest TEXT NOT NULL CHECK ({digest('effect_authorization_binding_digest')}),
    effective_turn_binding_digest TEXT NOT NULL CHECK ({digest('effective_turn_binding_digest')}),
    source_turn_receipt_hash TEXT NOT NULL CHECK ({digest('source_turn_receipt_hash')}),
    delivery_receipt_json TEXT CHECK (delivery_receipt_json IS NULL OR json_valid(delivery_receipt_json)),
    delivery_receipt_hash TEXT CHECK (delivery_receipt_hash IS NULL OR ({digest('delivery_receipt_hash')})),
    deadline_at TEXT NOT NULL CHECK ({stamp('deadline_at')}),
    created_at TEXT NOT NULL CHECK ({stamp('created_at')}),
    updated_at TEXT NOT NULL CHECK ({stamp('updated_at')}),
    CONSTRAINT uq_boundary_public_outbox_chunk UNIQUE (lead_key, aggregate_turn_id, chunk_index),
    CONSTRAINT ck_boundary_public_outbox_fence CHECK (fencing_token = claim_count),
    CONSTRAINT ck_boundary_public_outbox_e2e CHECK (
        (authorization_kind = 'conversation_test' AND qualification_id IS NULL AND scenario_id IS NULL)
        OR (authorization_kind = 'e2e' AND qualification_id IS NOT NULL AND scenario_id IS NOT NULL)
    ),
    CONSTRAINT ck_boundary_public_outbox_lease CHECK (
        (status IN ('leased','dispatch_fenced') AND owner IS NOT NULL
            AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (status NOT IN ('leased','dispatch_fenced') AND owner IS NULL
            AND lease_acquired_at IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT ck_boundary_public_outbox_slot CHECK (
        (status IN ('pending','leased','cancelled') AND dispatch_slots_consumed = 0)
        OR (status IN ('dispatch_fenced','delivered') AND dispatch_slots_consumed = 1)
        OR status = 'manual_review'
    ),
    CONSTRAINT ck_boundary_public_outbox_receipt CHECK (
        (status = 'delivered' AND delivery_receipt_json IS NOT NULL AND delivery_receipt_hash IS NOT NULL)
        OR (status <> 'delivered' AND delivery_receipt_json IS NULL AND delivery_receipt_hash IS NULL)
    ),
    CONSTRAINT fk_boundary_public_outbox_event FOREIGN KEY (lead_key, aggregate_turn_id)
        REFERENCES boundary_events (lead_key, aggregate_turn_id),
    CONSTRAINT fk_boundary_public_outbox_authority FOREIGN KEY
        (authorization_id, scope_subject_id, channel_scope, immutable_generation, allocation_id)
        REFERENCES boundary_dispatch_authority
        (authorization_id, scope_subject_id, channel_scope, generation, allocation_id)
) STRICT;

CREATE INDEX idx_boundary_public_outbox_claim
    ON boundary_public_outbox (status, lease_expires_at, lead_key, aggregate_turn_id, chunk_index);

CREATE TABLE legacy_import_claims (
    lead_key TEXT NOT NULL CONSTRAINT pk_legacy_import_claims PRIMARY KEY CHECK ({ident('lead_key')}),
    snapshot_hash TEXT NOT NULL CHECK ({digest('snapshot_hash')}),
    disposition TEXT NOT NULL CHECK (disposition IN ('migrated', 'manual_review', 'rejected')),
    state_hash TEXT NOT NULL CHECK ({digest('state_hash')}),
    claimed_at TEXT NOT NULL CHECK ({stamp('claimed_at')}),
    CONSTRAINT fk_legacy_import_claims_state FOREIGN KEY (lead_key)
        REFERENCES boundary_state (lead_key)
) STRICT;

CREATE TABLE decision_comparisons (
    comparison_id TEXT NOT NULL CONSTRAINT pk_decision_comparisons PRIMARY KEY CHECK ({ident('comparison_id')}),
    lead_key TEXT NOT NULL CHECK ({ident('lead_key')}),
    aggregate_turn_id TEXT NOT NULL CHECK ({ident('aggregate_turn_id')}),
    old_hash TEXT NOT NULL CHECK ({digest('old_hash')}),
    new_hash TEXT NOT NULL CHECK ({digest('new_hash')}),
    severity TEXT NOT NULL CHECK (severity IN ('equivalent', 'noncritical', 'critical')),
    changed_fields_json TEXT NOT NULL CHECK (json_valid(changed_fields_json)),
    created_at TEXT NOT NULL CHECK ({stamp('created_at')}),
    CONSTRAINT fk_decision_comparisons_event FOREIGN KEY (lead_key, aggregate_turn_id)
        REFERENCES boundary_events (lead_key, aggregate_turn_id)
) STRICT;
"""


def _normalized_sqlite_ddl(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def sqlite_v8_schema_fingerprint(connection: sqlite3.Connection) -> str:
    """Hash every declared v8 table/index/trigger DDL after whitespace normalization."""

    if type(connection) is not sqlite3.Connection:
        raise TypeError("connection must be an exact sqlite3.Connection")
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE type IN ('table','index','trigger') "
        "AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
        "ORDER BY type, name"
    ).fetchall()
    payload = json.dumps(
        [
            [kind, name, table, _normalized_sqlite_ddl(sql)]
            for kind, name, table, sql in rows
        ],
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"phase8-boundary-sqlite-ddl-v1\x00" + payload).hexdigest()


def expected_sqlite_v8_schema_fingerprint() -> str:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(render_sqlite_v8())
        return sqlite_v8_schema_fingerprint(connection)
    finally:
        connection.close()


def render_postgresql() -> str:
    """Return static PostgreSQL DDL; this function never opens a connection."""

    hash_check = "VALUE ~ '^[0-9a-f]{64}$'"
    return f"""CREATE TABLE boundary_state (
    lead_key text PRIMARY KEY CHECK (length(lead_key) BETWEEN 1 AND 256),
    version bigint NOT NULL CHECK (version >= 0),
    state_json jsonb NOT NULL,
    state_hash text NOT NULL CHECK ({hash_check.replace('VALUE', 'state_hash')}),
    fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE boundary_events (
    lead_key text NOT NULL REFERENCES boundary_state (lead_key),
    event_id text NOT NULL,
    event_hash text NOT NULL CHECK ({hash_check.replace('VALUE', 'event_hash')}),
    commit_hash text NOT NULL CHECK ({hash_check.replace('VALUE', 'commit_hash')}),
    state_version bigint NOT NULL CHECK (state_version >= 1),
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (lead_key, event_id),
    UNIQUE (lead_key, state_version)
);

CREATE TABLE boundary_commands (
    command_id text PRIMARY KEY,
    lead_key text NOT NULL,
    event_id text NOT NULL,
    command_type text NOT NULL CHECK (command_type IN ('reservation', 'payment_settlement')),
    command_json jsonb NOT NULL,
    command_hash text NOT NULL CHECK ({hash_check.replace('VALUE', 'command_hash')}),
    created_at timestamptz NOT NULL,
    UNIQUE (lead_key, event_id, command_id),
    FOREIGN KEY (lead_key, event_id) REFERENCES boundary_events (lead_key, event_id)
);

CREATE TABLE boundary_outbox (
    message_id text PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,
    lead_key text NOT NULL,
    event_id text NOT NULL,
    workflow_id text NOT NULL,
    command_id text,
    kind text NOT NULL,
    template_id text NOT NULL,
    payload_json jsonb NOT NULL,
    payload_hash text NOT NULL CHECK ({hash_check.replace('VALUE', 'payload_hash')}),
    created_at timestamptz NOT NULL,
    FOREIGN KEY (lead_key, event_id, command_id)
        REFERENCES boundary_commands (lead_key, event_id, command_id),
    FOREIGN KEY (lead_key, event_id) REFERENCES boundary_events (lead_key, event_id)
);

CREATE TABLE legacy_import_claims (
    lead_key text PRIMARY KEY REFERENCES boundary_state (lead_key),
    snapshot_hash text NOT NULL CHECK ({hash_check.replace('VALUE', 'snapshot_hash')}),
    disposition text NOT NULL CHECK (disposition IN ('migrated', 'manual_review', 'rejected')),
    state_hash text NOT NULL CHECK ({hash_check.replace('VALUE', 'state_hash')}),
    claimed_at timestamptz NOT NULL
);

CREATE TABLE decision_comparisons (
    comparison_id text PRIMARY KEY,
    lead_key text NOT NULL REFERENCES boundary_state (lead_key),
    event_id text NOT NULL,
    old_hash text NOT NULL CHECK ({hash_check.replace('VALUE', 'old_hash')}),
    new_hash text NOT NULL CHECK ({hash_check.replace('VALUE', 'new_hash')}),
    severity text NOT NULL CHECK (severity IN ('equivalent', 'noncritical', 'critical')),
    changed_fields_json jsonb NOT NULL,
    created_at timestamptz NOT NULL
);
"""


def schema_hash(dialect: Dialect) -> str:
    if dialect == "sqlite":
        payload = render_sqlite()
    elif dialect == "postgresql":
        payload = render_postgresql()
    else:
        raise ValueError("dialect must be exactly sqlite or postgresql")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = (
    "BOUNDARY_V8_TABLES",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_V8",
    "TABLE_NAMES",
    "expected_sqlite_v8_schema_fingerprint",
    "render_postgresql",
    "render_sqlite",
    "render_sqlite_v8",
    "schema_hash",
    "sqlite_v8_schema_fingerprint",
)
