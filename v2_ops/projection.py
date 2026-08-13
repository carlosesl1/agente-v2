from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
)
from v2_ops.sources import (
    BoundaryExecutionProjection,
    SQLiteBoundaryProjectionSource,
    SQLiteExecutionProjectionSource,
    SQLitePaymentProjectionSource,
)
from v2_ops.store import SQLiteOpsTraceWriter


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    projected: int
    skipped_existing: int
    degraded: int


def _finish(start: OpsNodeStart, *, output: dict[str, object]) -> OpsNodeFinish:
    return OpsNodeFinish.from_start(
        start,
        status=ExecutionStatus.COMPLETED,
        completed_at=start.started_at,
        output_summary=output,
        technical_metadata={"source": "authenticated_ledger", "projection_version": 1},
    )


def _node(
    row: BoundaryExecutionProjection,
    *,
    node_type: NodeType,
    ordinal: int,
    observed_at: datetime,
    parent_node_id: str | None,
    input_summary: dict[str, object],
    output_summary: dict[str, object],
) -> tuple[OpsNodeStart, OpsNodeFinish]:
    start = OpsNodeStart(
        execution_id=row.execution_id,
        node_type=node_type,
        ordinal=ordinal,
        started_at=observed_at,
        parent_node_id=parent_node_id,
        input_summary=input_summary,
        technical_metadata={"source": "authenticated_ledger", "projection_version": 1},
    )
    return start, _finish(start, output=output_summary)


def _pairs(
    row: BoundaryExecutionProjection,
    *,
    execution_source: SQLiteExecutionProjectionSource | None,
    payment_source: SQLitePaymentProjectionSource | None,
) -> tuple[tuple[OpsNodeStart, OpsNodeFinish], ...]:
    nodes: list[tuple[OpsNodeStart, OpsNodeFinish]] = []
    root = _node(
        row,
        node_type=NodeType.LEDGER_TURN,
        ordinal=1,
        observed_at=row.occurred_at,
        parent_node_id=None,
        input_summary={
            "source_event_fingerprint": row.source_event_hash,
            "source_receipt_fingerprint": row.source_turn_receipt_hash,
        },
        output_summary={
            "turn_receipt_fingerprint": row.turn_receipt_hash,
            "command_count": len(row.commands),
            "public_row_count": len(row.public_rows),
        },
    )
    nodes.append(root)
    parent = root[0].node_id
    ordinal = 2
    for command in row.commands:
        pair = _node(
            row,
            node_type=NodeType.LEDGER_COMMAND,
            ordinal=ordinal,
            observed_at=max(command.created_at, nodes[-1][0].started_at),
            parent_node_id=parent,
            input_summary={
                "command_fingerprint": command.command_hash,
                "command_type": command.command_type,
                "source_receipt_fingerprint": command.source_turn_receipt_hash,
            },
            output_summary={"persisted": True},
        )
        nodes.append(pair)
        parent = pair[0].node_id
        ordinal += 1
        if execution_source is not None:
            ledger = execution_source.for_command(
                command_id=command.command_id,
                command_hash=command.command_hash,
            )
            if ledger is not None:
                pair = _node(
                    row,
                    node_type=NodeType.LEDGER_RESERVATION,
                    ordinal=ordinal,
                    observed_at=max(ledger.updated_at, nodes[-1][0].started_at),
                    parent_node_id=parent,
                    input_summary={
                        "command_fingerprint": ledger.command_hash,
                        "dispatch_request_fingerprint": ledger.dispatch_request_hash,
                    },
                    output_summary={
                        "status": ledger.status,
                        "dispatch_fenced": ledger.dispatch_fenced_at is not None,
                        "outcome_fingerprint": ledger.outcome_hash,
                    },
                )
                nodes.append(pair)
                parent = pair[0].node_id
                ordinal += 1
        if payment_source is not None and command.payment_id is not None:
            payment = payment_source.for_payment(command.payment_id)
            if payment is not None:
                pair = _node(
                    row,
                    node_type=NodeType.LEDGER_PAYMENT,
                    ordinal=ordinal,
                    observed_at=max(payment.updated_at, nodes[-1][0].started_at),
                    parent_node_id=parent,
                    input_summary={"selection_fingerprint": payment.selection_hash},
                    output_summary={
                        "status": payment.status,
                        "dispatch_slots": payment.dispatch_slots,
                        "result_fingerprint": payment.result_hash,
                    },
                )
                nodes.append(pair)
                parent = pair[0].node_id
                ordinal += 1
    for public in row.public_rows:
        pair = _node(
            row,
            node_type=NodeType.LEDGER_PUBLIC_OUTBOX,
            ordinal=ordinal,
            observed_at=max(public.created_at, nodes[-1][0].started_at),
            parent_node_id=parent,
            input_summary={
                "chunk_index": public.chunk_index,
                "source_receipt_fingerprint": public.source_turn_receipt_hash,
            },
            output_summary={
                "status": public.status,
                "dispatch_slots_consumed": public.dispatch_slots_consumed,
                "receipt_recorded": public.delivery_receipt_hash is not None,
            },
        )
        nodes.append(pair)
        parent = pair[0].node_id
        ordinal += 1
    return tuple(nodes)


class LedgerOnlyProjector:
    def __init__(
        self,
        *,
        source: SQLiteBoundaryProjectionSource,
        writer: SQLiteOpsTraceWriter,
        execution_source: SQLiteExecutionProjectionSource | None = None,
        payment_source: SQLitePaymentProjectionSource | None = None,
    ) -> None:
        if type(source) is not SQLiteBoundaryProjectionSource:
            raise TypeError("source must be exact SQLiteBoundaryProjectionSource")
        if type(writer) is not SQLiteOpsTraceWriter:
            raise TypeError("writer must be exact SQLiteOpsTraceWriter")
        if (
            execution_source is not None
            and type(execution_source) is not SQLiteExecutionProjectionSource
        ):
            raise TypeError(
                "execution_source must be exact SQLiteExecutionProjectionSource or None"
            )
        if (
            payment_source is not None
            and type(payment_source) is not SQLitePaymentProjectionSource
        ):
            raise TypeError(
                "payment_source must be exact SQLitePaymentProjectionSource or None"
            )
        self._source = source
        self._writer = writer
        self._execution_source = execution_source
        self._payment_source = payment_source

    def run(self, *, limit: int) -> ProjectionResult:
        projected = 0
        skipped = 0
        degraded = 0
        for row in self._source.primary_executions(limit=limit):
            nodes = _pairs(
                row,
                execution_source=self._execution_source,
                payment_source=self._payment_source,
            )
            status = (
                ExecutionStatus.MANUAL_REVIEW
                if row.degraded_reasons
                else ExecutionStatus.COMPLETED
            )
            terminal = nodes[-1][1]
            execution = OpsExecution(
                execution_id=row.execution_id,
                lead_id=row.lead_id,
                received_at=row.occurred_at,
                status=status,
                trace_completeness=TraceCompleteness.LEDGER_ONLY,
                current_node_id=terminal.node_id,
                completed_at=max(terminal.completed_at, row.occurred_at),
                terminal_reason=(
                    "ledger_projection_degraded"
                    if row.degraded_reasons
                    else "ledger_projection_complete"
                ),
            )
            if self._writer.write_ledger_projection(execution, nodes):
                projected += 1
                degraded += int(bool(row.degraded_reasons))
            else:
                skipped += 1
        return ProjectionResult(projected, skipped, degraded)


__all__ = ["LedgerOnlyProjector", "ProjectionResult"]
