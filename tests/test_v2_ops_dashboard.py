from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
)
from v2_ops.dashboard import (
    DashboardRange,
    DashboardRecord,
    build_dashboard_snapshot,
)
from v2_ops.store import OpsExecutionView, SQLiteOpsTraceReader, SQLiteOpsTraceWriter

NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))


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


def persisted_execution(execution_id: str, received_at: datetime) -> OpsExecution:
    return OpsExecution(
        execution_id=execution_id,
        lead_id=f"lead:{execution_id}",
        received_at=received_at,
        status=ExecutionStatus.PENDING,
        trace_completeness=TraceCompleteness.COMPLETE_TRACE,
    )


def persisted_node(
    execution_id: str,
    node_type: NodeType,
    ordinal: int,
    started_at: datetime,
    *,
    parent_node_id: str | None = None,
) -> OpsNodeStart:
    return OpsNodeStart(
        execution_id=execution_id,
        node_type=node_type,
        ordinal=ordinal,
        parent_node_id=parent_node_id,
        started_at=started_at,
        input_summary={"kind": "bounded-summary"},
        input_full={"encrypted": "reader-must-not-decrypt-this"},
        technical_metadata={},
    )


def database_snapshot(path: Path) -> tuple[tuple[str, bool, bytes], ...]:
    return tuple(
        (candidate.name, candidate.exists(), candidate.read_bytes() if candidate.exists() else b"")
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
    )


def test_reader_returns_inclusive_recent_first_window_with_persisted_node_association(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "dashboard.sqlite3").resolve()
    lower = NOW - timedelta(days=7)
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(persisted_execution("outside", lower - timedelta(microseconds=1)))
        writer.write_execution(persisted_execution("lower", lower))
        first = persisted_node("lower", NodeType.MAYA_REQUEST, 1, lower + timedelta(seconds=1))
        writer.start_node(first)
        writer.finish_node(
            OpsNodeFinish.from_start(
                first,
                status=ExecutionStatus.COMPLETED,
                completed_at=first.started_at + timedelta(seconds=1),
                output_summary={},
                output_full={"encrypted": "output"},
                technical_metadata={},
            )
        )
        second = persisted_node(
            "lower",
            NodeType.MAYA_RESPONSE,
            2,
            lower + timedelta(seconds=3),
            parent_node_id=first.node_id,
        )
        writer.start_node(second)
        writer.finish_node(
            OpsNodeFinish.from_start(
                second,
                status=ExecutionStatus.COMPLETED,
                completed_at=second.started_at + timedelta(seconds=1),
                output_summary={},
                output_full={"encrypted": "output"},
                technical_metadata={},
            )
        )
        writer.write_execution(
            OpsExecution(
                execution_id="lower",
                lead_id="lead:lower",
                received_at=lower,
                completed_at=second.started_at + timedelta(seconds=1),
                status=ExecutionStatus.COMPLETED,
                trace_completeness=TraceCompleteness.COMPLETE_TRACE,
                current_node_id=second.node_id,
                terminal_reason="completed",
            )
        )
        writer.write_execution(persisted_execution("stale", NOW - timedelta(minutes=20)))
        stale = persisted_node(
            "stale", NodeType.CONVERSATION_REDUCER, 1, NOW - timedelta(minutes=10)
        )
        writer.start_node(stale)
        writer.write_execution(persisted_execution("upper", NOW))

    # The writer correctly advances current_node_id monotonically. This direct fixture
    # update creates a valid persisted historical witness whose current node is not the
    # final node in display order, so positional inference cannot satisfy the assertion.
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE executions SET current_node_id=? WHERE execution_id='lower'",
            (first.node_id,),
        )

    # A deliberately wrong key proves this aggregate path never decrypts full payloads.
    with SQLiteOpsTraceReader(path, OTHER_KEY) as reader:
        records = reader.list_dashboard_records(
            start_at=lower,
            end_at=NOW,
            now=NOW,
            stale_after=timedelta(minutes=5),
        )

    assert [item.execution.execution_id for item in records] == ["upper", "stale", "lower"]
    by_execution = {item.execution.execution_id: item for item in records}
    assert by_execution["stale"].execution.status == "running_stale"
    assert by_execution["stale"].node_types == (NodeType.CONVERSATION_REDUCER,)
    assert by_execution["stale"].current_node_type is NodeType.CONVERSATION_REDUCER
    assert by_execution["stale"].node_count == 1
    assert by_execution["lower"].node_types == (
        NodeType.MAYA_REQUEST,
        NodeType.MAYA_RESPONSE,
    )
    assert by_execution["lower"].current_node_type is NodeType.MAYA_REQUEST
    assert by_execution["lower"].node_count == 2
    assert by_execution["upper"].node_types == ()
    assert by_execution["upper"].current_node_type is None


def test_reader_repeated_window_calls_do_not_mutate_database_bytes_or_rows(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "read-only.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(persisted_execution("execution", NOW))
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before_counts = (
            connection.execute("SELECT count(*) FROM executions").fetchone()[0],
            connection.execute("SELECT count(*) FROM nodes").fetchone()[0],
        )

    with SQLiteOpsTraceReader(path, KEY) as reader:
        before_snapshot = database_snapshot(path)
        for _ in range(3):
            assert len(
                reader.list_dashboard_records(
                    start_at=NOW - DashboardRange.D7.duration,
                    end_at=NOW,
                    now=NOW,
                )
            ) == 1
        after_snapshot = database_snapshot(path)

    assert after_snapshot == before_snapshot
    with sqlite3.connect(path) as connection:
        after_counts = (
            connection.execute("SELECT count(*) FROM executions").fetchone()[0],
            connection.execute("SELECT count(*) FROM nodes").fetchone()[0],
        )
    assert after_counts == before_counts == (1, 0)


def test_database_snapshot_preserves_each_file_identity_existence_and_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshot.sqlite3"
    wal_path = Path(f"{path}-wal")
    shm_path = Path(f"{path}-shm")
    path.write_bytes(b"db")
    wal_path.write_bytes(b"wal")

    before = database_snapshot(path)
    path.write_bytes(b"dbw")
    wal_path.write_bytes(b"al")
    shm_path.write_bytes(b"")
    after = database_snapshot(path)

    assert b"".join(item[2] for item in before) == b"".join(item[2] for item in after)
    assert before == (
        ("snapshot.sqlite3", True, b"db"),
        ("snapshot.sqlite3-wal", True, b"wal"),
        ("snapshot.sqlite3-shm", False, b""),
    )
    assert after == (
        ("snapshot.sqlite3", True, b"dbw"),
        ("snapshot.sqlite3-wal", True, b"al"),
        ("snapshot.sqlite3-shm", True, b""),
    )
    assert after != before


def test_reader_rejects_unbounded_or_misaligned_dashboard_windows(tmp_path: Path) -> None:
    path = (tmp_path / "bounded.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY):
        pass
    with SQLiteOpsTraceReader(path, KEY) as reader:
        invalid_windows = (
            (NOW + timedelta(microseconds=1), NOW),
            (NOW - timedelta(days=30, microseconds=1), NOW),
            (NOW - timedelta(days=7), NOW - timedelta(microseconds=1)),
        )
        for start_at, end_at in invalid_windows:
            with pytest.raises(ValueError, match="bounded range"):
                reader.list_dashboard_records(
                    start_at=start_at,
                    end_at=end_at,
                    now=NOW,
                )


def test_reader_accepts_exactly_thirty_days(tmp_path: Path) -> None:
    path = (tmp_path / "exactly-thirty-days.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY):
        pass
    with SQLiteOpsTraceReader(path, KEY) as reader:
        assert reader.list_dashboard_records(
            start_at=NOW - timedelta(days=30),
            end_at=NOW,
            now=NOW,
        ) == ()


@pytest.mark.parametrize("field", ("start_at", "end_at", "now"))
@pytest.mark.parametrize(
    "invalid_time",
    (NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=-3)))),
    ids=("naive", "non-utc-timezone"),
)
def test_reader_rejects_each_non_exact_utc_time(
    tmp_path: Path, field: str, invalid_time: datetime
) -> None:
    path = (tmp_path / f"invalid-{field}.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY):
        pass
    arguments = {
        "start_at": NOW - timedelta(days=7),
        "end_at": NOW,
        "now": NOW,
    }
    arguments[field] = invalid_time
    with SQLiteOpsTraceReader(path, KEY) as reader:
        with pytest.raises(ValueError, match=rf"{field} must be an exact UTC datetime"):
            reader.list_dashboard_records(**arguments)


@pytest.mark.parametrize("stale_after", (timedelta(0), -timedelta(microseconds=1)))
def test_reader_rejects_non_positive_stale_after(
    tmp_path: Path, stale_after: timedelta
) -> None:
    path = (tmp_path / "invalid-stale-after.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY):
        pass
    with SQLiteOpsTraceReader(path, KEY) as reader:
        with pytest.raises(ValueError, match="stale_after must be a positive timedelta"):
            reader.list_dashboard_records(
                start_at=NOW - timedelta(days=7),
                end_at=NOW,
                now=NOW,
                stale_after=stale_after,
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
