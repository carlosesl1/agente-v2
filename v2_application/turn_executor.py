"""Atomic Phase 8 turn executor for the standalone Agente V2 runtime."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
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
    BoundaryState,
    ConversationIntentKind,
    KernelDecision,
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
from v2_application.conversation import (
    V2ConversationReducer,
    reservation_profile_ready,
)
from v2_application.read_bridge import bridge_availability_observation
from v2_application.passengers import (
    PassengerManifestConflict,
    merge_projection_manifest,
    projection_manifest_party,
    projection_manifest_status,
)
from v2_application.private_customer_collection import (
    PrivateCustomerCollection,
    collect_private_customer_facts,
)
from v2_application.private_customer_facts import (
    PrivateCustomerFactSnapshot,
    PrivateCustomerFactWriteResult,
    canonical_country_code,
    canonical_email,
    canonical_full_name,
)
from v2_application.reads import V2ReadService
from v2_application.relay_worker import (
    build_handoff_relay_bundle,
    build_reservation_relay_bundle,
)
from v2_application.reservations import ReservationAllocator
from v2_application.turns import validate_productive_proposal
from v2_contracts.channel import InboundBatch
from v2_contracts.critical_actions import ApprovalBasis, PendingCriticalActionContext
from v2_contracts.model import (
    AuditedModelTurn,
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


_EN_MONTHS: Final = {
    name: index
    for index, name in enumerate(
        (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ),
        start=1,
    )
}
_BIRTH_DMY_RE: Final = re.compile(
    r"\b(?:nasci\s+em|(?:minha\s+)?data\s+de\s+nascimento\s*(?:é|e|is|:)?|"
    r"i\s+was\s+born\s+(?:on\s+)?|(?:my\s+)?(?:date\s+of\s+birth|birth\s+date)\s*(?:is|:)?)"
    r"\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
    re.IGNORECASE,
)
_BIRTH_ISO_RE: Final = re.compile(
    r"\b(?:nasci\s+em|(?:minha\s+)?data\s+de\s+nascimento\s*(?:é|e|is|:)?|"
    r"i\s+was\s+born\s+(?:on\s+)?|(?:my\s+)?(?:date\s+of\s+birth|birth\s+date)\s*(?:is|:)?)"
    r"\s*(\d{4})-(\d{2})-(\d{2})\b",
    re.IGNORECASE,
)
_BIRTH_EN_MONTH_RE: Final = re.compile(
    r"\b(?:i\s+was\s+born\s+(?:on\s+)?|"
    r"(?:my\s+)?(?:date\s+of\s+birth|birth\s+date)\s*(?:is|:)?)"
    r"\s*(\d{1,2})\s+"
    r"(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+(\d{4})\b",
    re.IGNORECASE,
)
_GENDER_EN_RE: Final = re.compile(
    r"\b(?:i\s+am|i['’]m|(?:my\s+)?gender\s*(?:is|:)?)\s+(female|male)\b",
    re.IGNORECASE,
)
_GENDER_PT_RE: Final = re.compile(
    r"\b(?:sou|(?:meu\s+)?g[eê]nero(?:\s+cadastral)?\s*(?:é|e|:)?)\s+"
    r"(mulher|homem|feminino|masculino)\b",
    re.IGNORECASE,
)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_explicit_customer_facts(message: str) -> tuple[ModelFact, ...]:
    if type(message) is not str or not message:
        raise ValueError("message must be non-empty exact text")
    birth_dates: set[date] = set()
    for match in _BIRTH_DMY_RE.finditer(message):
        parsed = _safe_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        if parsed is not None:
            birth_dates.add(parsed)
    for match in _BIRTH_ISO_RE.finditer(message):
        parsed = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if parsed is not None:
            birth_dates.add(parsed)
    for match in _BIRTH_EN_MONTH_RE.finditer(message):
        parsed = _safe_date(
            int(match.group(3)),
            _EN_MONTHS[match.group(2).casefold()],
            int(match.group(1)),
        )
        if parsed is not None:
            birth_dates.add(parsed)

    genders: set[str] = set()
    for match in _GENDER_EN_RE.finditer(message):
        genders.add("f" if match.group(1).casefold() == "female" else "m")
    for match in _GENDER_PT_RE.finditer(message):
        genders.add(
            "f"
            if match.group(1).casefold() in {"mulher", "feminino"}
            else "m"
        )

    facts: list[ModelFact] = []
    if len(birth_dates) == 1:
        facts.append(ModelFact("birth_date", next(iter(birth_dates))))
    if len(genders) == 1:
        facts.append(ModelFact("gender", next(iter(genders))))
    return tuple(facts)


_COMMERCIAL_CATALOG_PATH: Final = (
    Path(__file__).resolve().parents[1] / "config" / "v2_public_commercial_catalog.json"
)
_EN_ACTIVITY_DATE_RE: Final = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_PT_ACTIVITY_DATE_RE: Final = re.compile(
    r"\b(\d{1,2})\s+de\s+"
    r"(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
    r"\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)
_PT_MONTHS: Final = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}


def _fold_public_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_like = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_like).split())


@lru_cache(maxsize=1)
def _catalog_aliases() -> tuple[tuple[str, str], ...]:
    payload = json.loads(_COMMERCIAL_CATALOG_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != "v2-public-commercial-catalog-v1":
        raise TurnExecutionError("public commercial catalog schema drifted")
    aliases: dict[str, str] = {}
    for product in payload.get("products", ()):
        canonical_id = product.get("canonical_id")
        candidates = (product.get("public_name"), *(product.get("aliases") or ()))
        if type(canonical_id) is not str:
            raise TurnExecutionError("public commercial catalog product is malformed")
        for candidate in candidates:
            if type(candidate) is not str:
                raise TurnExecutionError("public commercial catalog alias is malformed")
            folded = _fold_public_text(candidate)
            previous = aliases.get(folded)
            if previous is not None and previous != canonical_id:
                raise TurnExecutionError("public commercial catalog alias is ambiguous")
            aliases[folded] = canonical_id
    return tuple(sorted(aliases.items(), key=lambda item: (-len(item[0]), item[0])))


def _extract_explicit_commercial_facts(message: str) -> tuple[ModelFact, ...]:
    if type(message) is not str or not message:
        raise ValueError("message must be non-empty exact text")
    folded = _fold_public_text(message)
    padded = f" {folded} "
    payment_methods: set[str] = set()
    payment_patterns = {
        "stripe": (
            r"\bi choose card\b",
            r"\bi will use card\b",
            r"\bill use card\b",
            r"\b(?:eu )?vou usar cartao\b",
            r"\b(?:eu )?escolho cartao\b",
        ),
        "wise": (
            r"\bi choose wise\b",
            r"\bi will use wise\b",
            r"\bill use wise\b",
            r"\b(?:eu )?vou usar wise\b",
            r"\b(?:eu )?escolho wise\b",
        ),
        "pix": (
            r"\bi choose pix\b",
            r"\bi will use pix\b",
            r"\bill use pix\b",
            r"\b(?:eu )?vou usar pix\b",
            r"\b(?:eu )?escolho pix\b",
        ),
    }
    for method, patterns in payment_patterns.items():
        if any(re.search(pattern, folded) is not None for pattern in patterns):
            payment_methods.add(method)
    facts: list[ModelFact] = []
    if len(payment_methods) == 1:
        facts.append(ModelFact("payment_method", next(iter(payment_methods))))
    products = {
        canonical_id
        for alias, canonical_id in _catalog_aliases()
        if f" {alias} " in padded
    }
    if len(products) != 1:
        return tuple(facts)
    product_id = next(iter(products))

    activity_dates: set[date] = set()
    for match in _EN_ACTIVITY_DATE_RE.finditer(message):
        parsed = _safe_date(
            int(match.group(3)),
            _EN_MONTHS[match.group(1).casefold()],
            int(match.group(2)),
        )
        if parsed is not None:
            activity_dates.add(parsed)
    for match in _PT_ACTIVITY_DATE_RE.finditer(message):
        month = _fold_public_text(match.group(2))
        parsed = _safe_date(int(match.group(3)), _PT_MONTHS[month], int(match.group(1)))
        if parsed is not None:
            activity_dates.add(parsed)
    for match in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", message):
        parsed = _safe_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        if parsed is not None:
            activity_dates.add(parsed)
    activity_dates.difference_update(
        fact.value
        for fact in _extract_explicit_customer_facts(message)
        if fact.name == "birth_date" and type(fact.value) is date
    )

    number_words = {
        "zero": 0,
        "one": 1,
        "um": 1,
        "uma": 1,
        "dois": 2,
        "duas": 2,
    }
    adults: set[int] = set()
    for pattern in (
        r"\bfor\s+(one|\d+)\s+(?:adult|person|participant)s?\b",
        r"\bpara\s+(um|uma|dois|duas|\d+)\s+(?:adulto|adulta|pessoa|participante)s?\b",
    ):
        for match in re.finditer(pattern, folded):
            token = match.group(1)
            adults.add(number_words.get(token, int(token) if token.isdigit() else 0))
    if re.search(r"\b(?:so eu|just me)\b", folded):
        adults.add(1)
    adults.discard(0)

    children: set[int] = set()
    for pattern in (
        r"\b(?:adults|people|participants)\s+(?:and\s+|with\s+)?"
        r"(zero|one|\d+)\s+(?:child|children)\b",
        r"\b(?:adultos|adultas|pessoas|participantes)\s+(?:e\s+|com\s+)?"
        r"(zero|um|uma|\d+)\s+criancas?\b",
    ):
        for match in re.finditer(pattern, folded):
            token = match.group(1)
            children.add(number_words.get(token, int(token) if token.isdigit() else 0))

    english_markers = len(
        re.findall(r"\b(?:i|please|tour|booking|what|can|adult|person)\b", folded)
    )
    portuguese_markers = len(
        re.findall(
            r"\b(?:quero|reservar|roteiro|passeio|pagamento|adultos?|criancas?|"
            r"passageiros?|disponibilidade|confirmar)\b",
            folded,
        )
    )
    if english_markers >= 2 and english_markers > portuguese_markers:
        facts.append(ModelFact("language", "en"))
    elif portuguese_markers >= 2 and portuguese_markers > english_markers:
        facts.append(ModelFact("language", "pt-BR"))
    facts.extend(
        (
            ModelFact("service", "agency"),
            ModelFact("product_id", product_id),
        )
    )
    if len(activity_dates) == 1:
        facts.append(ModelFact("activity_date", next(iter(activity_dates))))
    if len(adults) == 1 and len(children) <= 1:
        child_count = next(iter(children)) if children else 0
        facts.extend(
            (
                ModelFact("adults", next(iter(adults))),
                ModelFact("children", child_count),
            )
        )
    return tuple(facts)


def _merge_explicit_customer_facts(
    proposal: ModelProposal,
    explicit_facts: tuple[ModelFact, ...],
) -> ModelProposal:
    if type(proposal) is not ModelProposal:
        raise TypeError("proposal must be an exact ModelProposal")
    existing = {item.name: item for item in proposal.facts}
    additions: list[ModelFact] = []
    for fact in explicit_facts:
        current = existing.get(fact.name)
        if (
            current is not None
            and fact.name == "language"
            and type(current.value) is str
            and type(fact.value) is str
            and current.value.casefold().split("-", 1)[0]
            == fact.value.casefold().split("-", 1)[0]
        ):
            continue
        if current is not None and current.value != fact.value:
            raise TurnExecutionError(
                f"model fact {fact.name} conflicts with explicit customer fact"
            )
        if current is None:
            additions.append(fact)
    return replace(proposal, facts=(*proposal.facts, *additions))


def _explicit_summary_preparation_requested(message: str) -> bool:
    if type(message) is not str or not message:
        raise ValueError("message must be non-empty exact text")
    folded = _fold_public_text(message)
    if any(
        phrase in folded
        for phrase in (
            "do not prepare the final booking summary",
            "dont prepare the final booking summary",
            "do not prepare the booking summary",
            "dont prepare the booking summary",
            "nao prepare o resumo final",
            "nao preparar o resumo final",
        )
    ):
        return False
    return any(
        phrase in folded
        for phrase in (
            "prepare the final booking summary",
            "prepare the booking summary",
            "prepare o resumo final",
            "preparar o resumo final",
        )
    )


def _structured_selection_review_required(
    state_facts: tuple[ModelFact, ...],
    explicit_facts: tuple[ModelFact, ...],
    *,
    private_profile_complete: bool,
    passenger_manifest_complete: bool = False,
) -> bool:
    """Gate one semantic selection review from closed structured facts only."""

    if (
        type(state_facts) is not tuple
        or any(type(item) is not ModelFact for item in state_facts)
        or type(explicit_facts) is not tuple
        or any(type(item) is not ModelFact for item in explicit_facts)
        or type(private_profile_complete) is not bool
        or type(passenger_manifest_complete) is not bool
    ):
        raise TypeError("selection review gate requires exact V2 contracts")
    if not private_profile_complete or not any(
        fact.name == "payment_method" for fact in explicit_facts
    ):
        return False
    values: dict[str, str | int | date] = {}
    for fact in (*state_facts, *explicit_facts):
        current = values.get(fact.name)
        if current is not None and current != fact.value:
            return False
        values[fact.name] = fact.value
    adults = values.get("adults")
    children = values.get("children", 0)
    if type(adults) is not int or type(children) is not int:
        return False
    passenger_ready = (
        type(values.get("birth_date")) is date
        and values.get("gender") in {"m", "f"}
        if adults + children == 1
        else passenger_manifest_complete
    )
    return (
        values.get("service") == "agency"
        and type(values.get("product_id")) is str
        and type(values.get("activity_date") or values.get("start_date")) is date
        and adults >= 1
        and children >= 0
        and values.get("payment_method") in {"stripe", "wise", "pix"}
        and passenger_ready
    )


def _force_structured_activity_summary_preparation(
    proposal: ModelProposal,
    *,
    state_facts: tuple[ModelFact, ...],
    explicit_facts: tuple[ModelFact, ...],
    passenger_manifest_complete: bool = False,
) -> ModelProposal:
    """Prepare one provider read; this cannot authorize or emit an effect."""

    if type(proposal) is not ModelProposal:
        raise TypeError("proposal must be an exact ModelProposal")
    if not _structured_selection_review_required(
        state_facts,
        explicit_facts,
        private_profile_complete=True,
        passenger_manifest_complete=passenger_manifest_complete,
    ):
        return proposal
    values = {fact.name: fact.value for fact in (*state_facts, *explicit_facts)}
    activity_date = values.get("activity_date") or values.get("start_date")
    adults = values["adults"]
    children = values.get("children", 0)
    return replace(
        proposal,
        intent="inform",
        read_requests=(
            ReadRequest(
                request_id=f"{proposal.source_event_id}:read:activity",
                kind=ReadKind.ACTIVITY,
                product_id=values["product_id"],
                activity_date=activity_date,
                adults=adults,
                children=children,
            ),
        ),
        target_offer_id=None,
        target_offer_ids=(),
        selection_requested=True,
        pending_disposition=None,
    )


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


def _explicit_customer_fact_commitment(
    frame_hash: str,
    event_hash: str,
    explicit_facts: tuple[ModelFact, ...],
) -> str:
    if not explicit_facts:
        return frame_hash
    for name, value in (("frame_hash", frame_hash), ("event_hash", event_hash)):
        if type(value) is not str or _HASH_RE.fullmatch(value) is None:
            raise ValueError(f"{name} must be a lowercase SHA-256")
    return _domain_hash(
        "v2-explicit-customer-facts-v1",
        _canonical(
            "v2-explicit-customer-facts",
            {
                "frame_hash": frame_hash,
                "event_hash": event_hash,
                "facts": [
                    {
                        "name": fact.name,
                        "value": (
                            fact.value.isoformat()
                            if type(fact.value) is date
                            else fact.value
                        ),
                    }
                    for fact in explicit_facts
                ],
            },
        ),
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
        if item.name not in ("critical_outcome", "passenger_manifest")
        and item.name not in PRIVATE_CUSTOMER_FACT_ORDER
    )


def _private_customer_fact_names(
    projection: ConversationProjection,
    *,
    private_facts: PrivateCustomerFactSnapshot | None = None,
    profile: PrivateCustomerBinding | None = None,
) -> tuple[str, ...]:
    present = {item.name for item in projection.facts}
    if private_facts is not None:
        if type(private_facts) is not PrivateCustomerFactSnapshot:
            raise TypeError("private_facts must be exact or None")
        present.update(private_facts.present_fact_names)
    if profile is not None:
        if type(profile) is not PrivateCustomerBinding:
            raise TypeError("profile must be exact or None")
        present.update(
            name
            for name, value in (
                ("full_name", profile.full_name),
                ("email", profile.email),
                ("phone_e164", profile.phone_e164),
                ("country_code", profile.country_code),
            )
            if value is not None
        )
    return tuple(name for name in PRIVATE_CUSTOMER_FACT_ORDER if name in present)


def _expected_private_customer_fact_names(
    profile: PrivateCustomerBinding,
    private_facts: PrivateCustomerFactSnapshot,
) -> tuple[str, ...]:
    full_name_missing = (
        private_facts.full_name is None
        and (profile.full_name is None or len(profile.full_name.split()) < 2)
    )
    email_missing = private_facts.email is None and profile.email is None
    country_missing = (
        private_facts.country_code is None and profile.country_code is None
    )
    return tuple(
        name
        for name, missing in (
            ("full_name", full_name_missing),
            ("email", email_missing),
            ("country_code", country_missing),
        )
        if missing
    )


_PRIVATE_CONVERSATION_FALLBACK_NAMES: Final = frozenset(
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


def _collection_only_proposal(
    proposal: ModelProposal,
    *,
    public_facts: tuple[ModelFact, ...],
    locale: str,
    invalid_fact_names: tuple[str, ...] = (),
) -> ModelProposal:
    return ModelProposal(
        source_event_id=proposal.source_event_id,
        intent="inform",
        reply_chunks=(
            _collection_reply(locale, invalid_fact_names=invalid_fact_names),
        ),
        facts=public_facts,
        read_requests=(),
        effect_proposals=(),
        passengers=(),
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
    if proposal.intent == "adjust":
        return False
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
        for method in ("load", "persist_turn", "turn_supplied_fact_names"):
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
        private_facts = self._private_customer_facts.load(batch.lead_id)
        if type(private_facts) is not PrivateCustomerFactSnapshot:
            raise TypeError("private customer owner must return an exact snapshot")
        collection_turn_fact_names = (
            self._private_customer_facts.turn_supplied_fact_names(
                batch.lead_id,
                batch.batch_id,
            )
        )
        private_collection = collect_private_customer_facts(
            batch.combined_text,
            expected_fact_names=_expected_private_customer_fact_names(
                profile,
                private_facts,
            ),
        )
        if type(private_collection) is not PrivateCustomerCollection:
            raise TypeError("private customer collector returned an invalid result")
        if private_collection.facts:
            private_facts = _persist_private_collection(
                self._private_customer_facts,
                lead_id=batch.lead_id,
                source_turn_id=batch.batch_id,
                source_event_hash=event_hash,
                facts=private_collection.facts,
                persisted_at=now,
            )
            collection_turn_fact_names = tuple(
                item.name for item in private_collection.facts
            )
        collection_only = bool(
            collection_turn_fact_names or private_collection.invalid_fact_names
        )
        explicit_customer_facts = (
            *_extract_explicit_commercial_facts(batch.combined_text),
            *_extract_explicit_customer_facts(batch.combined_text),
        )
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
            message=private_collection.sanitized_message,
            locale=projection.locale,
            state_version=current.version,
            state_facts=_state_model_facts(projection),
            private_customer_fact_names=_private_customer_fact_names(
                projection,
                private_facts=private_facts,
                profile=profile,
            ),
            passenger_manifest_status=_passenger_status(projection),
            critical_outcome=_critical_outcome(projection),
            pending_action=pending_action,
            private_profile_complete=effective_profile_complete,
            handoff_active=current.state.handoff is not None,
        )
        first_audited = self._model.complete_audited(request)
        if type(first_audited) is not AuditedModelTurn:
            raise TypeError("model must return exact AuditedModelTurn")
        first_proposal = _merge_explicit_customer_facts(
            validate_productive_proposal(first_audited.proposal),
            explicit_customer_facts,
        )
        if first_proposal.source_event_id != batch.batch_id:
            raise TurnExecutionError("model proposal source event diverged")
        (
            first_private_facts,
            first_public_facts,
            first_invalid_private_facts,
            first_phone_proposed,
        ) = _partition_private_customer_facts(first_proposal)
        if first_private_facts and not collection_only:
            private_facts = _persist_private_collection(
                self._private_customer_facts,
                lead_id=batch.lead_id,
                source_turn_id=batch.batch_id,
                source_event_hash=event_hash,
                facts=first_private_facts,
                persisted_at=now,
            )
            effective_profile_complete = reservation_profile_ready(
                profile,
                projection,
                now,
                private_facts=private_facts,
            )
        if (
            first_private_facts
            or first_invalid_private_facts
            or first_phone_proposed
        ):
            collection_only = True
        collection_invalid_fact_names = tuple(
            name
            for name in ("full_name", "email", "country_code")
            if name in private_collection.invalid_fact_names
            or name in first_invalid_private_facts
        )
        if collection_only:
            first_proposal = _collection_only_proposal(
                first_proposal,
                public_facts=first_public_facts,
                locale=projection.locale,
                invalid_fact_names=collection_invalid_fact_names,
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
            and pending_action is None
            and first_proposal.intent == "inform"
            and not first_proposal.read_requests
            and _structured_selection_review_required(
                _state_model_facts(projection),
                explicit_customer_facts,
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
                passenger_manifest_status=_passenger_status(
                    projection,
                    first_proposal,
                ),
            )
            review_audited = self._model.complete_audited(review_request)
            if type(review_audited) is not AuditedModelTurn:
                raise TypeError("model must return exact AuditedModelTurn")
            review_proposal = _merge_explicit_customer_facts(
                validate_productive_proposal(review_audited.proposal),
                explicit_customer_facts,
            )
            if review_proposal.source_event_id != batch.batch_id:
                raise TurnExecutionError("semantic review source event diverged")
            (
                review_private_facts,
                review_public_facts,
                review_invalid_private_facts,
                review_phone_proposed,
            ) = _partition_private_customer_facts(review_proposal)
            if (
                review_private_facts
                or review_invalid_private_facts
                or review_phone_proposed
            ):
                if review_private_facts:
                    private_facts = _persist_private_collection(
                        self._private_customer_facts,
                        lead_id=batch.lead_id,
                        source_turn_id=batch.batch_id,
                        source_event_hash=event_hash,
                        facts=review_private_facts,
                        persisted_at=now,
                    )
                    effective_profile_complete = reservation_profile_ready(
                        profile,
                        projection,
                        now,
                        private_facts=private_facts,
                    )
                collection_only = True
                first_proposal = _collection_only_proposal(
                    review_proposal,
                    public_facts=review_public_facts,
                    locale=projection.locale,
                    invalid_fact_names=review_invalid_private_facts,
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
            first_audited = AuditedModelTurn.from_frames(
                proposal=first_proposal,
                frames=(*first_audited.frames, *review_audited.frames),
                ephemeral_session_id=review_audited.closure.ephemeral_session_id,
            )
        if (
            not collection_only
            and selection_review
            and not first_proposal.selection_requested
            and _explicit_summary_preparation_requested(batch.combined_text)
        ):
            first_proposal = _force_structured_activity_summary_preparation(
                first_proposal,
                state_facts=_state_model_facts(projection),
                explicit_facts=explicit_customer_facts,
                passenger_manifest_complete=_passenger_manifest_complete(
                    projection,
                    first_proposal,
                ),
            )
            first_audited = AuditedModelTurn.from_frames(
                proposal=first_proposal,
                frames=first_audited.frames,
                ephemeral_session_id=first_audited.closure.ephemeral_session_id,
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
        request_hashes = tuple(item.canonical_hash() for item in read_requests)
        if len(request_hashes) != len(set(request_hashes)):
            raise TurnExecutionError("model proposed duplicate reads")
        v2_observations = ()
        if read_requests:
            accepted_observations: list[ReadObservation] = []
            read_floor = now
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
        if read_requests:
            followup = ModelRequest(
                request_id=_opaque("model-request", batch.batch_id, current.version, 2),
                lead_id=batch.lead_id,
                source_event_id=batch.batch_id,
                message=private_collection.sanitized_message,
                locale=projection.locale,
                state_version=current.version,
                observations=v2_observations,
                state_facts=_state_model_facts(projection),
                private_customer_fact_names=_private_customer_fact_names(
                projection,
                private_facts=private_facts,
                profile=profile,
            ),
                passenger_manifest_status=_passenger_status(
                    projection,
                    first_proposal,
                ),
                critical_outcome=_critical_outcome(projection),
                pending_action=pending_action,
                private_profile_complete=effective_profile_complete,
                handoff_active=current.state.handoff is not None,
            )
            second_audited = self._model.complete_audited(followup)
            if type(second_audited) is not AuditedModelTurn:
                raise TypeError("model must return exact AuditedModelTurn")
            proposal = _merge_explicit_customer_facts(
                validate_productive_proposal(second_audited.proposal),
                explicit_customer_facts,
            )
            if proposal.source_event_id != batch.batch_id:
                raise TurnExecutionError("model proposal source event diverged")
            (
                second_private_facts,
                second_public_facts,
                second_invalid_private_facts,
                second_phone_proposed,
            ) = _partition_private_customer_facts(proposal)
            if (
                second_private_facts
                or second_invalid_private_facts
                or second_phone_proposed
            ):
                if second_private_facts:
                    private_facts = _persist_private_collection(
                        self._private_customer_facts,
                        lead_id=batch.lead_id,
                        source_turn_id=batch.batch_id,
                        source_event_hash=event_hash,
                        facts=second_private_facts,
                        persisted_at=now,
                    )
                    effective_profile_complete = reservation_profile_ready(
                        profile,
                        projection,
                        now,
                        private_facts=private_facts,
                    )
                collection_only = True
                proposal = _collection_only_proposal(
                    proposal,
                    public_facts=second_public_facts,
                    locale=projection.locale,
                    invalid_fact_names=second_invalid_private_facts,
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
            second_fact_names = {item.name for item in proposal.facts}
            proposal = replace(
                proposal,
                facts=(
                    *proposal.facts,
                    *(
                        item
                        for item in first_proposal.facts
                        if item.name not in second_fact_names
                    ),
                ),
            )
            if proposal.read_requests:
                raise TurnExecutionError("model exceeded the single read round")
            if not collection_only:
                proposal = _repair_requested_activity_selection(
                    first_proposal,
                    proposal,
                    state_facts=_state_model_facts(projection),
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
            audited = AuditedModelTurn.combine((first_audited, second_audited))
        else:
            audited = first_audited
            proposal = first_proposal

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
        decision_private_facts = self._private_customer_facts.load(batch.lead_id)
        if type(decision_private_facts) is not PrivateCustomerFactSnapshot:
            raise TypeError("private customer owner must return an exact snapshot")
        if decision_private_facts.content_hash != private_facts.content_hash:
            raise TurnExecutionError("private customer facts changed during turn")
        private_facts = decision_private_facts

        frames = _frame_commitments(audited)
        final_frame_hash = frames[-1].canonical_hash()
        fact_commitment_hash = _explicit_customer_fact_commitment(
            final_frame_hash,
            event_hash,
            explicit_customer_facts,
        )
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
