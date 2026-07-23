"""Canonical V2 turn service: model/read outside SQLite, kernel commit inside."""

from __future__ import annotations

from datetime import date, timedelta

from reservation_boundary.coordinator import TurnCoordinator
from reservation_boundary.types import (
    ConversationIntent,
    ConversationIntentKind,
    DateSlot,
    IntegerSlot,
    IntentRequest,
    NormalizedMessage,
    StringSlot,
    TurnEnvelope,
    TypedFact,
)
from v2_application.reads import V2ReadService
from v2_contracts.channel import InboundBatch
from v2_contracts.model import (
    InvalidModelProposal,
    ModelFact,
    ModelProposal,
    ModelRequest,
    TurnResult,
)
from v2_contracts.ports import ModelPort
from v2_contracts.providers import ReadKind, ReadObservation


class _ModelIntent:
    def __init__(
        self,
        *,
        model: ModelPort,
        reads: V2ReadService,
        clock,
    ) -> None:
        self._model = model
        self._reads = reads
        self._clock = clock
        self.final_proposal: ModelProposal | None = None

    def interpret(self, request: IntentRequest) -> ConversationIntent:
        observations: tuple[ReadObservation, ...] = ()
        seen_kinds: set[ReadKind] = set()
        round_number = 0
        proposal = self._complete(request, observations, round_number)
        while proposal.read_requests:
            if proposal.effect_proposals:
                raise InvalidModelProposal("model cannot mix read and effect proposals")
            current_kinds = tuple(item.kind for item in proposal.read_requests)
            if len(current_kinds) != len(set(current_kinds)):
                raise InvalidModelProposal("model proposed duplicate read kinds in one cycle")
            if any(kind in seen_kinds for kind in current_kinds):
                raise InvalidModelProposal("model exceeded one read cycle per kind")
            seen_kinds.update(current_kinds)
            new_observations = tuple(
                self._reads.accept(
                    self._reads.read(read_request),
                    now=self._clock.now(),
                )
                for read_request in proposal.read_requests
            )
            observations = (*observations, *new_observations)
            round_number += 1
            proposal = self._complete(request, observations, round_number)
        self.final_proposal = proposal
        return self._intent(proposal)

    def _complete(
        self,
        request: IntentRequest,
        observations: tuple[ReadObservation, ...],
        round_number: int,
    ) -> ModelProposal:
        model_request = ModelRequest(
            request_id=f"{request.source_event_id}:model:{round_number}",
            lead_id=request.state.lead_key,
            source_event_id=request.source_event_id,
            message=request.message.text,
            locale=request.message.locale,
            state_version=request.state.version,
            observations=observations,
        )
        proposal = self._model.complete(model_request)
        if type(proposal) is not ModelProposal:
            raise InvalidModelProposal("model returned a noncanonical proposal")
        if proposal.source_event_id != request.source_event_id:
            raise InvalidModelProposal("model proposal does not bind the source event")
        if proposal.read_requests and proposal.effect_proposals:
            raise InvalidModelProposal("model cannot mix read and effect proposals")
        return proposal

    @staticmethod
    def _intent(proposal: ModelProposal) -> ConversationIntent:
        kinds = {
            "inform": ConversationIntentKind.INFORM,
            "select": ConversationIntentKind.SELECT,
            "adjust": ConversationIntentKind.ADJUST,
            "confirm": ConversationIntentKind.CONFIRM,
            "request_handoff": ConversationIntentKind.REQUEST_HANDOFF,
        }
        return ConversationIntent(
            kind=kinds[proposal.intent],
            source_event_id=proposal.source_event_id,
            facts=tuple(_typed_fact(item) for item in proposal.facts),
            target_offer_id=proposal.target_offer_id,
            confirmed_summary_version=proposal.confirmed_summary_version,
        )


def _typed_fact(fact: ModelFact) -> TypedFact:
    if fact.name in ("language", "service"):
        value = StringSlot(fact.value)
    elif fact.name in ("start_date", "end_date"):
        if type(fact.value) is not date:
            raise InvalidModelProposal("date fact changed type")
        value = DateSlot(fact.value)
    else:
        value = IntegerSlot(fact.value)
    return TypedFact(fact.name, value)


class V2TurnService:
    def __init__(
        self,
        *,
        store,
        lock,
        kernel,
        model: ModelPort,
        reads: V2ReadService,
        clock,
        turn_timeout: timedelta,
    ) -> None:
        if type(turn_timeout) is not timedelta or turn_timeout <= timedelta(0):
            raise ValueError("turn_timeout must be a positive exact timedelta")
        for value, method, name in (
            (store, "turn_transaction", "store"),
            (lock, "claim", "lock"),
            (kernel, "reduce", "kernel"),
            (model, "complete", "model"),
            (clock, "now", "clock"),
        ):
            if not hasattr(value, method):
                raise TypeError(f"{name} must implement {method}")
        if type(reads) is not V2ReadService:
            raise TypeError("reads must be an exact V2ReadService")
        self.store = store
        self._lock = lock
        self._kernel = kernel
        self._model = model
        self._reads = reads
        self._clock = clock
        self._turn_timeout = turn_timeout

    def handle(self, batch: InboundBatch) -> TurnResult:
        if type(batch) is not InboundBatch:
            raise TypeError("batch must be an exact InboundBatch")
        intent = _ModelIntent(model=self._model, reads=self._reads, clock=self._clock)
        coordinator = TurnCoordinator(
            lock=self._lock,
            store=self.store,
            intent=intent,
            kernel=self._kernel,
            clock=self._clock,
        )
        received_at = self._clock.now()
        plan = coordinator.coordinate(
            TurnEnvelope(
                lead_key=batch.lead_id,
                event_id=batch.batch_id,
                message=NormalizedMessage(batch.combined_text, "pt-BR"),
                received_at=received_at,
                deadline=received_at + self._turn_timeout,
            )
        )
        proposal = intent.final_proposal
        replies = () if proposal is None else proposal.reply_chunks
        return TurnResult(
            batch_id=batch.batch_id,
            state_version=plan.state.version,
            reply_chunks=replies,
            deduplicated=plan.deduplicated,
        )


__all__ = ["V2TurnService"]
