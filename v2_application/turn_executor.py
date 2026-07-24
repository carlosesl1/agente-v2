"""Atomic Phase 8 turn executor for the standalone Agente V2 runtime."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from reservation_boundary.conversation import (
    ConversationProjection,
    ConversationStage,
    MayaIntentClosure,
    MayaTurnClosure,
    MayaTurnProposal,
    PublicReplyChunk,
    PublicReplyType,
    PublicRoute,
    SourceEventIdentity,
    TranscriptCommitment,
    TranscriptDirection,
    TranscriptKind,
)
from reservation_boundary.serialization import semantic_hash, to_wire_json
from reservation_boundary.sqlite_store import (
    CommandRelayWrite,
    ConcurrencyConflict,
    InternalOutboxWrite,
    PublicOutboxWrite,
    SQLiteBoundaryStore,
    StateNotFound,
    TurnArtifactWrite,
    TurnReceipt,
)
from reservation_boundary.types import (
    BoundaryCommit,
    ConversationIntentKind,
    KernelDecision,
    StringSlot,
    TypedFact,
)
from reservation_domain import ReservationCommand, dumps_command
from reservation_followup import HandoffRequested
from v2_application.conversation import V2ConversationReducer
from v2_application.read_bridge import bridge_availability_observation
from v2_application.reads import V2ReadService
from v2_application.relay_worker import (
    build_handoff_relay_bundle,
    build_reservation_relay_bundle,
)
from v2_application.reservations import ReservationAllocator
from v2_application.turns import validate_productive_proposal
from v2_contracts.channel import InboundBatch
from v2_contracts.model import AuditedModelTurn, ModelFact, ModelProposal, ModelRequest
from v2_contracts.ports import AuditedModelPort
from v2_contracts.profile import PrivateCustomerBinding

_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_ZERO_HASH: Final = "0" * 64


class TurnExecutionError(RuntimeError):
    """The turn could not be reduced into one authenticated v8 commit."""


@dataclass(frozen=True, slots=True)
class PublicTurnAuthority:
    authorization_kind: str
    authorization_id: str
    scope_subject_id: str
    target_binding_hash: str
    channel_id: str
    channel_scope: str
    immutable_generation: int
    allocation_ids: tuple[str, ...]
    capability_policy_digest: str
    effect_authorization_binding_digest: str
    contract_digest: str
    allocation_manifest_hash: str
    deadline_at: datetime
    qualification_id: str | None = None
    scenario_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "authorization_id",
            "scope_subject_id",
            "channel_id",
            "channel_scope",
        ):
            value = getattr(self, name)
            if type(value) is not str or _ID_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be an exact opaque identifier")
        for name in (
            "target_binding_hash",
            "capability_policy_digest",
            "effect_authorization_binding_digest",
            "contract_digest",
            "allocation_manifest_hash",
        ):
            value = getattr(self, name)
            if type(value) is not str or _HASH_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be a lowercase SHA-256")
        if type(self.immutable_generation) is not int or self.immutable_generation < 1:
            raise ValueError("immutable_generation must be a positive exact integer")
        if type(self.allocation_ids) is not tuple or not self.allocation_ids:
            raise ValueError("allocation_ids must be a non-empty exact tuple")
        if any(
            type(item) is not str or _ID_RE.fullmatch(item) is None
            for item in self.allocation_ids
        ):
            raise ValueError("allocation_ids members must be opaque identifiers")
        if len(set(self.allocation_ids)) != len(self.allocation_ids):
            raise ValueError("allocation_ids must be unique")
        if self.authorization_kind == "conversation_test":
            if self.qualification_id is not None or self.scenario_id is not None:
                raise ValueError(
                    "conversation_test authority cannot carry E2E identity"
                )
        elif self.authorization_kind == "e2e":
            for value in (self.qualification_id, self.scenario_id):
                if type(value) is not str or _ID_RE.fullmatch(value) is None:
                    raise ValueError(
                        "e2e authority requires exact qualification/scenario IDs"
                    )
        else:
            raise ValueError("authorization_kind is outside the closed catalog")
        if (
            type(self.deadline_at) is not datetime
            or self.deadline_at.tzinfo is None
            or self.deadline_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("deadline_at must be an exact UTC datetime")


@dataclass(frozen=True, slots=True)
class V2TurnExecutionResult:
    receipt: TurnReceipt
    reply_chunks: tuple[str, ...]
    replayed: bool

    def __post_init__(self) -> None:
        if type(self.receipt) is not TurnReceipt:
            raise TypeError("receipt must be an exact TurnReceipt")
        if type(self.reply_chunks) is not tuple or any(
            type(item) is not str for item in self.reply_chunks
        ):
            raise TypeError("reply_chunks must be an exact string tuple")
        if type(self.replayed) is not bool:
            raise TypeError("replayed must be an exact bool")


@dataclass(frozen=True, slots=True)
class _PreparedTurn:
    commit: BoundaryCommit
    receipt: TurnReceipt
    artifacts: tuple[TurnArtifactWrite, ...]
    command_relays: tuple[CommandRelayWrite, ...]
    internal_jobs: tuple[InternalOutboxWrite, ...]
    public_rows: tuple[PublicOutboxWrite, ...]
    reply_chunks: tuple[str, ...]


def _canonical(schema: str, data: object) -> bytes:
    return json.dumps(
        {"schema": schema, "version": 1, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _domain_hash(domain: str, payload: bytes) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\x00" + payload).hexdigest()


def _opaque(prefix: str, *parts: object) -> str:
    payload = "\x00".join(str(item) for item in parts).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(payload).hexdigest()[:32]


def _source_events(batch: InboundBatch) -> tuple[SourceEventIdentity, ...]:
    return tuple(
        SourceEventIdentity(event.event_id, event.payload_hash)
        for event in batch.events
    )


def _event_hash(sources: tuple[SourceEventIdentity, ...]) -> str:
    return _domain_hash(
        "v2-aggregate-event-v1",
        _canonical(
            "v2-aggregate-event",
            [
                {
                    "source_event_id": item.source_event_id,
                    "source_event_hash": item.source_event_hash,
                }
                for item in sources
            ],
        ),
    )


def _reply_from_receipt(receipt: TurnReceipt) -> tuple[str, ...]:
    return tuple(
        PublicReplyChunk.from_canonical_bytes(row[2]).text
        for row in receipt.public_chunks
    )


def _genesis_projection(locale: str) -> ConversationProjection:
    return ConversationProjection(
        ConversationStage.RECEPTIONIST,
        (),
        locale,
        (),
        None,
    )


def _intent(proposal: ModelProposal) -> MayaIntentClosure:
    try:
        kind = ConversationIntentKind(proposal.intent)
    except ValueError as exc:
        raise TurnExecutionError(
            "model intent is outside the boundary catalog"
        ) from exc
    if kind is ConversationIntentKind.TOOL_REQUEST:
        raise TurnExecutionError("tool-request intent cannot enter the V2 runtime")
    selection = (
        proposal.target_offer_id if kind is ConversationIntentKind.SELECT else None
    )
    confirmation = (
        proposal.confirmed_summary_version
        if kind is ConversationIntentKind.CONFIRM
        else None
    )
    return MayaIntentClosure(
        kind=kind,
        selection=selection,
        confirmation=confirmation,
        handoff=kind is ConversationIntentKind.REQUEST_HANDOFF,
    )


def _route(
    projection: ConversationProjection, intent: MayaIntentClosure
) -> PublicRoute:
    if intent.kind is ConversationIntentKind.REQUEST_HANDOFF:
        return PublicRoute.HANDOFF
    return {
        ConversationStage.RECEPTIONIST: PublicRoute.RECEPTIONIST,
        ConversationStage.HOSTEL: PublicRoute.HOSTEL,
        ConversationStage.AGENCY: PublicRoute.AGENCY,
        ConversationStage.CLOSING: PublicRoute.CLOSING,
    }[projection.stage]


def _reply_type(reply_kind: str, route: PublicRoute) -> PublicReplyType:
    if route is PublicRoute.HANDOFF:
        return PublicReplyType.HANDOFF
    if reply_kind in {
        "profile_completion",
        "stale_confirmation",
        "profile_changed",
        "fresh_reads_required",
    }:
        return PublicReplyType.ASK_MORE
    if reply_kind == "summary":
        return PublicReplyType.QUALIFY
    return PublicReplyType.ANSWER


def _frame_commitments(audited: AuditedModelTurn) -> tuple[TranscriptCommitment, ...]:
    result = []
    previous = _ZERO_HASH
    for index, frame in enumerate(audited.frames, start=1):
        kind = (
            TranscriptKind.FINAL
            if index == len(audited.frames)
            else TranscriptKind.READ
        )
        item = TranscriptCommitment(
            direction=TranscriptDirection.CHILD_TO_PARENT,
            kind=kind,
            sequence=index,
            request_id=_opaque("model-frame", index, frame.request_hash),
            request_hash=frame.request_hash,
            response_hash=frame.stdout_hash,
            previous_frame_commitment=previous,
        )
        result.append(item)
        previous = item.canonical_hash()
    return tuple(result)


def _command_rows(commands: tuple[object, ...]) -> tuple[tuple[str, str], ...]:
    rows = []
    for command in commands:
        if type(command) is not ReservationCommand:
            raise TurnExecutionError("unsupported V2 command type")
        wire = dumps_command(command)
        rows.append((command.command_id, hashlib.sha256(wire.encode()).hexdigest()))
    return tuple(rows)


def _execution_commands(
    commands: tuple[object, ...],
) -> tuple[ReservationCommand, ...]:
    if type(commands) is not tuple or any(
        type(command) is not ReservationCommand for command in commands
    ):
        raise TurnExecutionError("unsupported V2 execution command type")
    return ReservationAllocator().expand_commands(commands)


def _state_model_facts(
    projection: ConversationProjection,
) -> tuple[ModelFact, ...]:
    return tuple(
        ModelFact(item.name, item.value.value)
        for item in projection.facts
    )


def _command_relays(
    aggregate_turn_id: str,
    commands: tuple[object, ...],
) -> tuple[CommandRelayWrite, ...]:
    result = []
    for command in commands:
        if type(command) is not ReservationCommand:
            raise TurnExecutionError("unsupported V2 command relay type")
        bundle = build_reservation_relay_bundle(command)
        result.append(
            CommandRelayWrite(
                relay_id=_opaque("relay", aggregate_turn_id, command.command_id),
                command_id=command.command_id,
                bundle_bytes=bundle.to_canonical_bytes(),
                bundle_hash=bundle.artifact_hash,
            )
        )
    return tuple(result)


def _handoff_jobs(
    aggregate_turn_id: str,
    request: HandoffRequested | None,
) -> tuple[InternalOutboxWrite, ...]:
    if request is None:
        return ()
    bundle = build_handoff_relay_bundle(request)
    return (
        InternalOutboxWrite(
            job_id=_opaque("handoff-job", aggregate_turn_id, request.handoff_id),
            job_kind="handoff_relay",
            artifact_bytes=bundle.to_canonical_bytes(),
            artifact_hash=bundle.artifact_hash,
            qualification_id=None,
            epoch=None,
            target_operation_id=_opaque(
                "handoff-operation", aggregate_turn_id, request.handoff_id
            ),
        ),
    )


class V2TurnExecutor:
    def __init__(
        self,
        *,
        store: SQLiteBoundaryStore,
        model: AuditedModelPort,
        reads: V2ReadService,
        profile: object,
        reducer: V2ConversationReducer,
        public_authority: object,
        clock: object,
        locale: str,
        turn_timeout: timedelta,
        max_commit_attempts: int,
    ) -> None:
        required = (
            (store, "acquire_fence", "store"),
            (model, "complete_audited", "model"),
            (profile, "read", "profile"),
            (public_authority, "resolve", "public_authority"),
            (clock, "now", "clock"),
        )
        for owner, method, name in required:
            if not callable(getattr(owner, method, None)):
                raise TypeError(f"{name} must expose {method}")
        if type(reads) is not V2ReadService:
            raise TypeError("reads must be an exact V2ReadService")
        if type(reducer) is not V2ConversationReducer:
            raise TypeError("reducer must be an exact V2ConversationReducer")
        if type(locale) is not str or not locale:
            raise ValueError("locale must be non-empty exact text")
        if type(turn_timeout) is not timedelta or turn_timeout <= timedelta(0):
            raise ValueError("turn_timeout must be a positive exact timedelta")
        if type(max_commit_attempts) is not int or not 1 <= max_commit_attempts <= 3:
            raise ValueError("max_commit_attempts must be an exact integer from 1 to 3")
        self._store = store
        self._model = model
        self._reads = reads
        self._profile = profile
        self._reducer = reducer
        self._public_authority = public_authority
        self._clock = clock
        self._locale = locale
        self._turn_timeout = turn_timeout
        self._max_commit_attempts = max_commit_attempts

    def execute(self, batch: InboundBatch) -> V2TurnExecutionResult:
        if type(batch) is not InboundBatch:
            raise TypeError("batch must be an exact InboundBatch")
        sources = _source_events(batch)
        event_hash = _event_hash(sources)
        replay = self._store.load_turn_receipt(batch.batch_id)
        if replay is not None:
            if replay.event_hash != event_hash or replay.source_events != sources:
                raise TurnExecutionError("aggregate turn replay identity diverged")
            return V2TurnExecutionResult(replay, _reply_from_receipt(replay), True)

        last_conflict: ConcurrencyConflict | None = None
        for _ in range(self._max_commit_attempts):
            try:
                prepared, expected_version, fencing_token = self._prepare(
                    batch,
                    sources=sources,
                    event_hash=event_hash,
                )
                self._store.commit_turn_v8(
                    expected_version=expected_version,
                    fencing_token=fencing_token,
                    commit=prepared.commit,
                    receipt=prepared.receipt,
                    artifacts=prepared.artifacts,
                    command_relays=prepared.command_relays,
                    internal_jobs=prepared.internal_jobs,
                    public_rows=prepared.public_rows,
                    committed_at=prepared.receipt.committed_at,
                )
                return V2TurnExecutionResult(
                    prepared.receipt,
                    prepared.reply_chunks,
                    False,
                )
            except ConcurrencyConflict as exc:
                last_conflict = exc
                replay = self._store.load_turn_receipt(batch.batch_id)
                if replay is not None:
                    if (
                        replay.event_hash != event_hash
                        or replay.source_events != sources
                    ):
                        raise TurnExecutionError(
                            "concurrent aggregate turn identity diverged"
                        ) from exc
                    return V2TurnExecutionResult(
                        replay,
                        _reply_from_receipt(replay),
                        True,
                    )
        raise ConcurrencyConflict(
            "turn commit attempts were exhausted"
        ) from last_conflict

    def _prepare(
        self,
        batch: InboundBatch,
        *,
        sources: tuple[SourceEventIdentity, ...],
        event_hash: str,
    ) -> tuple[_PreparedTurn, int, int]:
        now = self._clock.now()
        try:
            self._store.load_state(batch.lead_id)
        except StateNotFound:
            self._store.create_genesis(batch.lead_id, claimed_at=now)
        current, fencing_token = self._store.acquire_fence(batch.lead_id)
        projection = self._store.load_latest_conversation_projection(batch.lead_id)
        if projection is None:
            projection = _genesis_projection(self._locale)
        previous_receipt_hash = self._store.latest_turn_receipt_hash(batch.lead_id)

        profile = self._profile.read(batch.lead_id, now=now)
        if type(profile) is not PrivateCustomerBinding:
            raise TypeError("profile port must return exact PrivateCustomerBinding")
        request = ModelRequest(
            request_id=_opaque("model-request", batch.batch_id, current.version, 1),
            lead_id=batch.lead_id,
            source_event_id=batch.batch_id,
            message=batch.combined_text,
            locale=self._locale,
            state_version=current.version,
            state_facts=_state_model_facts(projection),
        )
        first_audited = self._model.complete_audited(request)
        if type(first_audited) is not AuditedModelTurn:
            raise TypeError("model must return exact AuditedModelTurn")
        if len(first_audited.frames) != 1:
            raise TurnExecutionError("each model call must return one audited exchange")
        first_proposal = validate_productive_proposal(first_audited.proposal)
        if first_proposal.source_event_id != batch.batch_id:
            raise TurnExecutionError("model proposal source event diverged")
        read_requests = first_proposal.read_requests
        request_hashes = tuple(item.canonical_hash() for item in read_requests)
        if len(request_hashes) != len(set(request_hashes)):
            raise TurnExecutionError("model proposed duplicate reads")
        v2_observations = ()
        if read_requests:
            v2_observations = tuple(
                self._reads.accept(self._reads.read(item), now=now)
                for item in read_requests
            )
            followup = ModelRequest(
                request_id=_opaque("model-request", batch.batch_id, current.version, 2),
                lead_id=batch.lead_id,
                source_event_id=batch.batch_id,
                message=batch.combined_text,
                locale=self._locale,
                state_version=current.version,
                observations=v2_observations,
                state_facts=_state_model_facts(projection),
            )
            second_audited = self._model.complete_audited(followup)
            if type(second_audited) is not AuditedModelTurn:
                raise TypeError("model must return exact AuditedModelTurn")
            if len(second_audited.frames) != 1:
                raise TurnExecutionError(
                    "each model call must return one audited exchange"
                )
            proposal = validate_productive_proposal(second_audited.proposal)
            if proposal.source_event_id != batch.batch_id:
                raise TurnExecutionError("model proposal source event diverged")
            if proposal.read_requests:
                raise TurnExecutionError("model exceeded the single read round")
            audited = AuditedModelTurn.combine((first_audited, second_audited))
        else:
            audited = first_audited
            proposal = first_proposal

        frames = _frame_commitments(audited)
        final_frame_hash = frames[-1].canonical_hash()
        boundary_reads = tuple(
            bridge_availability_observation(
                read_request,
                observation,
                lead_id=batch.lead_id,
                aggregate_turn_id=batch.batch_id,
                source_event=sources[0],
                deadline_at=now + self._turn_timeout,
                locale=self._locale,
                projection=projection,
                frame_commitment_hash=frames[0].canonical_hash(),
            )
            for read_request, observation in zip(read_requests, v2_observations)
        )
        decision = self._reducer.reduce(
            state=current.state,
            projection=projection,
            proposal=proposal,
            profile=profile,
            reads=v2_observations,
            fact_commitment_hash=final_frame_hash,
            now=now,
        )
        if not any(item.name == "language" for item in decision.projection.facts):
            language_fact = TypedFact(
                "language",
                StringSlot(self._locale),
                final_frame_hash,
            )
            decision = replace(
                decision,
                projection=replace(
                    decision.projection,
                    facts=(language_fact, *decision.projection.facts),
                ),
            )
        if decision.next_state.version != current.version + 1:
            raise TurnExecutionError("reducer did not advance state exactly once")
        facts = decision.projection.facts
        execution_commands = _execution_commands(decision.commands)
        kernel = KernelDecision(
            decision.next_state,
            execution_commands,
            (),
            (),
            (),
        )
        commit = BoundaryCommit(decision.next_state, execution_commands, (), ())
        kernel_bytes = to_wire_json(kernel).encode("utf-8")
        kernel_hash = semantic_hash(kernel)

        intent = _intent(proposal)
        route = _route(decision.projection, intent)
        reply_type = _reply_type(decision.public_reply.kind, route)
        closure = MayaTurnClosure(
            aggregate_turn_id=batch.batch_id,
            intent_closure=intent,
            public_text="\n".join(decision.public_reply.chunks),
            route=route,
            reply_type=reply_type,
            final_seq=len(frames),
            expected_prefix_mac=audited.closure.transcript_mac,
            ephemeral_session_id=audited.closure.ephemeral_session_id,
            zero_requests_in_flight=audited.closure.zero_requests_in_flight,
        )
        chunks = tuple(
            PublicReplyChunk(
                batch.batch_id,
                ordinal,
                text,
                closure.canonical_hash(),
            )
            for ordinal, text in enumerate(decision.public_reply.chunks)
        )
        graph_digest = _domain_hash(
            "v2-runtime-graph-v1",
            _canonical(
                "v2-runtime-graph",
                {
                    "frames": [item.canonical_hash() for item in frames],
                    "reads": [item.canonical_hash() for item in boundary_reads],
                    "facts": [item.canonical_hash() for item in facts],
                    "projection": decision.projection.canonical_hash(),
                    "kernel": kernel_hash,
                },
            ),
        )
        maya = MayaTurnProposal.from_accepted_closure(
            accepted_closure=closure,
            read_observations=boundary_reads,
            facts=facts,
            normalized_tool_proposals=(),
            learning_proposals=(),
            public_reply_chunks=chunks,
            final_transcript_commitment_hash=frames[-1].canonical_hash(),
            final_transcript_mac=audited.closure.transcript_mac,
            runtime_graph_digest=graph_digest,
        )

        authority = self._public_authority.resolve(
            batch,
            chunk_count=len(chunks),
            now=now,
        )
        if type(authority) is not PublicTurnAuthority:
            raise TypeError("authority port must return exact PublicTurnAuthority")
        if len(authority.allocation_ids) != len(chunks):
            raise TurnExecutionError(
                "public allocation count does not match reply chunks"
            )
        if authority.deadline_at <= now:
            raise TurnExecutionError("public authority deadline is expired")
        effective_binding = _domain_hash(
            "v2-effective-turn-binding-v1",
            _canonical(
                "v2-effective-turn-binding",
                {
                    "event_hash": event_hash,
                    "state_hash": semantic_hash(decision.next_state),
                    "projection_hash": decision.projection.canonical_hash(),
                    "capability_policy_digest": authority.capability_policy_digest,
                },
            ),
        )
        public_rows = tuple(
            PublicOutboxWrite(
                public_row_id=_opaque("public-row", batch.batch_id, chunk.ordinal),
                chunk=chunk,
                idempotency_key=_opaque(
                    "public-idempotency", batch.batch_id, chunk.ordinal
                ),
                target_binding_hash=authority.target_binding_hash,
                channel_id=authority.channel_id,
                channel_scope=authority.channel_scope,
                authorization_kind=authority.authorization_kind,
                authorization_id=authority.authorization_id,
                scope_subject_id=authority.scope_subject_id,
                allocation_id=authority.allocation_ids[chunk.ordinal],
                immutable_generation=authority.immutable_generation,
                qualification_id=authority.qualification_id,
                scenario_id=authority.scenario_id,
                capability_policy_digest=authority.capability_policy_digest,
                effect_authorization_binding_digest=(
                    authority.effect_authorization_binding_digest
                ),
                effective_turn_binding_digest=effective_binding,
                deadline_at=authority.deadline_at,
            )
            for chunk in chunks
        )
        artifacts = tuple(
            [
                TurnArtifactWrite(
                    _opaque("artifact-frame", batch.batch_id, item.sequence),
                    "frame_commitment",
                    item.sequence,
                    item.canonical_hash(),
                    item.to_canonical_bytes(),
                    item.canonical_hash(),
                )
                for item in frames
            ]
            + [
                TurnArtifactWrite(
                    _opaque("artifact-read", batch.batch_id, index),
                    "read_observation",
                    None,
                    item.frame_commitment_hash,
                    item.to_canonical_bytes(),
                    item.canonical_hash(),
                )
                for index, item in enumerate(boundary_reads)
            ]
            + [
                TurnArtifactWrite(
                    _opaque("artifact-fact", batch.batch_id, item.name),
                    "typed_fact",
                    None,
                    item.frame_commitment_hash,
                    item.to_canonical_bytes(),
                    item.canonical_hash(),
                )
                for item in facts
            ]
            + [
                TurnArtifactWrite(
                    _opaque("artifact-closure", batch.batch_id),
                    "maya_closure",
                    None,
                    frames[-1].canonical_hash(),
                    closure.to_canonical_bytes(),
                    closure.canonical_hash(),
                ),
                TurnArtifactWrite(
                    _opaque("artifact-maya", batch.batch_id),
                    "maya_proposal",
                    None,
                    frames[-1].canonical_hash(),
                    maya.to_canonical_bytes(),
                    maya.canonical_hash(),
                ),
                TurnArtifactWrite(
                    _opaque("artifact-kernel", batch.batch_id),
                    "kernel_decision",
                    None,
                    maya.canonical_hash(),
                    kernel_bytes,
                    kernel_hash,
                ),
            ]
        )
        command_rows = _command_rows(execution_commands)
        command_relays = _command_relays(batch.batch_id, execution_commands)
        internal_jobs = _handoff_jobs(batch.batch_id, decision.handoff_request)
        commit_now = self._clock.now()
        if (
            type(commit_now) is not datetime
            or commit_now.tzinfo is None
            or commit_now.utcoffset() != timedelta(0)
            or commit_now < now
        ):
            raise TurnExecutionError("commit clock is not monotonic UTC")
        if commit_now > now + self._turn_timeout:
            raise TurnExecutionError("turn deadline expired before commit")
        if not (profile.observed_at <= commit_now < profile.expires_at):
            raise TurnExecutionError("private profile expired before commit")
        for observation in v2_observations:
            self._reads.accept(observation, now=commit_now)
        if authority.deadline_at <= commit_now:
            raise TurnExecutionError("public authority expired before commit")
        receipt = TurnReceipt.create(
            aggregate_turn_id=batch.batch_id,
            event_hash=event_hash,
            source_events=sources,
            maya_proposal_hash=maya.canonical_hash(),
            kernel_decision_hash=kernel_hash,
            read_observations=tuple(
                (item.artifact_id, item.canonical_bytes, item.artifact_hash)
                for item in artifacts
                if item.artifact_kind == "read_observation"
            ),
            committed_state_version=decision.next_state.version,
            committed_state_hash=semantic_hash(decision.next_state),
            public_chunks=tuple(
                (
                    row.public_row_id,
                    row.chunk.ordinal,
                    row.chunk.to_canonical_bytes(),
                    row.chunk.canonical_hash(),
                )
                for row in public_rows
            ),
            command_rows=command_rows,
            relay_rows=tuple(
                (item.relay_id, item.bundle_hash) for item in command_relays
            ),
            internal_outbox_rows=tuple(
                (item.job_id, item.artifact_hash) for item in internal_jobs
            ),
            uds_transcript_mac=audited.closure.transcript_mac,
            uds_final_seq=len(frames),
            structural_graph_digest=graph_digest,
            capability_policy_digest=authority.capability_policy_digest,
            effective_stage_binding_digest=effective_binding,
            behavior_state_snapshot_digest=decision.projection.canonical_hash(),
            qualification_id=None,
            admission_sequence=None,
            admission_revision=None,
            commit_fence_token=None,
            allocation_manifest_hash=None,
            immutable_generation=None,
            allocation_ids=None,
            committed_at=commit_now,
            previous_turn_receipt_hash=previous_receipt_hash,
        )

        revalidated = self._store.load_state(batch.lead_id)
        if (
            revalidated.version != current.version
            or revalidated.semantic_hash != current.semantic_hash
        ):
            raise ConcurrencyConflict("state changed during external turn work")
        return (
            _PreparedTurn(
                commit=commit,
                receipt=receipt,
                artifacts=artifacts,
                command_relays=command_relays,
                internal_jobs=internal_jobs,
                public_rows=public_rows,
                reply_chunks=decision.public_reply.chunks,
            ),
            current.version,
            fencing_token,
        )


__all__ = [
    "PublicTurnAuthority",
    "TurnExecutionError",
    "V2TurnExecutionResult",
    "V2TurnExecutor",
]
