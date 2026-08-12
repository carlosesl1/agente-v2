"""Resolve public ManyChat ownership from authenticated local ledgers only."""

from __future__ import annotations

import hashlib

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_domain import ServiceKind
from reservation_domain.types import ReservationCommand
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_contracts.payments import BusinessUnit


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


__all__ = ["DurableLeadResolver", "payment_id_for_command"]
