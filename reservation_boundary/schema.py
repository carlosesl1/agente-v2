"""Literal six-table Phase 7 boundary schema for SQLite/PostgreSQL."""

from __future__ import annotations

import hashlib
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
    "SCHEMA_VERSION",
    "TABLE_NAMES",
    "render_postgresql",
    "render_sqlite",
    "schema_hash",
)
