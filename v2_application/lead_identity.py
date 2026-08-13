"""Resolve public ManyChat ownership from authenticated local ledgers only."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_domain import ServiceKind
from reservation_domain.types import ReservationCommand
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_contracts.payments import BusinessUnit


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class EffectTraceContext:
    execution_id: str
    lead_id: str
    source_turn_receipt_hash: str

    def __post_init__(self) -> None:
        if type(self.execution_id) is not str or not self.execution_id:
            raise ValueError("execution_id must be non-empty exact text")
        if (
            type(self.lead_id) is not str
            or not self.lead_id.startswith("manychat:")
            or not self.lead_id.removeprefix("manychat:").isdecimal()
        ):
            raise ValueError("lead_id must be one exact ManyChat lead")
        if (
            type(self.source_turn_receipt_hash) is not str
            or _HASH_RE.fullmatch(self.source_turn_receipt_hash) is None
        ):
            raise ValueError("source_turn_receipt_hash must be lowercase SHA-256")


class SQLiteEffectTraceContextResolver:
    """Resolve one effect to the primary source event through exact durable joins."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if type(connection) is not sqlite3.Connection:
            raise TypeError("connection must be exact sqlite3.Connection")
        self._connection = connection

    def _one(self, query: str, parameters: tuple[object, ...]) -> EffectTraceContext:
        rows = tuple(self._connection.execute(query, parameters))
        if len(rows) != 1:
            raise RuntimeError("effect does not have one durable primary source")
        lead_id, execution_id, effect_receipt, source_receipt = rows[0]
        if effect_receipt != source_receipt:
            raise RuntimeError("effect source correlation diverges")
        return EffectTraceContext(execution_id, lead_id, source_receipt)

    def for_command(self, command_id: str) -> EffectTraceContext:
        if type(command_id) is not str or not command_id:
            raise ValueError("command_id must be non-empty exact text")
        return self._one(
            "SELECT c.lead_key,s.source_event_id,c.source_turn_receipt_hash,"
            "s.source_turn_receipt_hash FROM boundary_commands c "
            "JOIN boundary_event_sources s ON s.lead_key=c.lead_key "
            "AND s.aggregate_turn_id=c.aggregate_turn_id AND s.source_index=0 "
            "WHERE c.command_id=?",
            (command_id,),
        )

    def for_public_row(self, public_row_id: str) -> EffectTraceContext:
        if type(public_row_id) is not str or not public_row_id:
            raise ValueError("public_row_id must be non-empty exact text")
        return self._one(
            "SELECT p.lead_key,s.source_event_id,p.source_turn_receipt_hash,"
            "s.source_turn_receipt_hash FROM boundary_public_outbox p "
            "JOIN boundary_event_sources s ON s.lead_key=p.lead_key "
            "AND s.aggregate_turn_id=p.aggregate_turn_id AND s.source_index=0 "
            "WHERE p.public_row_id=?",
            (public_row_id,),
        )

    def for_source_receipt(self, source_turn_receipt_hash: str) -> EffectTraceContext:
        if (
            type(source_turn_receipt_hash) is not str
            or _HASH_RE.fullmatch(source_turn_receipt_hash) is None
        ):
            raise ValueError("source_turn_receipt_hash must be lowercase SHA-256")
        return self._one(
            "SELECT lead_key,source_event_id,source_turn_receipt_hash,"
            "source_turn_receipt_hash FROM boundary_event_sources "
            "WHERE source_index=0 AND source_turn_receipt_hash=?",
            (source_turn_receipt_hash,),
        )


def _opaque(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()[:32]


def payment_id_for_command(command: ReservationCommand) -> str:
    if type(command) is not ReservationCommand:
        raise TypeError("command must be exact ReservationCommand")
    component = command.payload.components[0]
    unit = {
        ServiceKind.LODGING: BusinessUnit.HOSTEL,
        ServiceKind.ACTIVITY: BusinessUnit.AGENCY,
    }[component.service]
    return _opaque("payment", command.command_id, unit.value)


class DurableLeadResolver:
    """Fail closed unless one durable V2 identity owns the requested effect."""

    def __init__(
        self,
        *,
        boundary: SQLiteBoundaryStore,
        execution: SQLiteUnitOfWork,
        followup: SQLiteFollowupUnitOfWork,
    ) -> None:
        if type(boundary) is not SQLiteBoundaryStore:
            raise TypeError("boundary must be exact SQLiteBoundaryStore")
        if type(execution) is not SQLiteUnitOfWork:
            raise TypeError("execution must be exact SQLiteUnitOfWork")
        if type(followup) is not SQLiteFollowupUnitOfWork:
            raise TypeError("followup must be exact SQLiteFollowupUnitOfWork")
        self._boundary = boundary
        self._execution = execution
        self._followup = followup
        self._effect_trace = SQLiteEffectTraceContextResolver(
            boundary._connection
        )

    def effect_trace_for_command(self, command_id: str) -> EffectTraceContext:
        return self._effect_trace.for_command(command_id)

    def effect_trace_for_payment(self, payment_id: str) -> EffectTraceContext:
        if type(payment_id) is not str or not payment_id:
            raise ValueError("payment_id must be non-empty exact text")
        matches = tuple(
            command
            for command, _ in self._execution.list_outcome_projection_inputs()
            if payment_id_for_command(command) == payment_id
        )
        if len(matches) != 1:
            raise RuntimeError("payment does not have one durable command owner")
        return self.effect_trace_for_command(matches[0].command_id)

    def effect_trace_for_public_row(self, public_row_id: str) -> EffectTraceContext:
        return self._effect_trace.for_public_row(public_row_id)

    @staticmethod
    def _manychat_lead(value: object) -> str:
        if type(value) is not str or not value.startswith("manychat:"):
            raise RuntimeError("effect is not bound to a ManyChat lead")
        subscriber_id = value.removeprefix("manychat:")
        if not subscriber_id.isdecimal():
            raise RuntimeError("effect is not bound to a decimal ManyChat subscriber")
        return value

    def lead_id_for_command(self, command_id: str) -> str:
        if type(command_id) is not str or not command_id:
            raise ValueError("command_id must be non-empty exact text")
        rows = tuple(
            self._boundary._connection.execute(
                "SELECT lead_key FROM boundary_commands WHERE command_id=?",
                (command_id,),
            )
        )
        if len(rows) != 1:
            raise RuntimeError("command does not have one durable lead owner")
        return self._manychat_lead(rows[0][0])

    def lead_id_for_payment(self, payment_id: str) -> str:
        if type(payment_id) is not str or not payment_id:
            raise ValueError("payment_id must be non-empty exact text")
        matches = tuple(
            command
            for command, _ in self._execution.list_outcome_projection_inputs()
            if payment_id_for_command(command) == payment_id
        )
        if len(matches) != 1:
            raise RuntimeError("payment does not have one durable command owner")
        return self.lead_id_for_command(matches[0].command_id)

    def lead_id_for_handoff(self, handoff_id: str) -> str:
        workflow = self._followup.load_handoff(handoff_id)
        expected_hash = workflow.request.lead_key_hash
        rows = tuple(
            row[0]
            for row in self._boundary._connection.execute(
                "SELECT lead_key FROM boundary_state ORDER BY lead_key"
            )
            if hashlib.sha256(row[0].encode("utf-8")).hexdigest() == expected_hash
        )
        if len(rows) != 1:
            raise RuntimeError("handoff does not have one durable lead owner")
        return self._manychat_lead(rows[0])

    def subscriber_id_for_command(self, command_id: str) -> str:
        return self.lead_id_for_command(command_id).removeprefix("manychat:")

    def subscriber_id_for_payment(self, payment_id: str) -> str:
        return self.lead_id_for_payment(payment_id).removeprefix("manychat:")

    def subscriber_id_for_handoff(self, handoff_id: str) -> str:
        return self.lead_id_for_handoff(handoff_id).removeprefix("manychat:")


__all__ = [
    "DurableLeadResolver",
    "EffectTraceContext",
    "SQLiteEffectTraceContextResolver",
    "payment_id_for_command",
]
