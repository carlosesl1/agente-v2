from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from v2_ops.contracts import (
    MAX_FULL_JSON_BYTES,
    MAX_SUMMARY_JSON_BYTES,
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
    canonical_json_bytes,
    canonical_json_text,
    deterministic_node_id,
    format_utc_timestamp,
    validate_closed_json,
    validate_monotonic_ordinals,
)


UTC_NOW = datetime(2026, 8, 13, 12, 30, 45, 123456, tzinfo=timezone.utc)


def test_contract_enums_are_closed_and_exact() -> None:
    assert [item.value for item in ExecutionStatus] == [
        "pending",
        "running",
        "completed",
        "failed",
        "manual_review",
    ]
    assert [item.value for item in TraceCompleteness] == [
        "complete_trace",
        "partial_trace",
        "ledger_only",
    ]
    assert [item.value for item in NodeType] == [
        "manychat_webhook",
        "router_validation",
        "inbox_accept",
        "inbox_claim",
        "maya_request",
        "maya_response",
        "maya_correction",
        "maya_review",
        "maya_read_request",
        "provider_read_request",
        "provider_read_response",
        "maya_observation",
        "conversation_reducer",
        "turn_commit",
        "boundary_relay",
        "cloudbeds_reservation_request",
        "cloudbeds_reservation_response",
        "bokun_booking_request",
        "bokun_booking_response",
        "stripe_product",
        "stripe_price",
        "stripe_payment_link",
        "pix_instruction",
        "wise_instruction",
        "settlement",
        "public_outbox",
        "manychat_delivery_request",
        "manychat_delivery_response",
        "handoff_request",
        "handoff_delivery",
        "provider_reconciliation",
        "stripe_reconciliation",
        "manual_review",
    ]


def test_execution_dto_is_frozen_and_requires_exact_utc_time() -> None:
    execution = OpsExecution(
        execution_id="event-001",
        lead_id="manychat:opaque-lead",
        received_at=UTC_NOW,
        status=ExecutionStatus.RUNNING,
        trace_completeness=TraceCompleteness.COMPLETE_TRACE,
        current_node_id=None,
    )

    assert execution.received_at is UTC_NOW
    assert format_utc_timestamp(execution.received_at) == "2026-08-13T12:30:45.123456Z"
    with pytest.raises(FrozenInstanceError):
        execution.status = ExecutionStatus.COMPLETED  # type: ignore[misc]

    with pytest.raises(ValueError, match="UTC"):
        OpsExecution(
            execution_id="event-naive",
            lead_id="manychat:opaque-lead",
            received_at=UTC_NOW.replace(tzinfo=None),
        )
    with pytest.raises(ValueError, match="UTC"):
        OpsExecution(
            execution_id="event-offset",
            lead_id="manychat:opaque-lead",
            received_at=UTC_NOW.astimezone(timezone(timedelta(hours=-3))),
        )
    with pytest.raises(ValueError, match="before"):
        OpsExecution(
            execution_id="event-order",
            lead_id="manychat:opaque-lead",
            received_at=UTC_NOW,
            completed_at=UTC_NOW - timedelta(microseconds=1),
        )
    for terminal_status in (
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.MANUAL_REVIEW,
    ):
        with pytest.raises(ValueError, match="completed_at"):
            OpsExecution(
                execution_id="event-terminal",
                lead_id="manychat:opaque-lead",
                received_at=UTC_NOW,
                status=terminal_status,
            )
    for nonterminal_status in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
        with pytest.raises(ValueError, match="completed_at"):
            OpsExecution(
                execution_id="event-nonterminal",
                lead_id="manychat:opaque-lead",
                received_at=UTC_NOW,
                status=nonterminal_status,
                completed_at=UTC_NOW,
            )


def test_node_dtos_are_deeply_immutable_and_share_deterministic_identity() -> None:
    start = OpsNodeStart(
        execution_id="event-001",
        node_type=NodeType.PROVIDER_READ_REQUEST,
        ordinal=6,
        attempt=1,
        started_at=UTC_NOW,
        input_summary={"provider": "cloudbeds", "counts": {"offers": 1}},
        input_full={"operation": "availability", "parameters": ["date", "occupancy"]},
        technical_metadata={"claim_hash": "fixture-hash"},
    )
    finish = OpsNodeFinish.from_start(
        start,
        status=ExecutionStatus.COMPLETED,
        completed_at=UTC_NOW + timedelta(milliseconds=4),
        output_summary={"status": "available"},
        output_full={"offer_count": 1},
    )

    assert start.node_id == finish.node_id
    assert start.node_id == deterministic_node_id(
        execution_id="event-001",
        node_type=NodeType.PROVIDER_READ_REQUEST,
        ordinal=6,
        attempt=1,
    )
    assert len(start.node_id) == 64
    assert start.node_id != deterministic_node_id(
        execution_id="event-001",
        node_type=NodeType.PROVIDER_READ_REQUEST,
        ordinal=6,
        attempt=2,
    )
    with pytest.raises(FrozenInstanceError):
        start.ordinal = 7  # type: ignore[misc]
    with pytest.raises(TypeError):
        start.input_summary["provider"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        start.input_summary["counts"]["offers"] = 2  # type: ignore[index]
    assert canonical_json_text(start.input_summary) == (
        '{"counts":{"offers":1},"provider":"cloudbeds"}'
    )

    with pytest.raises(ValueError, match="before"):
        OpsNodeFinish.from_start(
            start,
            status=ExecutionStatus.FAILED,
            completed_at=UTC_NOW - timedelta(microseconds=1),
        )
    with pytest.raises(ValueError, match="terminal"):
        OpsNodeFinish.from_start(
            start,
            status=ExecutionStatus.RUNNING,
            completed_at=UTC_NOW,
        )
    with pytest.raises(TypeError, match="ExecutionStatus"):
        OpsNodeFinish.from_start(
            start,
            status="completed",  # type: ignore[arg-type]
            completed_at=UTC_NOW,
        )


def test_node_ordinals_are_strictly_monotonic_and_attempts_start_at_one() -> None:
    first = OpsNodeStart(
        execution_id="event-001",
        node_type=NodeType.MAYA_REQUEST,
        ordinal=1,
        attempt=1,
        started_at=UTC_NOW,
    )
    second = OpsNodeStart(
        execution_id="event-001",
        node_type=NodeType.MAYA_RESPONSE,
        ordinal=2,
        attempt=1,
        started_at=UTC_NOW + timedelta(milliseconds=1),
    )
    validate_monotonic_ordinals([first, second])

    duplicate = OpsNodeStart(
        execution_id="event-001",
        node_type=NodeType.MAYA_REVIEW,
        ordinal=2,
        attempt=1,
        started_at=UTC_NOW + timedelta(milliseconds=2),
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_monotonic_ordinals([first, second, duplicate])
    with pytest.raises(ValueError, match="attempt"):
        OpsNodeStart(
            execution_id="event-001",
            node_type=NodeType.MAYA_REQUEST,
            ordinal=1,
            attempt=0,
            started_at=UTC_NOW,
        )
    with pytest.raises(ValueError, match="ordinal"):
        OpsNodeStart(
            execution_id="event-001",
            node_type=NodeType.MAYA_REQUEST,
            ordinal=0,
            attempt=1,
            started_at=UTC_NOW,
        )


def test_canonical_json_is_deterministic_and_accepts_only_closed_json() -> None:
    value = {"unicode": "observação", "items": [True, None, 3], "a": {"z": 2}}

    assert canonical_json_text(value) == (
        '{"a":{"z":2},"items":[true,null,3],"unicode":"observação"}'
    )
    assert canonical_json_bytes(value) == canonical_json_text(value).encode("utf-8")
    assert validate_closed_json(value) == value

    for invalid in (
        {"not_json": object()},
        {"not_json": {1, 2}},
        {"not_json": (1, 2)},
        {1: "non-string-key"},
        {"not_finite": float("nan")},
        {"not_finite": float("inf")},
    ):
        with pytest.raises((TypeError, ValueError)):
            validate_closed_json(invalid)


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "authorization",
        "access_token",
        "client_secret",
        "password",
        "cookie",
        "raw_headers",
        "provider_signature",
        "email",
        "phone_number",
        "document_number",
        "passport",
        "customer_name",
    ],
)
def test_forbidden_keys_are_rejected_recursively(forbidden_key: str) -> None:
    with pytest.raises(ValueError, match="forbidden key"):
        validate_closed_json({"safe": [{"nested": {forbidden_key: "fixture"}}]})


@pytest.mark.parametrize(
    "forbidden_content",
    [
        "Bearer abc",
        "Basic xyz",
        "Bearer fixture-provider-credential",
        "Basic fixture-provider-credential",
        "https://provider.invalid/resource?X-Amz-Signature=fixture",
        "https://provider.invalid/resource?X-Amz-Signature%3Dfixture",
        "https://provider.invalid/resource?access_token=fixture",
        "https://fixture-user:fixture-pass@provider.invalid/resource",
        "sk_live_fixture_provider_credential",
    ],
)
def test_signed_urls_and_provider_credentials_are_rejected_in_content(
    forbidden_content: str,
) -> None:
    with pytest.raises(ValueError, match="credential|signed URL"):
        OpsNodeStart(
            execution_id="event-001",
            node_type=NodeType.PROVIDER_READ_REQUEST,
            ordinal=1,
            started_at=UTC_NOW,
            input_full={"value": forbidden_content},
        )


def test_payload_byte_limits_are_enforced() -> None:
    assert 0 < MAX_SUMMARY_JSON_BYTES < MAX_FULL_JSON_BYTES

    with pytest.raises(ValueError, match="byte limit"):
        OpsNodeStart(
            execution_id="event-001",
            node_type=NodeType.MAYA_REQUEST,
            ordinal=1,
            started_at=UTC_NOW,
            input_summary={"safe_text": "x" * MAX_SUMMARY_JSON_BYTES},
        )
    with pytest.raises(ValueError, match="byte limit"):
        OpsNodeStart(
            execution_id="event-001",
            node_type=NodeType.MAYA_REQUEST,
            ordinal=1,
            started_at=UTC_NOW,
            input_full={"safe_text": "x" * MAX_FULL_JSON_BYTES},
        )


@pytest.mark.parametrize("invalid_id", ["event\x00id", "x" * 257])
def test_execution_and_lead_ids_are_bounded_nul_free_text(invalid_id: str) -> None:
    with pytest.raises(ValueError, match="NUL|byte limit"):
        OpsExecution(
            execution_id=invalid_id,
            lead_id="manychat:opaque-lead",
            received_at=UTC_NOW,
        )
    with pytest.raises(ValueError, match="NUL|byte limit"):
        OpsExecution(
            execution_id="event-001",
            lead_id=invalid_id,
            received_at=UTC_NOW,
        )
