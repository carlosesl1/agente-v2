"""Reservation allocation and fenced provider execution for V2."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Final

from reservation_domain import (
    CommandPayload,
    ExecutionCertainty,
    ExecutionOutcome,
    ReservationCommand,
    ReservationOperation,
    ServiceKind,
    dumps_command,
    loads_command,
)
from reservation_domain.signature import (
    command_identity,
    operation_for_components,
    subject_signature,
)
from reservation_execution import DispatchPermit, DispatchRequest, PreparationFailure
from v2_contracts.ports import CommercialEffectGuard, ReservationPort
from v2_contracts.providers import (
    ProviderCertainty,
    ProviderDispatchPermit,
    ProviderExecutionResult,
    ProviderWriteAuthorization,
)


class DispatchRejected(ValueError):
    """Untrusted or unbound data attempted to cross the reservation write edge."""


_PROVIDER_OPERATION: Final = {
    "cloudbeds": ReservationOperation.RESERVE_LODGING,
    "bokun": ReservationOperation.BOOK_ACTIVITY,
}
_CERTAINTY: Final = {
    ProviderCertainty.NOT_CALLED: ExecutionCertainty.NOT_CALLED,
    ProviderCertainty.CALLED_NO_EFFECT: ExecutionCertainty.CALLED_NO_EFFECT,
    ProviderCertainty.EFFECT_CONFIRMED: ExecutionCertainty.EFFECT_CONFIRMED,
    ProviderCertainty.CALLED_UNKNOWN: ExecutionCertainty.CALLED_UNKNOWN,
}


def _provider_payload(command: ReservationCommand, provider: str) -> str:
    if len(command.payload.components) != 1:
        raise DispatchRejected("provider command must bind exactly one component")
    component = command.payload.components[0]
    expected_service = (
        ServiceKind.LODGING if provider == "cloudbeds" else ServiceKind.ACTIVITY
    )
    if component.service is not expected_service:
        raise DispatchRejected("provider and component service do not match")
    customer = command.payload.customer
    terms = command.payload.terms
    payload = {
        "schema": "v2-reservation-dispatch-v1",
        "command_id": command.command_id,
        "operation": command.operation.value,
        "offer": {
            "binding": component.provider_ref,
            "offer_id": component.offer_id,
            "start_date": component.start_date.isoformat(),
            "end_date": (
                component.end_date.isoformat() if component.end_date is not None else None
            ),
            "start_time": component.start_time,
            "party": {
                "adults": component.party.adults,
                "children": component.party.children,
            },
            "amount": format(component.total.amount, ".2f"),
            "currency": component.total.currency,
        },
        "customer": {
            "customer_ref": customer.customer_ref,
            "full_name": customer.full_name,
            "email": customer.email,
            "phone_e164": customer.phone_e164,
            "country_code": customer.country_code,
        },
        "terms": {
            "payment_method": terms.payment_method,
            "add_ons": [
                {
                    "code": item.code,
                    "quantity": item.quantity,
                    "unit_amount": format(item.unit_price.amount, ".2f"),
                    "currency": item.unit_price.currency,
                }
                for item in terms.add_ons
            ],
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True, slots=True)
class ReservationAllocation:
    source_command_id: str
    commands: tuple[ReservationCommand, ...]

    def __post_init__(self) -> None:
        if type(self.source_command_id) is not str or not self.source_command_id:
            raise ValueError("source_command_id must be exact non-empty text")
        if type(self.commands) is not tuple or not self.commands:
            raise ValueError("commands must be a non-empty exact tuple")
        if any(type(item) is not ReservationCommand for item in self.commands):
            raise TypeError("commands must contain exact ReservationCommand values")
        if len({item.command_id for item in self.commands}) != len(self.commands):
            raise ValueError("allocated command ids must be unique")


class ReservationAllocator:
    """Split a confirmed package before its enclosing atomic boundary commit."""

    def allocate(self, command: ReservationCommand) -> ReservationAllocation:
        if type(command) is not ReservationCommand:
            raise DispatchRejected("allocate requires an exact ReservationCommand")
        if command.operation is not ReservationOperation.RESERVE_PACKAGE:
            return ReservationAllocation(command.command_id, (command,))
        by_service = {item.service: item for item in command.payload.components}
        if set(by_service) != {ServiceKind.LODGING, ServiceKind.ACTIVITY} or len(
            command.payload.components
        ) != 2:
            raise DispatchRejected("package must contain one lodging and one activity")
        allocated = []
        for service in (ServiceKind.LODGING, ServiceKind.ACTIVITY):
            component = by_service[service]
            components = (component,)
            signature = subject_signature(
                components=components,
                customer=command.payload.customer,
                terms=command.payload.terms,
            )
            operation = operation_for_components(components)
            command_id, idempotency_key = command_identity(
                workflow_id=command.workflow_id,
                draft_id=command.draft_id,
                draft_version=command.draft_version,
                signature=signature,
                operation=operation,
            )
            allocated.append(
                ReservationCommand(
                    command_id=command_id,
                    idempotency_key=idempotency_key,
                    workflow_id=command.workflow_id,
                    draft_id=command.draft_id,
                    draft_version=command.draft_version,
                    subject_signature=signature,
                    operation=operation,
                    payload=CommandPayload(
                        components,
                        command.payload.customer,
                        command.payload.terms,
                    ),
                    created_at=command.created_at,
                )
            )
        return ReservationAllocation(command.command_id, tuple(allocated))

    def expand_commands(
        self,
        commands: tuple[ReservationCommand, ...],
    ) -> tuple[ReservationCommand, ...]:
        if type(commands) is not tuple or any(
            type(command) is not ReservationCommand for command in commands
        ):
            raise DispatchRejected("commands must be an exact ReservationCommand tuple")
        expanded = []
        for command in commands:
            expanded.extend(self.allocate(command).commands)
        if len({command.command_id for command in expanded}) != len(expanded):
            raise DispatchRejected("expanded command identities are not unique")
        return tuple(expanded)


class V2ReservationExecutionAdapter:
    """Bridge a canonical command to a neutral provider port after durable fencing."""

    adapter_version = 1

    def __init__(
        self,
        *,
        provider: str,
        port: ReservationPort,
        authorization: ProviderWriteAuthorization,
    ) -> None:
        if provider not in _PROVIDER_OPERATION:
            raise ValueError("provider is outside the V2 reservation allowlist")
        if getattr(port, "provider", None) != provider or not callable(
            getattr(port, "execute", None)
        ):
            raise TypeError("port does not implement the selected reservation provider")
        if type(authorization) is not ProviderWriteAuthorization:
            raise TypeError("authorization must be exact ProviderWriteAuthorization")
        if authorization.provider != provider:
            raise ValueError("authorization provider mismatch")
        self.provider = provider
        self.adapter_id = f"v2-{provider}-reservation"
        self._port = port
        self._authorization = authorization

    @property
    def operation(self) -> ReservationOperation:
        return _PROVIDER_OPERATION[self.provider]

    def prepare(self, command: ReservationCommand) -> DispatchRequest:
        if type(command) is not ReservationCommand:
            raise DispatchRejected("prepare requires an exact ReservationCommand")
        if command.operation is not self.operation:
            raise PreparationFailure("unsupported_operation", False, ())
        if not self._authorization.enabled:
            raise PreparationFailure("write_gate_closed", False, ())
        return DispatchRequest.from_command(
            command,
            dumps_command(command),
        )

    def dispatch_fenced(
        self,
        permit: DispatchPermit,
        request: DispatchRequest,
        *,
        idempotency_key: str,
    ) -> ExecutionOutcome:
        if type(permit) is not DispatchPermit or type(request) is not DispatchRequest:
            raise TypeError("dispatch_fenced requires exact durable permit and request")
        if (
            permit.command_id != request.command_id
            or permit.request_hash != request.payload_hash
            or request.operation is not self.operation
            or request.idempotency_key != idempotency_key
        ):
            raise DispatchRejected("durable permit does not bind the prepared request")
        command = loads_command(request.canonical_payload)
        if command.command_id != request.command_id:
            raise DispatchRejected("prepared command identity changed after fencing")
        provider_payload = _provider_payload(command, self.provider)
        provider_payload_hash = hashlib.sha256(provider_payload.encode("utf-8")).hexdigest()
        provider_permit = ProviderDispatchPermit(
            provider=self.provider,
            operation=self.operation.value,
            command_id=request.command_id,
            idempotency_key=idempotency_key,
            request_hash=request.payload_hash,
            payload_hash=provider_payload_hash,
            canonical_payload=provider_payload,
            fencing_token=permit.lease.fencing_token,
            authorization_id=self._authorization.authorization_id,
        )
        result = self._port.execute(provider_permit)
        if type(result) is not ProviderExecutionResult:
            raise TypeError("provider port returned a non-canonical result")
        provider_reference = (
            f"provider:{self.provider}:{result.provider_reference_fingerprint[:32]}"
            if result.provider_reference_fingerprint is not None
            else None
        )
        return ExecutionOutcome(
            command_id=request.command_id,
            certainty=_CERTAINTY[result.certainty],
            normalized_status=result.normalized_status,
            provider_reference=provider_reference,
            evidence=tuple(
                sorted({request.payload_hash, provider_payload_hash, *result.evidence})
            ),
        )

    def dispatch(
        self,
        request: DispatchRequest,
        *,
        idempotency_key: str,
    ) -> ExecutionOutcome:
        raise RuntimeError("V2 reservation dispatch requires a durable fence permit")


class RoutingReservationExecutionAdapter:
    """Select one provider adapter from the command's closed operation."""

    adapter_id = "v2-reservation-router"
    adapter_version = 1

    def __init__(
        self,
        adapters: tuple[V2ReservationExecutionAdapter, ...],
        effect_guard: CommercialEffectGuard,
    ) -> None:
        if type(adapters) is not tuple or not adapters:
            raise ValueError("adapters must be a non-empty exact tuple")
        if any(type(item) is not V2ReservationExecutionAdapter for item in adapters):
            raise TypeError("adapters must contain exact V2 reservation adapters")
        if not callable(getattr(effect_guard, "allows_workflow", None)):
            raise TypeError("effect_guard must implement CommercialEffectGuard")
        mapping = {item.operation: item for item in adapters}
        if len(mapping) != len(adapters):
            raise ValueError("one adapter per reservation operation is required")
        self._adapters = mapping
        self._effect_guard = effect_guard

    def prepare(self, command: ReservationCommand) -> DispatchRequest:
        if type(command) is not ReservationCommand:
            raise DispatchRejected("prepare requires an exact ReservationCommand")
        try:
            allowed = self._effect_guard.allows_workflow(command.workflow_id)
        except Exception as exc:
            raise PreparationFailure("effect_guard_unavailable", True, ()) from exc
        if type(allowed) is not bool:
            raise PreparationFailure("effect_guard_unavailable", True, ())
        if not allowed:
            raise PreparationFailure("active_handoff", False, ())
        adapter = self._adapters.get(command.operation)
        if adapter is None:
            raise PreparationFailure("unsupported_operation", False, ())
        return adapter.prepare(command)

    def dispatch_fenced(
        self,
        permit: DispatchPermit,
        request: DispatchRequest,
        *,
        idempotency_key: str,
    ) -> ExecutionOutcome:
        adapter = self._adapters.get(request.operation)
        if adapter is None:
            raise DispatchRejected("fenced operation has no provider adapter")
        return adapter.dispatch_fenced(
            permit,
            request,
            idempotency_key=idempotency_key,
        )

    def dispatch(
        self,
        request: DispatchRequest,
        *,
        idempotency_key: str,
    ) -> ExecutionOutcome:
        raise RuntimeError("V2 reservation routing requires a durable fence permit")


__all__ = [
    "DispatchRejected",
    "ReservationAllocation",
    "ReservationAllocator",
    "RoutingReservationExecutionAdapter",
    "V2ReservationExecutionAdapter",
]
