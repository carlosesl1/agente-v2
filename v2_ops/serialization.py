from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date, datetime
from typing import TypeAlias

from reservation_boundary.public_dispatch import (
    PublicAcceptanceReceipt,
    PublicDispatchClaim,
)
from v2_contracts.channel import InboundBatch, InboundEvent
from v2_contracts.model import ModelProposal, ModelRequest
from v2_contracts.payments import PaymentInstruction, StripeStepReceipt
from v2_contracts.providers import (
    ProviderDispatchPermit,
    ProviderExecutionResult,
    ReadObservation,
    ReadRequest,
)
from v2_ops.contracts import FrozenJSONValue, validate_closed_json


SerializedPair: TypeAlias = tuple[dict[str, FrozenJSONValue], dict[str, FrozenJSONValue]]

_MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
        "audio/mpeg",
        "audio/ogg",
    }
)
_PUBLIC_STATUSES = frozenset(
    {
        "available",
        "unavailable",
        "confirmed",
        "pending",
        "sold_out",
        "open",
        "closed",
        "ok",
        "success",
    }
)
_PUBLIC_CURRENCIES = frozenset({"BRL", "USD", "EUR", "GBP"})
_PROVIDER_RESULT_STATUSES = frozenset({"confirmed", "rejected", "unknown"})


def _hash(domain: str, value: str) -> str:
    if type(value) is not str:
        raise TypeError("fingerprinted value must be an exact string")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + value.encode("utf-8")).hexdigest()


def _bytes(value: str) -> int:
    return len(value.encode("utf-8"))


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _closed_pair(value: dict[str, object]) -> SerializedPair:
    validate_closed_json(value)
    summary = dict(value)
    full = dict(value)
    return summary, full


def _serialize_inbound_event(value: InboundEvent) -> SerializedPair:
    return _closed_pair(
        {
            "event_id": value.event_id,
            "lead_id": value.lead_id,
            "occurred_at": _timestamp(value.occurred_at),
            "payload_hash": value.payload_hash,
            "text_bytes": _bytes(value.text),
            "text_hash": _hash("v2-ops-inbound-text-v1", value.text),
            "has_text": bool(value.text),
            "has_media": value.media_url is not None,
            "media_type": (
                value.media_type if value.media_type in _MEDIA_TYPES else "other"
            ),
            "subscriber_fingerprint": _hash(
                "v2-ops-subscriber-v1", value.subscriber_id
            ),
            "conversation_fingerprint": _hash(
                "v2-ops-conversation-v1", value.conversation_id
            ),
        }
    )


def _serialize_inbound_batch(value: InboundBatch) -> SerializedPair:
    return _closed_pair(
        {
            "batch_id": value.batch_id,
            "lead_id": value.lead_id,
            "event_ids": [item.event_id for item in value.events],
            "event_count": len(value.events),
            "earliest_at": _timestamp(value.events[0].occurred_at),
            "latest_at": _timestamp(value.events[-1].occurred_at),
            "combined_text_bytes": _bytes(value.combined_text),
            "combined_text_hash": _hash(
                "v2-ops-batch-text-v1", value.combined_text
            ),
            "media_count": sum(item.media_url is not None for item in value.events),
            "subscriber_fingerprint": _hash(
                "v2-ops-subscriber-v1", value.subscriber_id
            ),
        }
    )


def _serialize_model_request(value: ModelRequest) -> SerializedPair:
    return _closed_pair(
        {
            "request_id": value.request_id,
            "lead_id": value.lead_id,
            "source_event_id": value.source_event_id,
            "locale": value.locale,
            "state_version": value.state_version,
            "message_bytes": _bytes(value.message),
            "message_hash": _hash("v2-ops-model-message-v1", value.message),
            "dialogue_count": len(value.recent_dialogue),
            "observation_count": len(value.observations),
            "consultation_count": len(value.consultation_history),
            "fact_count": len(value.state_facts),
            "private_fact_names": list(value.private_customer_fact_names),
            "private_profile_complete": value.private_profile_complete,
            "handoff_status": value.handoff_status,
            "confirmation_review_required": value.confirmation_review_required,
            "selection_review_required": value.selection_review_required,
            "progress_review_required": value.progress_review_required,
            "active_execution_status": value.active_execution_status,
            "recap_reuse_required": value.recap_reuse_required,
            "correction_reason_count": len(value.public_reply_correction_reasons),
        }
    )


def _serialize_model_proposal(value: ModelProposal) -> SerializedPair:
    reply_text = "\0".join(value.reply_chunks)
    read_shapes = [
        {"request_id": item.request_id, "kind": item.kind.value}
        for item in value.read_requests
    ]
    effect_kinds = [item.kind for item in value.effect_proposals]
    target_ids = value.target_offer_ids or (
        () if value.target_offer_id is None else (value.target_offer_id,)
    )
    approval_basis = value.approval_basis
    approval_shape = (
        None
        if approval_basis is None
        else approval_basis.value
    )
    return _closed_pair(
        {
            "source_event_id": value.source_event_id,
            "intent": value.intent,
            "reply_chunk_count": len(value.reply_chunks),
            "reply_bytes": sum(_bytes(item) for item in value.reply_chunks),
            "reply_hash": _hash("v2-ops-model-reply-v1", reply_text),
            "fact_shapes": [
                {
                    "fact_kind": item.name,
                    "value_kind": type(item.value).__name__.casefold(),
                }
                for item in value.facts
            ],
            "read_shapes": read_shapes,
            "effect_kinds": effect_kinds,
            "target_offer_fingerprints": [
                _hash("v2-ops-target-offer-v1", item) for item in target_ids
            ],
            "confirmed_summary_version": value.confirmed_summary_version,
            "confirmed_action_kinds": [
                item.value for item in value.confirmed_action_kinds
            ],
            "approval_basis": approval_shape,
            "selection_requested": value.selection_requested,
            "pending_disposition": value.pending_disposition,
            "passenger_count": len(value.passengers),
            "has_clarification": value.clarification_question is not None,
        }
    )


def _serialize_read_request(value: ReadRequest) -> SerializedPair:
    return _closed_pair(
        {
            "request_id": value.request_id,
            "kind": value.kind.value,
            "query_bytes": None if value.query is None else _bytes(value.query),
            "query_hash": (
                None
                if value.query is None
                else _hash("v2-ops-read-query-v1", value.query)
            ),
            "locale": value.locale,
            "check_in": _date(value.check_in),
            "check_out": _date(value.check_out),
            "adults": value.adults,
            "children": value.children,
            "product_id": value.product_id,
            "activity_date": _date(value.activity_date),
            "participants": value.participants,
            "offer_id": value.offer_id,
        }
    )


def _public_scalar(payload: dict[str, object], key: str) -> str | int | None:
    item = payload.get(key)
    if type(item) in {str, int}:
        return item
    return None


def _serialize_read_observation(value: ReadObservation) -> SerializedPair:
    payload = value.public_payload
    option_count = _public_scalar(payload, "option_count")
    offer_count = _public_scalar(payload, "offer_count")
    status = _public_scalar(payload, "status")
    currency = _public_scalar(payload, "currency")
    return _closed_pair(
        {
            "request_hash": value.request_hash,
            "provider": value.provider,
            "observed_at": _timestamp(value.observed_at),
            "expires_at": _timestamp(value.expires_at),
            "status": status if status in _PUBLIC_STATUSES else None,
            "currency": currency if currency in _PUBLIC_CURRENCIES else None,
            "option_count": option_count if type(option_count) is int else None,
            "offer_count": offer_count if type(offer_count) is int else None,
            "public_payload_key_count": len(payload),
            "private_binding_hash": value.private_binding_hash,
        }
    )


def _serialize_provider_permit(value: ProviderDispatchPermit) -> SerializedPair:
    return _closed_pair(
        {
            "provider": value.provider,
            "operation": value.operation,
            "command_fingerprint": _hash("v2-ops-command-v1", value.command_id),
            "idempotency_fingerprint": _hash(
                "v2-ops-provider-idempotency-v1", value.idempotency_key
            ),
            "request_hash": value.request_hash,
            "payload_hash": value.payload_hash,
            "fence_generation": value.fencing_token,
            "authority_fingerprint": _hash(
                "v2-ops-provider-authorization-v1", value.authorization_id
            ),
        }
    )


def _serialize_provider_result(value: ProviderExecutionResult) -> SerializedPair:
    return _closed_pair(
        {
            "certainty": value.certainty.value,
            "normalized_status": (
                value.normalized_status
                if value.normalized_status in _PROVIDER_RESULT_STATUSES
                else "other"
            ),
            "provider_reference_fingerprint": value.provider_reference_fingerprint,
            "evidence": list(value.evidence),
            "evidence_count": len(value.evidence),
        }
    )


def _serialize_stripe_receipt(value: StripeStepReceipt) -> SerializedPair:
    return _closed_pair(
        {
            "step": value.step.value,
            "status": value.status.value,
            "expected_metadata_hash": value.expected_metadata_hash,
            "account_fingerprint": _hash(
                "v2-ops-stripe-account-v1", value.account_profile_id
            ),
            "idempotency_fingerprint": _hash(
                "v2-ops-stripe-idempotency-v1", value.idempotency_key
            ),
            "provider_object_fingerprint": (
                None
                if value.provider_object_id is None
                else _hash(
                    "v2-ops-stripe-object-v1", value.provider_object_id
                )
            ),
            "has_canonical_url": value.canonical_url is not None,
        }
    )


def _serialize_payment_instruction(value: PaymentInstruction) -> SerializedPair:
    return _closed_pair(
        {
            "payment_fingerprint": _hash("v2-ops-payment-v1", value.payment_id),
            "reservation_fingerprint": _hash(
                "v2-ops-reservation-anchor-v1", value.reservation_anchor_id
            ),
            "method": value.method.value,
            "receiver_fingerprint": _hash(
                "v2-ops-payment-receiver-v1", value.receiver_profile_id
            ),
            "economic_version": value.economic_version,
            "settled": value.settled,
            "instruction_bytes": _bytes(value.public_text),
            "instruction_hash": _hash(
                "v2-ops-payment-instruction-v1", value.public_text
            ),
        }
    )


def _serialize_public_claim(value: PublicDispatchClaim) -> SerializedPair:
    channel_id = (
        "manychat" if value.channel_id.casefold().startswith("manychat") else "other"
    )
    if value.channel_scope in {"subscriber", "whatsapp"}:
        channel_scope = value.channel_scope
    elif value.channel_scope.casefold().startswith("manychat:"):
        channel_scope = "manychat"
    else:
        channel_scope = "other"
    return _closed_pair(
        {
            "public_row_fingerprint": _hash(
                "v2-ops-public-row-v1", value.public_row_id
            ),
            "lead_fingerprint": _hash("v2-ops-public-lead-v1", value.lead_key),
            "turn_fingerprint": _hash(
                "v2-ops-public-turn-v1", value.aggregate_turn_id
            ),
            "chunk_ordinal": value.chunk.ordinal,
            "chunk_hash": value.chunk.canonical_hash(),
            "chunk_bytes": _bytes(value.chunk.text),
            "chunk_author": value.chunk.author.value,
            "idempotency_fingerprint": _hash(
                "v2-ops-public-idempotency-v1", value.idempotency_key
            ),
            "target_binding_hash": value.target_binding_hash,
            "channel_id": channel_id,
            "channel_scope": channel_scope,
            "authority_fingerprint": _hash(
                "v2-ops-public-authorization-v1", value.authorization_id
            ),
            "allocation_fingerprint": _hash(
                "v2-ops-public-allocation-v1", value.allocation_id
            ),
            "immutable_generation": value.immutable_generation,
            "source_turn_receipt_hash": value.source_turn_receipt_hash,
            "deadline_at": _timestamp(value.deadline_at),
            "worker_fingerprint": _hash(
                "v2-ops-public-worker-v1", value.worker_id
            ),
            "fence_generation": value.fencing_token,
            "lease_expires_at": _timestamp(value.lease_expires_at),
        }
    )


def _serialize_public_receipt(value: PublicAcceptanceReceipt) -> SerializedPair:
    acceptance = value.acceptance
    return _closed_pair(
        {
            "public_row_fingerprint": _hash(
                "v2-ops-public-row-v1", value.public_row_id
            ),
            "idempotency_fingerprint": _hash(
                "v2-ops-public-idempotency-v1", value.idempotency_key
            ),
            "state": acceptance.state.value,
            "operations": [item.value for item in acceptance.operations],
            "operation_count": len(acceptance.operations),
            "provider_request_fingerprints": [
                None
                if item is None
                else _hash("v2-ops-provider-request-v1", item)
                for item in acceptance.provider_request_ids
            ],
            "dispatch_correlation_fingerprints": [
                _hash("v2-ops-dispatch-correlation-v1", item)
                for item in acceptance.dispatch_correlation_ids
            ],
            "accepted_at": _timestamp(value.accepted_at),
            "receipt_hash": value.canonical_hash(),
        }
    )


_SERIALIZERS: dict[type[object], Callable[[object], SerializedPair]] = {
    InboundEvent: lambda value: _serialize_inbound_event(value),
    InboundBatch: lambda value: _serialize_inbound_batch(value),
    ModelRequest: lambda value: _serialize_model_request(value),
    ModelProposal: lambda value: _serialize_model_proposal(value),
    ReadRequest: lambda value: _serialize_read_request(value),
    ReadObservation: lambda value: _serialize_read_observation(value),
    ProviderDispatchPermit: lambda value: _serialize_provider_permit(value),
    ProviderExecutionResult: lambda value: _serialize_provider_result(value),
    StripeStepReceipt: lambda value: _serialize_stripe_receipt(value),
    PaymentInstruction: lambda value: _serialize_payment_instruction(value),
    PublicDispatchClaim: lambda value: _serialize_public_claim(value),
    PublicAcceptanceReceipt: lambda value: _serialize_public_receipt(value),
}


def serialize_ops_value(value: object) -> SerializedPair:
    serializer = _SERIALIZERS.get(type(value))
    if serializer is None:
        raise TypeError("unsupported exact DTO type")
    return serializer(value)
