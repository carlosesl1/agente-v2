from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from v2_ops.contracts import ExecutionStatus, NodeType, TraceCompleteness
from v2_ops.dashboard import (
    DashboardRange,
    DashboardRecord,
    build_dashboard_snapshot,
)
from v2_ops.store import OpsExecutionView

NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)


def execution(
    execution_id: str,
    *,
    lead_id: str,
    received_at: datetime,
    status: str,
    completed_at: datetime | None = None,
    completeness: TraceCompleteness = TraceCompleteness.COMPLETE_TRACE,
    terminal_reason: str | None = None,
) -> OpsExecutionView:
    stored = ExecutionStatus.RUNNING if status == "running_stale" else ExecutionStatus(status)
    return OpsExecutionView(
        execution_id=execution_id,
        lead_id=lead_id,
        received_at=received_at,
        completed_at=completed_at,
        status=status,
        stored_status=stored,
        trace_completeness=completeness,
        current_node_id=None,
        terminal_reason=terminal_reason,
    )


def record(view: OpsExecutionView, *node_types: NodeType) -> DashboardRecord:
    return DashboardRecord(
        execution=view,
        node_types=tuple(node_types),
        current_node_type=node_types[-1] if node_types else None,
        node_count=len(node_types),
    )


def test_empty_snapshot_uses_zero_counts_and_null_rates() -> None:
    snapshot = build_dashboard_snapshot((), range_key=DashboardRange.D7, generated_at=NOW)
    assert snapshot.metrics == {
        "executions": 0,
        "distinct_leads": 0,
        "in_progress": 0,
        "completed": 0,
        "failed": 0,
        "manual_review": 0,
        "technical_completion_rate": None,
        "average_terminal_duration_ms": None,
    }
    assert len(snapshot.execution_series) == 7
    assert {point["count"] for point in snapshot.execution_series} == {0}
    assert snapshot.status_distribution == (
        {"status": "pending", "count": 0},
        {"status": "running", "count": 0},
        {"status": "running_stale", "count": 0},
        {"status": "completed", "count": 0},
        {"status": "failed", "count": 0},
        {"status": "manual_review", "count": 0},
    )
    assert snapshot.trace_distribution == (
        {"trace_completeness": "complete_trace", "count": 0},
        {"trace_completeness": "partial_trace", "count": 0},
        {"trace_completeness": "ledger_only", "count": 0},
    )
    assert snapshot.milestones == (
        {"milestone": "reservation", "count": 0},
        {"milestone": "payment", "count": 0},
        {"milestone": "public_delivery", "count": 0},
        {"milestone": "handoff", "count": 0},
    )
    assert snapshot.executions == ()


def test_metrics_use_received_at_window_and_exact_technical_mean() -> None:
    records = (
        record(
            execution(
                "a",
                lead_id="lead-1",
                received_at=NOW - timedelta(days=1),
                status="completed",
                completed_at=NOW - timedelta(days=1) + timedelta(seconds=2),
            )
        ),
        record(
            execution(
                "b",
                lead_id="lead-1",
                received_at=NOW - timedelta(hours=2),
                status="failed",
                completed_at=NOW - timedelta(hours=2) + timedelta(seconds=4),
            )
        ),
        record(
            execution(
                "c",
                lead_id="lead-2",
                received_at=NOW,
                status="running_stale",
            )
        ),
    )
    snapshot = build_dashboard_snapshot(records, range_key=DashboardRange.D7, generated_at=NOW)
    assert snapshot.metrics["executions"] == 3
    assert snapshot.metrics["distinct_leads"] == 2
    assert snapshot.metrics["in_progress"] == 1
    assert snapshot.metrics["completed"] == 1
    assert snapshot.metrics["failed"] == 1
    assert snapshot.metrics["technical_completion_rate"] == pytest.approx(100 / 3)
    assert snapshot.metrics["average_terminal_duration_ms"] == 3000


def test_all_six_statuses_witness_all_eight_metric_formulas_exactly() -> None:
    cases = (
        ("pending", None),
        ("running", None),
        ("running_stale", None),
        ("completed", timedelta(seconds=2)),
        ("failed", timedelta(seconds=5)),
        ("manual_review", timedelta(seconds=11)),
    )
    records = tuple(
        record(
            execution(
                f"metric-{index}",
                lead_id=("shared-lead" if index < 3 else f"terminal-lead-{index}"),
                received_at=NOW - timedelta(minutes=index + 1),
                status=status,
                completed_at=(
                    None
                    if duration is None
                    else NOW - timedelta(minutes=index + 1) + duration
                ),
            )
        )
        for index, (status, duration) in enumerate(cases)
    )

    snapshot = build_dashboard_snapshot(records, range_key=DashboardRange.D7, generated_at=NOW)

    assert snapshot.metrics == {
        "executions": 6,
        "distinct_leads": 4,
        "in_progress": 3,
        "completed": 1,
        "failed": 1,
        "manual_review": 1,
        "technical_completion_rate": pytest.approx(100 / 6),
        "average_terminal_duration_ms": 6000,
    }


def test_ranges_are_closed_and_boundaries_are_inclusive() -> None:
    assert [item.value for item in DashboardRange] == ["24h", "7d", "30d"]
    assert DashboardRange.parse("24h") is DashboardRange.H24
    with pytest.raises(ValueError, match="range"):
        DashboardRange.parse("today")
    with pytest.raises(ValueError, match="range"):
        DashboardRange.parse(7)  # type: ignore[arg-type]
    edge = record(
        execution(
            "edge",
            lead_id="lead",
            received_at=NOW - timedelta(hours=24),
            status="pending",
        )
    )
    older = record(
        execution(
            "older",
            lead_id="lead",
            received_at=NOW - timedelta(hours=24, microseconds=1),
            status="pending",
        )
    )
    future = record(
        execution(
            "future",
            lead_id="lead",
            received_at=NOW + timedelta(microseconds=1),
            status="pending",
        )
    )
    snapshot = build_dashboard_snapshot(
        (edge, older, future), range_key=DashboardRange.H24, generated_at=NOW
    )
    assert snapshot.metrics["executions"] == 1


@pytest.mark.parametrize(
    ("range_key", "expected_duration", "expected_buckets", "expected_bucket_duration"),
    (
        (DashboardRange.H24, timedelta(hours=24), 24, timedelta(hours=1)),
        (DashboardRange.D7, timedelta(days=7), 7, timedelta(days=1)),
        (DashboardRange.D30, timedelta(days=30), 30, timedelta(days=1)),
    ),
)
def test_each_range_has_independent_inclusive_limits_and_exact_zero_filled_buckets(
    range_key: DashboardRange,
    expected_duration: timedelta,
    expected_buckets: int,
    expected_bucket_duration: timedelta,
) -> None:
    lower = NOW - expected_duration
    records = (
        record(execution("lower", lead_id="lower", received_at=lower, status="pending")),
        record(
            execution(
                "before-lower",
                lead_id="before-lower",
                received_at=lower - timedelta(microseconds=1),
                status="pending",
            )
        ),
        record(execution("upper", lead_id="upper", received_at=NOW, status="pending")),
        record(
            execution(
                "after-upper",
                lead_id="after-upper",
                received_at=NOW + timedelta(microseconds=1),
                status="pending",
            )
        ),
    )
    snapshot = build_dashboard_snapshot(records, range_key=range_key, generated_at=NOW)
    assert snapshot.metrics["executions"] == 2
    assert len(snapshot.execution_series) == expected_buckets
    assert snapshot.execution_series[0] == {"start_at": lower, "count": 1}
    assert snapshot.execution_series[-1] == {
        "start_at": NOW - expected_bucket_duration,
        "count": 1,
    }
    assert tuple(point["start_at"] for point in snapshot.execution_series) == tuple(
        lower + index * expected_bucket_duration for index in range(expected_buckets)
    )
    assert all(point["count"] == 0 for point in snapshot.execution_series[1:-1])


def test_status_and_trace_distributions_emit_closed_catalogs_in_stable_order() -> None:
    statuses = ("pending", "running", "running_stale", "completed", "failed", "manual_review")
    completeness = (
        TraceCompleteness.COMPLETE_TRACE,
        TraceCompleteness.PARTIAL_TRACE,
        TraceCompleteness.LEDGER_ONLY,
    )
    records = tuple(
        record(
            execution(
                f"execution-{index}",
                lead_id=f"lead-{index}",
                received_at=NOW - timedelta(minutes=index),
                status=status,
                completed_at=(NOW - timedelta(minutes=index - 1))
                if status in {"completed", "failed", "manual_review"}
                else None,
                completeness=completeness[index % len(completeness)],
            )
        )
        for index, status in enumerate(statuses)
    )
    snapshot = build_dashboard_snapshot(records, range_key=DashboardRange.D7, generated_at=NOW)
    assert snapshot.status_distribution == tuple(
        {"status": status, "count": 1} for status in statuses
    )
    assert snapshot.trace_distribution == (
        {"trace_completeness": "complete_trace", "count": 2},
        {"trace_completeness": "partial_trace", "count": 2},
        {"trace_completeness": "ledger_only", "count": 2},
    )


def test_milestones_deduplicate_per_execution_while_node_ranking_counts_retries() -> None:
    overlapping = record(
        execution("overlap", lead_id="lead", received_at=NOW, status="running"),
        NodeType.CLOUDBEDS_RESERVATION_REQUEST,
        NodeType.STRIPE_PRODUCT,
        NodeType.STRIPE_PRODUCT,
        NodeType.PUBLIC_OUTBOX,
        NodeType.HANDOFF_REQUEST,
    )
    snapshot = build_dashboard_snapshot(
        (overlapping,), range_key=DashboardRange.D7, generated_at=NOW
    )
    assert snapshot.milestones == (
        {"milestone": "reservation", "count": 1},
        {"milestone": "payment", "count": 1},
        {"milestone": "public_delivery", "count": 1},
        {"milestone": "handoff", "count": 1},
    )
    assert snapshot.top_node_types[0] == {"node_type": "stripe_product", "count": 2}
    assert sum(item["count"] for item in snapshot.top_node_types) == 5
    assert snapshot.executions[0]["has_reservation"] is True
    assert snapshot.executions[0]["has_payment"] is True
    assert snapshot.executions[0]["has_public_delivery"] is True
    assert snapshot.executions[0]["has_handoff"] is True


def test_top_node_types_are_limited_to_ten_and_ties_use_enum_value_order() -> None:
    node_types = tuple(list(NodeType)[:12])
    snapshot = build_dashboard_snapshot(
        (record(execution("nodes", lead_id="lead", received_at=NOW, status="pending"), *node_types),),
        range_key=DashboardRange.D7,
        generated_at=NOW,
    )
    expected = sorted(item.value for item in node_types)[:10]
    assert [item["node_type"] for item in snapshot.top_node_types] == expected
    assert {item["count"] for item in snapshot.top_node_types} == {1}


def test_execution_rows_are_recent_first_bounded_and_use_only_approved_fields() -> None:
    records = tuple(
        record(
            execution(
                f"execution-{index:03d}",
                lead_id=f"lead-{index:03d}",
                received_at=NOW - timedelta(minutes=index),
                status="completed",
                completed_at=NOW - timedelta(minutes=index) + timedelta(milliseconds=1250),
                terminal_reason="terminal",
            ),
            NodeType.MAYA_RESPONSE,
        )
        for index in range(4)
    )
    tied = record(
        execution("zz-tie", lead_id="lead-tie", received_at=NOW, status="pending")
    )
    snapshot = build_dashboard_snapshot(
        (*records, tied),
        range_key=DashboardRange.D7,
        generated_at=NOW,
        table_limit=3,
    )
    assert [row["execution_id"] for row in snapshot.executions] == [
        "zz-tie",
        "execution-000",
        "execution-001",
    ]
    assert set(snapshot.executions[1]) == {
        "lead_id",
        "execution_id",
        "received_at",
        "duration_ms",
        "status",
        "trace_completeness",
        "current_node_type",
        "node_count",
        "has_reservation",
        "has_payment",
        "has_public_delivery",
        "has_handoff",
        "terminal_reason",
    }
    assert snapshot.executions[1]["duration_ms"] == 1250
    assert snapshot.executions[0]["duration_ms"] is None


def test_builder_rejects_non_utc_time_non_exact_range_and_invalid_table_limit() -> None:
    with pytest.raises(ValueError, match="generated_at"):
        build_dashboard_snapshot(
            (), range_key=DashboardRange.D7, generated_at=NOW.replace(tzinfo=None)
        )
    with pytest.raises(ValueError, match="range"):
        build_dashboard_snapshot((), range_key="7d", generated_at=NOW)  # type: ignore[arg-type]
    for invalid in (True, 0, 101):
        with pytest.raises(ValueError, match="table_limit"):
            build_dashboard_snapshot(
                (), range_key=DashboardRange.D7, generated_at=NOW, table_limit=invalid
            )


def test_dashboard_module_has_no_commercial_or_text_inference() -> None:
    source = Path("v2_ops/dashboard.py").read_text(encoding="utf-8").casefold()
    for forbidden in (
        "input_summary",
        "output_summary",
        "chapada_leads",
        "revenue",
        "sentiment",
    ):
        assert forbidden not in source
