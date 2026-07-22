"""Declarative Phase 5 schema and deterministic SQLite/PostgreSQL renderers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Literal

SCHEMA_VERSION = 5
SCHEMA_VERSION_V6 = 6

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
        f"CASE {month} "
        "WHEN 2 THEN CASE WHEN "
        f"{leap_year} THEN 29 ELSE 28 END "
        f"WHEN 4 THEN 30 WHEN 6 THEN 30 WHEN 9 THEN 30 WHEN 11 THEN 30 "
        "ELSE 31 END"
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


def _is_hash_column(column: ColumnContract) -> bool:
    return (
        column.check is not None
        and f"length({column.name}) = 64" in column.check
        and f"{column.name} = lower({column.name})" in column.check
    )


def _sqlite_no_nul_check(name: str) -> str:
    return f"instr({name}, char(0)) = 0"


def schema_migrations_contract() -> TableContract:
    return TableContract(
        name="schema_migrations",
        columns=(
            _integer("version", "version >= 1"),
            _hash("schema_hash"),
            _timestamp("applied_at"),
        ),
        table_constraints=(
            "CONSTRAINT pk_schema_migrations PRIMARY KEY (version)",
        ),
    )


def workflows_contract() -> TableContract:
    return TableContract(
        name="workflows",
        columns=(
            _text("workflow_id"),
            _integer("revision", "revision >= 0"),
            _text("state_type"),
            _text("state_json"),
            _hash("state_hash"),
            _timestamp("created_at"),
            _timestamp("updated_at"),
        ),
        table_constraints=(
            "CONSTRAINT pk_workflows PRIMARY KEY (workflow_id)",
        ),
    )


def domain_events_contract() -> TableContract:
    return TableContract(
        name="domain_events",
        columns=(
            _text("event_id"),
            _text("workflow_id"),
            _integer("revision", "revision >= 0"),
            _timestamp("occurred_at"),
            _text("event_type"),
            _text("event_json"),
            _hash("event_hash"),
        ),
        table_constraints=(
            "CONSTRAINT pk_domain_events PRIMARY KEY (event_id)",
            "CONSTRAINT fk_domain_events_workflow FOREIGN KEY (workflow_id) "
            "REFERENCES workflows (workflow_id)",
            "CONSTRAINT uq_domain_events_workflow_revision "
            "UNIQUE (workflow_id, revision)",
        ),
    )


def reservation_commands_contract() -> TableContract:
    return TableContract(
        name="reservation_commands",
        columns=(
            _text("command_id"),
            _text("idempotency_key"),
            _text("workflow_id"),
            _text("draft_id"),
            _integer("draft_version", "draft_version >= 1"),
            _hash("subject_signature"),
            _text(
                "operation",
                check=(
                    "operation IN ('reserve_lodging', 'book_activity', "
                    "'reserve_package')"
                ),
            ),
            _text("command_json"),
            _hash("command_hash"),
            _timestamp("created_at"),
        ),
        table_constraints=(
            "CONSTRAINT pk_reservation_commands PRIMARY KEY (command_id)",
            "CONSTRAINT fk_reservation_commands_workflow FOREIGN KEY (workflow_id) "
            "REFERENCES workflows (workflow_id)",
            "CONSTRAINT uq_reservation_commands_idempotency_key "
            "UNIQUE (idempotency_key)",
            "CONSTRAINT uq_reservation_commands_workflow UNIQUE (workflow_id)",
            "CONSTRAINT uq_reservation_commands_identity "
            "UNIQUE (workflow_id, draft_id, draft_version, operation)",
        ),
    )


def execution_ledger_contract() -> TableContract:
    return TableContract(
        name="execution_ledger",
        columns=(
            _text("command_id"),
            _text(
                "status",
                check=(
                    "status IN ('queued', 'preparing', 'dispatch_fenced', "
                    "'outcome_recorded', 'manual_review')"
                ),
            ),
            _text("claim_owner", nullable=True),
            _integer("fencing_token", "fencing_token >= 0"),
            _timestamp("lease_acquired_at", nullable=True),
            _timestamp("lease_expires_at", nullable=True),
            _integer("claim_count", "claim_count >= 0"),
            _integer(
                "preparation_failures",
                "preparation_failures >= 0 AND preparation_failures <= 3",
            ),
            _integer(
                "dispatch_slots_consumed",
                "dispatch_slots_consumed >= 0 AND dispatch_slots_consumed <= 1",
            ),
            _hash("dispatch_request_hash", nullable=True),
            _timestamp("dispatch_fenced_at", nullable=True),
            _text("outcome_json", nullable=True),
            _hash("outcome_hash", nullable=True),
            _timestamp("updated_at"),
        ),
        table_constraints=(
            "CONSTRAINT pk_execution_ledger PRIMARY KEY (command_id)",
            "CONSTRAINT fk_execution_ledger_command FOREIGN KEY (command_id) "
            "REFERENCES reservation_commands (command_id)",
            "CONSTRAINT ck_execution_ledger_lease_tuple CHECK "
            "((claim_owner IS NULL AND lease_acquired_at IS NULL AND "
            "lease_expires_at IS NULL) OR (claim_owner IS NOT NULL AND "
            "lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL))",
            "CONSTRAINT ck_execution_ledger_active_lease CHECK "
            "(claim_owner IS NULL OR (fencing_token >= 1 AND "
            "lease_expires_at > lease_acquired_at))",
            "CONSTRAINT ck_execution_ledger_dispatch_tuple CHECK "
            "((dispatch_slots_consumed = 0 AND dispatch_request_hash IS NULL AND "
            "dispatch_fenced_at IS NULL) OR (dispatch_slots_consumed = 1 AND "
            "dispatch_request_hash IS NOT NULL AND dispatch_fenced_at IS NOT NULL))",
            "CONSTRAINT ck_execution_ledger_outcome_tuple CHECK "
            "((outcome_json IS NULL AND outcome_hash IS NULL) OR "
            "(outcome_json IS NOT NULL AND outcome_hash IS NOT NULL))",
            "CONSTRAINT ck_execution_ledger_status_matrix CHECK "
            "((status = 'queued' AND claim_owner IS NULL AND "
            "dispatch_slots_consumed = 0 AND outcome_json IS NULL) OR "
            "(status = 'preparing' AND claim_owner IS NOT NULL AND "
            "claim_count >= 1 AND dispatch_slots_consumed = 0 AND "
            "outcome_json IS NULL) OR (status = 'dispatch_fenced' AND "
            "claim_owner IS NOT NULL AND claim_count >= 1 AND "
            "dispatch_slots_consumed = 1 AND outcome_json IS NULL) OR "
            "(status = 'outcome_recorded' AND claim_owner IS NULL AND "
            "dispatch_slots_consumed IN (0, 1) AND outcome_json IS NOT NULL) OR "
            "(status = 'manual_review' AND claim_owner IS NULL AND "
            "dispatch_slots_consumed = 1 AND outcome_json IS NOT NULL))",
        ),
    )


def outbox_messages_contract() -> TableContract:
    return TableContract(
        name="outbox_messages",
        columns=(
            _text("message_id"),
            _text("idempotency_key"),
            _text("workflow_id"),
            _text("command_id", nullable=True),
            _text(
                "kind",
                check=(
                    "kind IN ('summary_presented', 'execution_succeeded', "
                    "'execution_failed_no_effect', 'execution_not_called', "
                    "'execution_manual_review')"
                ),
            ),
            _text("template_id"),
            _text("payload_json"),
            _hash("payload_hash"),
            _text(
                "status",
                check="status IN ('pending', 'leased', 'delivered')",
            ),
            _text("claim_owner", nullable=True),
            _integer("fencing_token", "fencing_token >= 0"),
            _timestamp("lease_acquired_at", nullable=True),
            _timestamp("lease_expires_at", nullable=True),
            _integer("delivery_attempts", "delivery_attempts >= 0"),
            _timestamp("delivered_at", nullable=True),
            _hash("receipt_hash", nullable=True),
            _timestamp("created_at"),
            _timestamp("updated_at"),
        ),
        table_constraints=(
            "CONSTRAINT pk_outbox_messages PRIMARY KEY (message_id)",
            "CONSTRAINT fk_outbox_messages_workflow FOREIGN KEY (workflow_id) "
            "REFERENCES workflows (workflow_id)",
            "CONSTRAINT fk_outbox_messages_command FOREIGN KEY (command_id) "
            "REFERENCES reservation_commands (command_id)",
            "CONSTRAINT uq_outbox_messages_idempotency_key "
            "UNIQUE (idempotency_key)",
            "CONSTRAINT ck_outbox_messages_lease_tuple CHECK "
            "((claim_owner IS NULL AND lease_acquired_at IS NULL AND "
            "lease_expires_at IS NULL) OR (claim_owner IS NOT NULL AND "
            "lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL))",
            "CONSTRAINT ck_outbox_messages_active_lease CHECK "
            "(claim_owner IS NULL OR (fencing_token >= 1 AND "
            "lease_expires_at > lease_acquired_at))",
            "CONSTRAINT ck_outbox_messages_receipt_tuple CHECK "
            "((delivered_at IS NULL AND receipt_hash IS NULL) OR "
            "(delivered_at IS NOT NULL AND receipt_hash IS NOT NULL))",
            "CONSTRAINT ck_outbox_messages_status_matrix CHECK "
            "((status = 'pending' AND claim_owner IS NULL AND delivered_at IS NULL) "
            "OR (status = 'leased' AND claim_owner IS NOT NULL AND "
            "delivered_at IS NULL) OR (status = 'delivered' AND "
            "claim_owner IS NULL AND delivered_at IS NOT NULL AND "
            "delivery_attempts >= 1))",
        ),
    )


def schema_contract() -> tuple[TableContract, ...]:
    return (
        schema_migrations_contract(),
        workflows_contract(),
        domain_events_contract(),
        reservation_commands_contract(),
        execution_ledger_contract(),
        outbox_messages_contract(),
    )


def reservation_boundary_ingress_receipts_contract() -> TableContract:
    return TableContract(
        name="reservation_boundary_ingress_receipts",
        columns=(
            _hash("operation_id"),
            _hash("source_turn_receipt_hash"),
            _hash("artifact_hash"),
            _text("bundle_json"),
            _hash("target_commit_hash"),
            _hash("target_result_hash"),
            _text("receipt_json"),
            _hash("receipt_hash"),
            _text("qualification_id", nullable=True),
            ColumnContract("epoch", "INTEGER", "bigint", True, "epoch IS NULL OR epoch >= 1"),
            _text("scenario_id", nullable=True),
            _text("generation_id", nullable=True),
            _text("allocation_id", nullable=True),
            _hash("authority_row_hash", nullable=True),
            _timestamp("committed_at"),
        ),
        table_constraints=(
            "CONSTRAINT pk_reservation_boundary_ingress_receipts "
            "PRIMARY KEY (operation_id)",
            "CONSTRAINT uq_reservation_boundary_ingress_receipts_receipt "
            "UNIQUE (receipt_hash)",
            "CONSTRAINT ck_reservation_boundary_ingress_receipts_authority_tuple CHECK "
            "((qualification_id IS NULL AND epoch IS NULL AND scenario_id IS NULL AND "
            "generation_id IS NULL AND allocation_id IS NULL AND authority_row_hash IS NULL) "
            "OR (qualification_id IS NOT NULL AND epoch IS NOT NULL AND "
            "scenario_id IS NOT NULL AND generation_id IS NOT NULL AND "
            "allocation_id IS NOT NULL AND authority_row_hash IS NOT NULL))",
        ),
    )


def reservation_e2e_effect_authority_contract() -> TableContract:
    def nullable_ordinal(name: str) -> ColumnContract:
        return ColumnContract(name, "INTEGER", "bigint", True, f"{name} IS NULL OR {name} >= 0")

    return TableContract(
        name="reservation_e2e_effect_authority",
        columns=(
            _text("row_kind", check="row_kind IN ('generation_header', 'allocation')"),
            _text("installation_target", check="installation_target = 'reservation_e2e_effect_authority'"),
            _text("qualification_id"),
            _integer("epoch", "epoch >= 1"),
            _text("scenario_id"),
            _hash("contract_hash"),
            _hash("effect_authorization_binding_hash"),
            _hash("manifest_hash"),
            _text("generation_id"),
            _text("allocation_id"),
            nullable_ordinal("allocation_ordinal"),
            _hash("allocation_hash", nullable=True),
            _text("effect_family", nullable=True, check="effect_family IS NULL OR effect_family = 'reservation'"),
            _text("effect_kind", nullable=True, check="effect_kind IS NULL OR effect_kind IN ('provider_primary', 'provider_compensation')"),
            _text("effect_role", nullable=True, check="effect_role IS NULL OR effect_role IN ('primary', 'compensation')"),
            _hash("effect_scope_hash", nullable=True),
            _hash("workflow_scope_hash", nullable=True),
            _hash("channel_scope_hash", nullable=True),
            _hash("target_binding_hash", nullable=True),
            nullable_ordinal("message_ordinal"),
            _text("activation_parent_kind", nullable=True, check="activation_parent_kind IS NULL OR activation_parent_kind IN ('none', 'provider_allocation', 'internal_target_operation')"),
            _text("activation_parent_id", nullable=True),
            _hash("activation_parent_hash", nullable=True),
            _text("state", check="state IN ('open', 'closing', 'closed', 'available', 'bound', 'dispatch_fenced', 'terminal', 'manual_review')"),
            _text("bound_subject_id", nullable=True),
            _hash("bound_subject_hash", nullable=True),
            _text("child_decision_receipt_json", nullable=True),
            _hash("child_decision_receipt_hash", nullable=True),
            _integer("revision", "revision >= 0"),
            _hash("installation_operation_id", nullable=True),
            _text("installation_receipt_json", nullable=True),
            _hash("installation_receipt_hash", nullable=True),
            _hash("installed_allocation_aggregate_hash", nullable=True),
            _timestamp("installed_at"),
            _timestamp("closed_at", nullable=True),
        ),
        table_constraints=(
            "CONSTRAINT pk_reservation_e2e_effect_authority PRIMARY KEY (qualification_id, scenario_id, generation_id, allocation_id)",
            "CONSTRAINT uq_reservation_e2e_effect_authority_allocation UNIQUE (allocation_id)",
            "CONSTRAINT ck_reservation_e2e_effect_authority_row_matrix CHECK (((row_kind = 'generation_header' AND allocation_id = '__header__' AND allocation_ordinal IS NULL AND allocation_hash IS NULL AND effect_family IS NULL AND effect_kind IS NULL AND effect_role IS NULL AND effect_scope_hash IS NULL AND workflow_scope_hash IS NULL AND channel_scope_hash IS NULL AND target_binding_hash IS NULL AND message_ordinal IS NULL AND activation_parent_kind IS NULL AND activation_parent_id IS NULL AND activation_parent_hash IS NULL AND bound_subject_id IS NULL AND bound_subject_hash IS NULL AND child_decision_receipt_json IS NULL AND child_decision_receipt_hash IS NULL AND state IN ('open', 'closing', 'closed', 'manual_review')) OR (row_kind = 'allocation' AND allocation_id != '__header__' AND allocation_ordinal IS NOT NULL AND allocation_hash IS NOT NULL AND effect_family = 'reservation' AND effect_kind IS NOT NULL AND effect_role IS NOT NULL AND effect_scope_hash IS NOT NULL AND workflow_scope_hash IS NOT NULL AND channel_scope_hash IS NULL AND target_binding_hash IS NOT NULL AND message_ordinal IS NULL AND activation_parent_kind IS NOT NULL AND installation_operation_id IS NULL AND installation_receipt_json IS NULL AND installation_receipt_hash IS NULL AND installed_allocation_aggregate_hash IS NULL AND state IN ('available', 'bound', 'dispatch_fenced', 'terminal', 'closed', 'manual_review'))))",
            "CONSTRAINT ck_reservation_e2e_effect_authority_bind_tuple CHECK ((bound_subject_id IS NULL AND bound_subject_hash IS NULL) OR (bound_subject_id IS NOT NULL AND bound_subject_hash IS NOT NULL))",
            "CONSTRAINT ck_reservation_e2e_effect_authority_close_tuple CHECK (((state = 'closed') AND closed_at IS NOT NULL) OR (state != 'closed' AND closed_at IS NULL))",
        ),
    )


def schema_contract_v6() -> tuple[TableContract, ...]:
    return (
        *schema_contract(),
        reservation_boundary_ingress_receipts_contract(),
        reservation_e2e_effect_authority_contract(),
    )


PHASE5_V6_TABLES = tuple(table.name for table in schema_contract_v6())


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
        checks.append(_sqlite_no_nul_check(column.name))
    if dialect == "sqlite" and _is_hash_column(column):
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
    return _render("sqlite", schema_contract())


def render_sqlite_v6() -> str:
    return _render("sqlite", schema_contract_v6())


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


def schema_hash_v6() -> str:
    return hashlib.sha256(render_sqlite_v6().encode("utf-8")).hexdigest()
