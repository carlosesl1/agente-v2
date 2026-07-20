CREATE TABLE handoff_workflows (
    handoff_id text NOT NULL CHECK (length(handoff_id) >= 1),
    incident_key text NOT NULL CHECK (length(incident_key) >= 1),
    revision bigint NOT NULL CHECK (revision >= 0),
    status text NOT NULL CHECK (status IN ('requested', 'active', 'acknowledgement_pending', 'acknowledged', 'manual_review', 'completed', 'cancelled')),
    lead_key_hash text NOT NULL CHECK (length(lead_key_hash) = 64 AND lead_key_hash = lower(lead_key_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(lead_key_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    state_json text NOT NULL CHECK (length(state_json) >= 1),
    state_hash text NOT NULL CHECK (length(state_hash) = 64 AND state_hash = lower(state_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(state_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_handoff_workflows PRIMARY KEY (handoff_id),
    CONSTRAINT uq_handoff_workflows_incident_key UNIQUE (incident_key)
);

CREATE TABLE handoff_events (
    event_id text NOT NULL CHECK (length(event_id) >= 1),
    handoff_id text NOT NULL CHECK (length(handoff_id) >= 1),
    revision bigint NOT NULL CHECK (revision >= 1),
    event_type text NOT NULL CHECK (length(event_type) >= 1),
    event_json text NOT NULL CHECK (length(event_json) >= 1),
    event_hash text NOT NULL CHECK (length(event_hash) = 64 AND event_hash = lower(event_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(event_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    occurred_at timestamptz NOT NULL,
    CONSTRAINT pk_handoff_events PRIMARY KEY (event_id),
    CONSTRAINT fk_handoff_events_workflow FOREIGN KEY (handoff_id) REFERENCES handoff_workflows (handoff_id),
    CONSTRAINT uq_handoff_events_workflow_revision UNIQUE (handoff_id, revision)
);

CREATE TABLE handoff_outbox (
    message_id text NOT NULL CHECK (length(message_id) >= 1),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) >= 1),
    effect_id text NOT NULL CHECK (length(effect_id) >= 1),
    handoff_id text NOT NULL CHECK (length(handoff_id) >= 1),
    kind text NOT NULL CHECK (kind IN ('customer_acknowledgement', 'internal_email')),
    template_id text NOT NULL CHECK (length(template_id) >= 1),
    payload_json text NOT NULL CHECK (length(payload_json) >= 1),
    payload_hash text NOT NULL CHECK (length(payload_hash) = 64 AND payload_hash = lower(payload_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(payload_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    status text NOT NULL CHECK (status IN ('pending', 'leased', 'delivered')),
    claim_owner text CHECK (length(claim_owner) >= 1),
    fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
    lease_acquired_at timestamptz,
    lease_expires_at timestamptz,
    delivery_attempts bigint NOT NULL CHECK (delivery_attempts >= 0),
    delivered_at timestamptz,
    receipt_hash text CHECK (receipt_hash IS NULL OR (length(receipt_hash) = 64 AND receipt_hash = lower(receipt_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(receipt_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0)),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_handoff_outbox PRIMARY KEY (message_id),
    CONSTRAINT fk_handoff_outbox_workflow FOREIGN KEY (handoff_id) REFERENCES handoff_workflows (handoff_id),
    CONSTRAINT uq_handoff_outbox_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT uq_handoff_outbox_effect_id UNIQUE (effect_id),
    CONSTRAINT uq_handoff_outbox_receipt_binding UNIQUE (message_id, receipt_hash, delivered_at),
    CONSTRAINT ck_handoff_outbox_lease_tuple CHECK ((status = 'pending' AND claim_owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR (status = 'leased' AND claim_owner IS NOT NULL AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status = 'delivered' AND claim_owner IS NOT NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL)),
    CONSTRAINT ck_handoff_outbox_active_lease CHECK (status != 'leased' OR (claim_owner IS NOT NULL AND fencing_token >= 1 AND lease_expires_at > lease_acquired_at)),
    CONSTRAINT ck_handoff_outbox_receipt_tuple CHECK ((delivered_at IS NULL AND receipt_hash IS NULL) OR (delivered_at IS NOT NULL AND receipt_hash IS NOT NULL)),
    CONSTRAINT ck_handoff_outbox_fencing_history CHECK (fencing_token = delivery_attempts),
    CONSTRAINT ck_handoff_outbox_status_matrix CHECK ((status = 'pending' AND claim_owner IS NULL AND delivered_at IS NULL) OR (status = 'leased' AND claim_owner IS NOT NULL AND delivered_at IS NULL) OR (status = 'delivered' AND claim_owner IS NOT NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL AND fencing_token >= 1 AND delivery_attempts >= 1 AND delivered_at IS NOT NULL))
);

CREATE TABLE handoff_receipts (
    receipt_id text NOT NULL CHECK (length(receipt_id) >= 1),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) >= 1),
    message_id text NOT NULL CHECK (length(message_id) >= 1),
    receipt_json text NOT NULL CHECK (length(receipt_json) >= 1),
    receipt_hash text NOT NULL CHECK (length(receipt_hash) = 64 AND receipt_hash = lower(receipt_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(receipt_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    delivered_at timestamptz NOT NULL,
    CONSTRAINT pk_handoff_receipts PRIMARY KEY (receipt_id),
    CONSTRAINT fk_handoff_receipts_message FOREIGN KEY (message_id, receipt_hash, delivered_at) REFERENCES handoff_outbox (message_id, receipt_hash, delivered_at),
    CONSTRAINT uq_handoff_receipts_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT uq_handoff_receipts_message_id UNIQUE (message_id)
);

CREATE TABLE payment_workflows (
    payment_id text NOT NULL CHECK (length(payment_id) >= 1),
    revision bigint NOT NULL CHECK (revision >= 0),
    payment_version bigint NOT NULL CHECK (payment_version >= 1),
    economic_signature text NOT NULL CHECK (length(economic_signature) = 64 AND economic_signature = lower(economic_signature) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(economic_signature, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    status text NOT NULL CHECK (status IN ('awaiting_method', 'awaiting_financial_confirmation', 'awaiting_evidence', 'evidence_verified', 'settlement_queued', 'settling', 'paid', 'retryable', 'manual_review', 'expired', 'cancelled')),
    state_json text NOT NULL CHECK (length(state_json) >= 1),
    state_hash text NOT NULL CHECK (length(state_hash) = 64 AND state_hash = lower(state_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(state_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_workflows PRIMARY KEY (payment_id)
);

CREATE TABLE payment_events (
    event_id text NOT NULL CHECK (length(event_id) >= 1),
    payment_id text NOT NULL CHECK (length(payment_id) >= 1),
    revision bigint NOT NULL CHECK (revision >= 1),
    payment_version bigint NOT NULL CHECK (payment_version >= 1),
    economic_signature text NOT NULL CHECK (length(economic_signature) = 64 AND economic_signature = lower(economic_signature) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(economic_signature, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    event_type text NOT NULL CHECK (length(event_type) >= 1),
    event_json text NOT NULL CHECK (length(event_json) >= 1),
    event_hash text NOT NULL CHECK (length(event_hash) = 64 AND event_hash = lower(event_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(event_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    occurred_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_events PRIMARY KEY (event_id),
    CONSTRAINT fk_payment_events_workflow FOREIGN KEY (payment_id) REFERENCES payment_workflows (payment_id),
    CONSTRAINT uq_payment_events_workflow_revision UNIQUE (payment_id, revision)
);

CREATE TABLE payment_evidence_claims (
    claim_key text NOT NULL CHECK (length(claim_key) >= 1),
    payment_id text NOT NULL CHECK (length(payment_id) >= 1),
    payment_version bigint NOT NULL CHECK (payment_version >= 1),
    economic_signature text NOT NULL CHECK (length(economic_signature) = 64 AND economic_signature = lower(economic_signature) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(economic_signature, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    method text NOT NULL CHECK (method IN ('pix', 'wise', 'stripe')),
    evidence_json text NOT NULL CHECK (length(evidence_json) >= 1),
    evidence_hash text NOT NULL CHECK (length(evidence_hash) = 64 AND evidence_hash = lower(evidence_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(evidence_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    status text NOT NULL CHECK (status IN ('in_progress', 'completed', 'retryable', 'manual_review')),
    claimed_at timestamptz NOT NULL,
    consumed_at timestamptz,
    CONSTRAINT pk_payment_evidence_claims PRIMARY KEY (claim_key),
    CONSTRAINT fk_payment_evidence_claims_workflow FOREIGN KEY (payment_id) REFERENCES payment_workflows (payment_id),
    CONSTRAINT uq_payment_evidence_claims_binding UNIQUE (claim_key, payment_id, payment_version, economic_signature),
    CONSTRAINT ck_payment_evidence_claims_status_matrix CHECK (((status = 'in_progress' OR status = 'retryable') AND consumed_at IS NULL) OR ((status = 'completed' OR status = 'manual_review') AND consumed_at IS NOT NULL AND consumed_at >= claimed_at))
);

CREATE TABLE payment_commands (
    settlement_command_id text NOT NULL CHECK (length(settlement_command_id) >= 1),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) >= 1),
    payment_id text NOT NULL CHECK (length(payment_id) >= 1),
    payment_version bigint NOT NULL CHECK (payment_version >= 1),
    economic_signature text NOT NULL CHECK (length(economic_signature) = 64 AND economic_signature = lower(economic_signature) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(economic_signature, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    evidence_claim_key text NOT NULL CHECK (length(evidence_claim_key) >= 1),
    operation text NOT NULL CHECK (operation IN ('register_and_confirm')),
    command_json text NOT NULL CHECK (length(command_json) >= 1),
    command_hash text NOT NULL CHECK (length(command_hash) = 64 AND command_hash = lower(command_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(command_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_commands PRIMARY KEY (settlement_command_id),
    CONSTRAINT fk_payment_commands_workflow FOREIGN KEY (payment_id) REFERENCES payment_workflows (payment_id),
    CONSTRAINT fk_payment_commands_evidence FOREIGN KEY (evidence_claim_key, payment_id, payment_version, economic_signature) REFERENCES payment_evidence_claims (claim_key, payment_id, payment_version, economic_signature),
    CONSTRAINT uq_payment_commands_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT uq_payment_commands_subject UNIQUE (payment_id, payment_version, economic_signature),
    CONSTRAINT uq_payment_commands_binding UNIQUE (settlement_command_id, payment_id, payment_version, economic_signature)
);

CREATE TABLE payment_ledger (
    settlement_command_id text NOT NULL CHECK (length(settlement_command_id) >= 1),
    payment_id text NOT NULL CHECK (length(payment_id) >= 1),
    payment_version bigint NOT NULL CHECK (payment_version >= 1),
    economic_signature text NOT NULL CHECK (length(economic_signature) = 64 AND economic_signature = lower(economic_signature) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(economic_signature, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    status text NOT NULL CHECK (status IN ('queued', 'leased', 'dispatch_fenced', 'outcome_recorded', 'manual_review')),
    claim_owner text CHECK (length(claim_owner) >= 1),
    fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
    lease_acquired_at timestamptz,
    lease_expires_at timestamptz,
    claim_count bigint NOT NULL CHECK (claim_count >= 0),
    dispatch_slots_consumed bigint NOT NULL CHECK (dispatch_slots_consumed >= 0 AND dispatch_slots_consumed <= 1),
    dispatch_request_hash text CHECK (dispatch_request_hash IS NULL OR (length(dispatch_request_hash) = 64 AND dispatch_request_hash = lower(dispatch_request_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(dispatch_request_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0)),
    dispatch_fenced_at timestamptz,
    outcome_certainty text CHECK (outcome_certainty IS NULL OR outcome_certainty IN ('not_dispatched', 'dispatched_no_effect', 'settled', 'partial_settlement', 'dispatched_unknown')),
    outcome_json text CHECK (length(outcome_json) >= 1),
    outcome_hash text CHECK (outcome_hash IS NULL OR (length(outcome_hash) = 64 AND outcome_hash = lower(outcome_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(outcome_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0)),
    outcome_recorded_at timestamptz,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_ledger PRIMARY KEY (settlement_command_id),
    CONSTRAINT fk_payment_ledger_command FOREIGN KEY (settlement_command_id, payment_id, payment_version, economic_signature) REFERENCES payment_commands (settlement_command_id, payment_id, payment_version, economic_signature),
    CONSTRAINT uq_payment_ledger_subject UNIQUE (payment_id, payment_version, economic_signature),
    CONSTRAINT ck_payment_ledger_fencing_history CHECK (fencing_token = claim_count),
    CONSTRAINT ck_payment_ledger_lease_tuple CHECK ((claim_owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR (claim_owner IS NOT NULL AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CONSTRAINT ck_payment_ledger_active_lease CHECK (claim_owner IS NULL OR (fencing_token >= 1 AND lease_expires_at > lease_acquired_at)),
    CONSTRAINT ck_payment_ledger_dispatch_tuple CHECK ((dispatch_slots_consumed = 0 AND dispatch_request_hash IS NULL AND dispatch_fenced_at IS NULL) OR (dispatch_slots_consumed = 1 AND dispatch_request_hash IS NOT NULL AND dispatch_fenced_at IS NOT NULL AND fencing_token >= 1)),
    CONSTRAINT ck_payment_ledger_outcome_tuple CHECK ((outcome_certainty IS NULL AND outcome_json IS NULL AND outcome_hash IS NULL AND outcome_recorded_at IS NULL) OR (outcome_certainty IS NOT NULL AND outcome_json IS NOT NULL AND outcome_hash IS NOT NULL AND outcome_recorded_at IS NOT NULL)),
    CONSTRAINT ck_payment_ledger_status_matrix CHECK ((status = 'queued' AND claim_owner IS NULL AND claim_count >= 0 AND dispatch_slots_consumed = 0 AND outcome_json IS NULL) OR (status = 'leased' AND claim_owner IS NOT NULL AND claim_count >= 1 AND dispatch_slots_consumed = 0 AND outcome_json IS NULL) OR (status = 'dispatch_fenced' AND claim_owner IS NOT NULL AND claim_count >= 1 AND dispatch_slots_consumed = 1 AND outcome_json IS NULL) OR (status = 'outcome_recorded' AND claim_owner IS NULL AND outcome_json IS NOT NULL AND ((outcome_certainty = 'not_dispatched' AND dispatch_slots_consumed = 0) OR (outcome_certainty = 'settled' AND dispatch_slots_consumed = 1))) OR (status = 'manual_review' AND claim_owner IS NULL AND outcome_json IS NOT NULL AND dispatch_slots_consumed = 1 AND outcome_certainty IN ('dispatched_no_effect', 'partial_settlement', 'dispatched_unknown')))
);

CREATE TABLE payment_outbox (
    message_id text NOT NULL CHECK (length(message_id) >= 1),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) >= 1),
    effect_id text NOT NULL CHECK (length(effect_id) >= 1),
    payment_id text NOT NULL CHECK (length(payment_id) >= 1),
    payment_version bigint NOT NULL CHECK (payment_version >= 1),
    economic_signature text NOT NULL CHECK (length(economic_signature) = 64 AND economic_signature = lower(economic_signature) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(economic_signature, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    settlement_command_id text NOT NULL CHECK (length(settlement_command_id) >= 1),
    kind text NOT NULL CHECK (kind IN ('paid_state_transition', 'customer_payment_confirmation', 'internal_payment_email', 'booking_form', 'manual_review')),
    template_id text NOT NULL CHECK (length(template_id) >= 1),
    payload_json text NOT NULL CHECK (length(payload_json) >= 1),
    payload_hash text NOT NULL CHECK (length(payload_hash) = 64 AND payload_hash = lower(payload_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(payload_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    status text NOT NULL CHECK (status IN ('pending', 'leased', 'delivered')),
    claim_owner text CHECK (length(claim_owner) >= 1),
    fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
    lease_acquired_at timestamptz,
    lease_expires_at timestamptz,
    delivery_attempts bigint NOT NULL CHECK (delivery_attempts >= 0),
    delivered_at timestamptz,
    receipt_hash text CHECK (receipt_hash IS NULL OR (length(receipt_hash) = 64 AND receipt_hash = lower(receipt_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(receipt_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0)),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_outbox PRIMARY KEY (message_id),
    CONSTRAINT fk_payment_outbox_workflow FOREIGN KEY (payment_id) REFERENCES payment_workflows (payment_id),
    CONSTRAINT fk_payment_outbox_command FOREIGN KEY (settlement_command_id, payment_id, payment_version, economic_signature) REFERENCES payment_commands (settlement_command_id, payment_id, payment_version, economic_signature),
    CONSTRAINT uq_payment_outbox_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT uq_payment_outbox_effect_id UNIQUE (effect_id),
    CONSTRAINT uq_payment_outbox_receipt_binding UNIQUE (message_id, receipt_hash, delivered_at),
    CONSTRAINT ck_payment_outbox_lease_tuple CHECK ((claim_owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR (claim_owner IS NOT NULL AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CONSTRAINT ck_payment_outbox_active_lease CHECK (claim_owner IS NULL OR (fencing_token >= 1 AND lease_expires_at > lease_acquired_at)),
    CONSTRAINT ck_payment_outbox_receipt_tuple CHECK ((delivered_at IS NULL AND receipt_hash IS NULL) OR (delivered_at IS NOT NULL AND receipt_hash IS NOT NULL)),
    CONSTRAINT ck_payment_outbox_fencing_history CHECK (fencing_token = delivery_attempts),
    CONSTRAINT ck_payment_outbox_status_matrix CHECK ((status = 'pending' AND claim_owner IS NULL AND delivered_at IS NULL) OR (status = 'leased' AND claim_owner IS NOT NULL AND delivered_at IS NULL) OR (status = 'delivered' AND claim_owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL AND fencing_token >= 1 AND delivery_attempts >= 1 AND delivered_at IS NOT NULL))
);

CREATE TABLE payment_receipts (
    receipt_id text NOT NULL CHECK (length(receipt_id) >= 1),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) >= 1),
    message_id text NOT NULL CHECK (length(message_id) >= 1),
    receipt_json text NOT NULL CHECK (length(receipt_json) >= 1),
    receipt_hash text NOT NULL CHECK (length(receipt_hash) = 64 AND receipt_hash = lower(receipt_hash) AND length(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(receipt_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '')) = 0),
    delivered_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_receipts PRIMARY KEY (receipt_id),
    CONSTRAINT fk_payment_receipts_message FOREIGN KEY (message_id, receipt_hash, delivered_at) REFERENCES payment_outbox (message_id, receipt_hash, delivered_at),
    CONSTRAINT uq_payment_receipts_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT uq_payment_receipts_message_id UNIQUE (message_id)
);
