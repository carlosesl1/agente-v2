"""Read dispatch and freshness enforcement for V2."""

from __future__ import annotations

from datetime import datetime, timedelta

from v2_contracts.ports import ReadPort
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest


class ReadPortUnavailable(RuntimeError):
    pass


class ReadBindingMismatch(RuntimeError):
    pass


class StaleObservation(RuntimeError):
    pass


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
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be an exact UTC datetime")
        if observation.observed_at > now:
            raise ReadBindingMismatch("observation is from the future")
        if observation.expires_at <= now:
            raise StaleObservation("read observation expired")
        return observation


__all__ = [
    "ReadBindingMismatch",
    "ReadPortUnavailable",
    "StaleObservation",
    "V2ReadService",
]
