from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from reservation_boundary.conversation import PublicMessageAuthor, PublicReplyChunk
from reservation_boundary.public_dispatch import (
    PublicAcceptanceReceipt,
    PublicDispatchClaim,
)
from v2_contracts.channel import (
    InboundBatch,
    InboundEvent,
    PublicAcceptanceOperation,
    PublicAcceptanceState,
    PublicChannelAcceptance,
)
from v2_contracts.model import ModelFact, ModelProposal, ModelRequest
from v2_contracts.payments import (
    PaymentInstruction,
    PaymentMethod,
    StripeCreationStep,
    StripeStepReceipt,
    StripeStepStatus,
)
from v2_contracts.providers import (
    ProviderCertainty,
    ProviderDispatchPermit,
    ProviderExecutionResult,
    ReadKind,
    ReadObservation,
    ReadRequest,
)
from v2_ops.contracts import canonical_json_bytes, validate_closed_json
from v2_ops.serialization import serialize_ops_value


NOW = datetime(2026, 8, 13, 18, 0, 0, tzinfo=timezone.utc)
H = "a" * 64
PRIVATE_SENTINELS = (
    "subscriber-private-sentinel",
    "conversation-private-sentinel",
    "customer-message-private-sentinel",
    "maya-reply-private-sentinel",
    "knowledge-query-private-sentinel",
    "payload-private-sentinel",
    "canonical-payload-private-sentinel",
    "provider-reference-private-sentinel",
    "https://buy.stripe.com/private-url-sentinel",
    "pix-instruction-private-sentinel",
    "scope-subject-private-sentinel",
    "public-chunk-private-sentinel",
    "provider-request-private-sentinel",
    "dispatch-correlation-private-sentinel",
)


def _fixtures() -> tuple[object, ...]:
    event = InboundEvent(
        event_id="event:ops-serialization",
        lead_id="manychat:opaque-lead",
        subscriber_id=PRIVATE_SENTINELS[0],
        conversation_id=PRIVATE_SENTINELS[1],
        text=PRIVATE_SENTINELS[2],
        media_url="https://media.invalid/payload-private-sentinel",
        media_type="image/jpeg",
        occurred_at=NOW,
        payload_hash=H,
    )
    batch = InboundBatch(
        batch_id="batch:ops-serialization",
        lead_id=event.lead_id,
        subscriber_id=event.subscriber_id,
        events=(event,),
        combined_text=event.text,
    )
    model_request = ModelRequest(
        request_id="request:ops-serialization",
        lead_id=event.lead_id,
        source_event_id=event.event_id,
        message=PRIVATE_SENTINELS[2],
        locale="pt-BR",
        state_version=3,
        private_customer_fact_names=("email",),
    )
    model_proposal = ModelProposal(
        source_event_id=event.event_id,
        intent="inform",
        reply_chunks=(PRIVATE_SENTINELS[3],),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    read_request = ReadRequest(
        request_id="read:ops-serialization",
        kind=ReadKind.KNOWLEDGE,
        query=PRIVATE_SENTINELS[4],
        locale="pt-BR",
    )
    read_observation = ReadObservation(
        request_hash="b" * 64,
        provider="knowledge",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        public_payload={
            "status": "available",
            "currency": "BRL",
            "option_count": 2,
            "private": PRIVATE_SENTINELS[5],
        },
        private_binding_hash="c" * 64,
    )
    canonical_payload = json.dumps(
        {"private": PRIVATE_SENTINELS[6]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    permit = ProviderDispatchPermit(
        provider="cloudbeds",
        operation="reserve_lodging",
        command_id="command:ops-serialization",
        idempotency_key="idempotency:ops-serialization",
        request_hash="d" * 64,
        payload_hash=hashlib.sha256(canonical_payload.encode()).hexdigest(),
        canonical_payload=canonical_payload,
        fencing_token=7,
        authorization_id="authorization:ops-serialization",
    )
    provider_result = ProviderExecutionResult(
        certainty=ProviderCertainty.EFFECT_CONFIRMED,
        normalized_status="confirmed",
        provider_reference_fingerprint=hashlib.sha256(
            PRIVATE_SENTINELS[7].encode()
        ).hexdigest(),
        evidence=("e" * 64,),
        provider_reference=PRIVATE_SENTINELS[7],
    )
    stripe_receipt = StripeStepReceipt(
        step=StripeCreationStep.PAYMENT_LINK,
        status=StripeStepStatus.ACCEPTED,
        account_profile_id="account-private-sentinel",
        expected_metadata_hash="f" * 64,
        idempotency_key="stripe-idempotency-private-sentinel",
        provider_object_id="plink_private_sentinel",
        canonical_url=PRIVATE_SENTINELS[8],
    )
    payment_instruction = PaymentInstruction(
        payment_id="payment:ops-serialization",
        reservation_anchor_id="anchor:ops-serialization",
        method=PaymentMethod.PIX,
        receiver_profile_id="receiver-private-sentinel",
        economic_version=2,
        public_text=PRIVATE_SENTINELS[9],
    )
    chunk = PublicReplyChunk(
        aggregate_turn_id="turn:ops-serialization",
        ordinal=0,
        text=PRIVATE_SENTINELS[11],
        source_closure_hash="1" * 64,
        author=PublicMessageAuthor.MAYA,
    )
    claim = PublicDispatchClaim(
        public_row_id="public-row:ops-serialization",
        lead_key="lead-key:ops-serialization",
        aggregate_turn_id=chunk.aggregate_turn_id,
        chunk=chunk,
        idempotency_key="public-idempotency-private-sentinel",
        target_binding_hash="2" * 64,
        channel_id="manychat",
        channel_scope="subscriber",
        scope_subject_id=PRIVATE_SENTINELS[10],
        authorization_id="public-authorization-private-sentinel",
        allocation_id="public-allocation-private-sentinel",
        immutable_generation=4,
        source_turn_receipt_hash="3" * 64,
        deadline_at=NOW + timedelta(minutes=2),
        worker_id="worker-private-sentinel",
        fencing_token=5,
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    acceptance = PublicChannelAcceptance(
        state=PublicAcceptanceState.ACCEPTED_BY_MANYCHAT,
        operations=(PublicAcceptanceOperation.SEND_CONTENT,),
        provider_request_ids=(PRIVATE_SENTINELS[12],),
        dispatch_correlation_ids=(PRIVATE_SENTINELS[13],),
    )
    receipt = PublicAcceptanceReceipt(
        public_row_id=claim.public_row_id,
        idempotency_key=claim.idempotency_key,
        acceptance=acceptance,
        accepted_at=NOW + timedelta(seconds=3),
    )
    return (
        event,
        batch,
        model_request,
        model_proposal,
        read_request,
        read_observation,
        permit,
        provider_result,
        stripe_receipt,
        payment_instruction,
        claim,
        receipt,
    )


EXPECTED_KEYS = (
    frozenset(
        {
            "event_id",
            "lead_id",
            "occurred_at",
            "payload_hash",
            "text_bytes",
            "text_hash",
            "has_text",
            "has_media",
            "media_type",
            "subscriber_fingerprint",
            "conversation_fingerprint",
        }
    ),
    frozenset(
        {
            "batch_id",
            "lead_id",
            "event_ids",
            "event_count",
            "earliest_at",
            "latest_at",
            "combined_text_bytes",
            "combined_text_hash",
            "media_count",
            "subscriber_fingerprint",
        }
    ),
    frozenset(
        {
            "request_id",
            "lead_id",
            "source_event_id",
            "locale",
            "state_version",
            "message_bytes",
            "message_hash",
            "dialogue_count",
            "observation_count",
            "consultation_count",
            "fact_count",
            "private_fact_names",
            "private_profile_complete",
            "handoff_status",
            "confirmation_review_required",
            "selection_review_required",
            "progress_review_required",
            "active_execution_status",
            "recap_reuse_required",
            "correction_reason_count",
        }
    ),
    frozenset(
        {
            "source_event_id",
            "intent",
            "reply_chunk_count",
            "reply_bytes",
            "reply_hash",
            "fact_shapes",
            "read_shapes",
            "effect_kinds",
            "target_offer_fingerprints",
            "confirmed_summary_version",
            "confirmed_action_kinds",
            "approval_basis",
            "selection_requested",
            "pending_disposition",
            "passenger_count",
            "has_clarification",
        }
    ),
    frozenset(
        {
            "request_id",
            "kind",
            "query_bytes",
            "query_hash",
            "locale",
            "check_in",
            "check_out",
            "adults",
            "children",
            "product_id",
            "activity_date",
            "participants",
            "offer_id",
        }
    ),
    frozenset(
        {
            "request_hash",
            "provider",
            "observed_at",
            "expires_at",
            "status",
            "currency",
            "option_count",
            "offer_count",
            "public_payload_key_count",
            "private_binding_hash",
        }
    ),
    frozenset(
        {
            "provider",
            "operation",
            "command_fingerprint",
            "idempotency_fingerprint",
            "request_hash",
            "payload_hash",
            "fence_generation",
            "authority_fingerprint",
        }
    ),
    frozenset(
        {
            "certainty",
            "normalized_status",
            "provider_reference_fingerprint",
            "evidence",
            "evidence_count",
        }
    ),
    frozenset(
        {
            "step",
            "status",
            "expected_metadata_hash",
            "account_fingerprint",
            "idempotency_fingerprint",
            "provider_object_fingerprint",
            "has_canonical_url",
        }
    ),
    frozenset(
        {
            "payment_fingerprint",
            "reservation_fingerprint",
            "method",
            "receiver_fingerprint",
            "economic_version",
            "settled",
            "instruction_bytes",
            "instruction_hash",
        }
    ),
    frozenset(
        {
            "public_row_fingerprint",
            "lead_fingerprint",
            "turn_fingerprint",
            "chunk_ordinal",
            "chunk_hash",
            "chunk_bytes",
            "chunk_author",
            "idempotency_fingerprint",
            "target_binding_hash",
            "channel_id",
            "channel_scope",
            "authority_fingerprint",
            "allocation_fingerprint",
            "immutable_generation",
            "source_turn_receipt_hash",
            "deadline_at",
            "worker_fingerprint",
            "fence_generation",
            "lease_expires_at",
        }
    ),
    frozenset(
        {
            "public_row_fingerprint",
            "idempotency_fingerprint",
            "state",
            "operations",
            "operation_count",
            "provider_request_fingerprints",
            "dispatch_correlation_fingerprints",
            "accepted_at",
            "receipt_hash",
        }
    ),
)


def test_all_twelve_serializers_have_closed_exact_bounded_allowlists() -> None:
    fixtures = _fixtures()
    assert len(fixtures) == len(EXPECTED_KEYS) == 12
    for value, keys in zip(fixtures, EXPECTED_KEYS, strict=True):
        summary, full = serialize_ops_value(value)
        assert frozenset(summary) == keys
        assert frozenset(full) == keys
        validate_closed_json(summary)
        validate_closed_json(full)
        assert len(canonical_json_bytes(summary)) <= 16_384
        assert len(canonical_json_bytes(full)) <= 16_384


def test_forbidden_private_prose_urls_and_provider_values_are_absent() -> None:
    encoded = b"".join(
        canonical_json_bytes(part)
        for value in _fixtures()
        for part in serialize_ops_value(value)
    )
    for sentinel in PRIVATE_SENTINELS:
        assert sentinel.encode() not in encoded
    for extra in (
        "account-private-sentinel",
        "stripe-idempotency-private-sentinel",
        "plink_private_sentinel",
        "receiver-private-sentinel",
        "public-idempotency-private-sentinel",
        "public-authorization-private-sentinel",
        "public-allocation-private-sentinel",
        "worker-private-sentinel",
    ):
        assert extra.encode() not in encoded


def test_same_length_prose_changes_hash_without_revealing_prose() -> None:
    first = ModelRequest(
        request_id="request:first",
        lead_id="lead:opaque",
        source_event_id="event:first",
        message="segredo-AA",
        locale="pt-BR",
        state_version=0,
    )
    second = ModelRequest(
        request_id="request:second",
        lead_id="lead:opaque",
        source_event_id="event:second",
        message="segredo-BB",
        locale="pt-BR",
        state_version=0,
    )
    first_full = serialize_ops_value(first)[1]
    second_full = serialize_ops_value(second)[1]
    assert first_full["message_bytes"] == second_full["message_bytes"]
    assert first_full["message_hash"] != second_full["message_hash"]
    encoded = canonical_json_bytes(first_full) + canonical_json_bytes(second_full)
    assert b"segredo-AA" not in encoded
    assert b"segredo-BB" not in encoded


def test_nested_fact_values_and_arbitrary_public_labels_never_escape_allowlists() -> None:
    proposal = ModelProposal(
        source_event_id="event:fact-redaction",
        intent="inform",
        reply_chunks=("reply-safe-shape",),
        facts=(ModelFact(name="language", value="fact-value-private-sentinel"),),
        read_requests=(),
        effect_proposals=(),
    )
    observation = ReadObservation(
        request_hash="9" * 64,
        provider="knowledge",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
        public_payload={
            "status": "status-private-sentinel",
            "currency": "currency-private-sentinel",
        },
        private_binding_hash="8" * 64,
    )
    event = InboundEvent(
        event_id="event:media-redaction",
        lead_id="lead:media-redaction",
        subscriber_id="subscriber-media-private-sentinel",
        conversation_id="conversation-media-private-sentinel",
        text="event-media-private-sentinel",
        media_url="https://media.invalid/media-url-private-sentinel",
        media_type="application/x-private-sentinel",
        occurred_at=NOW,
        payload_hash="7" * 64,
    )

    proposal_full = serialize_ops_value(proposal)[1]
    observation_full = serialize_ops_value(observation)[1]
    event_full = serialize_ops_value(event)[1]
    encoded = canonical_json_bytes(proposal_full)
    assert proposal_full["fact_shapes"] == [
        {"fact_kind": "language", "value_kind": "str"}
    ]
    assert b"fact-value-private-sentinel" not in encoded
    assert observation_full["status"] is None
    assert observation_full["currency"] is None
    assert event_full["media_type"] == "other"


@pytest.mark.parametrize("value", _fixtures())
def test_subclasses_are_rejected_before_field_access(value: object) -> None:
    subclass = type(f"Sub{type(value).__name__}", (type(value),), {})
    forged = object.__new__(subclass)
    with pytest.raises(TypeError, match="unsupported exact DTO type"):
        serialize_ops_value(forged)


def test_unknown_and_duck_typed_values_fail_closed() -> None:
    class Duck:
        event_id = "event:duck"
        lead_id = "lead:duck"

    for value in (Duck(), {"event_id": "event:mapping"}, object()):
        with pytest.raises(TypeError, match="unsupported exact DTO type"):
            serialize_ops_value(value)
