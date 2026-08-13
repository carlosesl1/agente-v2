from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from v2_contracts.channel import InboundEvent
from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
)
from v2_ops.recording import (
    DegradationReason,
    NullOpsRecorder,
    SQLiteOpsRecorder,
    record_boundary,
    record_effect_boundary,
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


def _effect_value(event_id: str) -> InboundEvent:
    return InboundEvent(
        event_id=event_id,
        lead_id="manychat:1873018537",
        subscriber_id="1873018537",
        conversation_id="conversation:ops-recording",
        text="typed payload",
        media_url=None,
        media_type=None,
        occurred_at=NOW,
        payload_hash="a" * 64,
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


def test_effect_boundary_appends_after_terminal_and_calls_business_once(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "effect-boundary.sqlite3").resolve()
    calls = 0
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        recorder = SQLiteOpsRecorder(writer)
        turn = _node()
        recorder.start_execution(_execution())
        recorder.start_node(turn)
        recorder.finish_node(
            OpsNodeFinish.from_start(
                turn,
                status=ExecutionStatus.COMPLETED,
                completed_at=NOW + timedelta(seconds=2),
            )
        )
        recorder.finish_execution(
            OpsExecution(
                execution_id=turn.execution_id,
                lead_id="lead:opaque-recording",
                received_at=NOW,
                status=ExecutionStatus.COMPLETED,
                trace_completeness=TraceCompleteness.COMPLETE_TRACE,
                current_node_id=turn.node_id,
                completed_at=NOW + timedelta(seconds=2),
                terminal_reason="turn_committed",
            )
        )

        pending = OpsNodeStart(
            execution_id=turn.execution_id,
            node_type=NodeType.CLOUDBEDS_RESERVATION_REQUEST,
            ordinal=10_000,
            attempt=1,
            parent_node_id=turn.node_id,
            started_at=NOW + timedelta(seconds=3),
            input_summary={"phase": "pending"},
            technical_metadata={},
        )
        recorder.start_effect_node(pending)
        with SQLiteOpsTraceReader(path, KEY) as reader:
            pending_detail = reader.get_execution(turn.execution_id)
            assert reader.list_executions(
                status="running_stale",
                now=NOW + timedelta(hours=1),
            ).executions == ()
        assert pending_detail.status == ExecutionStatus.COMPLETED.value
        assert pending_detail.completed_at == NOW + timedelta(seconds=2)
        recorder.finish_effect_node(
            OpsNodeFinish.from_start(
                pending,
                status=ExecutionStatus.COMPLETED,
                completed_at=NOW + timedelta(seconds=3),
            )
        )

        def business() -> object:
            nonlocal calls
            calls += 1
            return _effect_value("event:result-dto")

        result = record_effect_boundary(
            recorder,
            execution_id=turn.execution_id,
            node_type=NodeType.CLOUDBEDS_RESERVATION_REQUEST,
            input_value=_effect_value("event:input-dto"),
            call=business,
            started_at=lambda: NOW + timedelta(seconds=3),
            completed_at=lambda: NOW + timedelta(seconds=4),
            full_content=True,
        )

    assert calls == 1
    assert result.event_id == "event:result-dto"
    with SQLiteOpsTraceReader(path, KEY) as reader:
        nodes = reader.list_nodes(turn.execution_id)
        detail = reader.get_execution(turn.execution_id)
    assert nodes[-1].node_type is NodeType.CLOUDBEDS_RESERVATION_REQUEST
    assert nodes[-1].stored_status is ExecutionStatus.COMPLETED
    assert detail.status == ExecutionStatus.COMPLETED.value
    assert detail.stored_status is ExecutionStatus.COMPLETED
    assert detail.completed_at == NOW + timedelta(seconds=2)


@pytest.mark.parametrize(
    "failure_stage",
    ("position", "start", "finish", "clock", "serialize"),
)
def test_effect_trace_failure_never_retries_or_replaces_result(
    failure_stage: str,
) -> None:
    class EffectFaultRecorder(NullOpsRecorder):
        __slots__ = ()

        def effect_node_position(self, execution_id: str) -> tuple[int, str]:
            if failure_stage == "position":
                raise TraceBaseFailure("position")
            return 10_000, "a" * 64

        def start_effect_node(self, item: OpsNodeStart) -> None:
            if failure_stage == "start":
                raise TraceBaseFailure("start")

        def finish_effect_node(self, item: object) -> None:
            if failure_stage == "finish":
                raise TraceBaseFailure("finish")

    calls = 0
    result = _effect_value("event:same-result")

    def business() -> InboundEvent:
        nonlocal calls
        calls += 1
        return result

    returned = record_effect_boundary(
        EffectFaultRecorder(),
        execution_id="event:ops-recording",
        node_type=NodeType.CLOUDBEDS_RESERVATION_REQUEST,
        input_value=(object() if failure_stage == "serialize" else result),
        call=business,
        started_at=lambda: NOW + timedelta(seconds=2),
        completed_at=(
            (lambda: (_ for _ in ()).throw(TraceBaseFailure("clock")))
            if failure_stage == "clock"
            else lambda: NOW + timedelta(seconds=3)
        ),
        full_content=True,
    )

    assert returned is result
    assert calls == 1


def test_effect_response_milestone_failure_never_retries_or_replaces_result() -> None:
    class ResponseFaultRecorder(NullOpsRecorder):
        __slots__ = ("positions",)

        def __init__(self) -> None:
            self.positions = 0

        def effect_node_position(self, execution_id: str) -> tuple[int, str]:
            del execution_id
            self.positions += 1
            return 10_000 + self.positions, "a" * 64

        def start_effect_node(self, item: OpsNodeStart) -> None:
            if item.node_type is NodeType.MANYCHAT_DELIVERY_RESPONSE:
                raise TraceBaseFailure("response")

    calls = 0
    result = _effect_value("event:response-milestone-result")

    def business() -> InboundEvent:
        nonlocal calls
        calls += 1
        return result

    returned = record_effect_boundary(
        ResponseFaultRecorder(),
        execution_id="event:response-milestone",
        node_type=NodeType.MANYCHAT_DELIVERY_REQUEST,
        response_node_type=NodeType.MANYCHAT_DELIVERY_RESPONSE,
        input_value=_effect_value("event:response-milestone-input"),
        call=business,
        started_at=lambda: NOW,
        completed_at=lambda: NOW + timedelta(seconds=1),
    )

    assert returned is result
    assert calls == 1
