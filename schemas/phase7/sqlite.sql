CREATE TABLE boundary_state (
    lead_key TEXT NOT NULL CONSTRAINT pk_boundary_state PRIMARY KEY CHECK (length(lead_key) BETWEEN 1 AND 256 AND instr(lead_key, char(0)) = 0),
    version INTEGER NOT NULL CHECK (version >= 0),
    state_json TEXT NOT NULL CHECK (json_valid(state_json)),
    state_hash TEXT NOT NULL CHECK (length(state_hash) = 64 AND state_hash = lower(state_hash) AND state_hash NOT GLOB '*[^0-9a-f]*'),
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
    created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 25 AND 32 AND substr(created_at, 11, 1) = 'T' AND substr(created_at, -6) = '+00:00' AND instr(created_at, char(0)) = 0),
    updated_at TEXT NOT NULL CHECK (length(updated_at) BETWEEN 25 AND 32 AND substr(updated_at, 11, 1) = 'T' AND substr(updated_at, -6) = '+00:00' AND instr(updated_at, char(0)) = 0)
) STRICT;

CREATE TABLE boundary_events (
    lead_key TEXT NOT NULL CHECK (length(lead_key) BETWEEN 1 AND 256 AND instr(lead_key, char(0)) = 0),
    event_id TEXT NOT NULL CHECK (length(event_id) BETWEEN 1 AND 256 AND instr(event_id, char(0)) = 0),
    event_hash TEXT NOT NULL CHECK (length(event_hash) = 64 AND event_hash = lower(event_hash) AND event_hash NOT GLOB '*[^0-9a-f]*'),
    commit_hash TEXT NOT NULL CHECK (length(commit_hash) = 64 AND commit_hash = lower(commit_hash) AND commit_hash NOT GLOB '*[^0-9a-f]*'),
    state_version INTEGER NOT NULL CHECK (state_version >= 1),
    occurred_at TEXT NOT NULL CHECK (length(occurred_at) BETWEEN 25 AND 32 AND substr(occurred_at, 11, 1) = 'T' AND substr(occurred_at, -6) = '+00:00' AND instr(occurred_at, char(0)) = 0),
    CONSTRAINT pk_boundary_events PRIMARY KEY (lead_key, event_id),
    CONSTRAINT uq_boundary_events_version UNIQUE (lead_key, state_version),
    CONSTRAINT fk_boundary_events_state FOREIGN KEY (lead_key)
        REFERENCES boundary_state (lead_key)
) STRICT;

CREATE TABLE boundary_commands (
    command_id TEXT NOT NULL CONSTRAINT pk_boundary_commands PRIMARY KEY CHECK (length(command_id) BETWEEN 1 AND 256 AND instr(command_id, char(0)) = 0),
    lead_key TEXT NOT NULL CHECK (length(lead_key) BETWEEN 1 AND 256 AND instr(lead_key, char(0)) = 0),
    event_id TEXT NOT NULL CHECK (length(event_id) BETWEEN 1 AND 256 AND instr(event_id, char(0)) = 0),
    command_type TEXT NOT NULL CHECK (command_type IN ('reservation', 'payment_settlement')),
    command_json TEXT NOT NULL CHECK (json_valid(command_json)),
    command_hash TEXT NOT NULL CHECK (length(command_hash) = 64 AND command_hash = lower(command_hash) AND command_hash NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 25 AND 32 AND substr(created_at, 11, 1) = 'T' AND substr(created_at, -6) = '+00:00' AND instr(created_at, char(0)) = 0),
    CONSTRAINT fk_boundary_commands_event FOREIGN KEY (lead_key, event_id)
        REFERENCES boundary_events (lead_key, event_id)
) STRICT;

CREATE TABLE boundary_outbox (
    message_id TEXT NOT NULL CONSTRAINT pk_boundary_outbox PRIMARY KEY CHECK (length(message_id) BETWEEN 1 AND 256 AND instr(message_id, char(0)) = 0),
    idempotency_key TEXT NOT NULL CONSTRAINT uq_boundary_outbox_idempotency UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 256 AND instr(idempotency_key, char(0)) = 0),
    lead_key TEXT NOT NULL CHECK (length(lead_key) BETWEEN 1 AND 256 AND instr(lead_key, char(0)) = 0),
    event_id TEXT NOT NULL CHECK (length(event_id) BETWEEN 1 AND 256 AND instr(event_id, char(0)) = 0),
    workflow_id TEXT NOT NULL CHECK (length(workflow_id) BETWEEN 1 AND 256 AND instr(workflow_id, char(0)) = 0),
    command_id TEXT CHECK (command_id IS NULL OR length(command_id) BETWEEN 1 AND 256 AND instr(command_id, char(0)) = 0),
    kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 256 AND instr(kind, char(0)) = 0),
    template_id TEXT NOT NULL CHECK (length(template_id) BETWEEN 1 AND 256 AND instr(template_id, char(0)) = 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64 AND payload_hash = lower(payload_hash) AND payload_hash NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 25 AND 32 AND substr(created_at, 11, 1) = 'T' AND substr(created_at, -6) = '+00:00' AND instr(created_at, char(0)) = 0),
    CONSTRAINT fk_boundary_outbox_event FOREIGN KEY (lead_key, event_id)
        REFERENCES boundary_events (lead_key, event_id)
) STRICT;

CREATE TABLE legacy_import_claims (
    lead_key TEXT NOT NULL CONSTRAINT pk_legacy_import_claims PRIMARY KEY CHECK (length(lead_key) BETWEEN 1 AND 256 AND instr(lead_key, char(0)) = 0),
    snapshot_hash TEXT NOT NULL CHECK (length(snapshot_hash) = 64 AND snapshot_hash = lower(snapshot_hash) AND snapshot_hash NOT GLOB '*[^0-9a-f]*'),
    disposition TEXT NOT NULL CHECK (disposition IN ('migrated', 'manual_review', 'rejected')),
    state_hash TEXT NOT NULL CHECK (length(state_hash) = 64 AND state_hash = lower(state_hash) AND state_hash NOT GLOB '*[^0-9a-f]*'),
    claimed_at TEXT NOT NULL CHECK (length(claimed_at) BETWEEN 25 AND 32 AND substr(claimed_at, 11, 1) = 'T' AND substr(claimed_at, -6) = '+00:00' AND instr(claimed_at, char(0)) = 0),
    CONSTRAINT fk_legacy_import_claims_state FOREIGN KEY (lead_key)
        REFERENCES boundary_state (lead_key)
) STRICT;

CREATE TABLE decision_comparisons (
    comparison_id TEXT NOT NULL CONSTRAINT pk_decision_comparisons PRIMARY KEY CHECK (length(comparison_id) BETWEEN 1 AND 256 AND instr(comparison_id, char(0)) = 0),
    lead_key TEXT NOT NULL CHECK (length(lead_key) BETWEEN 1 AND 256 AND instr(lead_key, char(0)) = 0),
    event_id TEXT NOT NULL CHECK (length(event_id) BETWEEN 1 AND 256 AND instr(event_id, char(0)) = 0),
    old_hash TEXT NOT NULL CHECK (length(old_hash) = 64 AND old_hash = lower(old_hash) AND old_hash NOT GLOB '*[^0-9a-f]*'),
    new_hash TEXT NOT NULL CHECK (length(new_hash) = 64 AND new_hash = lower(new_hash) AND new_hash NOT GLOB '*[^0-9a-f]*'),
    severity TEXT NOT NULL CHECK (severity IN ('equivalent', 'noncritical', 'critical')),
    changed_fields_json TEXT NOT NULL CHECK (json_valid(changed_fields_json)),
    created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 25 AND 32 AND substr(created_at, 11, 1) = 'T' AND substr(created_at, -6) = '+00:00' AND instr(created_at, char(0)) = 0),
    CONSTRAINT fk_decision_comparisons_state FOREIGN KEY (lead_key)
        REFERENCES boundary_state (lead_key)
) STRICT;
