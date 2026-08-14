"""Credential-redacting knowledge/Cérebro read adapter."""

from __future__ import annotations

from datetime import date, timedelta
import re

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
    def __init__(self, *, transport, clock, ttl: timedelta, groups_source=None) -> None:
        self._transport, self._clock, self._ttl = validated_adapter(transport, clock, ttl)
        if groups_source is not None and not callable(
            getattr(groups_source, "upcoming_groups", None)
        ):
            raise TypeError("groups_source must provide upcoming_groups")
        self._groups = groups_source

    def _formed_groups(self, query: str) -> list[dict[str, object]] | None:
        if self._groups is None:
            return None
        match = re.fullmatch(
            r"formed-groups:(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})",
            query,
        )
        if match is None:
            return None
        try:
            start_date = date.fromisoformat(match.group(1))
            end_date = date.fromisoformat(match.group(2))
            days = (end_date - start_date).days + 1
            if not 1 <= days <= 180:
                raise ValueError("group period must be between 1 and 180 days")
            groups = self._groups.upcoming_groups(
                start_date=start_date, days=days, max_groups=24
            )
            result = []
            for group in groups:
                product_id = group.canonical_product_id
                activity_date = group.activity_date
                participants = group.participant_count
                if (
                    type(product_id) is not str
                    or type(activity_date) is not date
                    or type(participants) is not int
                    or participants < 1
                ):
                    raise ValueError("group summary is invalid")
                result.append(
                    {
                        "product_id": product_id,
                        "activity_date": activity_date.isoformat(),
                        "participants": participants,
                    }
                )
            return result
        except Exception:
            return []

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
        formed_groups = self._formed_groups(request.query)
        if formed_groups is not None:
            public["formed_groups"] = formed_groups
        binding = {"request_hash": request.canonical_hash(), "sources": raw_sources}
        if formed_groups is not None:
            binding["formed_groups"] = formed_groups
        observed_at, expires_at = observed_window(self._clock, self._ttl)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="cerebro",
            observed_at=observed_at,
            expires_at=expires_at,
            public_payload=public,
            private_binding_hash=binding_hash(binding),
        )


__all__ = ["KnowledgeReadAdapter"]
