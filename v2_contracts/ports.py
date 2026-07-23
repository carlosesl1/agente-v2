"""Provider-neutral structural ports for the Agente V2."""

from __future__ import annotations

from typing import Protocol

from v2_contracts.model import ModelProposal, ModelRequest
from v2_contracts.providers import ReadObservation, ReadRequest


class ModelPort(Protocol):
    def complete(self, request: ModelRequest) -> ModelProposal: ...


class ReadPort(Protocol):
    def read(self, request: ReadRequest) -> ReadObservation: ...


__all__ = ["ModelPort", "ReadPort"]
