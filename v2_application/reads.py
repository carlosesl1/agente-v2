"""Read dispatch and freshness enforcement for V2."""

from __future__ import annotations

from datetime import datetime, timedelta

from reservation_domain import OfferSnapshot, ServiceKind
from v2_contracts.ports import ReadPort
from v2_contracts.private_offers import (
    PrivateOfferBinding,
    PrivateOfferQuery,
    PrivateOfferReadPort,
)
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest


class ReadPortUnavailable(RuntimeError):
    pass


class ReadBindingMismatch(RuntimeError):
    pass


class StaleObservation(RuntimeError):
    pass


class PrivateBindingMismatch(ValueError):
    """A re-read no longer binds the commercially authorized offer."""


class PrivateBindingUnavailable(RuntimeError):
    """The provider could not prove the binding before the fence."""


_PROVIDER_BY_SERVICE = {
    ServiceKind.LODGING: "cloudbeds",
    ServiceKind.ACTIVITY: "bokun",
}


def _utc(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value


def _query_from_component(component: OfferSnapshot) -> PrivateOfferQuery:
    lookup = component.lookup_id
    canonical_product_id = None
    if component.service is ServiceKind.LODGING:
        prefix = "lookup:"
        if not lookup.startswith(prefix):
            raise PrivateBindingMismatch("lodging lookup identity is invalid")
        request_hash = lookup.removeprefix(prefix)
    else:
        prefix = "lookup:"
        if not lookup.startswith(prefix) or ":" not in lookup.removeprefix(prefix):
            raise PrivateBindingMismatch("activity lookup identity is invalid")
        canonical_product_id, request_hash = lookup.removeprefix(prefix).rsplit(":", 1)
    try:
        return PrivateOfferQuery(
            service=component.service.value,
            offer_id=component.offer_id,
            request_hash=request_hash,
            binding_hash=component.provider_ref,
            canonical_product_id=canonical_product_id,
            start_date=component.start_date,
            end_date=component.end_date,
            start_time=component.start_time,
            adults=component.party.adults,
            children=component.party.children,
            total_amount=format(component.total.amount, ".2f"),
            currency=component.total.currency,
            available=component.available,
        )
    except (TypeError, ValueError) as exc:
        raise PrivateBindingMismatch(
            "component cannot form a private re-read query"
        ) from exc


class PrivateOfferBindingResolver:
    def __init__(self, ports: dict[ServiceKind, PrivateOfferReadPort]) -> None:
        if type(ports) is not dict or not ports:
            raise TypeError("ports must be a non-empty exact dict")
        for service, port in ports.items():
            if type(service) is not ServiceKind or not callable(
                getattr(port, "resolve", None)
            ):
                raise TypeError(
                    "ports must map exact ServiceKind values to resolver ports"
                )
        self._ports = dict(ports)

    def resolve(
        self, component: OfferSnapshot, *, now: datetime
    ) -> PrivateOfferBinding:
        if type(component) is not OfferSnapshot:
            raise TypeError("component must be an exact OfferSnapshot")
        instant = _utc(now, "now")
        port = self._ports.get(component.service)
        if port is None:
            raise PrivateBindingMismatch("private binding port is unavailable")
        query = _query_from_component(component)
        try:
            binding = port.resolve(query)
        except Exception as exc:
            raise PrivateBindingUnavailable("private provider re-read failed") from exc
        if type(binding) is not PrivateOfferBinding:
            raise PrivateBindingMismatch(
                "private binding port returned an invalid result"
            )
        expected_provider = _PROVIDER_BY_SERVICE[component.service]
        if not (
            binding.provider == expected_provider
            and binding.query == query
            and binding.observed_at <= instant < binding.expires_at
        ):
            raise PrivateBindingMismatch(
                "private re-read changed the commercial binding"
            )
        return binding


class V2ReadService:
    def __init__(self, ports: dict[ReadKind, ReadPort]) -> None:
        if type(ports) is not dict:
            raise TypeError("ports must be an exact dict")
        normalized: dict[ReadKind, ReadPort] = {}
        for kind, port in ports.items():
            if type(kind) is not ReadKind or not hasattr(port, "read"):
                raise TypeError("ports must map exact ReadKind values to read ports")
            normalized[kind] = port
        self._ports = normalized

    def read(self, request: ReadRequest) -> ReadObservation:
        if type(request) is not ReadRequest:
            raise TypeError("request must be an exact ReadRequest")
        port = self._ports.get(request.kind)
        if port is None:
            raise ReadPortUnavailable(f"read port unavailable for {request.kind.value}")
        observation = port.read(request)
        if type(observation) is not ReadObservation:
            raise ReadBindingMismatch("read port returned an invalid observation")
        if observation.request_hash != request.canonical_hash():
            raise ReadBindingMismatch("observation is not bound to the request")
        return observation

    def accept(self, observation: ReadObservation, *, now: datetime) -> ReadObservation:
        if type(observation) is not ReadObservation:
            raise TypeError("observation must be an exact ReadObservation")
        if (
            type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
        ):
            raise ValueError("now must be an exact UTC datetime")
        if observation.observed_at > now:
            raise ReadBindingMismatch("observation is from the future")
        if observation.expires_at <= now:
            raise StaleObservation("read observation expired")
        return observation


__all__ = [
    "PrivateBindingMismatch",
    "PrivateBindingUnavailable",
    "PrivateOfferBinding",
    "PrivateOfferBindingResolver",
    "PrivateOfferReadPort",
    "ReadBindingMismatch",
    "ReadPortUnavailable",
    "StaleObservation",
    "V2ReadService",
]
