"""Atomic Phase 8 turn executor for the standalone Agente V2 runtime."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Final

from reservation_boundary.conversation import (
    ConversationProjection,
    ConversationStage,
    DesiredService,
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
from reservation_boundary.reads import (
    Phase8ToolReadRequest,
    ReadObservation as BoundaryReadObservation,
    SanitizedLookupResult,
)
from reservation_boundary.serialization import semantic_hash
from reservation_boundary.sqlite_store import (
    CommandRelayWrite,
    ConcurrencyConflict,
    InternalOutboxWrite,
    PublicOutboxWrite,
    SQLiteBoundaryStore,
    StateNotFound,
    TurnArtifactWrite,
    TurnReceipt,
    kernel_decision_commitment,
)
from reservation_boundary.types import (
    ActivityGroupReadArguments,
    ActivityReadArguments,
    BoundaryCommit,
    BoundaryState,
    ConversationIntentKind,
    LodgingReadArguments,
    StringSlot,
    TypedFact,
)
from reservation_domain import (
    AwaitingConfirmationState,
    Party,
    ReservationCommand,
    ServiceKind,
    dumps_command,
)
from reservation_followup import HandoffRequested
from v2_application.active_execution import (
    active_execution_status,
    blocks_active_commercial_progression,
    execution_in_progress_reply,
    is_regressive_post_command_reply,
)
from v2_application.conversation import (
    ConversationReductionError,
    V2ConversationReducer,
    effective_customer_material_hash,
    reservation_profile_ready,
)
from v2_application.read_bridge import bridge_availability_observation
from v2_application.passengers import (
    PassengerManifestConflict,
    attach_projection_manifest,
    merge_projection_manifest,
    projection_manifest_fact,
    projection_manifest_party,
    projection_manifest_status,
)
from v2_application.private_customer_facts import (
    PrivateCustomerFactSnapshot,
    PrivateCustomerFactWriteResult,
    canonical_birth_date,
    canonical_country_code,
    canonical_email,
    canonical_full_name,
    canonical_gender,
)
from v2_application.public_reply import apply_positive_grounding
from v2_application.reads import V2ReadService
from v2_application.relay_worker import (
    build_handoff_relay_bundle,
    build_reservation_relay_bundle,
)
from v2_application.reservations import ReservationAllocator
from v2_application.turn_plan import (
    derive_adjustment_reads,
    normalize_initial_commercial_plan,
    preserve_initial_adjustment,
    preserve_initial_facts,
    reuses_fresh_consultation,
)
from v2_application.turns import validate_productive_proposal
from v2_contracts.channel import InboundBatch
from v2_contracts.critical_actions import ApprovalBasis, PendingCriticalActionContext
from v2_contracts.localization import customer_language_from_phone
from v2_contracts.model import (
    AuditedModelTurn,
    ConsultationHistoryEntry,
    ModelFact,
    ModelProposal,
    ModelRequest,
    PRIVATE_CUSTOMER_FACT_ORDER,
)
from v2_contracts.passengers import PassengerManifestStatus
from v2_contracts.ports import AuditedModelPort
from v2_contracts.profile import PrivateCustomerBinding
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

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
    private_profile_material_hash: str | None


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


def _structured_selection_review_required(
    state_facts: tuple[ModelFact, ...],
    current_facts: tuple[ModelFact, ...],
    *,
    private_profile_complete: bool,
    passenger_manifest_complete: bool = False,
) -> bool:
    """Gate one semantic selection review from closed structured facts only."""

    if (
        type(state_facts) is not tuple
        or any(type(item) is not ModelFact for item in state_facts)
        or type(current_facts) is not tuple
        or any(type(item) is not ModelFact for item in current_facts)
        or type(private_profile_complete) is not bool
        or type(passenger_manifest_complete) is not bool
    ):
        raise TypeError("selection review gate requires exact V2 contracts")
    if not private_profile_complete:
        return False
    values: dict[str, str | int | date] = {}
    for fact in (*state_facts, *current_facts):
        current = values.get(fact.name)
        if current is not None and current != fact.value:
            return False
        values[fact.name] = fact.value
    adults = values.get("adults")
    children = values.get("children", 0)
    if type(adults) is not int or type(children) is not int:
        return False
    commercially_complete = (
        type(values.get("product_id")) is str
        and adults >= 1
        and children >= 0
        and values.get("payment_method") in {"stripe", "wise", "pix"}
    )
    if not commercially_complete:
        return False
    if values.get("service") == "agency":
        return type(values.get("activity_date") or values.get("start_date")) is date
    if values.get("service") == "package":
        start_date = values.get("start_date")
        end_date = values.get("end_date")
        return (
            type(start_date) is date
            and type(end_date) is date
            and end_date > start_date
            and type(values.get("activity_date")) is date
        )
    return False


def _post_read_package_selection_review_required(
    proposal: ModelProposal,
    observations: tuple[ReadObservation, ...],
) -> bool:
    if type(proposal) is not ModelProposal:
        raise TypeError("package selection review proposal must be exact")
    if type(observations) is not tuple or any(
        type(item) is not ReadObservation for item in observations
    ):
        raise TypeError("package selection review observations must be exact")
    if (
        proposal.intent != "inform"
        or proposal.read_requests
        or proposal.target_offer_id is not None
        or proposal.target_offer_ids
        or proposal.selection_requested
    ):
        return False
    values = {item.name: item.value for item in proposal.facts}
    if values.get("service") != "package":
        return False
    providers = {
        item.provider
        for item in observations
        if item.public_payload.get("available") is not False
    }
    return {"cloudbeds", "bokun"}.issubset(providers)


def _repair_requested_activity_selection(
    first_proposal: ModelProposal,
    second_proposal: ModelProposal,
    *,
    state_facts: tuple[ModelFact, ...],
    observations: tuple[ReadObservation, ...],
    private_profile_complete: bool,
    passenger_manifest_complete: bool = False,
) -> ModelProposal:
    """Complete an explicitly signalled selection from exact structured evidence."""

    if (
        type(first_proposal) is not ModelProposal
        or type(second_proposal) is not ModelProposal
        or type(state_facts) is not tuple
        or any(type(item) is not ModelFact for item in state_facts)
        or type(observations) is not tuple
        or any(type(item) is not ReadObservation for item in observations)
        or type(private_profile_complete) is not bool
        or type(passenger_manifest_complete) is not bool
    ):
        raise TypeError("selection repair requires exact V2 contracts")
    if (
        not first_proposal.selection_requested
        or not private_profile_complete
        or second_proposal.intent not in ("inform", "select")
        or second_proposal.read_requests
        or len(first_proposal.read_requests) != 1
        or len(observations) != 1
    ):
        return second_proposal
    request = first_proposal.read_requests[0]
    observation = observations[0]
    if request.kind is not ReadKind.ACTIVITY:
        return second_proposal
    request_adults, request_children = request.activity_party()

    values: dict[str, str | int | date] = {}
    for fact in (*state_facts, *first_proposal.facts, *second_proposal.facts):
        current = values.get(fact.name)
        if current is not None and current != fact.value:
            return second_proposal
        values[fact.name] = fact.value
    activity_date = values.get("activity_date") or values.get("start_date")
    adults = values.get("adults")
    children = values.get("children", 0)
    if (
        values.get("service") != "agency"
        or values.get("product_id") != request.product_id
        or activity_date != request.activity_date
        or type(adults) is not int
        or type(children) is not int
        or adults < 1
        or children < 0
        or request_adults != adults
        or request_children != children
        or values.get("payment_method") not in {"stripe", "wise", "pix"}
        or (
            adults + children == 1
            and (
                type(values.get("birth_date")) is not date
                or values.get("gender") not in {"m", "f"}
            )
        )
        or (adults + children > 1 and not passenger_manifest_complete)
    ):
        return second_proposal

    payload = observation.public_payload
    offer_id = payload.get("offer_id")
    if (
        observation.request_hash != request.canonical_hash()
        or payload.get("available") is not True
        or payload.get("price_includes_booking_fee") is not True
        or payload.get("product_id") != request.product_id
        or payload.get("activity_date") != request.activity_date.isoformat()
        or payload.get("adults") != request_adults
        or payload.get("children") != request_children
        or payload.get("participants") != request_adults + request_children
        or type(offer_id) is not str
        or not offer_id.startswith("offer:")
    ):
        return second_proposal
    existing_fact_names = {item.name for item in second_proposal.facts}
    canonical_selection_facts = (
        ModelFact("service", "agency"),
        ModelFact("product_id", request.product_id),
        ModelFact("activity_date", request.activity_date),
        ModelFact("adults", adults),
        ModelFact("children", children),
        ModelFact("payment_method", values["payment_method"]),
        *(
            (
                ModelFact("birth_date", values["birth_date"]),
                ModelFact("gender", values["gender"]),
            )
            if adults + children == 1
            else ()
        ),
    )
    return replace(
        second_proposal,
        intent="select",
        facts=(
            *second_proposal.facts,
            *(
                fact
                for fact in canonical_selection_facts
                if fact.name not in existing_fact_names
            ),
        ),
        target_offer_id=offer_id,
        selection_requested=False,
    )


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


def _package_selection_commitment(target_offer_ids: tuple[str, ...]) -> str:
    if len(target_offer_ids) != 2 or len(set(target_offer_ids)) != 2:
        raise TurnExecutionError(
            "package selection closure requires exactly two distinct offer IDs"
        )
    preimage = b"v2-package-selection-v1\x00" + b"\x00".join(
        item.encode("utf-8") for item in sorted(target_offer_ids)
    )
    return "package-selection:" + hashlib.sha256(preimage).hexdigest()


def _intent(proposal: ModelProposal) -> MayaIntentClosure:
    try:
        kind = ConversationIntentKind(proposal.intent)
    except ValueError as exc:
        raise TurnExecutionError(
            "model intent is outside the boundary catalog"
        ) from exc
    if kind is ConversationIntentKind.TOOL_REQUEST:
        raise TurnExecutionError("tool-request intent cannot enter the V2 runtime")
    selection = None
    if kind is ConversationIntentKind.SELECT:
        selection = (
            _package_selection_commitment(proposal.target_offer_ids)
            if proposal.target_offer_ids
            else proposal.target_offer_id
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
        if item.name not in ("critical_outcome", "passenger_manifest")
        and item.name not in PRIVATE_CUSTOMER_FACT_ORDER
    )


_PRIVATE_ARTIFACT_FACT_NAMES: Final = frozenset(
    (*PRIVATE_CUSTOMER_FACT_ORDER, "passenger_manifest")
)


def _public_artifact_facts(facts: tuple[TypedFact, ...]) -> tuple[TypedFact, ...]:
    if type(facts) is not tuple or any(type(item) is not TypedFact for item in facts):
        raise TypeError("artifact facts must be an exact TypedFact tuple")
    return tuple(
        item for item in facts if item.name not in _PRIVATE_ARTIFACT_FACT_NAMES
    )


def _private_customer_fact_names(
    projection: ConversationProjection,
    *,
    private_facts: PrivateCustomerFactSnapshot | None = None,
    profile: PrivateCustomerBinding | None = None,
    now: datetime | None = None,
) -> tuple[str, ...]:
    present = {
        item.name
        for item in projection.facts
        if item.name not in {"full_name", "email", "phone_e164", "country_code"}
    }
    if private_facts is not None:
        if type(private_facts) is not PrivateCustomerFactSnapshot:
            raise TypeError("private_facts must be exact or None")
        present.update(private_facts.present_fact_names)
    if profile is not None:
        if type(profile) is not PrivateCustomerBinding:
            raise TypeError("profile must be exact or None")
        if (
            type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
        ):
            raise TypeError("profile presence markers require exact UTC now")
        if profile.observed_at <= now < profile.expires_at:
            for name, value, canonicalizer in (
                ("full_name", profile.full_name, canonical_full_name),
                ("email", profile.email, canonical_email),
                ("country_code", profile.country_code, canonical_country_code),
            ):
                if value is None:
                    continue
                try:
                    canonicalizer(value)
                except (TypeError, ValueError):
                    continue
                present.add(name)
            if profile.phone_e164 is not None:
                present.add("phone_e164")
    return tuple(name for name in PRIVATE_CUSTOMER_FACT_ORDER if name in present)


_PRIVATE_CONVERSATION_FALLBACK_NAMES: Final = frozenset(
    ("full_name", "email", "country_code", "birth_date", "gender")
)
_COMMAND_BLOCKING_PRIVATE_FACT_NAMES: Final = frozenset(
    ("full_name", "email", "country_code")
)


def _partition_private_customer_facts(
    proposal: ModelProposal,
) -> tuple[
    tuple[ModelFact, ...],
    tuple[ModelFact, ...],
    tuple[str, ...],
    bool,
]:
    private: list[ModelFact] = []
    public: list[ModelFact] = []
    invalid: set[str] = set()
    phone_proposed = False
    canonicalizers = {
        "full_name": canonical_full_name,
        "email": canonical_email,
        "country_code": canonical_country_code,
        "birth_date": lambda value: date.fromisoformat(
            canonical_birth_date(value)
        ),
        "gender": canonical_gender,
    }
    for fact in proposal.facts:
        if fact.name == "phone_e164":
            phone_proposed = True
            continue
        if fact.name in _PRIVATE_CONVERSATION_FALLBACK_NAMES:
            try:
                value = canonicalizers[fact.name](fact.value)
            except (TypeError, ValueError):
                invalid.add(fact.name)
                continue
            private.append(ModelFact(fact.name, value))
        else:
            public.append(fact)
    invalid_names = tuple(
        name
        for name in ("full_name", "email", "country_code")
        if name in invalid
    )
    return tuple(private), tuple(public), invalid_names, phone_proposed


def _authoritative_language_facts(
    facts: tuple[ModelFact, ...],
    locale: str,
) -> tuple[ModelFact, ...]:
    if type(facts) is not tuple or any(type(item) is not ModelFact for item in facts):
        raise TypeError("language authority requires exact model facts")
    if not any(item.name == "language" for item in facts):
        return facts
    return (
        *(item for item in facts if item.name != "language"),
        ModelFact("language", locale),
    )


def _authoritative_phone_locale_projection(
    projection: ConversationProjection,
    locale: str,
) -> ConversationProjection:
    if type(projection) is not ConversationProjection:
        raise TypeError("phone locale authority requires an exact projection")
    language_facts = tuple(
        fact for fact in projection.facts if fact.name == "language"
    )
    if len(language_facts) == 1 and language_facts[0].value.value == locale:
        return replace(projection, locale=locale)
    return replace(
        projection,
        locale=locale,
        facts=tuple(fact for fact in projection.facts if fact.name != "language"),
    )


def _authoritative_read_locales(
    requests: tuple[ReadRequest, ...],
    *,
    locale: str,
) -> tuple[ReadRequest, ...]:
    if type(requests) is not tuple or any(
        type(item) is not ReadRequest for item in requests
    ):
        raise TypeError("read locale authority requires exact read requests")
    if locale not in {"pt-BR", "en"}:
        raise ValueError("read locale authority requires a closed phone locale")
    localized_kinds = {
        ReadKind.KNOWLEDGE,
        ReadKind.ACTIVITY,
        ReadKind.ACTIVITY_DESCRIPTION,
    }
    return tuple(
        replace(item, locale=locale) if item.kind in localized_kinds else item
        for item in requests
    )


def _collection_reply(
    locale: str,
    *,
    invalid_fact_names: tuple[str, ...] = (),
) -> str:
    if invalid_fact_names:
        labels_pt = {
            "full_name": "nome completo",
            "email": "e-mail válido",
            "country_code": "país",
        }
        labels_en = {
            "full_name": "full name",
            "email": "valid email",
            "country_code": "country",
        }
        labels = labels_en if locale.startswith("en") else labels_pt
        requested = ", ".join(labels[name] for name in invalid_fact_names)
        if locale.startswith("en"):
            return f"Please send your {requested} again so I can continue."
        return f"Por favor, envie novamente {requested} para eu continuar."
    if locale.startswith("en"):
        return "Thank you. I saved those details so we can continue the reservation."
    return "Obrigado. Guardei esses dados para continuar a reserva."


def _consultation_reuse_fallback(locale: str) -> tuple[str, ...]:
    if locale.startswith("en"):
        return (
            "The earlier lookup for this same scope is still fresh. I can recap it, but selecting or booking requires the normal fresh-action checks.",
        )
    return (
        "A consulta anterior para esse mesmo escopo ainda está válida. Posso recapitulá-la, mas selecionar ou reservar exige as verificações normais da ação.",
    )


def _selection_binding_failure_proposal(
    proposal: ModelProposal,
    *,
    locale: str,
) -> ModelProposal:
    if proposal.intent != "select":
        raise ValueError("selection binding fallback requires select intent")
    reply = (
        "I couldn’t bind that choice to exactly one current option. Nothing was "
        "reserved; I’ll refresh it before preparing a summary."
        if locale.casefold().startswith("en")
        else "Não consegui vincular essa escolha a uma única opção atual. Nada foi "
        "reservado; vou atualizá-la antes de preparar um resumo."
    )
    return replace(
        proposal,
        intent="inform",
        reply_chunks=(reply,),
        target_offer_id=None,
        target_offer_ids=(),
        selection_requested=False,
    )


def _active_execution_guard_proposal(
    proposal: ModelProposal,
    *,
    locale: str,
) -> ModelProposal:
    return replace(
        proposal,
        intent="inform",
        reply_chunks=execution_in_progress_reply(locale),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        target_offer_id=None,
        target_offer_ids=(),
        confirmed_summary_version=None,
        confirmed_action_kinds=(),
        approval_basis=None,
        selection_requested=False,
        pending_disposition=None,
        passengers=(),
    )


def _collection_only_proposal(
    proposal: ModelProposal,
    *,
    public_facts: tuple[ModelFact, ...],
    locale: str,
    invalid_fact_names: tuple[str, ...] = (),
    revoke_pending: bool = False,
) -> ModelProposal:
    if type(revoke_pending) is not bool:
        raise TypeError("revoke_pending must be exact bool")
    return ModelProposal(
        source_event_id=proposal.source_event_id,
        intent="adjust" if revoke_pending else "inform",
        reply_chunks=(
            _collection_reply(locale, invalid_fact_names=invalid_fact_names),
        ),
        facts=public_facts,
        read_requests=(),
        effect_proposals=(),
        pending_disposition="revoke" if revoke_pending else None,
        passengers=(),
    )


def _private_update_no_command_proposal(
    proposal: ModelProposal,
    *,
    pending_action: PendingCriticalActionContext | None,
    locale: str,
) -> ModelProposal:
    if proposal.intent == "select" or (
        proposal.intent == "inform" and proposal.read_requests
    ):
        return proposal
    if proposal.intent == "request_handoff":
        reply = (
            "I'll connect you with a person."
            if locale.startswith("en")
            else "Vou encaminhar seu atendimento para uma pessoa."
        )
        return ModelProposal(
            source_event_id=proposal.source_event_id,
            intent="request_handoff",
            reply_chunks=(reply,),
            facts=proposal.facts,
            read_requests=(),
            effect_proposals=(),
        )
    if pending_action is not None:
        reply = (
            "I updated your details. I'll present a new summary before asking for confirmation."
            if locale.startswith("en")
            else "Atualizei seus dados. Vou apresentar um novo resumo antes de pedir confirmação."
        )
        return ModelProposal(
            source_event_id=proposal.source_event_id,
            intent="adjust",
            reply_chunks=(reply,),
            facts=proposal.facts,
            read_requests=(),
            effect_proposals=(),
            pending_disposition="revoke",
            passengers=proposal.passengers,
        )
    return ModelProposal(
        source_event_id=proposal.source_event_id,
        intent="inform",
        reply_chunks=(_collection_reply(locale),),
        facts=proposal.facts,
        read_requests=(),
        effect_proposals=(),
        passengers=proposal.passengers,
    )


def _persist_private_collection(
    owner: object,
    *,
    lead_id: str,
    source_turn_id: str,
    source_event_hash: str,
    facts: tuple[ModelFact, ...],
    persisted_at: datetime,
) -> PrivateCustomerFactSnapshot:
    result = owner.persist_turn(
        lead_id=lead_id,
        source_turn_id=source_turn_id,
        source_event_hash=source_event_hash,
        facts=facts,
        persisted_at=persisted_at,
    )
    if type(result) is not PrivateCustomerFactWriteResult:
        raise TypeError("private customer owner returned an invalid write")
    return result.snapshot


def _activity_party_for_manifest(
    projection: ConversationProjection,
    proposal: ModelProposal | None = None,
) -> Party | None:
    values = {
        item.name: item.value.value
        for item in projection.facts
        if item.name != "passenger_manifest"
    }
    if proposal is not None:
        values.update({item.name: item.value for item in proposal.facts})
    service = values.get("service")
    activity_desired = (
        service in ("agency", "package")
        or DesiredService.AGENCY in projection.desired_services
    )
    if not activity_desired:
        if service == "hostel":
            return None
        try:
            return projection_manifest_party(projection)
        except (TypeError, ValueError) as exc:
            raise TurnExecutionError(
                "persisted passenger manifest party is invalid"
            ) from exc
    adults = values.get("adults")
    children = values.get("children", 0)
    if type(adults) is not int or type(children) is not int:
        try:
            return projection_manifest_party(projection)
        except (TypeError, ValueError) as exc:
            raise TurnExecutionError(
                "persisted passenger manifest party is invalid"
            ) from exc
    try:
        return Party(adults, children)
    except (TypeError, ValueError) as exc:
        raise TurnExecutionError("activity party for passenger manifest is invalid") from exc


def _passenger_status(
    projection: ConversationProjection,
    proposal: ModelProposal | None = None,
) -> PassengerManifestStatus | None:
    party = _activity_party_for_manifest(projection, proposal)
    try:
        return projection_manifest_status(projection, party)
    except (TypeError, ValueError) as exc:
        raise TurnExecutionError("passenger manifest status is invalid") from exc


def _merge_passenger_updates(
    projection: ConversationProjection,
    proposal: ModelProposal,
    *,
    frame_commitment_hash: str,
) -> ConversationProjection:
    party = _activity_party_for_manifest(projection, proposal)
    try:
        return merge_projection_manifest(
            projection,
            proposal.passengers,
            party,
            frame_commitment_hash=frame_commitment_hash,
            allow_replacement=(
                proposal.intent == "adjust"
                and proposal.pending_disposition == "revoke"
            ),
        )
    except (PassengerManifestConflict, TypeError, ValueError) as exc:
        raise TurnExecutionError("passenger manifest update was rejected") from exc


def _passenger_manifest_complete(
    projection: ConversationProjection,
    proposal: ModelProposal | None = None,
) -> bool:
    status = _passenger_status(projection, proposal)
    return status is not None and not status.missing_by_position


def _critical_outcome(projection: ConversationProjection) -> str | None:
    matches = tuple(
        item for item in projection.facts if item.name == "critical_outcome"
    )
    if not matches:
        return None
    if len(matches) != 1 or type(matches[0].value) is not StringSlot:
        raise TurnExecutionError("critical outcome projection is invalid")
    outcome = matches[0].value.value
    if outcome not in ("proposal_revoked_after_refresh", "proposal_expired"):
        raise TurnExecutionError("critical outcome projection is outside the catalog")
    return outcome


def _consultation_history_entry(
    observation: BoundaryReadObservation,
    *,
    now: datetime,
) -> ConsultationHistoryEntry:
    if type(observation) is not BoundaryReadObservation:
        raise TypeError("consultation history requires exact boundary observations")
    if not observation.safe_for_public_claims:
        raise TurnExecutionError("consultation history is not public-safe")
    try:
        request = Phase8ToolReadRequest.from_canonical_bytes(
            observation.request_bytes
        )
        result = SanitizedLookupResult.from_canonical_bytes(
            observation.typed_result_bytes
        )
    except (TypeError, ValueError) as exc:
        raise TurnExecutionError("consultation history cannot be reconstructed") from exc
    if result.observed_at > now:
        raise TurnExecutionError("consultation history is from the future")
    arguments = request.arguments
    if type(arguments) is LodgingReadArguments:
        query = {
            "check_in": arguments.check_in.isoformat(),
            "check_out": arguments.check_out.isoformat(),
            "adults": arguments.adults,
            "children": arguments.children,
        }
    elif type(arguments) is ActivityReadArguments:
        query = {
            "product_id": arguments.activity_id,
            "activity_date": arguments.activity_date.isoformat(),
            "adults": arguments.participants,
            "children": 0,
        }
    elif type(arguments) is ActivityGroupReadArguments:
        query = {
            "product_id": arguments.activity_id,
            "activity_date": arguments.activity_date.isoformat(),
            "adults": arguments.adults,
            "children": arguments.children,
        }
    else:
        raise TurnExecutionError("consultation history read kind is unsupported")
    all_offers = [
        {
            "public_label": item.public_label,
            "start_date": item.start_date.isoformat(),
            "end_date": item.end_date.isoformat() if item.end_date is not None else None,
            "start_time": (
                item.start_time.strftime("%H:%M")
                if item.start_time is not None
                else None
            ),
            "adults": item.adults,
            "children": item.children,
            "total_amount": format(item.total_amount, "f"),
            "currency": item.currency,
        }
        for item in result.offers
    ]
    offers = all_offers[:32]
    return ConsultationHistoryEntry(
        observation_hash=observation.canonical_hash(),
        observed_at=result.observed_at,
        expires_at=result.expires_at,
        fresh_at_turn_start=result.observed_at <= now < result.expires_at,
        public_context={
            "service": result.service.value,
            "status": result.status.value,
            "query": query,
            "offers": offers,
            "offer_count": len(all_offers),
            "offers_truncated": len(all_offers) > len(offers),
        },
    )


def _consultation_history(
    store: SQLiteBoundaryStore,
    lead_id: str,
    *,
    now: datetime,
) -> tuple[ConsultationHistoryEntry, ...]:
    observations = store.load_recent_public_lookup_observations(lead_id, limit=8)
    entries = tuple(
        _consultation_history_entry(item, now=now) for item in observations
    )
    return tuple(
        sorted(entries, key=lambda item: (item.observed_at, item.observation_hash))
    )


def _confirmation_read_requests(
    state: BoundaryState,
    projection: ConversationProjection,
    proposal: ModelProposal,
) -> tuple[ReadRequest, ...]:
    workflow = state.workflow
    if (
        type(workflow) is not AwaitingConfirmationState
        or proposal.intent != "confirm"
        or proposal.confirmed_summary_version != workflow.draft.version
    ):
        return ()
    values = {item.name: item.value.value for item in projection.facts}
    requests: list[ReadRequest] = []
    seen_kinds: set[ReadKind] = set()
    for component in workflow.draft.components:
        if component.service is ServiceKind.LODGING:
            if component.end_date is None or ReadKind.LODGING in seen_kinds:
                return ()
            request = ReadRequest(
                request_id=_opaque(
                    "confirm-read-lodging",
                    proposal.source_event_id,
                    component.offer_id,
                ),
                kind=ReadKind.LODGING,
                check_in=component.start_date,
                check_out=component.end_date,
                adults=component.party.adults,
                children=component.party.children,
            )
        elif component.service is ServiceKind.ACTIVITY:
            product_id = values.get("product_id")
            if (
                type(product_id) is not str
                or not product_id
                or ReadKind.ACTIVITY in seen_kinds
            ):
                return ()
            request = ReadRequest(
                request_id=_opaque(
                    "confirm-read-activity",
                    proposal.source_event_id,
                    component.offer_id,
                ),
                kind=ReadKind.ACTIVITY,
                product_id=product_id,
                activity_date=component.start_date,
                adults=component.party.adults,
                children=component.party.children,
            )
        else:
            return ()
        requests.append(request)
        seen_kinds.add(request.kind)
    return tuple(requests)


def _critical_confirmation_bound(
    pending_action: PendingCriticalActionContext | None,
    proposal: ModelProposal,
    *,
    now: datetime,
    material_scope_bound: bool,
) -> bool:
    if pending_action is None:
        return False
    if type(pending_action) is not PendingCriticalActionContext:
        raise TypeError("pending_action must be exact PendingCriticalActionContext or None")
    if type(proposal) is not ModelProposal:
        raise TypeError("proposal must be an exact ModelProposal")
    if (
        type(now) is not datetime
        or now.tzinfo is None
        or now.utcoffset() != timedelta(0)
    ):
        raise ValueError("critical confirmation time must be exact UTC")
    if type(material_scope_bound) is not bool:
        raise TypeError("material_scope_bound must be an exact bool")
    return (
        material_scope_bound
        and now < pending_action.expires_at
        and proposal.intent == "confirm"
        and proposal.confirmed_summary_version == pending_action.summary_version
        and proposal.confirmed_action_kinds == pending_action.action_kinds
        and proposal.approval_basis is ApprovalBasis.CONTEXTUAL_REFERENCE
    )


def _critical_model_reads_allowed(
    pending_action: PendingCriticalActionContext | None,
    proposal: ModelProposal,
    *,
    now: datetime,
    material_scope_bound: bool,
) -> bool:
    if type(proposal) is not ModelProposal:
        raise TypeError("proposal must be an exact ModelProposal")
    if pending_action is None:
        return proposal.intent != "confirm"
    if type(pending_action) is not PendingCriticalActionContext:
        raise TypeError("pending_action must be exact PendingCriticalActionContext or None")
    if proposal.intent == "confirm":
        return _critical_confirmation_bound(
            pending_action,
            proposal,
            now=now,
            material_scope_bound=material_scope_bound,
        )
    return True


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
        private_customer_facts: object,
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
        for method in (
            "load",
            "persist_turn",
            "turn_supplied_fact_names",
            "load_passenger_manifest",
            "persist_passenger_manifest",
        ):
            if not callable(getattr(private_customer_facts, method, None)):
                raise TypeError(f"private_customer_facts must expose {method}")
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
        self._private_customer_facts = private_customer_facts
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
                if prepared.private_profile_material_hash is not None:
                    profile_now = self._clock.now()
                    current_profile = self._profile.read(batch.lead_id, now=profile_now)
                    if type(current_profile) is not PrivateCustomerBinding:
                        raise TypeError(
                            "profile port must return exact PrivateCustomerBinding"
                        )
                    if not (
                        current_profile.observed_at
                        <= profile_now
                        < current_profile.expires_at
                    ):
                        raise TurnExecutionError("private profile expired before commit")
                    current_projection = (
                        self._store.load_latest_conversation_projection(batch.lead_id)
                    )
                    if current_projection is None:
                        current_projection = _genesis_projection(self._locale)
                    current_projection = attach_projection_manifest(
                        current_projection,
                        self._private_customer_facts.load_passenger_manifest(
                            batch.lead_id
                        ),
                    )
                    current_private_facts = self._private_customer_facts.load(
                        batch.lead_id
                    )
                    if type(current_private_facts) is not PrivateCustomerFactSnapshot:
                        raise TypeError(
                            "private customer owner must return an exact snapshot"
                        )
                    if effective_customer_material_hash(
                        current_profile,
                        current_projection,
                        profile_now,
                        private_facts=current_private_facts,
                    ) != (
                        prepared.private_profile_material_hash
                    ):
                        raise TurnExecutionError("private profile changed before commit")
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
        projection = attach_projection_manifest(
            projection,
            self._private_customer_facts.load_passenger_manifest(batch.lead_id),
        )
        previous_receipt_hash = self._store.latest_turn_receipt_hash(batch.lead_id)
        consultation_history = _consultation_history(
            self._store,
            batch.lead_id,
            now=now,
        )

        profile = self._profile.read(batch.lead_id, now=now)
        if type(profile) is not PrivateCustomerBinding:
            raise TypeError("profile port must return exact PrivateCustomerBinding")
        if (
            profile.phone_e164 is not None
            and profile.observed_at <= now < profile.expires_at
        ):
            projection = _authoritative_phone_locale_projection(
                projection,
                customer_language_from_phone(profile.phone_e164).value,
            )
        private_facts = self._private_customer_facts.load(batch.lead_id)
        if type(private_facts) is not PrivateCustomerFactSnapshot:
            raise TypeError("private customer owner must return an exact snapshot")
        journal_fact_names = (
            self._private_customer_facts.turn_supplied_fact_names(
                batch.lead_id,
                batch.batch_id,
            )
        )
        private_update_turn = bool(
            set(journal_fact_names) & _COMMAND_BLOCKING_PRIVATE_FACT_NAMES
        )
        collection_only = False
        effective_profile_complete = reservation_profile_ready(
            profile,
            projection,
            now,
            private_facts=private_facts,
        )
        pending_action = (
            None
            if current.state.handoff is not None
            else self._reducer.pending_action(
                current.state.workflow,
                locale=projection.locale,
            )
        )
        request = ModelRequest(
            request_id=_opaque("model-request", batch.batch_id, current.version, 1),
            lead_id=batch.lead_id,
            source_event_id=batch.batch_id,
            message=batch.combined_text,
            locale=projection.locale,
            state_version=current.version,
            consultation_history=consultation_history,
            state_facts=_authoritative_language_facts(
                _state_model_facts(projection),
                projection.locale,
            ),
            private_customer_fact_names=_private_customer_fact_names(
                projection,
                private_facts=private_facts,
                profile=profile,
                now=now,
            ),
            passenger_manifest_status=_passenger_status(projection),
            critical_outcome=_critical_outcome(projection),
            pending_action=pending_action,
            private_profile_complete=effective_profile_complete,
            handoff_active=current.state.handoff is not None,
            active_execution_status=active_execution_status(current.state),
        )
        first_audited = self._model.complete_audited(request)
        if type(first_audited) is not AuditedModelTurn:
            raise TypeError("model must return exact AuditedModelTurn")
        first_proposal = validate_productive_proposal(first_audited.proposal)
        if first_proposal.source_event_id != batch.batch_id:
            raise TurnExecutionError("model proposal source event diverged")
        first_proposal = replace(
            first_proposal,
            read_requests=_authoritative_read_locales(
                first_proposal.read_requests,
                locale=projection.locale,
            ),
        )
        (
            first_private_facts,
            first_public_facts,
            first_invalid_private_facts,
            _first_phone_proposed,
        ) = _partition_private_customer_facts(first_proposal)
        first_public_facts = _authoritative_language_facts(
            first_public_facts,
            projection.locale,
        )
        if first_private_facts:
            private_facts = _persist_private_collection(
                self._private_customer_facts,
                lead_id=batch.lead_id,
                source_turn_id=batch.batch_id,
                source_event_hash=event_hash,
                facts=first_private_facts,
                persisted_at=now,
            )
            private_update_turn = private_update_turn or any(
                item.name in _COMMAND_BLOCKING_PRIVATE_FACT_NAMES
                for item in first_private_facts
            )
            effective_profile_complete = reservation_profile_ready(
                profile,
                projection,
                now,
                private_facts=private_facts,
            )
        collection_only = bool(first_invalid_private_facts)
        first_proposal = replace(first_proposal, facts=first_public_facts)
        if collection_only:
            first_proposal = _collection_only_proposal(
                first_proposal,
                public_facts=first_public_facts,
                locale=projection.locale,
                invalid_fact_names=first_invalid_private_facts,
                revoke_pending=pending_action is not None,
            )
        else:
            first_proposal = (
                normalize_initial_commercial_plan(first_proposal)
                if pending_action is None
                else first_proposal
            )
        if blocks_active_commercial_progression(current.state, first_proposal):
            first_proposal = _active_execution_guard_proposal(
                first_proposal,
                locale=projection.locale,
            )
        first_audited = AuditedModelTurn.from_frames(
            proposal=first_proposal,
            frames=first_audited.frames,
            ephemeral_session_id=first_audited.closure.ephemeral_session_id,
        )
        first_frame_hash = _frame_commitments(first_audited)[-1].canonical_hash()
        projection = _merge_passenger_updates(
            projection,
            first_proposal,
            frame_commitment_hash=first_frame_hash,
        )
        selection_review = (
            not collection_only
            and current.state.handoff is None
            and active_execution_status(current.state) is None
            and pending_action is None
            and first_proposal.intent == "inform"
            and not first_proposal.read_requests
            and _structured_selection_review_required(
                _authoritative_language_facts(
                    _state_model_facts(projection),
                    projection.locale,
                ),
                first_proposal.facts,
                private_profile_complete=effective_profile_complete,
                passenger_manifest_complete=_passenger_manifest_complete(
                    projection,
                    first_proposal,
                ),
            )
        )
        confirmation_review = (
            not collection_only
            and pending_action is not None
            and first_proposal.intent == "inform"
            and not first_proposal.read_requests
        )
        if selection_review or confirmation_review:
            review_request = replace(
                request,
                request_id=_opaque(
                    (
                        "model-confirmation-review"
                        if confirmation_review
                        else "model-selection-review"
                    ),
                    batch.batch_id,
                    current.version,
                ),
                confirmation_review_required=confirmation_review,
                selection_review_required=selection_review,
                private_customer_fact_names=_private_customer_fact_names(
                    projection,
                    private_facts=private_facts,
                    profile=profile,
                    now=now,
                ),
                private_profile_complete=effective_profile_complete,
                passenger_manifest_status=_passenger_status(
                    projection,
                    first_proposal,
                ),
            )
            review_audited = self._model.complete_audited(review_request)
            if type(review_audited) is not AuditedModelTurn:
                raise TypeError("model must return exact AuditedModelTurn")
            review_proposal = validate_productive_proposal(review_audited.proposal)
            if review_proposal.source_event_id != batch.batch_id:
                raise TurnExecutionError("semantic review source event diverged")
            (
                review_private_facts,
                review_public_facts,
                review_invalid_private_facts,
                _review_phone_proposed,
            ) = _partition_private_customer_facts(review_proposal)
            review_public_facts = _authoritative_language_facts(
                review_public_facts,
                projection.locale,
            )
            if review_private_facts:
                private_facts = _persist_private_collection(
                    self._private_customer_facts,
                    lead_id=batch.lead_id,
                    source_turn_id=batch.batch_id,
                    source_event_hash=event_hash,
                    facts=review_private_facts,
                    persisted_at=now,
                )
                private_update_turn = private_update_turn or any(
                    item.name in _COMMAND_BLOCKING_PRIVATE_FACT_NAMES
                    for item in review_private_facts
                )
                effective_profile_complete = reservation_profile_ready(
                    profile,
                    projection,
                    now,
                    private_facts=private_facts,
                )
            review_proposal = replace(review_proposal, facts=review_public_facts)
            if review_invalid_private_facts:
                collection_only = True
                first_proposal = _collection_only_proposal(
                    review_proposal,
                    public_facts=review_public_facts,
                    locale=projection.locale,
                    invalid_fact_names=review_invalid_private_facts,
                    revoke_pending=pending_action is not None,
                )
            elif (
                selection_review
                and review_proposal.selection_requested
                or confirmation_review
                and review_proposal.intent
                in ("confirm", "adjust", "request_handoff")
            ):
                first_proposal = review_proposal
                review_frame_hash = _frame_commitments(review_audited)[-1].canonical_hash()
                projection = _merge_passenger_updates(
                    projection,
                    review_proposal,
                    frame_commitment_hash=review_frame_hash,
                )
            review_audited = AuditedModelTurn.from_frames(
                proposal=review_proposal,
                frames=review_audited.frames,
                ephemeral_session_id=review_audited.closure.ephemeral_session_id,
            )
            first_audited = AuditedModelTurn.from_frames(
                proposal=first_proposal,
                frames=(*first_audited.frames, *review_audited.frames),
                ephemeral_session_id=review_audited.closure.ephemeral_session_id,
            )
        if not collection_only and pending_action is None:
            first_proposal = normalize_initial_commercial_plan(first_proposal)
            first_audited = AuditedModelTurn.from_frames(
                proposal=first_proposal,
                frames=first_audited.frames,
                ephemeral_session_id=first_audited.closure.ephemeral_session_id,
            )
        if private_update_turn and not collection_only:
            first_proposal = _private_update_no_command_proposal(
                first_proposal,
                pending_action=pending_action,
                locale=projection.locale,
            )
        material_scope_bound = (
            self._reducer.confirmation_projection_matches(
                current.state.workflow,
                projection,
            )
            and self._reducer.confirmation_capability_available(
                current.state.workflow,
                now=now,
            )
        )
        critical_confirmation_bound = _critical_confirmation_bound(
            pending_action,
            first_proposal,
            now=now,
            material_scope_bound=material_scope_bound,
        )
        model_reads_allowed = _critical_model_reads_allowed(
            pending_action,
            first_proposal,
            now=now,
            material_scope_bound=material_scope_bound,
        )
        if pending_action is not None and first_proposal.intent == "confirm":
            read_requests = (
                _confirmation_read_requests(
                    current.state,
                    projection,
                    first_proposal,
                )
                if critical_confirmation_bound
                else ()
            )
            derived_confirmation_reads = bool(read_requests)
        elif not model_reads_allowed:
            read_requests = ()
            derived_confirmation_reads = False
        else:
            read_requests = first_proposal.read_requests
            derived_confirmation_reads = False
            if not read_requests:
                read_requests = derive_adjustment_reads(
                    current.state,
                    projection,
                    first_proposal,
                )
        read_requests = _authoritative_read_locales(
            read_requests,
            locale=projection.locale,
        )
        authenticated_phone_ready = (
            profile.phone_e164 is not None
            and profile.observed_at <= now < profile.expires_at
        )
        if not authenticated_phone_ready and read_requests:
            read_requests = tuple(
                item for item in read_requests if item.kind is ReadKind.KNOWLEDGE
            )
            derived_confirmation_reads = False
        if blocks_active_commercial_progression(current.state, first_proposal):
            read_requests = ()
            derived_confirmation_reads = False
            first_proposal = _active_execution_guard_proposal(
                first_proposal,
                locale=projection.locale,
            )
            first_audited = AuditedModelTurn.from_frames(
                proposal=first_proposal,
                frames=first_audited.frames,
                ephemeral_session_id=first_audited.closure.ephemeral_session_id,
            )
        pre_read_floor = now
        reused_consultation = False
        consultation_reuse_proposal: ModelProposal | None = None
        if (
            pending_action is None
            and not private_update_turn
            and bool(read_requests)
            and read_requests == first_proposal.read_requests
        ):
            reuse_now = self._clock.now()
            if (
                type(reuse_now) is not datetime
                or reuse_now.tzinfo is None
                or reuse_now.utcoffset() != timedelta(0)
                or reuse_now < pre_read_floor
            ):
                raise TurnExecutionError("consultation reuse clock is not monotonic UTC")
            if reuse_now > now + self._turn_timeout:
                raise TurnExecutionError("turn deadline expired before consultation reuse")
            pre_read_floor = reuse_now
            reused_consultation = reuses_fresh_consultation(
                first_proposal,
                consultation_history,
                now=reuse_now,
            )
        if reused_consultation:
            consultation_reuse_proposal = first_proposal
            read_requests = ()
            derived_confirmation_reads = False
            first_proposal = replace(
                first_proposal,
                read_requests=(),
                target_offer_id=None,
                target_offer_ids=(),
                selection_requested=False,
            )
            first_audited = AuditedModelTurn.from_frames(
                proposal=first_proposal,
                frames=first_audited.frames,
                ephemeral_session_id=first_audited.closure.ephemeral_session_id,
            )
        request_hashes = tuple(item.canonical_hash() for item in read_requests)
        if len(request_hashes) != len(set(request_hashes)):
            raise TurnExecutionError("model proposed duplicate reads")
        v2_observations = ()
        if read_requests:
            accepted_observations: list[ReadObservation] = []
            read_floor = pre_read_floor
            for item in read_requests:
                read_now = self._clock.now()
                if (
                    type(read_now) is not datetime
                    or read_now.tzinfo is None
                    or read_now.utcoffset() != timedelta(0)
                    or read_now < read_floor
                ):
                    raise TurnExecutionError("pre-read clock is not monotonic UTC")
                if derived_confirmation_reads:
                    read_scope_bound = (
                        self._reducer.confirmation_projection_matches(
                            current.state.workflow,
                            projection,
                        )
                        and self._reducer.confirmation_capability_available(
                            current.state.workflow,
                            now=read_now,
                        )
                    )
                    if not _critical_confirmation_bound(
                        pending_action,
                        first_proposal,
                        now=read_now,
                        material_scope_bound=read_scope_bound,
                    ):
                        read_requests = ()
                        accepted_observations.clear()
                        break
                observation = self._reads.read(item)
                observed_now = self._clock.now()
                if (
                    type(observed_now) is not datetime
                    or observed_now.tzinfo is None
                    or observed_now.utcoffset() != timedelta(0)
                    or observed_now < read_now
                ):
                    raise TurnExecutionError("read clock is not monotonic UTC")
                accepted_observations.append(
                    self._reads.accept(observation, now=observed_now)
                )
                read_floor = observed_now
            v2_observations = tuple(accepted_observations)
        if not read_requests and first_proposal.read_requests:
            first_proposal = replace(first_proposal, read_requests=())
        if read_requests or reused_consultation:
            followup = ModelRequest(
                request_id=_opaque("model-request", batch.batch_id, current.version, 2),
                lead_id=batch.lead_id,
                source_event_id=batch.batch_id,
                message=batch.combined_text,
                locale=projection.locale,
                state_version=current.version,
                observations=v2_observations,
                consultation_history=consultation_history,
                state_facts=_authoritative_language_facts(
                    _state_model_facts(projection),
                    projection.locale,
                ),
                private_customer_fact_names=_private_customer_fact_names(
                    projection,
                    private_facts=private_facts,
                    profile=profile,
                    now=now,
                ),
                passenger_manifest_status=_passenger_status(
                    projection,
                    first_proposal,
                ),
                critical_outcome=_critical_outcome(projection),
                pending_action=pending_action,
                private_profile_complete=effective_profile_complete,
                handoff_active=current.state.handoff is not None,
                active_execution_status=active_execution_status(current.state),
                recap_reuse_required=reused_consultation,
            )
            second_audited = self._model.complete_audited(followup)
            if type(second_audited) is not AuditedModelTurn:
                raise TypeError("model must return exact AuditedModelTurn")
            proposal = validate_productive_proposal(second_audited.proposal)
            if proposal.source_event_id != batch.batch_id:
                raise TurnExecutionError("model proposal source event diverged")
            if reused_consultation and (
                proposal.intent != "inform"
                or proposal.facts
                or proposal.read_requests
                or proposal.effect_proposals
                or proposal.target_offer_id is not None
                or proposal.target_offer_ids
                or proposal.selection_requested
                or proposal.passengers
            ):
                proposal = replace(
                    first_proposal,
                    reply_chunks=_consultation_reuse_fallback(projection.locale),
                )
            (
                second_private_facts,
                second_public_facts,
                second_invalid_private_facts,
                _second_phone_proposed,
            ) = _partition_private_customer_facts(proposal)
            second_public_facts = _authoritative_language_facts(
                second_public_facts,
                projection.locale,
            )
            if second_private_facts:
                private_facts = _persist_private_collection(
                    self._private_customer_facts,
                    lead_id=batch.lead_id,
                    source_turn_id=batch.batch_id,
                    source_event_hash=event_hash,
                    facts=second_private_facts,
                    persisted_at=now,
                )
                private_update_turn = private_update_turn or any(
                    item.name in _COMMAND_BLOCKING_PRIVATE_FACT_NAMES
                    for item in second_private_facts
                )
                effective_profile_complete = reservation_profile_ready(
                    profile,
                    projection,
                    now,
                    private_facts=private_facts,
                )
            proposal = replace(proposal, facts=second_public_facts)
            if second_invalid_private_facts:
                collection_only = True
                proposal = _collection_only_proposal(
                    proposal,
                    public_facts=second_public_facts,
                    locale=projection.locale,
                    invalid_fact_names=second_invalid_private_facts,
                    revoke_pending=pending_action is not None,
                )
            if blocks_active_commercial_progression(current.state, proposal):
                proposal = _active_execution_guard_proposal(
                    proposal,
                    locale=projection.locale,
                )
            second_audited = AuditedModelTurn.from_frames(
                proposal=proposal,
                frames=second_audited.frames,
                ephemeral_session_id=second_audited.closure.ephemeral_session_id,
            )
            second_frame_hash = _frame_commitments(second_audited)[-1].canonical_hash()
            projection = _merge_passenger_updates(
                projection,
                proposal,
                frame_commitment_hash=second_frame_hash,
            )
            proposal = preserve_initial_facts(first_proposal, proposal)
            if proposal.read_requests:
                raise TurnExecutionError("model exceeded the single read round")
            if (
                not collection_only
                and effective_profile_complete
                and _post_read_package_selection_review_required(
                    proposal,
                    v2_observations,
                )
            ):
                selection_review_request = replace(
                    followup,
                    request_id=_opaque(
                        "model-post-read-selection-review",
                        batch.batch_id,
                        current.version,
                    ),
                    selection_review_required=True,
                    passenger_manifest_status=_passenger_status(
                        projection,
                        proposal,
                    ),
                )
                selection_review_audited = self._model.complete_audited(
                    selection_review_request
                )
                if type(selection_review_audited) is not AuditedModelTurn:
                    raise TypeError("model must return exact AuditedModelTurn")
                selection_review_proposal = validate_productive_proposal(
                    selection_review_audited.proposal
                )
                if selection_review_proposal.source_event_id != batch.batch_id:
                    raise TurnExecutionError(
                        "post-read selection review source event diverged"
                    )
                if (
                    selection_review_proposal.intent
                    in ("inform", "select", "request_handoff")
                    and not selection_review_proposal.read_requests
                ):
                    proposal = replace(
                        selection_review_proposal,
                        facts=proposal.facts,
                        passengers=(),
                    )
                selection_review_audited = AuditedModelTurn.from_frames(
                    proposal=proposal,
                    frames=selection_review_audited.frames,
                    ephemeral_session_id=(
                        selection_review_audited.closure.ephemeral_session_id
                    ),
                )
                second_audited = AuditedModelTurn.combine(
                    (second_audited, selection_review_audited)
                )
            if not collection_only:
                proposal = _repair_requested_activity_selection(
                    first_proposal,
                    proposal,
                    state_facts=_authoritative_language_facts(
                        _state_model_facts(projection),
                        projection.locale,
                    ),
                    observations=v2_observations,
                    private_profile_complete=effective_profile_complete,
                    passenger_manifest_complete=_passenger_manifest_complete(
                        projection,
                        proposal,
                    ),
                )
                if derived_confirmation_reads and (
                    proposal.intent != "confirm"
                    or proposal.confirmed_summary_version
                    != first_proposal.confirmed_summary_version
                    or proposal.confirmed_action_kinds
                    != first_proposal.confirmed_action_kinds
                    or proposal.approval_basis is not first_proposal.approval_basis
                ):
                    proposal = replace(
                        first_proposal,
                        reply_chunks=proposal.reply_chunks,
                        read_requests=(),
                    )
                proposal = preserve_initial_adjustment(first_proposal, proposal)
            audited = AuditedModelTurn.combine((first_audited, second_audited))
        else:
            audited = first_audited
            proposal = first_proposal

        if private_update_turn and not collection_only:
            proposal = _private_update_no_command_proposal(
                proposal,
                pending_action=pending_action,
                locale=projection.locale,
            )
        if blocks_active_commercial_progression(current.state, proposal):
            proposal = _active_execution_guard_proposal(
                proposal,
                locale=projection.locale,
            )
        elif is_regressive_post_command_reply(
            current.state,
            proposal,
        ):
            proposal = replace(
                proposal,
                reply_chunks=execution_in_progress_reply(projection.locale),
            )
        proposal = apply_positive_grounding(
            proposal,
            v2_observations,
            locale=projection.locale,
            force=private_update_turn,
        )

        decision_now = self._clock.now()
        if (
            type(decision_now) is not datetime
            or decision_now.tzinfo is None
            or decision_now.utcoffset() != timedelta(0)
            or decision_now < now
        ):
            raise TurnExecutionError("decision clock is not monotonic UTC")
        if decision_now > now + self._turn_timeout:
            raise TurnExecutionError("turn deadline expired before decision")
        if reused_consultation and (
            consultation_reuse_proposal is None
            or not reuses_fresh_consultation(
                consultation_reuse_proposal,
                consultation_history,
                now=decision_now,
            )
        ):
            raise TurnExecutionError("consultation expired before recap decision")
        decision_private_facts = self._private_customer_facts.load(batch.lead_id)
        if type(decision_private_facts) is not PrivateCustomerFactSnapshot:
            raise TypeError("private customer owner must return an exact snapshot")
        if decision_private_facts.content_hash != private_facts.content_hash:
            raise TurnExecutionError("private customer facts changed during turn")
        private_facts = decision_private_facts
        profile_sensitive = (
            proposal.intent in {"select", "confirm"}
            or proposal.selection_requested
            or pending_action is not None
        )
        if profile_sensitive:
            decision_profile = self._profile.read(batch.lead_id, now=decision_now)
            if type(decision_profile) is not PrivateCustomerBinding:
                raise TypeError("profile port must return exact PrivateCustomerBinding")
            initial_profile_material = effective_customer_material_hash(
                profile,
                projection,
                decision_now,
                private_facts=private_facts,
            )
            decision_profile_material = effective_customer_material_hash(
                decision_profile,
                projection,
                decision_now,
                private_facts=private_facts,
            )
            if decision_profile_material != initial_profile_material:
                raise TurnExecutionError("private profile changed during turn")
            profile = decision_profile

        frames = _frame_commitments(audited)
        final_frame_hash = frames[-1].canonical_hash()
        fact_commitment_hash = final_frame_hash
        boundary_reads = tuple(
            bridge_availability_observation(
                read_request,
                observation,
                lead_id=batch.lead_id,
                aggregate_turn_id=batch.batch_id,
                source_event=sources[0],
                deadline_at=now + self._turn_timeout,
                locale=projection.locale,
                projection=projection,
                frame_commitment_hash=frames[0].canonical_hash(),
            )
            for read_request, observation in zip(read_requests, v2_observations)
            if read_request.kind in (ReadKind.LODGING, ReadKind.ACTIVITY)
        )
        try:
            decision = self._reducer.reduce(
                state=current.state,
                projection=projection,
                proposal=proposal,
                profile=profile,
                private_facts=private_facts,
                reads=v2_observations,
                fact_commitment_hash=fact_commitment_hash,
                now=decision_now,
            )
        except ConversationReductionError:
            if proposal.intent != "select":
                raise
            proposal = _selection_binding_failure_proposal(
                proposal,
                locale=projection.locale,
            )
            audited = AuditedModelTurn.from_frames(
                proposal=proposal,
                frames=audited.frames,
                ephemeral_session_id=audited.closure.ephemeral_session_id,
            )
            decision = self._reducer.reduce(
                state=current.state,
                projection=projection,
                proposal=proposal,
                profile=profile,
                private_facts=private_facts,
                reads=v2_observations,
                fact_commitment_hash=fact_commitment_hash,
                now=decision_now,
            )
        if not any(item.name == "language" for item in decision.projection.facts):
            language_fact = TypedFact(
                "language",
                StringSlot(projection.locale),
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
        manifest_fact = projection_manifest_fact(decision.projection)
        if manifest_fact is not None:
            self._private_customer_facts.persist_passenger_manifest(
                lead_id=batch.lead_id,
                source_turn_id=batch.batch_id,
                source_event_hash=event_hash,
                fact=manifest_fact,
                persisted_at=decision_now,
            )
        public_projection = replace(
            decision.projection,
            facts=_public_artifact_facts(decision.projection.facts),
        )
        execution_commands = _execution_commands(decision.commands)
        if private_update_turn and execution_commands:
            raise TurnExecutionError(
                "private customer update cannot authorize a reservation command"
            )
        commit = BoundaryCommit(decision.next_state, execution_commands, (), ())
        kernel_bytes, kernel_hash = kernel_decision_commitment(
            decision.next_state,
            execution_commands,
        )

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
        artifact_facts = _public_artifact_facts(public_projection.facts)
        maya = MayaTurnProposal.from_accepted_closure(
            accepted_closure=closure,
            read_observations=boundary_reads,
            facts=artifact_facts,
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
            now=decision_now,
        )
        if type(authority) is not PublicTurnAuthority:
            raise TypeError("authority port must return exact PublicTurnAuthority")
        if len(authority.allocation_ids) != len(chunks):
            raise TurnExecutionError(
                "public allocation count does not match reply chunks"
            )
        if authority.deadline_at <= decision_now:
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
                for item in artifact_facts
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
        if private_update_turn and (command_rows or command_relays):
            raise TurnExecutionError(
                "private customer update cannot persist reservation effects"
            )
        internal_jobs = _handoff_jobs(batch.batch_id, decision.handoff_request)
        commit_now = self._clock.now()
        if (
            type(commit_now) is not datetime
            or commit_now.tzinfo is None
            or commit_now.utcoffset() != timedelta(0)
            or commit_now < decision_now
        ):
            raise TurnExecutionError("commit clock is not monotonic UTC")
        if commit_now > now + self._turn_timeout:
            raise TurnExecutionError("turn deadline expired before commit")
        approval_deadline = self._reducer.approval_deadline(pending_action)
        if command_rows and (
            approval_deadline is None or commit_now >= approval_deadline
        ):
            raise TurnExecutionError("critical approval expired before commit")
        if command_rows and (
            not self._reducer.confirmation_projection_matches(
                current.state.workflow,
                projection,
            )
            or not self._reducer.confirmation_capability_available(
                current.state.workflow,
                now=commit_now,
            )
        ):
            raise TurnExecutionError("critical approval scope changed before commit")
        commit_private_facts = self._private_customer_facts.load(batch.lead_id)
        if type(commit_private_facts) is not PrivateCustomerFactSnapshot:
            raise TypeError("private customer owner must return an exact snapshot")
        if commit_private_facts.content_hash != private_facts.content_hash:
            raise TurnExecutionError("private customer facts changed before commit")
        private_profile_material_hash = None
        if command_rows:
            private_profile_material_hash = effective_customer_material_hash(
                profile,
                projection,
                commit_now,
                private_facts=commit_private_facts,
            )
            if private_profile_material_hash is None:
                raise TurnExecutionError("private profile changed before commit")
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
            behavior_state_snapshot_digest=public_projection.canonical_hash(),
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
                private_profile_material_hash=private_profile_material_hash,
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
