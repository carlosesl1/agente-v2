"""Tool-free Hermes child-process adapter for Maya V2."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Final

from v2_contracts.confirmation_review import (
    ContextualConfirmationDecision,
    ContextualConfirmationReview,
    InvalidContextualConfirmationReview,
)
from v2_contracts.critical_actions import ApprovalBasis, CriticalActionKind
from v2_contracts.model import (
    AuditedModelTurn,
    AuditedTranscriptFrame,
    EffectProposal,
    InvalidModelProposal,
    ModelFact,
    ModelProposal,
    ModelRequest,
    proposal_requires_progress_review,
)
from v2_contracts.providers import ReadKind, ReadRequest
from v2_contracts.passengers import PassengerInput

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
_CONFIRMATION_REVIEW_FIELDS: Final = frozenset(
    ("schema", "source_event_id", "decision")
)
_CONFIRMATION_REVIEW_SYSTEM_PROMPT: Final = """
You are a narrow semantic reviewer for one pending critical action. You have no tools
and no authority to execute anything. Compare the complete current message with the
complete pending public summary. Return exactly one JSON object with exactly these
fields: schema, source_event_id, decision. schema must be
"v2-contextual-confirmation-review-v1". Copy source_event_id exactly. decision must be
one of "approve", "reject", "adjust", or "uncertain".

Use "approve" only when the complete current message unconditionally approves the
complete pending summary without changing, narrowing, postponing, or conditioning any
material term or action. Use "reject" for refusal, cancellation, postponement, or
withdrawal. Use "adjust" when the message adds a condition or changes any product,
date, party, amount, currency, payment term, or action scope. Use "uncertain" for a
question, ambiguity, hesitation, unrelated text, or insufficient evidence. Judge the
meaning of the complete message in context; never decide from the presence or absence
of a word, token, emoji, substring, or fixed expression. Return no rationale, reply,
Markdown, or extra field.
""".strip()
_CONFIRMATION_REVIEW_REPAIR_SUFFIX: Final = """
PROTOCOL REPAIR: the previous child response was rejected by the closed parser.
Return exactly one v2-contextual-confirmation-review-v1 JSON object with only schema,
source_event_id, and decision. Copy source_event_id exactly. decision must be approve,
reject, adjust, or uncertain under the supplied semantic contract. Return no rationale,
reply, Markdown, or extra field.
""".strip()
_PROTOCOL_REPAIR_SUFFIX: Final = """

PROTOCOL REPAIR: the previous child response was rejected by the closed parser.
Return exactly one v2-model-proposal-v7 JSON object and no commentary. reply_chunks
must contain one or two non-empty trimmed customer-facing strings. Do not add tools,
effects, IDs, or facts that are not justified by the original request and observations.
When observations are present in the request, use them and return read_requests as an
empty list; the parent permits only one provider-read round per turn. selection_requested
must be false by default; it may be true only on an inform proposal with a fresh read when
the current message unambiguously asks to prepare or reserve the current option. It is
false for questions, hypotheticals, uncertainty or informational availability checks.
pending_disposition must be null except for adjust: preserve keeps an unchanged pending
summary during questions or recap requests; revoke is for refusal or material change.
clarification_question must be null unless you need one explicit customer answer to
continue. When non-null, copy that exact question into customer-facing reply_chunks.
When pending_action is present, classify the latest message in relation to that exact
public summary. Uma confirmação semântica curta como “Sim”, “Pode reservar”,
“Confirmado” ou “Isso mesmo” pode usar intent=confirm; copy summary_version and
action_kinds exactly and set approval_basis to contextual_reference. Sem pending_action,
ou diante de dúvida, pergunta, recusa ou mudança material, não use intent=confirm.
""".strip()


_PRIVATE_PROFILE_SYSTEM_SUFFIX: Final = """
PRIVATE RESERVATION HOLDER PROTOCOL:
- The current message is the complete original customer text. It may contain private
  context intentionally supplied for service. Interpret that complete context directly.
- private_customer_fact_names is a presence-only list for durable fields already known.
  Never ask again for a listed field unless the customer explicitly corrects it.
- Output full_name, email, or country_code only when the complete message semantically
  identifies that value as belonging to the reservation holder. country_code must be ISO
  alpha-2. Do not infer country from phone, language, locale, or defaults.
- First-person self-identification is holder evidence. A spouse, companion, passenger,
  hostel, property, agency, or other third party is not the holder by default. Use another
  person's values only when the customer explicitly says that person is or will be the
  reservation holder.
- If holder attribution is genuinely ambiguous, do not guess. Ask one natural
  clarification question and emit no guessed private fact, selection, confirmation, or
  effect. A complete availability/price query may still emit its provider read in the
  same frame; holder identity is a write-boundary requirement, not a read prerequisite.
- Never output phone_e164 from conversational text. Authenticated phone identity exists
  only when phone_e164 is present in private_customer_fact_names; a typed phone may inform
  conversation but cannot replace that identity.
- Newly interpreted holder facts may accompany a read request in the same proposal. The
  parent validates and persists them before dispatching any provider read.
- If one message both corrects holder data and appears to confirm an older summary, the
  correction wins: emit adjust with pending_disposition=revoke, never confirm. A fresh
  summary and a later natural confirmation are required.
- Avoid unnecessarily echoing exact private values in customer-facing reply_chunks. Never
  mention schemas, providers, payloads, state, bindings, or technical validation.
""".strip()


_COMMERCIAL_PROGRESSION_SYSTEM_SUFFIX: Final = """
CURRENT-TURN COMMERCIAL PROGRESSION:
- The request contains the complete original customer message. When that message already
  provides the service, date or period, and party needed for an availability or price
  check, emit the corresponding typed facts and read_requests in this same frame.
- You are the sole semantic owner of facts and informational read intent. The parent does
  not parse customer language, inject facts, or infer informational reads from keywords.
- Never reply that you are ready to check, will check later, or need the customer to send
  another booking message when the current message already has the required query data.
- "one adult" or "1 adulto" with no other traveler mentioned means adults=1 and
  children=0 for this read; do not ask a redundant children question.
- For one activity participant, including service=package, emit explicit birth_date and
  gender from the current message as typed facts and keep passengers empty. Do this in the
  same frame even when the message also asks to keep the package or prepare its summary.
- When selection_review_required is true without observations, first extract any explicit
  individual birth_date/gender or group passenger updates from the complete current
  message. If that message asks to prepare the current option, preserve the complete
  commercial facts, set selection_requested=true, and emit the exact fresh read now.
- This progression authorizes only read_requests. It never authorizes a reservation,
  payment, handoff, delivery, or effect.
- For a package, resolve the customer's lodging and activity references semantically
  against current observations. When both are unambiguous and all selection requirements
  are complete, select exactly the two matching public offer IDs atomically.
- When selection_review_required is true and observations are present, this is a post-read
  semantic adjudication pass. Re-read the complete customer message against the observed
  options. If the customer unambiguously committed to one lodging option and one activity
  option, return intent=select with exactly those two public IDs in target_offer_ids;
  otherwise remain inform. Do not emit new or changed facts: for select, repeat only the
  exact commercial facts required by the select contract. Emit no passenger updates, new
  reads, or effects, and never choose by list position unless the customer requested that
  criterion.
""".strip()


_TURN_COMPLETION_SYSTEM_SUFFIX: Final = """
FINAL TURN COMPLETION RULES (highest salience):
- recent committed dialogue, when present, is private context for continuity. Use it to
  resolve references, the last open question, and what the customer is continuing now.
- Never route by a word, substring, regex, alias list, or fixed phrase. Interpret the
  complete current message semantically against dialogue, state facts and observations.
- If the current message already completes a safe informational query, emit all typed facts
  and the required read_requests now. Do not answer with readiness or ask for the same data.
- If exactly one customer answer is missing, set clarification_question to that exact
  customer-facing question and include it in reply_chunks. Otherwise set it to null.
- handoff_status is verified operational state, not a generic active flag. requested,
  active and acknowledgement_pending prove only internal/pending relay; never claim a human
  received or is following. acknowledged proves the external handoff operation was accepted,
  not that a person read it. completed may be described as completed; manual_review and
  cancelled must be stated without inventing delivery.
- When progress_review_required=true, the preceding valid proposal had no structured
  progression and no typed clarification. Reinterpret once. Advance now, ask the one
  necessary clarification, or give a conclusive grounded answer. Never fabricate a read,
  fact, effect, delivery or human acknowledgement.
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
  the corresponding fresh provider read; do not emit a historical offer ID or invent one.
""".strip()

_ACTIVE_EXECUTION_SYSTEM_SUFFIX: Final = """
ACTIVE EXECUTION STATUS:
- active_execution_status is null unless a reservation command is already queued or
  executing. When present, never submit, select, refresh, or promise that command again.
- For a short reaffirmation or progress follow-up, state naturally that the existing
  reservation is already being processed. Do not ask the customer to choose an option.
- A genuinely unrelated non-commercial question may still be answered normally. Any
  material change or new commercial scope waits for a terminal execution outcome and must
  never create a second command.
""".strip()

_RECAP_REUSE_SYSTEM_SUFFIX: Final = """
FRESH CONSULTATION REUSE:
- recap_reuse_required=true means the parent proved that every informational read from
  the earlier frame exactly matches fresh committed consultation_history.
- Answer the complete current message from that public recap context. Emit no read,
  selection, confirmation, effect, private fact, or passenger update.
- This is recap-only and cannot authorize any action. If the customer wants to select,
  reserve, confirm, or change scope, say a fresh check will be required for that action.
""".strip()

_ACTIVITY_INFORMATION_ROUTING_SYSTEM_SUFFIX: Final = """
KNOWN ACTIVITY INFORMATION ROUTING:
- When the customer asks about a known activity's difficulty, duration, preparation, or what to bring, request activity_description in the initial frame.
- Use knowledge in that initial frame only for non-catalog context. If both are relevant, request both before observations are returned; never defer activity_description to a recursive read.
- A request not to check availability forbids availability reads, not the read-only activity_description needed to answer the product question.
""".strip()


_PUBLIC_REPLY_CORRECTION_SUFFIX: Final = """
PUBLIC REPLY CORRECTION
The previous candidate could not be published for the listed closed reasons.
You, Maya, must write the corrected customer-facing reply.
Do not repeat private values. Do not request another read after observations.
Do not strengthen operational status beyond exact receipts.
Return one valid v2-model-proposal-v7 frame. The parent will not rewrite it.
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


def _request_wire(request: ModelRequest, system_prompt: str) -> bytes:
    observations = [
        {
            "request_hash": item.request_hash,
            "provider": item.provider,
            "observed_at": item.observed_at.isoformat(),
            "expires_at": item.expires_at.isoformat(),
            "public_payload": item.public_payload,
        }
        for item in request.observations
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
        "locale": request.locale,
        "state_version": request.state_version,
        "critical_outcome": request.critical_outcome,
        "private_profile_complete": request.private_profile_complete,
        "handoff_status": request.handoff_status,
        "confirmation_review_required": request.confirmation_review_required,
        "selection_review_required": request.selection_review_required,
        "progress_review_required": request.progress_review_required,
        "active_execution_status": request.active_execution_status,
        "recap_reuse_required": request.recap_reuse_required,
        "public_reply_correction_reasons": [
            item.value for item in request.public_reply_correction_reasons
        ],
        "observations": observations,
        "consultation_history": consultation_history,
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
    if request.private_customer_fact_names:
        user_payload["private_customer_fact_names"] = list(
            request.private_customer_fact_names
        )
    if request.passenger_manifest_status is not None:
        user_payload["passenger_manifest_status"] = (
            request.passenger_manifest_status.to_public_dict()
        )
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
                + _RECAP_REUSE_SYSTEM_SUFFIX
                + "\n\n"
                + _ACTIVITY_INFORMATION_ROUTING_SYSTEM_SUFFIX
                + "\n\n"
                + _TURN_COMPLETION_SYSTEM_SUFFIX
                + (
                    "\n\n" + _PUBLIC_REPLY_CORRECTION_SUFFIX
                    if request.public_reply_correction_reasons
                    else ""
                )
            ),
            "messages": messages,
        }
    )


def _confirmation_review_wire(request: ModelRequest, system_prompt: str) -> bytes:
    if not request.confirmation_review_required or request.pending_action is None:
        raise InvalidModelProposal(
            "contextual confirmation review requires an exact pending action"
        )
    pending = request.pending_action
    user_payload = {
        "request_id": request.request_id,
        "source_event_id": request.source_event_id,
        "message": request.message,
        "locale": request.locale,
        "pending_action": {
            "summary_version": pending.summary_version,
            "action_kinds": [item.value for item in pending.action_kinds],
            "public_summary": pending.public_summary,
            "expires_at": pending.expires_at.isoformat(),
        },
    }
    return _canonical(
        {
            "system_prompt": system_prompt,
            "messages": [["user", _canonical(user_payload).decode("utf-8")]],
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
    if type(value) is not dict or set(value) != expected:
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


def _proposal(payload: bytes, source_event_id: str) -> ModelProposal:
    try:
        decoded = json.loads(payload, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InvalidModelProposal("model response is not valid JSON") from exc
    if type(decoded) is not dict:
        raise InvalidModelProposal("model response fields mismatch")
    schema = decoded.get("schema")

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
    if decoded["source_event_id"] != source_event_id:
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
    if decoded["intent"] == "inform" and pending_disposition == "preserve":
        pending_disposition = None
    reply_chunks = tuple(
        unicodedata.normalize("NFKC", item).strip()
        if type(item) is str
        else item
        for item in _tuple_items(decoded["reply_chunks"], "reply_chunks")
    )
    clarification_question = (
        unicodedata.normalize("NFKC", decoded["clarification_question"]).strip()
        if schema == "v2-model-proposal-v7"
        and type(decoded["clarification_question"]) is str
        else decoded["clarification_question"]
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


def _confirmation_review(
    payload: bytes,
    source_event_id: str,
) -> ContextualConfirmationReview:
    try:
        decoded = json.loads(payload, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InvalidModelProposal(
            "confirmation review response is not valid JSON"
        ) from exc
    if type(decoded) is not dict or set(decoded) != _CONFIRMATION_REVIEW_FIELDS:
        raise InvalidModelProposal("confirmation review response fields mismatch")
    if decoded["schema"] != "v2-contextual-confirmation-review-v1":
        raise InvalidModelProposal("confirmation review response schema mismatch")
    if decoded["source_event_id"] != source_event_id:
        raise InvalidModelProposal("confirmation review source event mismatch")
    try:
        decision = ContextualConfirmationDecision(decoded["decision"])
        return ContextualConfirmationReview(
            source_event_id=decoded["source_event_id"],
            decision=decision,
        )
    except (TypeError, ValueError, InvalidContextualConfirmationReview) as exc:
        raise InvalidModelProposal("confirmation review decision is invalid") from exc


def _proposal_from_confirmation_review(
    request: ModelRequest,
    review: ContextualConfirmationReview,
) -> ModelProposal:
    if (
        not request.confirmation_review_required
        or request.pending_action is None
        or review.source_event_id != request.source_event_id
    ):
        raise InvalidModelProposal(
            "confirmation review is not bound to the pending request"
        )
    english = request.locale.lower().startswith("en")
    if review.decision is ContextualConfirmationDecision.APPROVE:
        return ModelProposal(
            source_event_id=request.source_event_id,
            intent="confirm",
            reply_chunks=(
                "Confirmation received. I will process exactly the summary above."
                if english
                else "Confirmação recebida. Vou processar exatamente o resumo acima.",
            ),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=request.pending_action.summary_version,
            confirmed_action_kinds=request.pending_action.action_kinds,
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        )
    if review.decision in (
        ContextualConfirmationDecision.REJECT,
        ContextualConfirmationDecision.ADJUST,
    ):
        return ModelProposal(
            source_event_id=request.source_event_id,
            intent="adjust",
            reply_chunks=(
                "Understood. I will not execute the previous summary."
                if english
                else "Entendido. Não vou executar o resumo anterior.",
            ),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            pending_disposition="revoke",
        )
    return ModelProposal(
        source_event_id=request.source_event_id,
        intent="inform",
        reply_chunks=(
            "I have not considered the pending summary confirmed."
            if english
            else "Ainda não considerei o resumo pendente confirmado.",
        ),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )


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
        except (OSError, subprocess.SubprocessError):
            return None, self._failure_frame(
                stdin_bytes=stdin_bytes,
                reason="child_process_failed",
            )
        returncode = getattr(result, "returncode", None)
        stdout = getattr(result, "stdout", None)
        stderr = getattr(result, "stderr", None)
        if (
            type(returncode) is not int
            or type(stdout) is not bytes
            or type(stderr) is not bytes
        ):
            return None, self._failure_frame(
                stdin_bytes=stdin_bytes,
                reason="invalid_process_result",
            )
        if returncode != 0:
            return None, self._failure_frame(
                stdin_bytes=stdin_bytes,
                reason="child_nonzero_exit",
            )
        marker_at = stdout.rfind(_RESULT_MARKER)
        if marker_at < 0:
            return None, self._failure_frame(
                stdin_bytes=stdin_bytes,
                reason="result_marker_missing",
            )
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
                _proposal(response, request.source_event_id)
                if decode is None
                else decode(response)
            )
        except InvalidModelProposal:
            return None, frame
        if request.observations and proposal.read_requests:
            session_hash = hashlib.sha256(stdin_bytes).hexdigest()[:32]
            return (
                AuditedModelTurn.from_frames(
                    proposal=self._recursive_read_fallback(request, proposal),
                    frames=(frame,),
                    ephemeral_session_id=(
                        f"deterministic:recursive-read-fallback:{session_hash}"
                    ),
                ),
                frame,
            )
        turn = AuditedModelTurn.from_exchange(
            proposal=proposal,
            stdin_bytes=stdin_bytes,
            stdout_bytes=stdout,
            response_bytes=response,
            transcript_key=self._transcript_key,
            ephemeral_session_id="uds:" + hashlib.sha256(stdin_bytes).hexdigest()[:32],
        )
        return turn, turn.frames[0]

    @staticmethod
    def _fallback_proposal(request: ModelRequest) -> ModelProposal:
        if request.locale.lower().startswith("en"):
            text = "I couldn't complete that reply just now. Could you repeat your last message?"
        else:
            text = (
                "Não consegui concluir essa resposta agora. "
                "Pode repetir sua última mensagem?"
            )
        return ModelProposal(
            source_event_id=request.source_event_id,
            intent="inform",
            reply_chunks=(text,),
            facts=(),
            read_requests=(),
            effect_proposals=(),
        )

    @staticmethod
    def _recursive_read_fallback(
        request: ModelRequest,
        proposal: ModelProposal,
    ) -> ModelProposal:
        negative_providers = {
            observation.provider
            for observation in request.observations
            if observation.public_payload.get("available") is False
        }
        english = request.locale.lower().startswith("en")
        if {"bokun", "cloudbeds"}.issubset(negative_providers):
            text = (
                "The requested lodging and activity are not available for the "
                "consulted dates. Nothing was booked. I can check other dates."
                if english
                else "A hospedagem e o passeio solicitados não estão disponíveis para "
                "as datas consultadas. Nada foi reservado. Posso consultar outras datas."
            )
        elif "bokun" in negative_providers:
            text = (
                "The requested activity is not available for the consulted date. "
                "Nothing was booked. I can check another date."
                if english
                else "O passeio solicitado não está disponível para a data consultada. "
                "Nada foi reservado. Posso consultar outra data."
            )
        elif "cloudbeds" in negative_providers:
            text = (
                "The requested lodging is not available for the consulted dates. "
                "Nothing was booked. I can check other dates."
                if english
                else "A hospedagem solicitada não está disponível para as datas consultadas. "
                "Nada foi reservado. Posso consultar outras datas."
            )
        elif ReadKind.ACTIVITY_DESCRIPTION in {
            item.kind for item in proposal.read_requests
        }:
            text = (
                "I did not find enough verified detail to answer safely. I can check "
                "the activity's official description in a new lookup."
                if english
                else "Não encontrei detalhes verificados suficientes para responder com "
                "segurança. Posso verificar a descrição oficial do passeio em uma nova "
                "consulta."
            )
        else:
            text = (
                "I couldn't safely complete that response from the verified information. "
                "Please ask me to check it again."
                if english
                else "Não consegui concluir essa resposta com segurança a partir das "
                "informações verificadas. Peça para eu consultar novamente."
            )
        return ModelProposal(
            source_event_id=request.source_event_id,
            intent="inform",
            reply_chunks=(text,),
            facts=(),
            read_requests=(),
            effect_proposals=(),
        )

    def _maybe_progress_review(
        self,
        request: ModelRequest,
        turn: AuditedModelTurn,
    ) -> AuditedModelTurn:
        if (
            request.progress_review_required
            or request.observations
            or request.pending_action is not None
            or request.handoff_status is not None
            or request.confirmation_review_required
            or request.selection_review_required
            or request.active_execution_status is not None
            or request.recap_reuse_required
            or request.public_reply_correction_reasons
            or not proposal_requires_progress_review(turn.proposal)
        ):
            return turn
        review_request = replace(
            request,
            request_id=(
                "progress-review:"
                + hashlib.sha256(request.request_id.encode("utf-8")).hexdigest()
            ),
            progress_review_required=True,
        )
        reviewed = self._complete_audited(
            review_request,
            allow_protocol_repair=False,
        )
        return AuditedModelTurn.from_frames(
            proposal=reviewed.proposal,
            frames=(*turn.frames, *reviewed.frames),
            ephemeral_session_id=reviewed.closure.ephemeral_session_id,
        )

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
        if request.confirmation_review_required:
            base_prompt = _CONFIRMATION_REVIEW_SYSTEM_PROMPT
            prompts = (base_prompt,) + (
                (base_prompt + "\n\n" + _CONFIRMATION_REVIEW_REPAIR_SUFFIX,)
                if allow_protocol_repair
                else ()
            )
            wire = _confirmation_review_wire

            def decode(response: bytes) -> ModelProposal:
                return _proposal_from_confirmation_review(
                    request,
                    _confirmation_review(response, request.source_event_id),
                )

        else:
            base_prompt = self._system_prompt
            prompts = (base_prompt,) + (
                (base_prompt + "\n\n" + _PROTOCOL_REPAIR_SUFFIX,)
                if allow_protocol_repair
                else ()
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
                return self._maybe_progress_review(request, turn)
            attempted_frames.append(frame)

        proposal = self._fallback_proposal(request)
        fallback_response = _canonical(
            {
                "schema": "v2-deterministic-protocol-fallback-v1",
                "source_event_id": request.source_event_id,
                "intent": proposal.intent,
                "reply_chunks": list(proposal.reply_chunks),
                "facts": [],
                "read_requests": [],
                "effect_proposals": [],
            }
        )
        fallback_stdin = _canonical(
            {
                "schema": "v2-deterministic-protocol-fallback-request-v1",
                "request_hash": hashlib.sha256(original_stdin).hexdigest(),
                "failed_attempts": len(attempted_frames),
            }
        )
        fallback_frame = AuditedTranscriptFrame.create(
            stdin_bytes=fallback_stdin,
            stdout_bytes=b"V2_DETERMINISTIC_FALLBACK\x00" + fallback_response,
            response_bytes=fallback_response,
            transcript_key=self._transcript_key,
        )
        session_hash = hashlib.sha256(fallback_stdin).hexdigest()[:32]
        return AuditedModelTurn.from_frames(
            proposal=proposal,
            frames=(*attempted_frames, fallback_frame),
            ephemeral_session_id=f"deterministic:protocol-fallback:{session_hash}",
        )


__all__ = ["HermesModelAdapter"]
