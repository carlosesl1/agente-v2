"""Tool-free Hermes child-process adapter for Maya V2."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import unicodedata
from collections.abc import Callable
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
Return exactly one v2-model-proposal-v6 JSON object and no commentary. reply_chunks
must contain one or two non-empty trimmed customer-facing strings. Do not add tools,
effects, IDs, or facts that are not justified by the original request and observations.
When observations are present in the request, use them and return read_requests as an
empty list; the parent permits only one provider-read round per turn. selection_requested
must be false by default; it may be true only on an inform proposal with a fresh read when
the current message unambiguously asks to prepare or reserve the current option. It is
false for questions, hypotheticals, uncertainty or informational availability checks.
pending_disposition must be null except for adjust: preserve keeps an unchanged pending
summary during questions or recap requests; revoke is for refusal or material change.
When pending_action is present, classify the latest message in relation to that exact
public summary. Uma confirmação semântica curta como “Sim”, “Pode reservar”,
“Confirmado” ou “Isso mesmo” pode usar intent=confirm; copy summary_version and
action_kinds exactly and set approval_basis to contextual_reference. Sem pending_action,
ou diante de dúvida, pergunta, recusa ou mudança material, não use intent=confirm.
""".strip()


_PRIVATE_PROFILE_SYSTEM_SUFFIX: Final = """
PRIVATE RESERVATION PROFILE PROTOCOL:
- private_customer_fact_names is a presence-only list. Never ask again for a listed field.
- Never request or accept a conversational phone number. phone_e164 is valid only when
  present through the authenticated channel marker; if absent, do not prepare or confirm
  a reservable summary.
- When full_name, email, or country_code is missing, ask naturally only for the missing
  fields. Do not mention schemas, providers, payloads, state, bindings, or technical blocks.
- Bracketed private-field markers in the current message replace values already captured
  by the parent. Never echo, reconstruct, guess, or request those values again.
- A message containing a newly supplied private-field marker is collection-only: acknowledge
  it naturally, with no read, selection, summary, confirmation, effect, or phone fact.
- Output facts may name full_name, email, or country_code only when explicitly supplied;
  never output phone_e164. Never infer country from phone, language, locale, or defaults.
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
    user_payload = {
        "request_id": request.request_id,
        "lead_id": request.lead_id,
        "source_event_id": request.source_event_id,
        "message": request.message,
        "locale": request.locale,
        "state_version": request.state_version,
        "critical_outcome": request.critical_outcome,
        "private_profile_complete": request.private_profile_complete,
        "handoff_active": request.handoff_active,
        "confirmation_review_required": request.confirmation_review_required,
        "selection_review_required": request.selection_review_required,
        "observations": observations,
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
    return _canonical(
        {
            "system_prompt": (
                system_prompt + "\n\n" + _PRIVATE_PROFILE_SYSTEM_SUFFIX
            ),
            "messages": [["user", _canonical(user_payload).decode("utf-8")]],
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
    else:
        raise InvalidModelProposal("model response schema mismatch")
    if set(decoded) != expected_fields:
        raise InvalidModelProposal("model response fields mismatch")
    if decoded["source_event_id"] != source_event_id:
        raise InvalidModelProposal("model response source event mismatch")
    pending_disposition = (
        decoded["pending_disposition"]
        if schema in ("v2-model-proposal-v5", "v2-model-proposal-v6")
        else None
    )
    if decoded["intent"] == "inform" and pending_disposition == "preserve":
        pending_disposition = None
    try:
        return ModelProposal(
            source_event_id=decoded["source_event_id"],
            intent=decoded["intent"],
            reply_chunks=tuple(
                unicodedata.normalize("NFKC", item).strip()
                if type(item) is str
                else item
                for item in _tuple_items(decoded["reply_chunks"], "reply_chunks")
            ),
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
                )
                else False
            ),
            pending_disposition=pending_disposition,
            passengers=(
                tuple(
                    _passenger(item)
                    for item in _tuple_items(decoded["passengers"], "passengers")
                )
                if schema == "v2-model-proposal-v6"
                else ()
            ),
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

    def complete_audited(self, request: ModelRequest) -> AuditedModelTurn:
        if type(request) is not ModelRequest:
            raise TypeError("request must be an exact ModelRequest")
        if request.confirmation_review_required:
            base_prompt = _CONFIRMATION_REVIEW_SYSTEM_PROMPT
            prompts = (
                base_prompt,
                base_prompt + "\n\n" + _CONFIRMATION_REVIEW_REPAIR_SUFFIX,
            )
            wire = _confirmation_review_wire

            def decode(response: bytes) -> ModelProposal:
                return _proposal_from_confirmation_review(
                    request,
                    _confirmation_review(response, request.source_event_id),
                )

        else:
            base_prompt = self._system_prompt
            prompts = (
                base_prompt,
                base_prompt + "\n\n" + _PROTOCOL_REPAIR_SUFFIX,
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
                if not attempted_frames:
                    return turn
                return AuditedModelTurn.from_frames(
                    proposal=turn.proposal,
                    frames=(*attempted_frames, *turn.frames),
                    ephemeral_session_id=turn.closure.ephemeral_session_id,
                )
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
