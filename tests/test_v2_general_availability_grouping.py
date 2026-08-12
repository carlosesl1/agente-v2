from __future__ import annotations

from dataclasses import replace

from reservation_domain import ReservationCommand
from reservation_domain.signature import command_identity
from tests.test_v2_outcome_projector import _package_command
from v2_application.reservations import ReservationAllocator


def _for_other_lead_with_reused_draft(command: ReservationCommand) -> ReservationCommand:
    customer = replace(
        command.payload.customer,
        customer_ref="effective-customer:other-lead",
    )
    payload = replace(command.payload, customer=customer)
    from reservation_domain.signature import subject_signature

    signature = subject_signature(
        components=payload.components,
        customer=payload.customer,
        terms=payload.terms,
    )
    command_id, idempotency_key = command_identity(
        workflow_id="workflow:other-lead",
        draft_id=command.draft_id,
        draft_version=command.draft_version,
        signature=signature,
        operation=command.operation,
    )
    return replace(
        command,
        command_id=command_id,
        idempotency_key=idempotency_key,
        workflow_id="workflow:other-lead",
        subject_signature=signature,
        payload=payload,
    )


def test_projection_grouping_keeps_reused_draft_ids_isolated_by_customer() -> None:
    first = ReservationAllocator().allocate(_package_command()).commands[0]
    second = _for_other_lead_with_reused_draft(first)

    groups: dict[tuple[str, str, int], list[ReservationCommand]] = {}
    for command in (first, second):
        groups.setdefault(
            (
                command.payload.customer.customer_ref,
                command.draft_id,
                command.draft_version,
            ),
            [],
        ).append(command)

    assert len(groups) == 2
    assert {key[0] for key in groups} == {
        first.payload.customer.customer_ref,
        second.payload.customer.customer_ref,
    }
