from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable

from v2_ops.contracts import NodeType, TraceCompleteness
from v2_ops.store import OpsExecutionView


class DashboardRange(str, Enum):
    H24 = "24h"
    D7 = "7d"
    D30 = "30d"

    @classmethod
    def parse(cls, value: str) -> "DashboardRange":
        if type(value) is not str:
            raise ValueError("range is outside the closed catalog")
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError("range is outside the closed catalog") from exc

    @property
    def duration(self) -> timedelta:
        return {
            DashboardRange.H24: timedelta(hours=24),
            DashboardRange.D7: timedelta(days=7),
            DashboardRange.D30: timedelta(days=30),
        }[self]

    @property
    def buckets(self) -> int:
        return {
            DashboardRange.H24: 24,
            DashboardRange.D7: 7,
            DashboardRange.D30: 30,
        }[self]

    @property
    def bucket_duration(self) -> timedelta:
        return timedelta(hours=1) if self is DashboardRange.H24 else timedelta(days=1)


RESERVATION_NODES = frozenset(
    {
        NodeType.CLOUDBEDS_RESERVATION_REQUEST,
        NodeType.CLOUDBEDS_RESERVATION_RESPONSE,
        NodeType.BOKUN_BOOKING_REQUEST,
        NodeType.BOKUN_BOOKING_RESPONSE,
        NodeType.LEDGER_RESERVATION,
    }
)
PAYMENT_NODES = frozenset(
    {
        NodeType.STRIPE_PRODUCT,
        NodeType.STRIPE_PRICE,
        NodeType.STRIPE_PAYMENT_LINK,
        NodeType.PIX_INSTRUCTION,
        NodeType.WISE_INSTRUCTION,
        NodeType.SETTLEMENT,
        NodeType.STRIPE_RECONCILIATION,
        NodeType.LEDGER_PAYMENT,
    }
)
DELIVERY_NODES = frozenset(
    {
        NodeType.PUBLIC_OUTBOX,
        NodeType.MANYCHAT_DELIVERY_REQUEST,
        NodeType.MANYCHAT_DELIVERY_RESPONSE,
        NodeType.LEDGER_PUBLIC_OUTBOX,
    }
)
HANDOFF_NODES = frozenset({NodeType.HANDOFF_REQUEST, NodeType.HANDOFF_DELIVERY})

_VISIBLE_STATUSES = (
    "pending",
    "running",
    "running_stale",
    "completed",
    "failed",
    "manual_review",
)
_IN_PROGRESS_STATUSES = frozenset({"pending", "running", "running_stale"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "manual_review"})
_TRACE_COMPLETENESS = (
    TraceCompleteness.COMPLETE_TRACE,
    TraceCompleteness.PARTIAL_TRACE,
    TraceCompleteness.LEDGER_ONLY,
)
_MILESTONES = (
    ("reservation", RESERVATION_NODES),
    ("payment", PAYMENT_NODES),
    ("public_delivery", DELIVERY_NODES),
    ("handoff", HANDOFF_NODES),
)


@dataclass(frozen=True, slots=True)
class DashboardRecord:
    execution: OpsExecutionView
    node_types: tuple[NodeType, ...]
    current_node_type: NodeType | None
    node_count: int


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    generated_at: datetime
    range_key: DashboardRange
    metrics: dict[str, int | float | None]
    execution_series: tuple[dict[str, object], ...]
    status_distribution: tuple[dict[str, object], ...]
    trace_distribution: tuple[dict[str, object], ...]
    milestones: tuple[dict[str, object], ...]
    top_node_types: tuple[dict[str, object], ...]
    executions: tuple[dict[str, object], ...]


def _duration_ms(execution: OpsExecutionView) -> float | None:
    if execution.completed_at is None:
        return None
    duration = execution.completed_at - execution.received_at
    if duration < timedelta(0):
        raise ValueError("execution duration cannot be negative")
    return duration.total_seconds() * 1000


def _presence(node_types: set[NodeType]) -> dict[str, bool]:
    return {
        "has_reservation": bool(node_types & RESERVATION_NODES),
        "has_payment": bool(node_types & PAYMENT_NODES),
        "has_public_delivery": bool(node_types & DELIVERY_NODES),
        "has_handoff": bool(node_types & HANDOFF_NODES),
    }


def build_dashboard_snapshot(
    records: Iterable[DashboardRecord],
    *,
    range_key: DashboardRange,
    generated_at: datetime,
    table_limit: int = 100,
) -> DashboardSnapshot:
    if type(generated_at) is not datetime or generated_at.tzinfo is not timezone.utc:
        raise ValueError("generated_at must be an exact UTC datetime")
    if type(range_key) is not DashboardRange:
        raise ValueError("range is outside the closed catalog")
    if type(table_limit) is not int or not 1 <= table_limit <= 100:
        raise ValueError("table_limit is outside the bounded range")

    lower = generated_at - range_key.duration
    selected: list[DashboardRecord] = []
    for item in records:
        if type(item) is not DashboardRecord:
            raise TypeError("records must contain exact DashboardRecord values")
        received_at = item.execution.received_at
        if lower <= received_at <= generated_at:
            selected.append(item)

    status_counts = Counter(item.execution.status for item in selected)
    unknown_statuses = set(status_counts) - set(_VISIBLE_STATUSES)
    if unknown_statuses:
        raise ValueError("execution status is outside the closed catalog")
    trace_counts = Counter(item.execution.trace_completeness for item in selected)
    if set(trace_counts) - set(_TRACE_COMPLETENESS):
        raise ValueError("trace completeness is outside the closed catalog")

    terminal_durations = [
        duration
        for item in selected
        if item.execution.status in _TERMINAL_STATUSES
        and (duration := _duration_ms(item.execution)) is not None
    ]
    total = len(selected)
    completed = status_counts["completed"]
    metrics: dict[str, int | float | None] = {
        "executions": total,
        "distinct_leads": len({item.execution.lead_id for item in selected}),
        "in_progress": sum(status_counts[status] for status in _IN_PROGRESS_STATUSES),
        "completed": completed,
        "failed": status_counts["failed"],
        "manual_review": status_counts["manual_review"],
        "technical_completion_rate": None if total == 0 else completed / total * 100,
        "average_terminal_duration_ms": (
            None
            if not terminal_durations
            else sum(terminal_durations) / len(terminal_durations)
        ),
    }

    bucket_counts = [0] * range_key.buckets
    bucket_seconds = range_key.bucket_duration.total_seconds()
    for item in selected:
        index = int((item.execution.received_at - lower).total_seconds() // bucket_seconds)
        bucket_counts[min(index, range_key.buckets - 1)] += 1
    execution_series = tuple(
        {
            "start_at": lower + index * range_key.bucket_duration,
            "count": count,
        }
        for index, count in enumerate(bucket_counts)
    )

    status_distribution = tuple(
        {"status": status, "count": status_counts[status]} for status in _VISIBLE_STATUSES
    )
    trace_distribution = tuple(
        {
            "trace_completeness": completeness.value,
            "count": trace_counts[completeness],
        }
        for completeness in _TRACE_COMPLETENESS
    )

    node_sets = [set(item.node_types) for item in selected]
    milestones = tuple(
        {
            "milestone": name,
            "count": sum(bool(node_types & catalog) for node_types in node_sets),
        }
        for name, catalog in _MILESTONES
    )

    node_counts = Counter(node_type for item in selected for node_type in item.node_types)
    top_node_types = tuple(
        {"node_type": node_type.value, "count": count}
        for node_type, count in sorted(
            node_counts.items(), key=lambda pair: (-pair[1], pair[0].value)
        )[:10]
    )

    recent = sorted(
        selected,
        key=lambda item: (item.execution.received_at, item.execution.execution_id),
        reverse=True,
    )[:table_limit]
    execution_rows = []
    for item in recent:
        execution = item.execution
        row: dict[str, object] = {
            "lead_id": execution.lead_id,
            "execution_id": execution.execution_id,
            "received_at": execution.received_at,
            "duration_ms": _duration_ms(execution),
            "status": execution.status,
            "trace_completeness": execution.trace_completeness.value,
            "current_node_type": (
                None if item.current_node_type is None else item.current_node_type.value
            ),
            "node_count": item.node_count,
            **_presence(set(item.node_types)),
            "terminal_reason": execution.terminal_reason,
        }
        execution_rows.append(row)

    return DashboardSnapshot(
        generated_at=generated_at,
        range_key=range_key,
        metrics=metrics,
        execution_series=execution_series,
        status_distribution=status_distribution,
        trace_distribution=trace_distribution,
        milestones=milestones,
        top_node_types=top_node_types,
        executions=tuple(execution_rows),
    )
