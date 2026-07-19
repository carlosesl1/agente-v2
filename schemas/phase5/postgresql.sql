CREATE TABLE schema_migrations (
    version bigint NOT NULL CHECK (version >= 1),
    schema_hash text NOT NULL CHECK (length(schema_hash) = 64 AND schema_hash = lower(schema_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(schema_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    applied_at timestamptz NOT NULL,
    CONSTRAINT pk_schema_migrations PRIMARY KEY (version)
);

CREATE TABLE workflows (
    workflow_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 0),
    state_type text NOT NULL,
    state_json text NOT NULL,
    state_hash text NOT NULL CHECK (length(state_hash) = 64 AND state_hash = lower(state_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(state_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_workflows PRIMARY KEY (workflow_id)
);

CREATE TABLE domain_events (
    event_id text NOT NULL,
    workflow_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 0),
    occurred_at timestamptz NOT NULL,
    event_type text NOT NULL,
    event_json text NOT NULL,
    event_hash text NOT NULL CHECK (length(event_hash) = 64 AND event_hash = lower(event_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(event_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    CONSTRAINT pk_domain_events PRIMARY KEY (event_id),
    CONSTRAINT fk_domain_events_workflow FOREIGN KEY (workflow_id) REFERENCES workflows (workflow_id),
    CONSTRAINT uq_domain_events_workflow_revision UNIQUE (workflow_id, revision)
);

CREATE TABLE reservation_commands (
    command_id text NOT NULL,
    idempotency_key text NOT NULL,
    workflow_id text NOT NULL,
    draft_id text NOT NULL,
    draft_version bigint NOT NULL CHECK (draft_version >= 1),
    subject_signature text NOT NULL CHECK (length(subject_signature) = 64 AND subject_signature = lower(subject_signature) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(subject_signature, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    operation text NOT NULL CHECK (operation IN ('reserve_lodging', 'book_activity', 'reserve_package')),
    command_json text NOT NULL,
    command_hash text NOT NULL CHECK (length(command_hash) = 64 AND command_hash = lower(command_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(command_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_reservation_commands PRIMARY KEY (command_id),
    CONSTRAINT fk_reservation_commands_workflow FOREIGN KEY (workflow_id) REFERENCES workflows (workflow_id),
    CONSTRAINT uq_reservation_commands_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT uq_reservation_commands_workflow UNIQUE (workflow_id),
    CONSTRAINT uq_reservation_commands_identity UNIQUE (workflow_id, draft_id, draft_version, operation)
);

CREATE TABLE execution_ledger (
    command_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued', 'preparing', 'dispatch_fenced', 'outcome_recorded', 'manual_review')),
    claim_owner text,
    fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
    lease_acquired_at timestamptz,
    lease_expires_at timestamptz,
    claim_count bigint NOT NULL CHECK (claim_count >= 0),
    preparation_failures bigint NOT NULL CHECK (preparation_failures >= 0 AND preparation_failures <= 3),
    dispatch_slots_consumed bigint NOT NULL CHECK (dispatch_slots_consumed >= 0 AND dispatch_slots_consumed <= 1),
    dispatch_request_hash text CHECK (dispatch_request_hash IS NULL OR (length(dispatch_request_hash) = 64 AND dispatch_request_hash = lower(dispatch_request_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(dispatch_request_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0)),
    dispatch_fenced_at timestamptz,
    outcome_json text,
    outcome_hash text CHECK (outcome_hash IS NULL OR (length(outcome_hash) = 64 AND outcome_hash = lower(outcome_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(outcome_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0)),
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_execution_ledger PRIMARY KEY (command_id),
    CONSTRAINT fk_execution_ledger_command FOREIGN KEY (command_id) REFERENCES reservation_commands (command_id),
    CONSTRAINT ck_execution_ledger_lease_tuple CHECK ((claim_owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR (claim_owner IS NOT NULL AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CONSTRAINT ck_execution_ledger_active_lease CHECK (claim_owner IS NULL OR (fencing_token >= 1 AND lease_expires_at > lease_acquired_at)),
    CONSTRAINT ck_execution_ledger_dispatch_tuple CHECK ((dispatch_slots_consumed = 0 AND dispatch_request_hash IS NULL AND dispatch_fenced_at IS NULL) OR (dispatch_slots_consumed = 1 AND dispatch_request_hash IS NOT NULL AND dispatch_fenced_at IS NOT NULL)),
    CONSTRAINT ck_execution_ledger_outcome_tuple CHECK ((outcome_json IS NULL AND outcome_hash IS NULL) OR (outcome_json IS NOT NULL AND outcome_hash IS NOT NULL)),
    CONSTRAINT ck_execution_ledger_status_matrix CHECK ((status = 'queued' AND claim_owner IS NULL AND dispatch_slots_consumed = 0 AND outcome_json IS NULL) OR (status = 'preparing' AND claim_owner IS NOT NULL AND claim_count >= 1 AND dispatch_slots_consumed = 0 AND outcome_json IS NULL) OR (status = 'dispatch_fenced' AND claim_owner IS NOT NULL AND claim_count >= 1 AND dispatch_slots_consumed = 1 AND outcome_json IS NULL) OR (status = 'outcome_recorded' AND claim_owner IS NULL AND dispatch_slots_consumed IN (0, 1) AND outcome_json IS NOT NULL) OR (status = 'manual_review' AND claim_owner IS NULL AND dispatch_slots_consumed = 1 AND outcome_json IS NOT NULL))
);

CREATE TABLE outbox_messages (
    message_id text NOT NULL,
    idempotency_key text NOT NULL,
    workflow_id text NOT NULL,
    command_id text,
    kind text NOT NULL CHECK (kind IN ('summary_presented', 'execution_succeeded', 'execution_failed_no_effect', 'execution_not_called', 'execution_manual_review')),
    template_id text NOT NULL,
    payload_json text NOT NULL,
    payload_hash text NOT NULL CHECK (length(payload_hash) = 64 AND payload_hash = lower(payload_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(payload_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    status text NOT NULL CHECK (status IN ('pending', 'leased', 'delivered')),
    claim_owner text,
    fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
    lease_acquired_at timestamptz,
    lease_expires_at timestamptz,
    delivery_attempts bigint NOT NULL CHECK (delivery_attempts >= 0),
    delivered_at timestamptz,
    receipt_hash text CHECK (receipt_hash IS NULL OR (length(receipt_hash) = 64 AND receipt_hash = lower(receipt_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(receipt_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0)),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_outbox_messages PRIMARY KEY (message_id),
    CONSTRAINT fk_outbox_messages_workflow FOREIGN KEY (workflow_id) REFERENCES workflows (workflow_id),
    CONSTRAINT fk_outbox_messages_command FOREIGN KEY (command_id) REFERENCES reservation_commands (command_id),
    CONSTRAINT uq_outbox_messages_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT ck_outbox_messages_lease_tuple CHECK ((claim_owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR (claim_owner IS NOT NULL AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CONSTRAINT ck_outbox_messages_active_lease CHECK (claim_owner IS NULL OR (fencing_token >= 1 AND lease_expires_at > lease_acquired_at)),
    CONSTRAINT ck_outbox_messages_receipt_tuple CHECK ((delivered_at IS NULL AND receipt_hash IS NULL) OR (delivered_at IS NOT NULL AND receipt_hash IS NOT NULL)),
    CONSTRAINT ck_outbox_messages_status_matrix CHECK ((status = 'pending' AND claim_owner IS NULL AND delivered_at IS NULL) OR (status = 'leased' AND claim_owner IS NOT NULL AND delivered_at IS NULL) OR (status = 'delivered' AND claim_owner IS NULL AND delivered_at IS NOT NULL AND delivery_attempts >= 1))
);
