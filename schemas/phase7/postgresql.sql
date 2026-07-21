CREATE TABLE boundary_state (
    lead_key text PRIMARY KEY CHECK (length(lead_key) BETWEEN 1 AND 256),
    version bigint NOT NULL CHECK (version >= 0),
    state_json jsonb NOT NULL,
    state_hash text NOT NULL CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE boundary_events (
    lead_key text NOT NULL REFERENCES boundary_state (lead_key),
    event_id text NOT NULL,
    event_hash text NOT NULL CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    commit_hash text NOT NULL CHECK (commit_hash ~ '^[0-9a-f]{64}$'),
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
    command_hash text NOT NULL CHECK (command_hash ~ '^[0-9a-f]{64}$'),
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
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    FOREIGN KEY (lead_key, event_id) REFERENCES boundary_events (lead_key, event_id)
);

CREATE TABLE legacy_import_claims (
    lead_key text PRIMARY KEY REFERENCES boundary_state (lead_key),
    snapshot_hash text NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    disposition text NOT NULL CHECK (disposition IN ('migrated', 'manual_review', 'rejected')),
    state_hash text NOT NULL CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    claimed_at timestamptz NOT NULL
);

CREATE TABLE decision_comparisons (
    comparison_id text PRIMARY KEY,
    lead_key text NOT NULL REFERENCES boundary_state (lead_key),
    event_id text NOT NULL,
    old_hash text NOT NULL CHECK (old_hash ~ '^[0-9a-f]{64}$'),
    new_hash text NOT NULL CHECK (new_hash ~ '^[0-9a-f]{64}$'),
    severity text NOT NULL CHECK (severity IN ('equivalent', 'noncritical', 'critical')),
    changed_fields_json jsonb NOT NULL,
    created_at timestamptz NOT NULL
);
