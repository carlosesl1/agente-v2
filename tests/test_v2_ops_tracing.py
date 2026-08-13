from __future__ import annotations

from datetime import date, datetime, timezone

from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
)
from v2_ops.recording import DegradationReason
from v2_ops.recording import SQLiteOpsRecorder
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter
from v2_ops.tracing import OpsExecutionTrace
from v2_contracts.providers import ReadKind, ReadRequest

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class RecordingSink:
    def __init__(self) -> None:
        self.executions: list[OpsExecution] = []
        self.starts: list[OpsNodeStart] = []
        self.finishes: list[OpsNodeFinish] = []
        self.degradations: list[tuple[DegradationReason, str | None, str | None]] = []

    def finish_execution(self, item: OpsExecution) -> None:
        self.executions.append(item)

    def start_node(self, item: OpsNodeStart) -> None:
        self.starts.append(item)

    def finish_node(self, item: OpsNodeFinish) -> None:
        self.finishes.append(item)

    def report_degradation(self, reason, *, execution_id=None, node_id=None) -> None:
        self.degradations.append((reason, execution_id, node_id))


class Unsupported:
    pass


def test_trace_serialization_failure_calls_business_exactly_once() -> None:
    sink = RecordingSink()
    calls = 0

    def business() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    trace = OpsExecutionTrace(execution_id="event:trace-once", recorder=sink)

    assert trace.call(NodeType.MAYA_REQUEST, business, input_value=Unsupported()) == "ok"
    assert calls == 1
    assert sink.starts == []
    assert sink.degradations[0][0] is DegradationReason.RESULT_SERIALIZATION_FAILED


def test_trace_terminal_execution_references_latest_completed_node() -> None:
    sink = RecordingSink()
    trace = OpsExecutionTrace(execution_id="event:trace-terminal", recorder=sink)
    trace.record_value(
        NodeType.MAYA_READ_REQUEST,
        ReadRequest(
            request_id="read:trace-terminal",
            kind=ReadKind.LODGING,
            check_in=date(2026, 8, 14),
            check_out=date(2026, 8, 15),
            adults=1,
            children=0,
        ),
    )

    trace.finish_execution(
        lead_id="manychat:123",
        received_at=NOW,
        status=ExecutionStatus.COMPLETED,
        terminal_reason="turn_committed",
    )

    assert len(sink.starts) == len(sink.finishes) == 1
    assert sink.starts[0].parent_node_id is not None
    assert sink.executions[-1].current_node_id == sink.finishes[-1].node_id
    assert sink.executions[-1].status is ExecutionStatus.COMPLETED
    assert sink.degradations == []


def test_trace_persists_monotonic_encrypted_graph_in_real_sqlite(tmp_path) -> None:
    path = tmp_path / "ops.sqlite3"
    key = b"k" * 32
    writer = SQLiteOpsTraceWriter(path, key)
    recorder = SQLiteOpsRecorder(writer)
    received_at = datetime.now(timezone.utc)
    execution = OpsExecution(
        execution_id="event:trace-sqlite",
        lead_id="manychat:123",
        received_at=received_at,
        trace_completeness=TraceCompleteness.PARTIAL_TRACE,
    )
    recorder.start_execution(execution)
    claim = OpsNodeStart(
        execution_id=execution.execution_id,
        node_type=NodeType.INBOX_CLAIM,
        ordinal=1,
        started_at=received_at,
    )
    recorder.start_node(claim)
    recorder.finish_node(
        OpsNodeFinish.from_start(
            claim,
            status=ExecutionStatus.COMPLETED,
            completed_at=received_at,
        )
    )
    trace = OpsExecutionTrace(
        execution_id=execution.execution_id,
        recorder=recorder,
        full_content=True,
    )
    trace.record_value(
        NodeType.MAYA_READ_REQUEST,
        ReadRequest(
            request_id="read:trace-sqlite",
            kind=ReadKind.LODGING,
            check_in=date(2026, 8, 14),
            check_out=date(2026, 8, 15),
            adults=1,
            children=0,
        ),
    )
    trace.finish_execution(
        lead_id=execution.lead_id,
        received_at=received_at,
        status=ExecutionStatus.COMPLETED,
        terminal_reason="turn_committed",
    )
    writer.close()

    reader = SQLiteOpsTraceReader(path, key)
    stored = reader.get_execution(execution.execution_id)
    nodes = reader.list_nodes(execution.execution_id)
    full = reader.read_full(
        execution.execution_id,
        nodes[-1].node_id,
        side="input",
    )
    reader.close()

    assert stored is not None
    assert stored.status == "completed"
    assert stored.stored_status is ExecutionStatus.COMPLETED
    assert [item.ordinal for item in nodes] == [1, 2]
    assert nodes[1].parent_node_id == nodes[0].node_id
    assert stored.current_node_id == nodes[1].node_id
    assert full.value["kind"] == "lodging"
    assert full.value["request_id"] == "read:trace-sqlite"
