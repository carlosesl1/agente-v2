"""Tool-free Hermes child-process adapter for Maya V2."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Final
from zoneinfo import ZoneInfo

from v2_adapters.execution_context import component_wire
from v2_contracts.critical_actions import ApprovalBasis, CriticalActionKind
from v2_contracts.model import (
    AuditedModelTurn,
    AuditedTranscriptFrame,
    EffectProposal,
    InvalidModelProposal,
    ModelFact,
    ModelProposal,
    ModelRequest,
)
from v2_contracts.model_wire import (
    CHILD_INPUT_REJECTED_EXIT,
    CHILD_INVALID_RESPONSE_EXIT,
    V8_RESPONSE_FIELDS,
    ChildExecutionFailed,
    ChildInputRejected,
)
from v2_contracts.passengers import PassengerInput
from v2_contracts.providers import ReadKind, ReadRequest

_RESULT_MARKER: Final = b"PHASE8_RESULT\x00"
_CHILD_ENV_ALLOWLIST: Final = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "HOME",
        "HERMES_HOME",
        "HERMES_MODEL",
        "HERMES_PROFILE",
        "HERMES_PROVIDER",
        "LANG",
        "LC_ALL",
        "NOUS_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "XAI_API_KEY",
        "XDG_CONFIG_HOME",
    }
)
_RESPONSE_FIELDS_V1: Final = frozenset(
    (
        "schema",
        "source_event_id",
        "intent",
        "reply_chunks",
        "facts",
        "read_requests",
        "effect_proposals",
        "target_offer_id",
        "confirmed_summary_version",
    )
)
_RESPONSE_FIELDS_V2: Final = frozenset((*_RESPONSE_FIELDS_V1, "target_offer_ids"))
_RESPONSE_FIELDS_V3: Final = frozenset(
    (*_RESPONSE_FIELDS_V2, "confirmed_action_kinds", "approval_basis")
)
_RESPONSE_FIELDS_V4: Final = frozenset((*_RESPONSE_FIELDS_V3, "selection_requested"))
_RESPONSE_FIELDS_V5: Final = frozenset((*_RESPONSE_FIELDS_V4, "pending_disposition"))
_RESPONSE_FIELDS_V6: Final = frozenset((*_RESPONSE_FIELDS_V5, "passengers"))
_RESPONSE_FIELDS_V7: Final = frozenset(
    (*_RESPONSE_FIELDS_V6, "clarification_question")
)
_PROTOCOL_REPAIR_SUFFIX: Final = """

PROTOCOL REPAIR: the previous child response was rejected by the closed parser.
Return exactly one V8 object with only intent, reply_chunks, facts, read_requests,
selected_choice_refs, selection_requested, pending_action_disposition, and passengers.
Each reply_chunks item contains text and expects_reply. Write each public message once;
mark at most the final chunk as expects_reply=true. Do not add tools, effects, mechanical
IDs, or facts not justified by the original request and observations.
For passenger updates or selection, supply the service scope in facts if absent from
state_facts, using the original conversation. Reuse a known scope unless the customer
changes it. Do not invent scope or ask the customer to repeat an already clear choice.
When observations are present in the request, use them and return read_requests as an
empty list; the parent permits only one provider-read round per turn. selection_requested
must be false by default; it may be true only on an inform proposal with a fresh read when
the current message unambiguously asks to prepare or reserve the current option. It is
false for questions, hypotheticals, uncertainty or informational availability checks.
pending_action_disposition must be null except for adjust: preserve keeps an unchanged
pending summary during questions or recap requests; revoke is for refusal or material
change. If exactly one answer is needed, write that question once in the final reply chunk
and mark expects_reply=true.
When pending_action is present, classify the latest message in relation to that exact
public summary. Uma confirmação semântica curta como “Sim”, “Pode reservar”,
“Confirmado” ou “Isso mesmo” pode usar intent=confirm; the parent binds the exact pending
authority. Sem pending_action, ou diante de dúvida, pergunta, recusa ou mudança material,
não use intent=confirm.
""".strip()


_PRIVATE_PROFILE_SYSTEM_SUFFIX: Final = """
SERVICE CUSTOMER CONTEXT:
- state_facts contains known values, including the reservation holder and contact data.
  passengers contains the known people with stable positions within this party and an
  explicit is_holder role. Reuse these values and the dialogue; do not collect them again.
- You interpret who each fact belongs to. Never assume that equal names or position 1
  identify the holder. When the customer designates an existing passenger as holder,
  emit a passenger update at that position with is_holder=true; do not copy the person's
  values to separate holder facts. Other fields may be null for a role-only update.
  is_holder=null preserves the role; false detaches that passenger. Only one holder.
- Correct linked person data via passengers at the same position. Use standalone holder
  facts when no passenger is linked. Contact email and phone_e164 are reusable booking
  contact values; a conversational phone never changes the ManyChat recipient identity.
- Use all known conversation data, not just the latest message. Current explicit corrections
  override older values. Never infer nationality from phone or language. If attribution is
  genuinely ambiguous, ask naturally rather than assigning a person. This does not block
  an independent availability query.
- A material correction is not confirmation of an older summary: use adjust/revoke and
  obtain a fresh summary before executing. Interpret facts and read requests together.
- Lead-provided data can be repeated naturally. Reply to the customer's question even when
  updating facts. Do not expose internal protocol vocabulary to the customer.
""".strip()


_COMMERCIAL_PROGRESSION_SYSTEM_SUFFIX: Final = """
CURRENT-TURN COMMERCIAL PROGRESSION:
- The request contains the complete original customer message. When that message already
  provides the service, date or period, and party needed for an availability or price
  check, emit the corresponding typed facts and read_requests in this same frame.
- Emit the service scope from the full conversation as a typed service fact:
  service=hostel for lodging (including private rooms), service=agency for activities,
  service=package when both are in scope. This records interest, not booking permission.
  Preserve a known service in state_facts unless the customer changes the scope; checking
  one component of a package does not drop the other. Product IDs and tool reads do not
  populate this fact. Do not invent a scope when the conversation is genuinely ambiguous.
- Before emitting passengers or requesting/selecting an option, ensure that the service
  is supplied in facts or already known in state_facts. Passenger updates require an
  activity scope (agency or package); lodging holder data goes in standalone facts.
  Recover a missing scope from the original dialogue, not by asking the customer to
  repeat an already clear choice. If genuinely unclear, ask naturally without selecting
  or emitting an unbound passenger update.
- You are the sole semantic owner of facts and informational read intent. The parent does
  not parse customer language, inject facts, or infer informational reads from keywords.
- Never reply that you are ready to check, will check later, or need the customer to send
  another booking message when the current message already has the required query data.
- "one adult" or "1 adulto" with no other traveler mentioned means adults=1 and
  children=0 for this read; do not ask a redundant children question.
- For one activity participant, including service=package, emit explicit birth_date and
  gender from the current message as typed facts and keep passengers empty. Do this in the
  same frame even when the message also asks to keep the package or prepare its summary.
- This progression authorizes only read_requests. It never authorizes a reservation,
  payment, handoff, delivery, or effect.
- For a package, resolve the customer's lodging and activity references semantically
  against current observations. When both are unambiguous and all selection requirements
  are complete, select exactly the two matching public choice_ref values atomically.
- After a provider read, select only the public choices explicitly justified by the
  complete customer message and current observations; otherwise remain inform.

PROGRESSIVE HANDOFF TRIAGE:
- An explicit human request, sensitive complaint, or real safety concern requires immediate
  request_handoff with no collection question in that turn.
- Discount, coupon, negotiation, and operational consultation/reservation/payment difficulty
  require human help only after useful safe triage. While commercial facts are still missing,
  continue collecting the next useful safe commercial facts instead of request_handoff.
- Before selection, collect service, product or preferences, dates, adults, and children.
  After selection, collect the contact, holder, passenger, and payment fields already required
  by the existing reservation contract. Never add a new field, invent a provider result, or
  promise a negotiated value.
- Once progressive handoff is appropriate, request_handoff is terminal for that turn and must
  not be combined with another read, collection question, selection, or confirmation.
- An uncertain reservation outcome must remain uncertain. A payment problem must never repeat
  or recreate an existing reservation.
""".strip()


_TURN_COMPLETION_SYSTEM_SUFFIX: Final = """
FINAL TURN COMPLETION RULES (highest salience):
- recent committed dialogue, when present, is private context for continuity. Use it to
  resolve references, the last open question, and what the customer is continuing now.
- Never route by a word, substring, regex, alias list, or fixed phrase. Interpret the
  complete current message semantically against dialogue, state facts and observations.
- If the current message already completes a safe informational query, emit all typed facts
  and the required read_requests now. Do not answer with readiness or ask for the same data.
- If exactly one customer answer is missing, write that exact customer-facing question once
  in the final reply chunk and mark expects_reply=true. Otherwise no chunk expects a reply.
- handoff_status is verified operational state, not a generic active flag. requested,
  active and acknowledgement_pending prove only internal/pending relay; never claim a human
  received or is following. acknowledged proves the external handoff operation was accepted,
  not that a person read it. completed may be described as completed; manual_review and
  cancelled must be stated without inventing delivery.
""".strip()

_CURRENT_OBSERVATION_COMPLETION_SUFFIX: Final = """
CURRENT PROVIDER RESULT COMPLETION (highest salience):
- observations is non-empty, so this is the post-read answer frame. Answer the complete
  current customer request now from every relevant current public observation.
- For each relevant positive offer, state its availability and the exact final total and currency
  when those fields are present. Never describe an exact current observed value as unconfirmed.
- Preserve useful public labels, dates, start time and formed-group status when present and
  relevant. Report negative or unknown results faithfully and never invent a missing field.
- Do not request another provider read after observations. Do not broaden selection,
  confirmation, reservation, payment, handoff or any effect authority.
""".strip()


_CONSULTATION_HISTORY_SYSTEM_SUFFIX: Final = """
COMMITTED CONSULTATION HISTORY PROTOCOL:
- consultation_history contains authenticated public lookup evidence from earlier
  committed turns. It is recap-only context, not a current provider observation.
- Use it to answer comparisons, recaps, and questions about what was previously found,
  including prior prices, availability, and proven unavailability. Preserve positive and
  negative results; never claim that a prior consultation did not happen when it is listed.
- `offer_count` is the total result count. When `offers_truncated=true`, say the recap carries
  only the bounded first options rather than claiming they were the complete result set.
- Say that a result was found at the recorded time. If fresh_at_turn_start is false, make
  clear that current availability or price needs another check.
- consultation_history must never authorize selection, confirmation, reservation,
  payment, handoff, or any effect. Those paths require a fresh provider read in the current
  turn and the normal typed authority gates. Only observations contains current-turn reads.
- When the current message asks to select, reserve, or act on a historical option, request
  the corresponding fresh provider read; do not emit a historical choice_ref or invent one.
""".strip()

_ACTIVE_EXECUTION_SYSTEM_SUFFIX: Final = """
OPERATION RESULTS AND COMMUNICATION:
- execution_components contains authenticated per-operation facts. active_execution_status
  is only the current group's summary; never replace individual results with that label.
- Preserve every confirmed component and its provider_reference even when another failed
  or is unknown. Null certainty means no recorded final outcome, not proof of no call.
- Payment initiation (including a link/instruction) is distinct from settlement. Source
  unavailable means unknown; not_recorded means no record in that source, not paid.
- operational_messages contains persisted asynchronous chunks in enqueue order, each with
  its own status. pending/leased/manual_review do not prove the customer received it;
  accepted_by_manychat proves channel API acceptance only, not delivery or reading.
- Never repeat or replace the already-commanded workflow, especially an uncertain effect.
  Questions, new facts and independent read-only consultations remain possible. A read
  does not authorize a new selection or effect; history does not establish fresh availability.
""".strip()


_ACTIVITY_INFORMATION_ROUTING_SYSTEM_SUFFIX: Final = """
KNOWN ACTIVITY INFORMATION ROUTING:
- When the customer asks about a known activity's difficulty, duration, preparation, or what to bring, request activity_description in the initial frame.
- Use knowledge in that initial frame only for non-catalog context. If both are relevant, request both before observations are returned; never defer activity_description to a recursive read.
- A request not to check availability forbids availability reads, not the read-only activity_description needed to answer the product question.
""".strip()


_FORMED_GROUPS_SYSTEM_SUFFIX: Final = """
SIMPLE FORMED-GROUP RECOMMENDATION:
- For an open request asking what activities fit a period or which activity is more
  recommended, use the existing knowledge read once. Set its query exactly to
  formed-groups:YYYY-MM-DD:YYYY-MM-DD with the interpreted inclusive start and end dates.
- Recommend by suitability first. Among equally suitable options, prefer a formed group.
  Do not hide other suitable activities merely because they have no formed group.
- formed_groups comes only from the scheduling sheet. It is not current Bókun availability
  or a selectable offer. After the customer chooses, request one fresh activity read for
  the exact product, date and party before selection or booking.
""".strip()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidModelProposal(f"duplicate model response key: {key}")
        result[key] = value
    return result


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidModelProposal("model input is not closed JSON") from exc


def _read_wire(request: ReadRequest) -> dict[str, object]:
    return json.loads(request.to_canonical_bytes())


def _choice_projection(
    observations: tuple[object, ...],
) -> tuple[list[dict[str, object]], dict[str, str]]:
    provider_kinds = {"cloudbeds": "lodging", "bokun": "activity"}
    counters = {"lodging": 0, "activity": 0}
    offer_refs: dict[tuple[str, str], str] = {}
    ref_offers: dict[str, str] = {}

    def transform(value: object, *, kind: str | None) -> object:
        if type(value) is list:
            return [transform(item, kind=kind) for item in value]
        if type(value) is not dict:
            return value
        result: dict[str, object] = {}
        offer_id = value.get("offer_id")
        choice_ref: str | None = None
        if offer_id is not None:
            if kind is None:
                raise InvalidModelProposal("offer identity has no model choice kind")
            if type(offer_id) is not str or not offer_id or offer_id != offer_id.strip():
                raise InvalidModelProposal("offer identity is invalid")
            if "choice_ref" in value:
                raise InvalidModelProposal("observation already contains a choice reference")
            offer_key = (kind, offer_id)
            choice_ref = offer_refs.get(offer_key)
            if choice_ref is None:
                counters[kind] += 1
                choice_ref = f"{kind}:{counters[kind]}"
                offer_refs[offer_key] = choice_ref
                ref_offers[choice_ref] = offer_id
        for key, item in value.items():
            if key == "offer_id":
                continue
            result[key] = transform(item, kind=kind)
        if choice_ref is not None:
            result["choice_ref"] = choice_ref
        return result

    projected: list[dict[str, object]] = []
    for observation in observations:
        provider = getattr(observation, "provider", None)
        payload = getattr(observation, "public_payload", None)
        if type(provider) is not str or type(payload) is not dict:
            raise InvalidModelProposal("model observation is invalid")
        public_payload = transform(payload, kind=provider_kinds.get(provider))
        if type(public_payload) is not dict:
            raise InvalidModelProposal("model observation payload is invalid")
        projected.append(public_payload)
    return projected, ref_offers


def _request_wire(
    request: ModelRequest,
    system_prompt: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> bytes:
    current = (now or (lambda: datetime.now(timezone.utc)))()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("business clock must be timezone-aware")
    business_now = current.astimezone(ZoneInfo("America/Bahia"))
    offset = business_now.strftime("%z")
    projected_payloads, _ = _choice_projection(request.observations)
    observations = [
        {
            "request_hash": item.request_hash,
            "provider": item.provider,
            "observed_at": item.observed_at.isoformat(),
            "expires_at": item.expires_at.isoformat(),
            "public_payload": public_payload,
        }
        for item, public_payload in zip(
            request.observations, projected_payloads, strict=True
        )
    ]
    consultation_history = [
        {
            "observed_at": item.observed_at.isoformat(),
            "expires_at": item.expires_at.isoformat(),
            "fresh_at_turn_start": item.fresh_at_turn_start,
            "usage": "recap_only",
            "public_context": item.public_context,
        }
        for item in request.consultation_history
    ]
    user_payload = {
        "request_id": request.request_id,
        "lead_id": request.lead_id,
        "source_event_id": request.source_event_id,
        "message": request.message,
        "trigger": request.trigger,
        "action_rejection": request.action_rejection,
        "completion_events": [
            {"event_id": e.event_id, "kind": e.kind, "command_ids": list(e.command_ids),
             "payment_id": e.payment_id, "occurred_at": e.occurred_at.isoformat()}
            for e in request.completion_events
        ],
        "attachments": [
            {"media_type": item.media_type, "content_status": item.content_status.value}
            for item in request.attachments
        ],
        "locale": request.locale,
        "state_version": request.state_version,
        "critical_outcome": request.critical_outcome,
        "private_profile_complete": request.private_profile_complete,
        "handoff_status": request.handoff_status,
        "active_execution_status": request.active_execution_status,
        "execution_components": [component_wire(c) for c in request.execution_components],
        "operational_messages": [
            {"outbox_id": m.outbox_id, "release_id": m.release_id,
             "source_message_id": m.source_message_id, "chunk_index": m.chunk_index,
             "text": m.text, "author": m.author.value, "status": m.status,
             "updated_at": m.updated_at.isoformat()}
            for m in request.operational_messages
        ],
        "observations": observations,
        "consultation_history": consultation_history,
        "business_clock": {
            "timestamp": business_now.isoformat(timespec="seconds"),
            "current_date": business_now.date().isoformat(),
            "current_time": business_now.time().isoformat(timespec="seconds"),
            "timezone": "America/Bahia",
            "utc_offset": f"{offset[:3]}:{offset[3:]}",
        },
    }
    if request.state_facts:
        user_payload["state_facts"] = [
            {
                "name": item.name,
                "value": (
                    item.value.isoformat()
                    if isinstance(item.value, date)
                    else item.value
                ),
            }
            for item in request.state_facts
        ]
    user_payload["passengers"] = [
        {
            "position": item.position,
            "participant_type": item.participant_type,
            "full_name": item.full_name,
            "birth_date": item.birth_date.isoformat() if item.birth_date else None,
            "gender": item.gender,
            "country_code": item.country_code,
            "is_holder": item.is_holder,
        }
        for item in request.passengers
    ]
    if request.pending_action is not None:
        user_payload["pending_action"] = {
            "summary_version": request.pending_action.summary_version,
            "action_kinds": [
                item.value for item in request.pending_action.action_kinds
            ],
            "public_summary": request.pending_action.public_summary,
            "expires_at": request.pending_action.expires_at.isoformat(),
        }
    messages: list[list[str]] = []
    for exchange in request.recent_dialogue:
        messages.extend(
            (
                ["user", exchange.customer_message],
                ["assistant", "\n\n".join(exchange.assistant_reply_chunks)],
            )
        )
    messages.append(["user", _canonical(user_payload).decode("utf-8")])
    return _canonical(
        {
            "system_prompt": (
                system_prompt
                + "\n\n"
                + _PRIVATE_PROFILE_SYSTEM_SUFFIX
                + "\n\n"
                + _COMMERCIAL_PROGRESSION_SYSTEM_SUFFIX
                + "\n\n"
                + _CONSULTATION_HISTORY_SYSTEM_SUFFIX
                + "\n\n"
                + _ACTIVE_EXECUTION_SYSTEM_SUFFIX
                + "\n\n"
                + _ACTIVITY_INFORMATION_ROUTING_SYSTEM_SUFFIX
                + "\n\n"
                + _FORMED_GROUPS_SYSTEM_SUFFIX
                + "\n\n"
                + _TURN_COMPLETION_SYSTEM_SUFFIX
                + "\n\nUse business_clock as the authoritative Bahia date and time for hoje, amanhã, agora, horários and all relative dates. Never expose the internal clock object."
                + (
                    "\n\n" + _CURRENT_OBSERVATION_COMPLETION_SUFFIX
                    if request.observations
                    else ""
                )
            ),
            "messages": messages,
        }
    )


def _fact(value: object) -> ModelFact:
    if type(value) is not dict or set(value) != {"name", "value"}:
        raise InvalidModelProposal("model fact fields mismatch")
    name = value["name"]
    fact_value = value["value"]
    if name in ("start_date", "end_date", "activity_date", "birth_date"):
        if type(fact_value) is not str:
            raise InvalidModelProposal("date fact must be an ISO string")
        try:
            fact_value = date.fromisoformat(fact_value)
        except ValueError as exc:
            raise InvalidModelProposal("date fact is invalid") from exc
    return ModelFact(name, fact_value)


def _read_request(value: object) -> ReadRequest:
    if type(value) is not dict:
        raise InvalidModelProposal("read request must be an exact object")
    fields = dict(value)
    try:
        fields["kind"] = ReadKind(fields["kind"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidModelProposal("read kind is invalid") from exc
    for field in ("check_in", "check_out", "activity_date"):
        raw = fields.get(field)
        if raw is not None:
            if type(raw) is not str:
                raise InvalidModelProposal(f"{field} must be an ISO date")
            try:
                fields[field] = date.fromisoformat(raw)
            except ValueError as exc:
                raise InvalidModelProposal(f"{field} is invalid") from exc
    try:
        return ReadRequest(**fields)
    except (TypeError, ValueError) as exc:
        raise InvalidModelProposal("read request is invalid") from exc


def _effect(value: object) -> EffectProposal:
    if type(value) is not dict or set(value) != {"kind", "arguments"}:
        raise InvalidModelProposal("effect proposal fields mismatch")
    return EffectProposal(value["kind"], value["arguments"])


def _passenger(value: object) -> PassengerInput:
    expected = {
        "position",
        "participant_type",
        "full_name",
        "birth_date",
        "gender",
        "country_code",
    }
    if type(value) is not dict or set(value) not in (
        expected,
        expected | {"is_holder"},
    ):
        raise InvalidModelProposal("passenger update fields mismatch")
    birth_date = value["birth_date"]
    if birth_date is not None:
        if type(birth_date) is not str:
            raise InvalidModelProposal("passenger birth_date must be ISO text or null")
        try:
            birth_date = date.fromisoformat(birth_date)
        except ValueError as exc:
            raise InvalidModelProposal("passenger birth_date is invalid") from exc
    try:
        return PassengerInput(
            position=value["position"],
            participant_type=value["participant_type"],
            full_name=value["full_name"],
            birth_date=birth_date,
            gender=value["gender"],
            country_code=value["country_code"],
            is_holder=value.get("is_holder"),
        )
    except (TypeError, ValueError) as exc:
        raise InvalidModelProposal("passenger update is invalid") from exc


def _tuple_items(value: object, name: str) -> tuple[object, ...]:
    if type(value) is not list:
        raise InvalidModelProposal(f"{name} must be an exact list")
    return tuple(value)


def _critical_actions(value: object) -> tuple[CriticalActionKind, ...]:
    raw = _tuple_items(value, "confirmed_action_kinds")
    try:
        return tuple(CriticalActionKind(item) for item in raw)
    except (TypeError, ValueError) as exc:
        raise InvalidModelProposal("critical action kind is invalid") from exc


def _approval_basis(value: object) -> ApprovalBasis | None:
    if value is None:
        return None
    try:
        return ApprovalBasis(value)
    except (TypeError, ValueError) as exc:
        raise InvalidModelProposal("approval basis is invalid") from exc


def _v8_reply_chunks(value: object) -> tuple[tuple[str, ...], str | None]:
    raw_chunks = _tuple_items(value, "reply_chunks")
    if not 1 <= len(raw_chunks) <= 2:
        raise InvalidModelProposal("v8 reply_chunks must contain one or two items")
    chunks: list[str] = []
    reply_index: int | None = None
    for index, item in enumerate(raw_chunks):
        if type(item) is not dict or set(item) != {"text", "expects_reply"}:
            raise InvalidModelProposal("v8 reply chunk fields mismatch")
        text = item["text"]
        expects_reply = item["expects_reply"]
        if type(text) is not str or not text or text != text.strip():
            raise InvalidModelProposal("v8 reply text must be non-empty exact text")
        if type(expects_reply) is not bool:
            raise InvalidModelProposal("v8 expects_reply must be an exact boolean")
        if expects_reply:
            if reply_index is not None:
                raise InvalidModelProposal("v8 may expect one customer reply")
            reply_index = index
        chunks.append(text)
    if reply_index is not None and reply_index != len(chunks) - 1:
        raise InvalidModelProposal("v8 reply expectation must be on the final chunk")
    clarification = chunks[-1] if reply_index is not None else None
    return tuple(chunks), clarification


def _v8_date(value: object, name: str) -> date:
    if type(value) is not str:
        raise InvalidModelProposal(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidModelProposal(f"{name} is invalid") from exc


def _v8_read_request(value: object, request: ModelRequest) -> ReadRequest:
    if type(value) is not dict:
        raise InvalidModelProposal("v8 read request must be an exact object")
    kind_value = value.get("kind")
    try:
        kind = ReadKind(kind_value)
    except (TypeError, ValueError) as exc:
        raise InvalidModelProposal("v8 read kind is invalid") from exc
    suffixes = {
        ReadKind.KNOWLEDGE: "knowledge",
        ReadKind.LODGING: "lodging",
        ReadKind.ACTIVITY: "activity",
        ReadKind.ROOM_DESCRIPTION: "room-description",
        ReadKind.ACTIVITY_DESCRIPTION: "activity-description",
    }
    fields: dict[str, object] = {
        "request_id": f"{request.source_event_id}:read:{suffixes[kind]}",
        "kind": kind,
    }
    if kind is ReadKind.KNOWLEDGE:
        expected = {"kind", "query"}
        fields.update(query=value.get("query"), locale=request.locale)
    elif kind is ReadKind.LODGING:
        expected = {"kind", "check_in", "check_out", "adults", "children"}
        fields.update(
            check_in=_v8_date(value.get("check_in"), "check_in"),
            check_out=_v8_date(value.get("check_out"), "check_out"),
            adults=value.get("adults"),
            children=value.get("children"),
        )
    elif kind is ReadKind.ACTIVITY:
        expected = {
            "kind",
            "product_id",
            "activity_date",
            "adults",
            "children",
        }
        fields.update(
            product_id=value.get("product_id"),
            activity_date=_v8_date(value.get("activity_date"), "activity_date"),
            adults=value.get("adults"),
            children=value.get("children"),
            locale=request.locale,
        )
    elif kind is ReadKind.ACTIVITY_DESCRIPTION:
        expected = {"kind", "product_id"}
        fields.update(product_id=value.get("product_id"), locale=request.locale)
    else:
        expected = {"kind", "choice_ref"}
        choice_ref = value.get("choice_ref")
        _, ref_offers = _choice_projection(request.observations)
        if (
            type(choice_ref) is not str
            or not choice_ref.startswith("lodging:")
            or choice_ref not in ref_offers
        ):
            raise InvalidModelProposal("v8 room choice reference is invalid")
        fields.update(offer_id=ref_offers[choice_ref])
    if set(value) != expected:
        raise InvalidModelProposal("v8 read request fields mismatch")
    try:
        return ReadRequest(**fields)
    except (TypeError, ValueError) as exc:
        raise InvalidModelProposal("v8 read request is invalid") from exc


def _v8_proposal(decoded: dict[str, object], request: ModelRequest) -> ModelProposal:
    if set(decoded) != V8_RESPONSE_FIELDS:
        raise InvalidModelProposal("v8 model response fields mismatch")
    reply_chunks, clarification_question = _v8_reply_chunks(decoded["reply_chunks"])
    intent = decoded["intent"]
    pending = request.pending_action
    if intent == "confirm":
        if pending is None:
            raise InvalidModelProposal("v8 confirmation requires a pending action")
        confirmed_summary_version = pending.summary_version
        confirmed_action_kinds = pending.action_kinds
        approval_basis = ApprovalBasis.CONTEXTUAL_REFERENCE
    else:
        confirmed_summary_version = None
        confirmed_action_kinds = ()
        approval_basis = None
    selected_refs = _tuple_items(
        decoded["selected_choice_refs"], "selected_choice_refs"
    )
    if (
        len(selected_refs) > 2
        or any(type(item) is not str for item in selected_refs)
        or len(set(selected_refs)) != len(selected_refs)
    ):
        raise InvalidModelProposal("v8 selected choice references are invalid")
    _, ref_offers = _choice_projection(request.observations)
    if any(item not in ref_offers for item in selected_refs):
        raise InvalidModelProposal("v8 selected choice is not current")
    if len(selected_refs) == 2 and {
        item.split(":", 1)[0] for item in selected_refs
    } != {"lodging", "activity"}:
        raise InvalidModelProposal("v8 package choices require lodging and activity")
    selected_offers = tuple(ref_offers[item] for item in selected_refs)
    target_offer_id = selected_offers[0] if len(selected_offers) == 1 else None
    target_offer_ids = selected_offers if len(selected_offers) == 2 else ()
    try:
        proposal = ModelProposal(
            source_event_id=request.source_event_id,
            intent=intent,
            reply_chunks=reply_chunks,
            facts=tuple(
                _fact(item) for item in _tuple_items(decoded["facts"], "facts")
            ),
            read_requests=tuple(
                _v8_read_request(item, request)
                for item in _tuple_items(decoded["read_requests"], "read_requests")
            ),
            effect_proposals=(),
            target_offer_id=target_offer_id,
            confirmed_summary_version=confirmed_summary_version,
            target_offer_ids=target_offer_ids,
            confirmed_action_kinds=confirmed_action_kinds,
            approval_basis=approval_basis,
            selection_requested=decoded["selection_requested"],
            pending_disposition=decoded["pending_action_disposition"],
            passengers=tuple(
                _passenger(item)
                for item in _tuple_items(decoded["passengers"], "passengers")
            ),
            clarification_question=clarification_question,
        )
    except (TypeError, ValueError) as exc:
        if type(exc) is InvalidModelProposal:
            raise
        raise InvalidModelProposal("v8 model proposal is invalid") from exc

    # Validate the model-owned scope at the decoding boundary so the existing
    # bounded protocol repair can fix omissions before passenger/state mutation.
    # A consultation or selected reference is not evidence of customer intent.
    declared = {fact.name: fact.value for fact in proposal.facts}
    services = ("hostel", "agency", "package")
    if "service" in declared and declared["service"] not in services:
        raise InvalidModelProposal("v8 service fact must be hostel, agency or package")
    effective = {fact.name: fact.value for fact in request.state_facts}
    effective.update(declared)
    service = effective.get("service")
    if proposal.passengers and service not in ("agency", "package"):
        raise InvalidModelProposal("v8 passengers require an activity service scope")
    if (proposal.intent == "select" or proposal.selection_requested) and service not in services:
        raise InvalidModelProposal("v8 selection requires a service scope")
    return proposal


def _proposal(
    payload: bytes,
    source_event_id: str | ModelRequest,
    *,
    require_v7: bool = False,
    require_v8: bool = False,
    normalize_legacy_inform_preserve: bool = True,
) -> ModelProposal:
    if require_v7 and require_v8:
        raise ValueError("proposal cannot require both V7 and V8")
    try:
        decoded = json.loads(payload, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InvalidModelProposal("model response is not valid JSON") from exc
    if type(decoded) is not dict:
        raise InvalidModelProposal("model response fields mismatch")
    request = source_event_id if type(source_event_id) is ModelRequest else None
    schema = decoded.get("schema")
    if schema is None and request is not None:
        if require_v7:
            raise InvalidModelProposal("model response must use proposal V7")
        return _v8_proposal(decoded, request)
    if require_v8:
        raise InvalidModelProposal("productive model response must use proposal V8")
    if require_v7 and schema != "v2-model-proposal-v7":
        raise InvalidModelProposal("model response must use proposal V7")

    if schema == "v2-model-proposal-v1":
        expected_fields = _RESPONSE_FIELDS_V1
    elif schema == "v2-model-proposal-v2":
        expected_fields = _RESPONSE_FIELDS_V2
    elif schema == "v2-model-proposal-v3":
        expected_fields = _RESPONSE_FIELDS_V3
    elif schema == "v2-model-proposal-v4":
        expected_fields = _RESPONSE_FIELDS_V4
    elif schema == "v2-model-proposal-v5":
        expected_fields = _RESPONSE_FIELDS_V5
    elif schema == "v2-model-proposal-v6":
        expected_fields = _RESPONSE_FIELDS_V6
    elif schema == "v2-model-proposal-v7":
        expected_fields = _RESPONSE_FIELDS_V7
    else:
        raise InvalidModelProposal("model response schema mismatch")
    if set(decoded) != expected_fields:
        raise InvalidModelProposal("model response fields mismatch")
    expected_source_event_id = (
        request.source_event_id if request is not None else source_event_id
    )
    if decoded["source_event_id"] != expected_source_event_id:
        raise InvalidModelProposal("model response source event mismatch")
    pending_disposition = (
        decoded["pending_disposition"]
        if schema
        in (
            "v2-model-proposal-v5",
            "v2-model-proposal-v6",
            "v2-model-proposal-v7",
        )
        else None
    )
    if (
        normalize_legacy_inform_preserve
        and decoded["intent"] == "inform"
        and pending_disposition == "preserve"
    ):
        pending_disposition = None
    reply_chunks = _tuple_items(decoded["reply_chunks"], "reply_chunks")
    clarification_question = (
        decoded["clarification_question"]
        if schema == "v2-model-proposal-v7"
        else None
    )
    try:
        return ModelProposal(
            source_event_id=decoded["source_event_id"],
            intent=decoded["intent"],
            reply_chunks=reply_chunks,
            facts=tuple(
                _fact(item) for item in _tuple_items(decoded["facts"], "facts")
            ),
            read_requests=tuple(
                _read_request(item)
                for item in _tuple_items(decoded["read_requests"], "read_requests")
            ),
            effect_proposals=tuple(
                _effect(item)
                for item in _tuple_items(
                    decoded["effect_proposals"], "effect_proposals"
                )
            ),
            target_offer_id=decoded["target_offer_id"],
            confirmed_summary_version=decoded["confirmed_summary_version"],
            target_offer_ids=(
                tuple(
                    _tuple_items(decoded["target_offer_ids"], "target_offer_ids")
                )
                if schema
                in (
                    "v2-model-proposal-v2",
                    "v2-model-proposal-v3",
                    "v2-model-proposal-v4",
                    "v2-model-proposal-v5",
                    "v2-model-proposal-v6",
                    "v2-model-proposal-v7",
                )
                else ()
            ),
            confirmed_action_kinds=(
                _critical_actions(decoded["confirmed_action_kinds"])
                if schema
                in (
                    "v2-model-proposal-v3",
                    "v2-model-proposal-v4",
                    "v2-model-proposal-v5",
                    "v2-model-proposal-v6",
                    "v2-model-proposal-v7",
                )
                else ()
            ),
            approval_basis=(
                _approval_basis(decoded["approval_basis"])
                if schema
                in (
                    "v2-model-proposal-v3",
                    "v2-model-proposal-v4",
                    "v2-model-proposal-v5",
                    "v2-model-proposal-v6",
                    "v2-model-proposal-v7",
                )
                else None
            ),
            selection_requested=(
                decoded["selection_requested"]
                if schema
                in (
                    "v2-model-proposal-v4",
                    "v2-model-proposal-v5",
                    "v2-model-proposal-v6",
                    "v2-model-proposal-v7",
                )
                else False
            ),
            pending_disposition=pending_disposition,
            passengers=(
                tuple(
                    _passenger(item)
                    for item in _tuple_items(decoded["passengers"], "passengers")
                )
                if schema in ("v2-model-proposal-v6", "v2-model-proposal-v7")
                else ()
            ),
            clarification_question=clarification_question,
        )
    except (TypeError, ValueError) as exc:
        if type(exc) is InvalidModelProposal:
            raise
        raise InvalidModelProposal("model proposal is invalid") from exc


class HermesModelAdapter:
    def __init__(
        self,
        *,
        command: tuple[str, ...],
        system_prompt: str,
        timeout: int,
        transcript_key: bytes,
        run: Callable[..., object] = subprocess.run,
        environ: dict[str, str] | None = None,
    ) -> None:
        if (
            type(command) is not tuple
            or not command
            or any(
                type(item) is not str or not item or "\x00" in item for item in command
            )
        ):
            raise ValueError("command must be a non-empty exact string tuple")
        if type(system_prompt) is not str or not system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty exact string")
        if type(timeout) is not int or timeout < 1:
            raise ValueError("timeout must be a positive exact integer")
        if not callable(run):
            raise TypeError("run must be callable")
        if type(transcript_key) is not bytes or len(transcript_key) < 32:
            raise ValueError("transcript_key must contain at least 32 exact bytes")
        self._command = command
        self._system_prompt = system_prompt
        self._timeout = timeout
        self._transcript_key = transcript_key
        self._run = run
        source = os.environ if environ is None else environ
        if type(source) is not dict and environ is not None:
            raise TypeError("environ must be an exact dict")
        self._child_env = {
            key: value
            for key, value in source.items()
            if key in _CHILD_ENV_ALLOWLIST and type(value) is str and "\x00" not in value
        }

    def complete(self, request: ModelRequest) -> ModelProposal:
        return self.complete_audited(request).proposal

    def _failure_frame(
        self,
        *,
        stdin_bytes: bytes,
        reason: str,
    ) -> AuditedTranscriptFrame:
        response = _canonical(
            {
                "schema": "v2-model-attempt-failure-v1",
                "reason": reason,
            }
        )
        stdout = b"V2_MODEL_ATTEMPT_FAILURE\x00" + response
        return AuditedTranscriptFrame.create(
            stdin_bytes=stdin_bytes,
            stdout_bytes=stdout,
            response_bytes=response,
            transcript_key=self._transcript_key,
        )

    def _attempt(
        self,
        request: ModelRequest,
        *,
        stdin_bytes: bytes,
        decode: Callable[[bytes], ModelProposal] | None = None,
    ) -> tuple[AuditedModelTurn | None, AuditedTranscriptFrame]:
        try:
            result = self._run(
                self._command,
                input=stdin_bytes,
                capture_output=True,
                timeout=self._timeout,
                check=False,
                env=self._child_env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ChildExecutionFailed("child process failed") from exc
        returncode = getattr(result, "returncode", None)
        stdout = getattr(result, "stdout", None)
        stderr = getattr(result, "stderr", None)
        if (
            type(returncode) is not int
            or type(stdout) is not bytes
            or type(stderr) is not bytes
        ):
            raise ChildExecutionFailed("invalid child process result")
        if returncode == CHILD_INPUT_REJECTED_EXIT:
            # Output repair cannot fix an unchanged, invalid input envelope.
            raise ChildInputRejected("child rejected the model request")
        if returncode == CHILD_INVALID_RESPONSE_EXIT:
            return None, self._failure_frame(
                stdin_bytes=stdin_bytes,
                reason="model_response_invalid",
            )
        if returncode != 0:
            raise ChildExecutionFailed("child exited without a model response")
        marker_at = stdout.rfind(_RESULT_MARKER)
        if marker_at < 0:
            raise ChildExecutionFailed("child result marker missing")
        response = stdout[marker_at + len(_RESULT_MARKER) :]
        if not response or len(response) > 128 * 1024:
            return None, self._failure_frame(
                stdin_bytes=stdin_bytes,
                reason="response_size_invalid",
            )
        frame = AuditedTranscriptFrame.create(
            stdin_bytes=stdin_bytes,
            stdout_bytes=stdout,
            response_bytes=response,
            transcript_key=self._transcript_key,
        )
        try:
            proposal = (
                _proposal(
                    response,
                    request,
                    require_v8=True,
                )
                if decode is None
                else decode(response)
            )
        except InvalidModelProposal:
            return None, frame
        if request.observations and proposal.read_requests:
            return None, frame
        turn = AuditedModelTurn.from_exchange(
            proposal=proposal,
            stdin_bytes=stdin_bytes,
            stdout_bytes=stdout,
            response_bytes=response,
            transcript_key=self._transcript_key,
            ephemeral_session_id="uds:" + hashlib.sha256(stdin_bytes).hexdigest()[:32],
        )
        return turn, turn.frames[0]


    def complete_audited(self, request: ModelRequest) -> AuditedModelTurn:
        return self._complete_audited(request, allow_protocol_repair=True)

    def _complete_audited(
        self,
        request: ModelRequest,
        *,
        allow_protocol_repair: bool,
    ) -> AuditedModelTurn:
        if type(request) is not ModelRequest:
            raise TypeError("request must be an exact ModelRequest")
        if type(allow_protocol_repair) is not bool:
            raise TypeError("allow_protocol_repair must be an exact bool")
        base_prompt = self._system_prompt
        prompts = (base_prompt,) + (
            (base_prompt + "\n\n" + _PROTOCOL_REPAIR_SUFFIX,)
            if allow_protocol_repair else ()
        )
        wire = _request_wire
        decode = None
        original_stdin = wire(request, base_prompt)
        attempted_frames: list[AuditedTranscriptFrame] = []
        for prompt in prompts:
            stdin_bytes = original_stdin if prompt == base_prompt else wire(request, prompt)
            turn, frame = self._attempt(
                request,
                stdin_bytes=stdin_bytes,
                decode=decode,
            )
            if turn is not None:
                if attempted_frames:
                    turn = AuditedModelTurn.from_frames(
                        proposal=turn.proposal,
                        frames=(*attempted_frames, *turn.frames),
                        ephemeral_session_id=turn.closure.ephemeral_session_id,
                    )
                return turn
            attempted_frames.append(frame)

        raise InvalidModelProposal(
            "model proposal remained invalid after bounded attempts"
        )


__all__ = ["HermesModelAdapter"]
