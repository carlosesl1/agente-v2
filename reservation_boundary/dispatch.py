"""Unique literal dispatch catalog for the 13 observed active v2 tools."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Mapping

from reservation_domain import (
    ExecutionQueuedState,
    ReservationCommand,
    ReservationOperation,
    ServiceKind,
    UncertainState,
)
from reservation_followup import BusinessUnit, PaymentSettlementCommand, PaymentStatus

from reservation_boundary.types import (
    ActivityDescriptionArguments,
    ActivityPaymentArguments,
    ActivityReadArguments,
    ActivityReservationArguments,
    BoundaryCommand,
    BoundaryState,
    CommandMigrationDisposition,
    DateSlot,
    DispatchKind,
    FaqReadArguments,
    IntegerSlot,
    LodgingPaymentArguments,
    LodgingReadArguments,
    LodgingReservationArguments,
    RoomDescriptionArguments,
    StateCommitArguments,
    StringSlot,
    StripeLinkArguments,
    ToolArguments,
    ToolDispatchRequest,
    TurnPlanReason,
    TypedFact,
    WiseVerificationArguments,
)


class DispatchRejected(ValueError):
    """Request cannot cross the typed dispatch boundary."""


@dataclass(frozen=True, slots=True)
class ToolContract:
    kind: DispatchKind
    arguments_type: type[object]
    command_migration: CommandMigrationDisposition | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not DispatchKind:
            raise TypeError("kind must be exact DispatchKind")
        if type(self.arguments_type) is not type:
            raise TypeError("arguments_type must be an exact class")
        if self.kind is DispatchKind.COMMAND:
            if type(self.command_migration) is not CommandMigrationDisposition:
                raise ValueError("command contract requires migration disposition")
        elif self.command_migration is not None:
            raise ValueError("non-command contract cannot carry migration disposition")


@dataclass(frozen=True, slots=True)
class DispatchResult:
    tool_name: str
    kind: DispatchKind
    command_migration: CommandMigrationDisposition | None
    read_request: ToolDispatchRequest | None
    commands: tuple[BoundaryCommand, ...]
    facts: tuple[TypedFact, ...]
    reason: TurnPlanReason

    def __post_init__(self) -> None:
        if type(self.tool_name) is not str or not self.tool_name:
            raise TypeError("tool_name must be exact nonempty text")
        if type(self.kind) is not DispatchKind:
            raise TypeError("kind must be exact DispatchKind")
        if self.command_migration is not None and type(self.command_migration) is not CommandMigrationDisposition:
            raise TypeError("command_migration must be exact or None")
        if self.read_request is not None and type(self.read_request) is not ToolDispatchRequest:
            raise TypeError("read_request must be exact or None")
        if type(self.commands) is not tuple:
            raise TypeError("commands must be an exact tuple")
        if any(type(item) not in (ReservationCommand, PaymentSettlementCommand) for item in self.commands):
            raise TypeError("commands must contain exact BoundaryCommand values")
        if type(self.facts) is not tuple or any(type(item) is not TypedFact for item in self.facts):
            raise TypeError("facts must contain exact TypedFact values")
        if type(self.reason) is not TurnPlanReason:
            raise TypeError("reason must be exact TurnPlanReason")
        if self.kind is DispatchKind.READ and (
            self.read_request is None or self.commands or self.facts
        ):
            raise ValueError("read result shape is invalid")
        if self.kind is DispatchKind.STATE_COMMIT and (
            self.read_request is not None or self.commands or not self.facts
        ):
            raise ValueError("state commit result shape is invalid")


CATALOG: Final[Mapping[str, ToolContract]] = MappingProxyType(
    {
        "cerebro_consultar": ToolContract(DispatchKind.READ, FaqReadArguments),
        "cloudbeds_consultar_hospedagem_v2": ToolContract(
            DispatchKind.READ,
            LodgingReadArguments,
        ),
        "cloudbeds_descrever_quartos": ToolContract(
            DispatchKind.READ,
            RoomDescriptionArguments,
        ),
        "bokun_consultar_passeio_v2": ToolContract(
            DispatchKind.READ,
            ActivityReadArguments,
        ),
        "bokun_consultar_descricao": ToolContract(
            DispatchKind.READ,
            ActivityDescriptionArguments,
        ),
        "cloudbeds_criar_reserva_v2": ToolContract(
            DispatchKind.COMMAND,
            LodgingReservationArguments,
            CommandMigrationDisposition.RESERVATION,
        ),
        "bokun_agendar_passeio_v2": ToolContract(
            DispatchKind.COMMAND,
            ActivityReservationArguments,
            CommandMigrationDisposition.RESERVATION,
        ),
        "cloudbeds_lancar_pagamento_confirmar_reserva": ToolContract(
            DispatchKind.COMMAND,
            LodgingPaymentArguments,
            CommandMigrationDisposition.PAYMENT_SETTLEMENT,
        ),
        "bokun_lancar_pagamento_confirmar_reserva": ToolContract(
            DispatchKind.COMMAND,
            ActivityPaymentArguments,
            CommandMigrationDisposition.PAYMENT_SETTLEMENT,
        ),
        "wise_verificar_pagamento": ToolContract(
            DispatchKind.COMMAND,
            WiseVerificationArguments,
            CommandMigrationDisposition.BLOCKED_UNMIGRATED,
        ),
        "cloudbeds_gerar_link_pagamento_stripe": ToolContract(
            DispatchKind.COMMAND,
            StripeLinkArguments,
            CommandMigrationDisposition.BLOCKED_UNMIGRATED,
        ),
        "bokun_gerar_link_pagamento_stripe": ToolContract(
            DispatchKind.COMMAND,
            StripeLinkArguments,
            CommandMigrationDisposition.BLOCKED_UNMIGRATED,
        ),
        "chapada_commit_state": ToolContract(
            DispatchKind.STATE_COMMIT,
            StateCommitArguments,
        ),
    }
)
ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "availability": "cloudbeds_consultar_hospedagem_v2",
        "activity_availability": "bokun_consultar_passeio_v2",
    }
)
_STATE_FACT_TYPES: Final = MappingProxyType(
    {
        "language": StringSlot,
        "service": StringSlot,
        "start_date": DateSlot,
        "end_date": DateSlot,
        "adults": IntegerSlot,
        "children": IntegerSlot,
    }
)


def command_migration_counts() -> dict[str, int]:
    counts = {
        item.value: 0
        for item in CommandMigrationDisposition
    }
    for contract in CATALOG.values():
        if contract.command_migration is not None:
            counts[contract.command_migration.value] += 1
    return counts


def _utc(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise DispatchRejected(f"{name} must be an exact UTC datetime")
    return value


def _manual(
    tool_name: str,
    contract: ToolContract,
) -> DispatchResult:
    return DispatchResult(
        tool_name,
        DispatchKind.COMMAND,
        contract.command_migration,
        None,
        (),
        (),
        TurnPlanReason.MANUAL_REVIEW,
    )


def _reservation_command(
    tool_name: str,
    arguments: object,
    state: BoundaryState,
) -> ReservationCommand:
    workflow = state.workflow
    if type(workflow) is UncertainState:
        raise DispatchRejected("called_unknown requires manual review")
    if type(workflow) is not ExecutionQueuedState:
        raise DispatchRejected("reservation command is not durably authorized")
    command = workflow.command
    if command.operation is ReservationOperation.RESERVE_PACKAGE:
        raise DispatchRejected("package command cannot be split by a provider tool")
    if tool_name == "cloudbeds_criar_reserva_v2":
        expected_operation = ReservationOperation.RESERVE_LODGING
        expected_service = ServiceKind.LODGING
    elif tool_name == "bokun_agendar_passeio_v2":
        expected_operation = ReservationOperation.BOOK_ACTIVITY
        expected_service = ServiceKind.ACTIVITY
    else:
        raise DispatchRejected("tool is not a reservation write")
    if command.operation is not expected_operation or len(command.payload.components) != 1:
        raise DispatchRejected("authorized command operation does not match tool")
    component = command.payload.components[0]
    if component.service is not expected_service:
        raise DispatchRejected("authorized service does not match tool")
    if (
        arguments.offer_id != component.offer_id
        or arguments.summary_version != command.draft_version
        or arguments.confirmation_signature != command.subject_signature
    ):
        raise DispatchRejected("tool arguments do not bind authorized reservation command")
    return command


def _payment_command(
    tool_name: str,
    arguments: LodgingPaymentArguments | ActivityPaymentArguments,
    state: BoundaryState,
) -> PaymentSettlementCommand:
    expected_unit = (
        BusinessUnit.HOSTEL
        if tool_name == "cloudbeds_lancar_pagamento_confirmar_reserva"
        else BusinessUnit.AGENCY
    )
    matches = []
    for payment in state.payments:
        command = payment.settlement_command
        anchor = payment.subject.confirmed_reservation_anchor
        if (
            payment.status is PaymentStatus.SETTLEMENT_QUEUED
            and command is not None
            and anchor.business_unit is expected_unit
            and anchor.payment_target_id == arguments.anchor_id
        ):
            matches.append((payment, command, anchor))
    if len(matches) != 1:
        raise DispatchRejected("payment command is not uniquely authorized")
    payment, command, anchor = matches[0]
    amount_minor = int(Decimal(arguments.amount.value) * 100)
    if (
        command.evidence_claim_key != arguments.evidence_id
        or payment.subject.amount_minor != amount_minor
        or anchor.amount_minor != amount_minor
        or payment.subject.currency != arguments.currency
        or anchor.currency != arguments.currency
    ):
        raise DispatchRejected("tool arguments do not bind settlement command")
    return command


def _state_facts(arguments: StateCommitArguments) -> tuple[TypedFact, ...]:
    names = tuple(item.name for item in arguments.facts)
    if len(set(names)) != len(names):
        raise DispatchRejected("state facts contain duplicate names")
    for fact in arguments.facts:
        expected_type = _STATE_FACT_TYPES.get(fact.name)
        if expected_type is None or type(fact.value) is not expected_type:
            raise DispatchRejected("state fact is outside the closed whitelist")
    return arguments.facts


class ToolDispatch:
    """Validate and classify without executing any provider capability."""

    def dispatch(
        self,
        request: ToolDispatchRequest,
        *,
        current_state: BoundaryState,
        now: datetime,
    ) -> DispatchResult:
        if type(request) is not ToolDispatchRequest:
            raise TypeError("request must be exact ToolDispatchRequest")
        if type(current_state) is not BoundaryState:
            raise TypeError("current_state must be exact BoundaryState")
        if request.lead_key != current_state.lead_key:
            raise DispatchRejected("request lead does not bind current state")
        if _utc(now, "now") >= request.deadline:
            raise DispatchRejected("tool deadline exceeded")
        canonical_name = ALIASES.get(request.tool_name, request.tool_name)
        contract = CATALOG.get(canonical_name)
        if contract is None:
            raise DispatchRejected("unknown tool")
        if type(request.arguments) is not contract.arguments_type:
            raise DispatchRejected("tool arguments do not match literal catalog")
        canonical_request = (
            request
            if canonical_name == request.tool_name
            else replace(request, tool_name=canonical_name)
        )
        if contract.kind is DispatchKind.READ:
            return DispatchResult(
                canonical_name,
                DispatchKind.READ,
                None,
                canonical_request,
                (),
                (),
                TurnPlanReason.COMPLETED,
            )
        if contract.kind is DispatchKind.STATE_COMMIT:
            facts = _state_facts(request.arguments)
            return DispatchResult(
                canonical_name,
                DispatchKind.STATE_COMMIT,
                None,
                None,
                (),
                facts,
                TurnPlanReason.COMPLETED,
            )
        if contract.command_migration is CommandMigrationDisposition.BLOCKED_UNMIGRATED:
            return _manual(canonical_name, contract)
        if type(current_state.workflow) is UncertainState:
            return _manual(canonical_name, contract)
        if contract.command_migration is CommandMigrationDisposition.RESERVATION:
            command = _reservation_command(canonical_name, request.arguments, current_state)
        elif contract.command_migration is CommandMigrationDisposition.PAYMENT_SETTLEMENT:
            command = _payment_command(canonical_name, request.arguments, current_state)
        else:
            raise DispatchRejected("command migration is not closed")
        return DispatchResult(
            canonical_name,
            DispatchKind.COMMAND,
            contract.command_migration,
            None,
            (command,),
            (),
            TurnPlanReason.COMPLETED,
        )


__all__ = (
    "ALIASES",
    "CATALOG",
    "DispatchRejected",
    "DispatchResult",
    "ToolContract",
    "ToolDispatch",
    "command_migration_counts",
)
