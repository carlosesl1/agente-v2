"""Credential-redacting knowledge/Cérebro read adapter."""

from __future__ import annotations

from datetime import timedelta

from v2_adapters._provider_common import (
    ProviderReadError,
    binding_hash,
    exact_dict,
    observed_window,
    text,
    validated_adapter,
)
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest


class KnowledgeReadAdapter:
    def __init__(self, *, transport, clock, ttl: timedelta) -> None:
        self._transport, self._clock, self._ttl = validated_adapter(transport, clock, ttl)

    def read(self, request: ReadRequest) -> ReadObservation:
        if type(request) is not ReadRequest or request.kind is not ReadKind.KNOWLEDGE:
            raise TypeError("knowledge adapter requires an exact knowledge ReadRequest")
        payload = {"query": request.query, "locale": request.locale}
        response = exact_dict(self._transport("knowledge", payload), "knowledge response")
        answer = text(response.get("answer"), "knowledge answer")
        raw_sources = response.get("sources", [])
        if type(raw_sources) is not list or any(type(item) is not str for item in raw_sources):
            raise ProviderReadError("knowledge sources must be exact strings")
        public = {"answer": answer, "sources": list(raw_sources)}
        observed_at, expires_at = observed_window(self._clock, self._ttl)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="cerebro",
            observed_at=observed_at,
            expires_at=expires_at,
            public_payload=public,
            private_binding_hash=binding_hash(
                {"request_hash": request.canonical_hash(), "sources": raw_sources}
            ),
        )


__all__ = ["KnowledgeReadAdapter"]
