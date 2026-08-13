from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, TypeVar

from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
)
from v2_ops.serialization import serialize_ops_value


T = TypeVar("T")


class DegradationReason(str, Enum):
    EXECUTION_START_FAILED = "execution_start_failed"
    NODE_START_FAILED = "node_start_failed"
    RESULT_SERIALIZATION_FAILED = "result_serialization_failed"
    NODE_FINISH_FAILED = "node_finish_failed"
    EXECUTION_FINISH_FAILED = "execution_finish_failed"


@dataclass(frozen=True, slots=True)
class OpsDegradation:
    reason: DegradationReason
    execution_id: str
    node_id: str | None


class TraceWriter(Protocol):
    def write_execution(self, item: OpsExecution) -> None: ...

    def start_node(self, item: OpsNodeStart) -> None: ...

    def finish_node(self, item: OpsNodeFinish) -> None: ...

    def effect_node_position(self, execution_id: str) -> tuple[int, str]: ...

    def start_effect_node(self, item: OpsNodeStart) -> None: ...

    def finish_effect_node(self, item: OpsNodeFinish) -> None: ...


class OpsRecorder(Protocol):
    def start_execution(self, item: OpsExecution) -> None: ...

    def start_node(self, item: OpsNodeStart) -> None: ...

    def finish_node(self, item: OpsNodeFinish) -> None: ...

    def finish_execution(self, item: OpsExecution) -> None: ...

    def effect_node_position(self, execution_id: str) -> tuple[int, str]: ...

    def start_effect_node(self, item: OpsNodeStart) -> None: ...

    def finish_effect_node(self, item: OpsNodeFinish) -> None: ...

    def report_degradation(
        self,
        reason: DegradationReason,
        *,
        execution_id: str,
        node_id: str | None,
    ) -> None: ...


class NullOpsRecorder:
    __slots__ = ()

    def start_execution(self, item: OpsExecution) -> None:
        del item

    def start_node(self, item: OpsNodeStart) -> None:
        del item

    def finish_node(self, item: OpsNodeFinish) -> None:
        del item

    def finish_execution(self, item: OpsExecution) -> None:
        del item

    def effect_node_position(self, execution_id: str) -> tuple[int, str]:
        del execution_id
        raise RuntimeError("null recorder has no effect position")

    def start_effect_node(self, item: OpsNodeStart) -> None:
        del item

    def finish_effect_node(self, item: OpsNodeFinish) -> None:
        del item

    def report_degradation(
        self,
        reason: DegradationReason,
        *,
        execution_id: str,
        node_id: str | None,
    ) -> None:
        del reason, execution_id, node_id


class SQLiteOpsRecorder:
    __slots__ = ("_writer", "_on_degraded")

    def __init__(
        self,
        writer: TraceWriter,
        *,
        on_degraded: Callable[[OpsDegradation], object] | None = None,
    ) -> None:
        self._writer = writer
        self._on_degraded = on_degraded

    def start_execution(self, item: OpsExecution) -> None:
        self._writer.write_execution(item)

    def start_node(self, item: OpsNodeStart) -> None:
        self._writer.start_node(item)

    def finish_node(self, item: OpsNodeFinish) -> None:
        self._writer.finish_node(item)

    def finish_execution(self, item: OpsExecution) -> None:
        self._writer.write_execution(item)

    def effect_node_position(self, execution_id: str) -> tuple[int, str]:
        return self._writer.effect_node_position(execution_id)

    def start_effect_node(self, item: OpsNodeStart) -> None:
        self._writer.start_effect_node(item)

    def finish_effect_node(self, item: OpsNodeFinish) -> None:
        self._writer.finish_effect_node(item)

    def report_degradation(
        self,
        reason: DegradationReason,
        *,
        execution_id: str,
        node_id: str | None,
    ) -> None:
        callback = self._on_degraded
        if callback is None:
            return
        try:
            callback(
                OpsDegradation(
                    reason=reason,
                    execution_id=execution_id,
                    node_id=node_id,
                )
            )
        except BaseException:
            return


def _record_best_effort(
    recorder: OpsRecorder,
    operation: Callable[[], None],
    reason: DegradationReason,
    *,
    execution_id: str,
    node_id: str | None,
) -> bool:
    try:
        operation()
        return True
    except BaseException:
        try:
            recorder.report_degradation(
                reason,
                execution_id=execution_id,
                node_id=node_id,
            )
        except BaseException:
            pass
        return False


def _terminal_execution(
    execution: OpsExecution,
    node: OpsNodeStart,
    *,
    status: ExecutionStatus,
    completed_at: datetime,
    terminal_reason: str,
) -> OpsExecution:
    return OpsExecution(
        execution_id=execution.execution_id,
        lead_id=execution.lead_id,
        received_at=execution.received_at,
        status=status,
        trace_completeness=execution.trace_completeness,
        current_node_id=node.node_id,
        completed_at=completed_at,
        terminal_reason=terminal_reason,
    )


def record_node_boundary(
    recorder: OpsRecorder,
    *,
    node: OpsNodeStart,
    call: Callable[[], T],
    completed_at: Callable[[], datetime],
    serialize_result: Callable[
        [T], tuple[dict[str, object], dict[str, object] | None]
    ]
    | None = None,
) -> T:
    """Record one node without terminalizing its containing execution."""

    _record_best_effort(
        recorder,
        lambda: recorder.start_node(node),
        DegradationReason.NODE_START_FAILED,
        execution_id=node.execution_id,
        node_id=node.node_id,
    )
    try:
        result = call()
    except BaseException:
        try:
            finished_at = completed_at()
            failed = OpsNodeFinish.from_start(
                node,
                status=ExecutionStatus.FAILED,
                completed_at=finished_at,
                error={"kind": "wrapped_call_failed"},
            )
            _record_best_effort(
                recorder,
                lambda: recorder.finish_node(failed),
                DegradationReason.NODE_FINISH_FAILED,
                execution_id=node.execution_id,
                node_id=node.node_id,
            )
        except BaseException:
            try:
                recorder.report_degradation(
                    DegradationReason.NODE_FINISH_FAILED,
                    execution_id=node.execution_id,
                    node_id=node.node_id,
                )
            except BaseException:
                pass
        raise

    output_summary: dict[str, object] = {}
    output_full: dict[str, object] | None = None
    if serialize_result is not None:
        try:
            output_summary, output_full = serialize_result(result)
        except BaseException:
            try:
                recorder.report_degradation(
                    DegradationReason.RESULT_SERIALIZATION_FAILED,
                    execution_id=node.execution_id,
                    node_id=node.node_id,
                )
            except BaseException:
                pass
    try:
        finished_at = completed_at()
        completed = OpsNodeFinish.from_start(
            node,
            status=ExecutionStatus.COMPLETED,
            completed_at=finished_at,
            output_summary=output_summary,
            output_full=output_full,
        )
        _record_best_effort(
            recorder,
            lambda: recorder.finish_node(completed),
            DegradationReason.NODE_FINISH_FAILED,
            execution_id=node.execution_id,
            node_id=node.node_id,
        )
    except BaseException:
        try:
            recorder.report_degradation(
                DegradationReason.NODE_FINISH_FAILED,
                execution_id=node.execution_id,
                node_id=node.node_id,
            )
        except BaseException:
            pass
    return result


def record_effect_boundary(
    recorder: OpsRecorder,
    *,
    execution_id: str,
    node_type: NodeType,
    input_value: object,
    call: Callable[[], T],
    started_at: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    completed_at: Callable[[], datetime],
    response_node_type: NodeType | None = None,
    full_content: bool = False,
    technical_metadata: dict[str, object] | None = None,
) -> T:
    """Record one post-commit effect without changing its call semantics."""

    if type(full_content) is not bool:
        raise TypeError("full_content must be an exact bool")
    try:
        ordinal, parent_node_id = recorder.effect_node_position(execution_id)
        input_summary, input_full = serialize_ops_value(input_value)
        node = OpsNodeStart(
            execution_id=execution_id,
            node_type=node_type,
            ordinal=ordinal,
            parent_node_id=parent_node_id,
            started_at=started_at(),
            input_summary=input_summary,
            input_full=input_full if full_content else None,
            technical_metadata=technical_metadata or {},
        )
    except BaseException:
        try:
            recorder.report_degradation(
                DegradationReason.RESULT_SERIALIZATION_FAILED,
                execution_id=execution_id,
                node_id=None,
            )
        except BaseException:
            pass
        return call()
    try:
        recorder.start_effect_node(node)
    except BaseException:
        try:
            recorder.report_degradation(
                DegradationReason.NODE_START_FAILED,
                execution_id=execution_id,
                node_id=node.node_id,
            )
        except BaseException:
            pass
    try:
        result = call()
    except BaseException:
        try:
            recorder.finish_effect_node(
                OpsNodeFinish.from_start(
                    node,
                    status=ExecutionStatus.FAILED,
                    completed_at=completed_at(),
                    error={"kind": "wrapped_call_failed"},
                )
            )
        except BaseException:
            try:
                recorder.report_degradation(
                    DegradationReason.NODE_FINISH_FAILED,
                    execution_id=execution_id,
                    node_id=node.node_id,
                )
            except BaseException:
                pass
        raise
    try:
        output_summary, output_full = serialize_ops_value(result)
    except BaseException:
        output_summary, output_full = {}, None
        try:
            recorder.report_degradation(
                DegradationReason.RESULT_SERIALIZATION_FAILED,
                execution_id=execution_id,
                node_id=node.node_id,
            )
        except BaseException:
            pass
    try:
        recorder.finish_effect_node(
            OpsNodeFinish.from_start(
                node,
                status=ExecutionStatus.COMPLETED,
                completed_at=completed_at(),
                output_summary=output_summary,
                output_full=output_full if full_content else None,
            )
        )
    except BaseException:
        try:
            recorder.report_degradation(
                DegradationReason.NODE_FINISH_FAILED,
                execution_id=execution_id,
                node_id=node.node_id,
            )
        except BaseException:
            pass
    if response_node_type is not None:
        record_effect_value(
            recorder,
            execution_id=execution_id,
            node_type=response_node_type,
            value=result,
            observed_at=completed_at,
            full_content=full_content,
            technical_metadata={
                **(technical_metadata or {}),
                "trace_stage": response_node_type.value,
            },
        )
    return result


def record_effect_value(
    recorder: OpsRecorder,
    *,
    execution_id: str,
    node_type: NodeType,
    value: object,
    observed_at: Callable[[], datetime],
    full_content: bool = False,
    technical_metadata: dict[str, object] | None = None,
) -> None:
    """Append a typed effect milestone without any business call."""

    try:
        ordinal, parent_node_id = recorder.effect_node_position(execution_id)
        summary, full = serialize_ops_value(value)
        instant = observed_at()
        node = OpsNodeStart(
            execution_id=execution_id,
            node_type=node_type,
            ordinal=ordinal,
            parent_node_id=parent_node_id,
            started_at=instant,
            input_summary=summary,
            input_full=full if full_content else None,
            technical_metadata=technical_metadata or {},
        )
        recorder.start_effect_node(node)
        recorder.finish_effect_node(
            OpsNodeFinish.from_start(
                node,
                status=ExecutionStatus.COMPLETED,
                completed_at=instant,
                output_summary=summary,
                output_full=full if full_content else None,
            )
        )
    except BaseException:
        try:
            recorder.report_degradation(
                DegradationReason.NODE_FINISH_FAILED,
                execution_id=execution_id,
                node_id=None,
            )
        except BaseException:
            pass


def record_boundary(
    recorder: OpsRecorder,
    *,
    execution: OpsExecution,
    node: OpsNodeStart,
    call: Callable[[], T],
    completed_at: Callable[[], datetime],
    serialize_result: Callable[
        [T], tuple[dict[str, object], dict[str, object] | None]
    ]
    | None = None,
) -> T:
    _record_best_effort(
        recorder,
        lambda: recorder.start_execution(execution),
        DegradationReason.EXECUTION_START_FAILED,
        execution_id=execution.execution_id,
        node_id=node.node_id,
    )
    _record_best_effort(
        recorder,
        lambda: recorder.start_node(node),
        DegradationReason.NODE_START_FAILED,
        execution_id=execution.execution_id,
        node_id=node.node_id,
    )

    try:
        result = call()
    except BaseException:
        try:
            finished_at = completed_at()
            failed_node = OpsNodeFinish.from_start(
                node,
                status=ExecutionStatus.FAILED,
                completed_at=finished_at,
                error={"kind": "wrapped_call_failed"},
            )
            _record_best_effort(
                recorder,
                lambda: recorder.finish_node(failed_node),
                DegradationReason.NODE_FINISH_FAILED,
                execution_id=execution.execution_id,
                node_id=node.node_id,
            )
            failed_execution = _terminal_execution(
                execution,
                node,
                status=ExecutionStatus.FAILED,
                completed_at=finished_at,
                terminal_reason="wrapped_call_failed",
            )
            _record_best_effort(
                recorder,
                lambda: recorder.finish_execution(failed_execution),
                DegradationReason.EXECUTION_FINISH_FAILED,
                execution_id=execution.execution_id,
                node_id=node.node_id,
            )
        except BaseException:
            try:
                recorder.report_degradation(
                    DegradationReason.NODE_FINISH_FAILED,
                    execution_id=execution.execution_id,
                    node_id=node.node_id,
                )
            except BaseException:
                pass
        raise

    output_summary: dict[str, object] = {}
    output_full: dict[str, object] | None = None
    if serialize_result is not None:
        try:
            output_summary, output_full = serialize_result(result)
        except BaseException:
            try:
                recorder.report_degradation(
                    DegradationReason.RESULT_SERIALIZATION_FAILED,
                    execution_id=execution.execution_id,
                    node_id=node.node_id,
                )
            except BaseException:
                pass
            output_summary = {}
            output_full = None

    try:
        finished_at = completed_at()
        completed_node = OpsNodeFinish.from_start(
            node,
            status=ExecutionStatus.COMPLETED,
            completed_at=finished_at,
            output_summary=output_summary,
            output_full=output_full,
        )
        _record_best_effort(
            recorder,
            lambda: recorder.finish_node(completed_node),
            DegradationReason.NODE_FINISH_FAILED,
            execution_id=execution.execution_id,
            node_id=node.node_id,
        )
        completed_execution = _terminal_execution(
            execution,
            node,
            status=ExecutionStatus.COMPLETED,
            completed_at=finished_at,
            terminal_reason="wrapped_call_completed",
        )
        _record_best_effort(
            recorder,
            lambda: recorder.finish_execution(completed_execution),
            DegradationReason.EXECUTION_FINISH_FAILED,
            execution_id=execution.execution_id,
            node_id=node.node_id,
        )
    except BaseException:
        try:
            recorder.report_degradation(
                DegradationReason.NODE_FINISH_FAILED,
                execution_id=execution.execution_id,
                node_id=node.node_id,
            )
        except BaseException:
            pass
    return result
