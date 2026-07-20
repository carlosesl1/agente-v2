"""Declarative Phase 6 follow-up schema and deterministic SQL renderers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Literal

SCHEMA_VERSION = 1

Dialect = Literal["sqlite", "postgresql"]


@dataclass(frozen=True, slots=True)
class ColumnContract:
    name: str
    sqlite_type: str
    postgresql_type: str
    nullable: bool = False
    check: str | None = None


@dataclass(frozen=True, slots=True)
class TableContract:
    name: str
    columns: tuple[ColumnContract, ...]
    table_constraints: tuple[str, ...]


def _text(
    name: str,
    *,
    nullable: bool = False,
    check: str | None = None,
) -> ColumnContract:
    return ColumnContract(name, "TEXT", "text", nullable, check)


def _integer(name: str, check: str) -> ColumnContract:
    return ColumnContract(name, "INTEGER", "bigint", check=check)


def _timestamp(name: str, *, nullable: bool = False) -> ColumnContract:
    return ColumnContract(name, "TEXT", "timestamptz", nullable)


def _nonempty(name: str) -> str:
    return f"length({name}) >= 1"


def _sqlite_timestamp_check(name: str) -> str:
    date = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
    time = "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]"
    whole_seconds = f"{date}T{time}+00:00"
    microseconds = f"{date}T{time}.[0-9][0-9][0-9][0-9][0-9][0-9]+00:00"
    year = f"CAST(substr({name}, 1, 4) AS INTEGER)"
    month = f"CAST(substr({name}, 6, 2) AS INTEGER)"
    day = f"CAST(substr({name}, 9, 2) AS INTEGER)"
    hour = f"CAST(substr({name}, 12, 2) AS INTEGER)"
    minute = f"CAST(substr({name}, 15, 2) AS INTEGER)"
    second = f"CAST(substr({name}, 18, 2) AS INTEGER)"
    leap_year = (
        f"(({year} % 4 = 0 AND {year} % 100 != 0) OR {year} % 400 = 0)"
    )
    days_in_month = (
        f"CASE {month} WHEN 2 THEN CASE WHEN {leap_year} THEN 29 ELSE 28 END "
        f"WHEN 4 THEN 30 WHEN 6 THEN 30 WHEN 9 THEN 30 WHEN 11 THEN 30 ELSE 31 END"
    )
    shape = (
        f"((length({name}) = 25 AND {name} GLOB '{whole_seconds}') OR "
        f"(length({name}) = 32 AND {name} GLOB '{microseconds}' AND "
        f"substr({name}, 21, 6) != '000000'))"
    )
    return (
        f"({shape} AND {year} BETWEEN 1 AND 9999 AND {month} BETWEEN 1 AND 12 "
        f"AND {day} BETWEEN 1 AND ({days_in_month}) "
        f"AND {hour} BETWEEN 0 AND 23 AND {minute} BETWEEN 0 AND 59 "
        f"AND {second} BETWEEN 0 AND 59)"
    )


def _hash_check(name: str, *, nullable: bool) -> str:
    remainder = name
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    closed_hash = (
        f"length({name}) = 64 AND {name} = lower({name}) "
        f"AND length({remainder}) = 0"
    )
    if nullable:
        return f"{name} IS NULL OR ({closed_hash})"
    return closed_hash


def _hash(name: str, *, nullable: bool = False) -> ColumnContract:
    return _text(name, nullable=nullable, check=_hash_check(name, nullable=nullable))


def _sqlite_no_nul_check(name: str) -> str:
    return f"instr({name}, char(0)) = 0"


def handoff_workflows_contract() -> TableContract:
    return TableContract(
        "handoff_workflows",
        (
            _text("handoff_id", check=_nonempty("handoff_id")),
            _text("incident_key", check=_nonempty("incident_key")),
            _integer("revision", "revision >= 0"),
            _text(
                "status",
                check=(
                    "status IN ('requested', 'active', 'acknowledgement_pending', "
                    "'acknowledged', 'manual_review', 'completed', 'cancelled')"
                ),
            ),
            _hash("lead_key_hash"),
            _text("state_json", check=_nonempty("state_json")),
            _hash("state_hash"),
            _timestamp("created_at"),
            _timestamp("updated_at"),
        ),
        (
            "CONSTRAINT pk_handoff_workflows PRIMARY KEY (handoff_id)",
            "CONSTRAINT uq_handoff_workflows_incident_key UNIQUE (incident_key)",
        ),
    )


def handoff_events_contract() -> TableContract:
    return TableContract(
        "handoff_events",
        (
            _text("event_id", check=_nonempty("event_id")),
            _text("handoff_id", check=_nonempty("handoff_id")),
            _integer("revision", "revision >= 1"),
            _text("event_type", check=_nonempty("event_type")),
            _text("event_json", check=_nonempty("event_json")),
            _hash("event_hash"),
            _timestamp("occurred_at"),
        ),
        (
            "CONSTRAINT pk_handoff_events PRIMARY KEY (event_id)",
            "CONSTRAINT fk_handoff_events_workflow FOREIGN KEY (handoff_id) "
            "REFERENCES handoff_workflows (handoff_id)",
            "CONSTRAINT uq_handoff_events_workflow_revision UNIQUE (handoff_id, revision)",
        ),
    )


def _outbox_columns(prefix: str) -> tuple[ColumnContract, ...]:
    workflow_id = "handoff_id" if prefix == "handoff" else "payment_id"
    base: list[ColumnContract] = [
        _text("message_id", check=_nonempty("message_id")),
        _text("idempotency_key", check=_nonempty("idempotency_key")),
        _text("effect_id", check=_nonempty("effect_id")),
        _text(workflow_id, check=_nonempty(workflow_id)),
    ]
    if prefix == "payment":
        base.extend(
            (
                _integer("payment_version", "payment_version >= 1"),
                _hash("economic_signature"),
                _text(
                    "settlement_command_id",
                    check=_nonempty("settlement_command_id"),
                ),
            )
        )
    kind_check = (
        "kind IN ('customer_acknowledgement', 'internal_email')"
        if prefix == "handoff"
        else "kind IN ('customer_payment_confirmation', 'internal_payment_email', 'booking_form')"
    )
    base.extend(
        (
            _text("kind", check=kind_check),
            _text("template_id", check=_nonempty("template_id")),
            _text("payload_json", check=_nonempty("payload_json")),
            _hash("payload_hash"),
            _text("status", check="status IN ('pending', 'leased', 'delivered')"),
            _text("claim_owner", nullable=True, check=_nonempty("claim_owner")),
            _integer("fencing_token", "fencing_token >= 0"),
            _timestamp("lease_acquired_at", nullable=True),
            _timestamp("lease_expires_at", nullable=True),
            _integer("delivery_attempts", "delivery_attempts >= 0"),
            _timestamp("delivered_at", nullable=True),
            _hash("receipt_hash", nullable=True),
            _timestamp("created_at"),
            _timestamp("updated_at"),
        )
    )
    return tuple(base)


def _outbox_constraints(prefix: str) -> tuple[str, ...]:
    table = f"{prefix}_outbox"
    workflow_table = f"{prefix}_workflows"
    workflow_id = "handoff_id" if prefix == "handoff" else "payment_id"
    if prefix == "handoff":
        lease_tuple = (
            "((status = 'pending' AND claim_owner IS NULL AND lease_acquired_at IS NULL "
            "AND lease_expires_at IS NULL) OR (status = 'leased' AND claim_owner IS NOT NULL "
            "AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status = 'delivered' AND claim_owner IS NOT NULL AND lease_acquired_at IS NULL "
            "AND lease_expires_at IS NULL))"
        )
        active_lease = (
            "(status != 'leased' OR (claim_owner IS NOT NULL AND fencing_token >= 1 "
            "AND lease_expires_at > lease_acquired_at))"
        )
        status_matrix = (
            "((status = 'pending' AND claim_owner IS NULL AND delivered_at IS NULL) OR "
            "(status = 'leased' AND claim_owner IS NOT NULL AND delivered_at IS NULL) OR "
            "(status = 'delivered' AND claim_owner IS NOT NULL AND "
            "lease_acquired_at IS NULL AND lease_expires_at IS NULL AND "
            "fencing_token >= 1 AND delivery_attempts >= 1 AND delivered_at IS NOT NULL))"
        )
    else:
        lease_tuple = (
            "((claim_owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) "
            "OR (claim_owner IS NOT NULL AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL))"
        )
        active_lease = (
            "(claim_owner IS NULL OR (fencing_token >= 1 "
            "AND lease_expires_at > lease_acquired_at))"
        )
        status_matrix = (
            "((status = 'pending' AND claim_owner IS NULL AND delivered_at IS NULL) OR "
            "(status = 'leased' AND claim_owner IS NOT NULL AND delivered_at IS NULL) OR "
            "(status = 'delivered' AND claim_owner IS NULL AND "
            "lease_acquired_at IS NULL AND lease_expires_at IS NULL AND "
            "fencing_token >= 1 AND delivery_attempts >= 1 AND delivered_at IS NOT NULL))"
        )
    constraints = [
        f"CONSTRAINT pk_{table} PRIMARY KEY (message_id)",
        f"CONSTRAINT fk_{table}_workflow FOREIGN KEY ({workflow_id}) "
        f"REFERENCES {workflow_table} ({workflow_id})",
    ]
    if prefix == "payment":
        constraints.append(
            "CONSTRAINT fk_payment_outbox_command FOREIGN KEY "
            "(settlement_command_id, payment_id, payment_version, economic_signature) "
            "REFERENCES payment_commands "
            "(settlement_command_id, payment_id, payment_version, economic_signature)"
        )
    constraints.extend(
        (
            f"CONSTRAINT uq_{table}_idempotency_key UNIQUE (idempotency_key)",
            f"CONSTRAINT uq_{table}_effect_id UNIQUE (effect_id)",
            f"CONSTRAINT uq_{table}_receipt_binding UNIQUE "
            "(message_id, receipt_hash, delivered_at)",
            f"CONSTRAINT ck_{table}_lease_tuple CHECK {lease_tuple}",
            f"CONSTRAINT ck_{table}_active_lease CHECK {active_lease}",
            f"CONSTRAINT ck_{table}_receipt_tuple CHECK "
            "((delivered_at IS NULL AND receipt_hash IS NULL) OR "
            "(delivered_at IS NOT NULL AND receipt_hash IS NOT NULL))",
            f"CONSTRAINT ck_{table}_fencing_history CHECK "
            "(fencing_token >= delivery_attempts)",
            f"CONSTRAINT ck_{table}_status_matrix CHECK {status_matrix}",
        )
    )
    return tuple(constraints)


def handoff_outbox_contract() -> TableContract:
    return TableContract(
        "handoff_outbox",
        _outbox_columns("handoff"),
        _outbox_constraints("handoff"),
    )


def _receipts_contract(prefix: str) -> TableContract:
    table = f"{prefix}_receipts"
    outbox = f"{prefix}_outbox"
    return TableContract(
        table,
        (
            _text("receipt_id", check=_nonempty("receipt_id")),
            _text("idempotency_key", check=_nonempty("idempotency_key")),
            _text("message_id", check=_nonempty("message_id")),
            _text("receipt_json", check=_nonempty("receipt_json")),
            _hash("receipt_hash"),
            _timestamp("delivered_at"),
        ),
        (
            f"CONSTRAINT pk_{table} PRIMARY KEY (receipt_id)",
            f"CONSTRAINT fk_{table}_message FOREIGN KEY "
            "(message_id, receipt_hash, delivered_at) "
            f"REFERENCES {outbox} (message_id, receipt_hash, delivered_at)",
            f"CONSTRAINT uq_{table}_idempotency_key UNIQUE (idempotency_key)",
            f"CONSTRAINT uq_{table}_message_id UNIQUE (message_id)",
        ),
    )


def handoff_receipts_contract() -> TableContract:
    return _receipts_contract("handoff")


def payment_workflows_contract() -> TableContract:
    return TableContract(
        "payment_workflows",
        (
            _text("payment_id", check=_nonempty("payment_id")),
            _integer("revision", "revision >= 0"),
            _integer("payment_version", "payment_version >= 1"),
            _hash("economic_signature"),
            _text(
                "status",
                check=(
                    "status IN ('awaiting_method', 'awaiting_financial_confirmation', "
                    "'awaiting_evidence', 'evidence_verified', 'settlement_queued', "
                    "'settling', 'paid', 'retryable', 'manual_review', 'expired', "
                    "'cancelled')"
                ),
            ),
            _text("state_json", check=_nonempty("state_json")),
            _hash("state_hash"),
            _timestamp("created_at"),
            _timestamp("updated_at"),
        ),
        ("CONSTRAINT pk_payment_workflows PRIMARY KEY (payment_id)",),
    )


def payment_events_contract() -> TableContract:
    return TableContract(
        "payment_events",
        (
            _text("event_id", check=_nonempty("event_id")),
            _text("payment_id", check=_nonempty("payment_id")),
            _integer("revision", "revision >= 1"),
            _integer("payment_version", "payment_version >= 1"),
            _hash("economic_signature"),
            _text("event_type", check=_nonempty("event_type")),
            _text("event_json", check=_nonempty("event_json")),
            _hash("event_hash"),
            _timestamp("occurred_at"),
        ),
        (
            "CONSTRAINT pk_payment_events PRIMARY KEY (event_id)",
            "CONSTRAINT fk_payment_events_workflow FOREIGN KEY (payment_id) "
            "REFERENCES payment_workflows (payment_id)",
            "CONSTRAINT uq_payment_events_workflow_revision UNIQUE (payment_id, revision)",
        ),
    )


def payment_evidence_claims_contract() -> TableContract:
    return TableContract(
        "payment_evidence_claims",
        (
            _text("claim_key", check=_nonempty("claim_key")),
            _text("payment_id", check=_nonempty("payment_id")),
            _integer("payment_version", "payment_version >= 1"),
            _hash("economic_signature"),
            _text("method", check="method IN ('pix', 'wise', 'stripe')"),
            _text("evidence_json", check=_nonempty("evidence_json")),
            _hash("evidence_hash"),
            _text(
                "status",
                check="status IN ('in_progress', 'completed', 'retryable', 'manual_review')",
            ),
            _timestamp("claimed_at"),
            _timestamp("consumed_at", nullable=True),
        ),
        (
            "CONSTRAINT pk_payment_evidence_claims PRIMARY KEY (claim_key)",
            "CONSTRAINT fk_payment_evidence_claims_workflow FOREIGN KEY (payment_id) "
            "REFERENCES payment_workflows (payment_id)",
            "CONSTRAINT uq_payment_evidence_claims_binding UNIQUE "
            "(claim_key, payment_id, payment_version, economic_signature)",
            "CONSTRAINT ck_payment_evidence_claims_status_matrix CHECK "
            "(((status = 'in_progress' OR status = 'retryable') AND consumed_at IS NULL) "
            "OR ((status = 'completed' OR status = 'manual_review') "
            "AND consumed_at IS NOT NULL AND consumed_at >= claimed_at))",
        ),
    )


def payment_commands_contract() -> TableContract:
    return TableContract(
        "payment_commands",
        (
            _text(
                "settlement_command_id",
                check=_nonempty("settlement_command_id"),
            ),
            _text("idempotency_key", check=_nonempty("idempotency_key")),
            _text("payment_id", check=_nonempty("payment_id")),
            _integer("payment_version", "payment_version >= 1"),
            _hash("economic_signature"),
            _text("evidence_claim_key", check=_nonempty("evidence_claim_key")),
            _text("operation", check="operation IN ('register_and_confirm')"),
            _text("command_json", check=_nonempty("command_json")),
            _hash("command_hash"),
            _timestamp("created_at"),
        ),
        (
            "CONSTRAINT pk_payment_commands PRIMARY KEY (settlement_command_id)",
            "CONSTRAINT fk_payment_commands_workflow FOREIGN KEY (payment_id) "
            "REFERENCES payment_workflows (payment_id)",
            "CONSTRAINT fk_payment_commands_evidence FOREIGN KEY "
            "(evidence_claim_key, payment_id, payment_version, economic_signature) "
            "REFERENCES payment_evidence_claims "
            "(claim_key, payment_id, payment_version, economic_signature)",
            "CONSTRAINT uq_payment_commands_idempotency_key UNIQUE (idempotency_key)",
            "CONSTRAINT uq_payment_commands_subject UNIQUE "
            "(payment_id, payment_version, economic_signature)",
            "CONSTRAINT uq_payment_commands_binding UNIQUE "
            "(settlement_command_id, payment_id, payment_version, economic_signature)",
        ),
    )


def payment_ledger_contract() -> TableContract:
    return TableContract(
        "payment_ledger",
        (
            _text(
                "settlement_command_id",
                check=_nonempty("settlement_command_id"),
            ),
            _text("payment_id", check=_nonempty("payment_id")),
            _integer("payment_version", "payment_version >= 1"),
            _hash("economic_signature"),
            _text(
                "status",
                check=(
                    "status IN ('queued', 'leased', 'dispatch_fenced', "
                    "'outcome_recorded', 'manual_review')"
                ),
            ),
            _text("claim_owner", nullable=True, check=_nonempty("claim_owner")),
            _integer("fencing_token", "fencing_token >= 0"),
            _timestamp("lease_acquired_at", nullable=True),
            _timestamp("lease_expires_at", nullable=True),
            _integer("claim_count", "claim_count >= 0"),
            _integer(
                "dispatch_slots_consumed",
                "dispatch_slots_consumed >= 0 AND dispatch_slots_consumed <= 1",
            ),
            _hash("dispatch_request_hash", nullable=True),
            _timestamp("dispatch_fenced_at", nullable=True),
            _text(
                "outcome_certainty",
                nullable=True,
                check=(
                    "outcome_certainty IS NULL OR outcome_certainty IN "
                    "('not_dispatched', 'dispatched_no_effect', 'settled', "
                    "'partial_settlement', 'dispatched_unknown')"
                ),
            ),
            _text("outcome_json", nullable=True, check=_nonempty("outcome_json")),
            _hash("outcome_hash", nullable=True),
            _timestamp("outcome_recorded_at", nullable=True),
            _timestamp("updated_at"),
        ),
        (
            "CONSTRAINT pk_payment_ledger PRIMARY KEY (settlement_command_id)",
            "CONSTRAINT fk_payment_ledger_command FOREIGN KEY "
            "(settlement_command_id, payment_id, payment_version, economic_signature) "
            "REFERENCES payment_commands "
            "(settlement_command_id, payment_id, payment_version, economic_signature)",
            "CONSTRAINT uq_payment_ledger_subject UNIQUE "
            "(payment_id, payment_version, economic_signature)",
            "CONSTRAINT ck_payment_ledger_fencing_history CHECK "
            "(fencing_token = claim_count)",
            "CONSTRAINT ck_payment_ledger_lease_tuple CHECK "
            "((claim_owner IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) "
            "OR (claim_owner IS NOT NULL AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL))",
            "CONSTRAINT ck_payment_ledger_active_lease CHECK "
            "(claim_owner IS NULL OR (fencing_token >= 1 "
            "AND lease_expires_at > lease_acquired_at))",
            "CONSTRAINT ck_payment_ledger_dispatch_tuple CHECK "
            "((dispatch_slots_consumed = 0 AND dispatch_request_hash IS NULL "
            "AND dispatch_fenced_at IS NULL) OR "
            "(dispatch_slots_consumed = 1 AND dispatch_request_hash IS NOT NULL "
            "AND dispatch_fenced_at IS NOT NULL AND fencing_token >= 1))",
            "CONSTRAINT ck_payment_ledger_outcome_tuple CHECK "
            "((outcome_certainty IS NULL AND outcome_json IS NULL AND outcome_hash IS NULL "
            "AND outcome_recorded_at IS NULL) OR "
            "(outcome_certainty IS NOT NULL AND outcome_json IS NOT NULL "
            "AND outcome_hash IS NOT NULL AND outcome_recorded_at IS NOT NULL))",
            "CONSTRAINT ck_payment_ledger_status_matrix CHECK "
            "((status = 'queued' AND claim_owner IS NULL AND claim_count >= 0 "
            "AND dispatch_slots_consumed = 0 AND outcome_json IS NULL) OR "
            "(status = 'leased' AND claim_owner IS NOT NULL AND claim_count >= 1 "
            "AND dispatch_slots_consumed = 0 AND outcome_json IS NULL) OR "
            "(status = 'dispatch_fenced' AND claim_owner IS NOT NULL "
            "AND claim_count >= 1 AND dispatch_slots_consumed = 1 "
            "AND outcome_json IS NULL) OR "
            "(status = 'outcome_recorded' AND claim_owner IS NULL AND "
            "outcome_json IS NOT NULL AND "
            "((outcome_certainty = 'not_dispatched' AND dispatch_slots_consumed = 0) "
            "OR (outcome_certainty = 'settled' AND dispatch_slots_consumed = 1))) OR "
            "(status = 'manual_review' AND claim_owner IS NULL "
            "AND outcome_json IS NOT NULL AND dispatch_slots_consumed = 1 "
            "AND outcome_certainty IN "
            "('dispatched_no_effect', 'partial_settlement', 'dispatched_unknown')))"
        ),
    )


def payment_outbox_contract() -> TableContract:
    return TableContract(
        "payment_outbox",
        _outbox_columns("payment"),
        _outbox_constraints("payment"),
    )


def payment_receipts_contract() -> TableContract:
    return _receipts_contract("payment")


def schema_contract() -> tuple[TableContract, ...]:
    return (
        handoff_workflows_contract(),
        handoff_events_contract(),
        handoff_outbox_contract(),
        handoff_receipts_contract(),
        payment_workflows_contract(),
        payment_events_contract(),
        payment_evidence_claims_contract(),
        payment_commands_contract(),
        payment_ledger_contract(),
        payment_outbox_contract(),
        payment_receipts_contract(),
    )


def _render_column(dialect: Dialect, column: ColumnContract) -> str:
    sql_type = column.sqlite_type if dialect == "sqlite" else column.postgresql_type
    parts = [column.name, sql_type]
    if not column.nullable:
        parts.append("NOT NULL")
    checks: list[str] = []
    if column.check is not None:
        checks.append(column.check)
    if dialect == "sqlite" and column.postgresql_type == "timestamptz":
        checks.append(_sqlite_timestamp_check(column.name))
    if dialect == "sqlite" and column.sqlite_type == "TEXT":
        checks.append(_sqlite_no_nul_check(column.name))
    parts.extend(f"CHECK ({check})" for check in checks)
    return " ".join(parts)


def _render(dialect: Dialect, contract: tuple[TableContract, ...]) -> str:
    if dialect not in ("sqlite", "postgresql"):
        raise ValueError(f"unsupported schema dialect: {dialect}")
    tables: list[str] = []
    for table in contract:
        definitions = [
            *(_render_column(dialect, column) for column in table.columns),
            *table.table_constraints,
        ]
        body = ",\n".join(f"    {definition}" for definition in definitions)
        strict = " STRICT" if dialect == "sqlite" else ""
        tables.append(f"CREATE TABLE {table.name} (\n{body}\n){strict};")
    return "\n\n".join(tables) + "\n"


def render_sqlite() -> str:
    return "PRAGMA foreign_keys = ON;\n\n" + _render("sqlite", schema_contract())


def render_postgresql() -> str:
    return _render("postgresql", schema_contract())


def schema_hash(dialect: str) -> str:
    if dialect == "sqlite":
        sql = render_sqlite()
    elif dialect == "postgresql":
        sql = render_postgresql()
    else:
        raise ValueError(f"unsupported schema dialect: {dialect}")
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


__all__ = [
    "SCHEMA_VERSION",
    "ColumnContract",
    "TableContract",
    "render_postgresql",
    "render_sqlite",
    "schema_contract",
    "schema_hash",
]
