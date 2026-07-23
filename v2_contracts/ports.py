"""Provider-neutral structural ports for the Agente V2."""

from __future__ import annotations

from typing import Protocol

from v2_contracts.model import ModelProposal, ModelRequest
from v2_contracts.providers import (
    ProviderDispatchPermit,
    ProviderExecutionResult,
    ReadObservation,
    ReadRequest,
)


class ModelPort(Protocol):
    def complete(self, request: ModelRequest) -> ModelProposal: ...


class ReadPort(Protocol):
    def read(self, request: ReadRequest) -> ReadObservation: ...


class ReservationPort(Protocol):
    provider: str

    def execute(self, permit: ProviderDispatchPermit) -> ProviderExecutionResult: ...


__all__ = ["ModelPort", "ReadPort", "ReservationPort"]
