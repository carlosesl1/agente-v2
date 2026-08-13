from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeStart,
    TraceCompleteness,
)
from v2_ops.recording import (
    DegradationReason,
    NullOpsRecorder,
    SQLiteOpsRecorder,
    record_boundary,
    record_node_boundary,
)
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter


NOW = datetime(2026, 8, 13, 18, 0, 0, tzinfo=timezone.utc)
KEY = bytes(range(32))


def _execution(execution_id: str = "event:ops-recording") -> OpsExecution:
    return OpsExecution(
        execution_id=execution_id,
        lead_id="lead:opaque-recording",
        received_at=NOW,
        trace_completeness=TraceCompleteness.COMPLETE_TRACE,
    )


def _node(
    execution_id: str = "event:ops-recording",
    *,
    ordinal: int = 1,
) -> OpsNodeStart:
    return OpsNodeStart(
        execution_id=execution_id,
        node_type=NodeType.PROVIDER_READ_REQUEST,
        ordinal=ordinal,
        started_at=NOW + timedelta(seconds=1),
        input_summary={"request_kind": "lodging"},
        input_full={"request_kind": "lodging"},
        technical_metadata={"attempt_kind": "initial"},
    )


class FaultWriter:
    def __init__(self, failing_operation: str) -> None:
        self.failing_operation = failing_operation
        self.write_execution_calls = 0
        self.start_node_calls = 0
        self.finish_node_calls = 0

    def write_execution(self, item: object) -> None:
        self.write_execution_calls += 1
        operation = (
            "start_execution" if self.write_execution_calls == 1 else "finish_execution"
        )
        if self.failing_operation == operation:
            raise RuntimeError("trace store unavailable")

    def start_node(self, item: object) -> None:
        self.start_node_calls += 1
        if self.failing_operation == "start_node":
            raise RuntimeError("trace store unavailable")

    def finish_node(self, item: object) -> None:
        self.finish_node_calls += 1
        if self.failing_operation == "finish_node":
            raise RuntimeError("trace store unavailable")


def test_null_recorder_is_a_stateless_noop_and_does_not_report_degradation() -> None:
    recorder = NullOpsRecorder()
    calls = 0
    result = object()

    def business_call() -> object:
        nonlocal calls
        calls += 1
        return result

    returned = record_boundary(
        recorder,
        execution=_execution(),
        node=_node(),
        call=business_call,
        completed_at=lambda: NOW + timedelta(seconds=2),
        serialize_result=lambda value: ({"ok": True}, {"ok": True}),
    )

    assert returned is result
    assert calls == 1
    assert not hasattr(recorder, "__dict__")
    assert recorder.report_degradation(
        DegradationReason.NODE_START_FAILED,
        execution_id="event:opaque",
        node_id="a" * 64,
    ) is None


def test_sqlite_recorder_persists_completed_node_and_execution(tmp_path: Path) -> None:
    path = (tmp_path / "recording-complete.sqlite3").resolve()
    result = object()
    calls = 0

    def business_call() -> object:
        nonlocal calls
        calls += 1
        return result

    with SQLiteOpsTraceWriter(path, KEY) as writer:
        returned = record_boundary(
            SQLiteOpsRecorder(writer),
            execution=_execution(),
            node=_node(),
            call=business_call,
            completed_at=lambda: NOW + timedelta(seconds=2),
            serialize_result=lambda value: (
                {"result_kind": "typed"},
                {"result_kind": "typed"},
            ),
        )

    assert returned is result
    assert calls == 1
    with SQLiteOpsTraceReader(path, KEY) as reader:
        execution = reader.get_execution("event:ops-recording", now=NOW)
        nodes = reader.list_nodes("event:ops-recording", now=NOW)
        assert execution.stored_status is ExecutionStatus.COMPLETED
        assert execution.current_node_id == _node().node_id
        assert len(nodes) == 1
        assert nodes[0].stored_status is ExecutionStatus.COMPLETED
        assert nodes[0].output_summary == {"result_kind": "typed"}
        assert reader.read_full(
            "event:ops-recording", nodes[0].node_id, side="output"
        ).value == {"result_kind": "typed"}


def test_sqlite_recorder_persists_same_business_exception_as_failure(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "recording-failed.sqlite3").resolve()
    failure = ValueError("business failure remains private")
    calls = 0

    def business_call() -> object:
        nonlocal calls
        calls += 1
        raise failure

    with SQLiteOpsTraceWriter(path, KEY) as writer:
        with pytest.raises(ValueError) as caught:
            record_boundary(
                SQLiteOpsRecorder(writer),
                execution=_execution(),
                node=_node(),
                call=business_call,
                completed_at=lambda: NOW + timedelta(seconds=2),
            )

    assert caught.value is failure
    assert calls == 1
    with SQLiteOpsTraceReader(path, KEY) as reader:
        execution = reader.get_execution("event:ops-recording", now=NOW)
        nodes = reader.list_nodes("event:ops-recording", now=NOW)
        assert execution.stored_status is ExecutionStatus.FAILED
        assert execution.terminal_reason == "wrapped_call_failed"
        assert nodes[0].stored_status is ExecutionStatus.FAILED
        assert nodes[0].error == {"kind": "wrapped_call_failed"}
        assert "business failure remains private" not in path.read_bytes().decode(
            "utf-8", errors="ignore"
        )


@pytest.mark.parametrize(
    "failing_operation",
    ["start_execution", "start_node", "finish_node", "finish_execution"],
)
def test_trace_store_failure_never_retries_or_changes_business_result(
    failing_operation: str,
) -> None:
    writer = FaultWriter(failing_operation)
    degradations = []
    calls = 0
    result = object()

    def business_call() -> object:
        nonlocal calls
        calls += 1
        return result

    returned = record_boundary(
        SQLiteOpsRecorder(writer, on_degraded=degradations.append),
        execution=_execution(),
        node=_node(),
        call=business_call,
        completed_at=lambda: NOW + timedelta(seconds=2),
    )

    assert returned is result
    assert calls == 1
    assert len(degradations) == 1
    assert degradations[0].execution_id == "event:ops-recording"
    assert degradations[0].node_id == _node().node_id
    assert type(degradations[0].reason) is DegradationReason


@pytest.mark.parametrize("failing_operation", ["start_execution", "finish_node"])
def test_trace_and_degradation_callback_failures_preserve_same_exception_object(
    failing_operation: str,
) -> None:
    writer = FaultWriter(failing_operation)
    failure = LookupError("same object")
    calls = 0

    def broken_callback(event: object) -> None:
        raise RuntimeError("degradation callback failed")

    def business_call() -> object:
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(LookupError) as caught:
        record_boundary(
            SQLiteOpsRecorder(writer, on_degraded=broken_callback),
            execution=_execution(),
            node=_node(),
            call=business_call,
            completed_at=lambda: NOW + timedelta(seconds=2),
        )

    assert caught.value is failure
    assert calls == 1


def test_result_serialization_and_callback_failure_preserve_identity_and_one_call() -> None:
    writer = FaultWriter("none")
    result = object()
    calls = 0

    def broken_callback(event: object) -> None:
        raise RuntimeError("callback failure")

    def broken_serializer(value: object) -> tuple[dict[str, object], None]:
        assert value is result
        raise ValueError("typed serializer failure")

    def business_call() -> object:
        nonlocal calls
        calls += 1
        return result

    returned = record_boundary(
        SQLiteOpsRecorder(writer, on_degraded=broken_callback),
        execution=_execution(),
        node=_node(),
        call=business_call,
        completed_at=lambda: NOW + timedelta(seconds=2),
        serialize_result=broken_serializer,
    )

    assert returned is result
    assert calls == 1
    assert writer.finish_node_calls == 1


def test_completion_clock_failure_after_call_cannot_replace_business_result() -> None:
    calls = 0
    result = object()
    degradations = []

    def business_call() -> object:
        nonlocal calls
        calls += 1
        return result

    def broken_clock() -> datetime:
        raise RuntimeError("trace clock failed")

    returned = record_boundary(
        SQLiteOpsRecorder(FaultWriter("none"), on_degraded=degradations.append),
        execution=_execution(),
        node=_node(),
        call=business_call,
        completed_at=broken_clock,
    )

    assert returned is result
    assert calls == 1
    assert [item.reason for item in degradations] == [
        DegradationReason.NODE_FINISH_FAILED
    ]


class TraceBaseFailure(BaseException):
    pass


class BaseFaultRecorder(NullOpsRecorder):
    __slots__ = ("operation",)

    def __init__(self, operation: str) -> None:
        self.operation = operation

    def start_execution(self, item: object) -> None:
        if self.operation == "start_execution":
            raise TraceBaseFailure("trace start failed")

    def finish_node(self, item: object) -> None:
        if self.operation == "finish_node":
            raise TraceBaseFailure("trace finish failed")


@pytest.mark.parametrize("operation", ["start_execution", "finish_node"])
def test_trace_baseexception_never_replaces_success_or_retries_business_call(
    operation: str,
) -> None:
    result = object()
    calls = 0

    def business_call() -> object:
        nonlocal calls
        calls += 1
        return result

    returned = record_boundary(
        BaseFaultRecorder(operation),
        execution=_execution(),
        node=_node(),
        call=business_call,
        completed_at=lambda: NOW + timedelta(seconds=2),
    )

    assert returned is result
    assert calls == 1


def test_trace_clock_baseexception_never_replaces_success() -> None:
    result = object()
    calls = 0

    def business_call() -> object:
        nonlocal calls
        calls += 1
        return result

    def broken_clock() -> datetime:
        raise TraceBaseFailure("trace clock failed")

    returned = record_boundary(
        NullOpsRecorder(),
        execution=_execution(),
        node=_node(),
        call=business_call,
        completed_at=broken_clock,
    )

    assert returned is result
    assert calls == 1


def test_business_baseexception_keeps_identity_and_is_called_once() -> None:
    failure = TraceBaseFailure("business base failure")
    calls = 0

    def business_call() -> object:
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(TraceBaseFailure) as caught:
        record_boundary(
            NullOpsRecorder(),
            execution=_execution(),
            node=_node(),
            call=business_call,
            completed_at=lambda: NOW + timedelta(seconds=2),
        )

    assert caught.value is failure
    assert calls == 1


def test_node_only_boundaries_allow_a_monotonic_multi_node_execution(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "recording-multi-node.sqlite3").resolve()
    calls: list[int] = []
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        recorder = SQLiteOpsRecorder(writer)
        execution = _execution("event:multi-node")
        recorder.start_execution(execution)
        for ordinal in (1, 2):
            result = record_node_boundary(
                recorder,
                node=_node("event:multi-node", ordinal=ordinal),
                call=lambda ordinal=ordinal: calls.append(ordinal) or ordinal,
                completed_at=lambda ordinal=ordinal: NOW
                + timedelta(seconds=ordinal + 1),
                serialize_result=lambda value: ({"ordinal": value}, None),
            )
            assert result == ordinal

    assert calls == [1, 2]
    with SQLiteOpsTraceReader(path, KEY) as reader:
        detail = reader.get_execution("event:multi-node", now=NOW)
        nodes = reader.list_nodes("event:multi-node", now=NOW)
        assert detail.stored_status is ExecutionStatus.RUNNING
        assert detail.current_node_id == nodes[-1].node_id
        assert [node.ordinal for node in nodes] == [1, 2]
        assert [node.output_summary for node in nodes] == [
            {"ordinal": 1},
            {"ordinal": 2},
        ]
