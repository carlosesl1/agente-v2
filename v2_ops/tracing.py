from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, TypeVar

from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
    deterministic_node_id,
)
from v2_ops.recording import DegradationReason, NullOpsRecorder, OpsRecorder
from v2_ops.serialization import serialize_ops_value

T = TypeVar("T")


class OpsExecutionTrace:
    """One best-effort, process-local ordinal stream for an existing execution."""

    def __init__(
        self,
        *,
        execution_id: str,
        recorder: OpsRecorder | None = None,
        full_content: bool = False,
        first_ordinal: int = 5,
    ) -> None:
        if type(execution_id) is not str or not execution_id:
            raise ValueError("execution_id must be non-empty exact text")
        if type(full_content) is not bool:
            raise TypeError("full_content must be an exact bool")
        if type(first_ordinal) is not int or first_ordinal < 1:
            raise ValueError("first_ordinal must be positive")
        self.execution_id = execution_id
        self._recorder = NullOpsRecorder() if recorder is None else recorder
        self._full_content = full_content
        self._next_ordinal = first_ordinal
        self._parent_node_id: str | None = deterministic_node_id(
            execution_id=execution_id,
            node_type=NodeType.INBOX_CLAIM,
            ordinal=4,
            attempt=1,
        )

    @property
    def current_node_id(self) -> str | None:
        return self._parent_node_id

    def finish_execution(
        self,
        *,
        lead_id: str,
        received_at: datetime,
        status: ExecutionStatus,
        terminal_reason: str,
    ) -> None:
        try:
            item = OpsExecution(
                execution_id=self.execution_id,
                lead_id=lead_id,
                received_at=received_at,
                status=status,
                trace_completeness=TraceCompleteness.PARTIAL_TRACE,
                current_node_id=self._parent_node_id,
                completed_at=datetime.now(timezone.utc),
                terminal_reason=terminal_reason,
            )
            self._recorder.finish_execution(item)
        except BaseException:
            self._report(
                DegradationReason.EXECUTION_FINISH_FAILED,
                node_id=self._parent_node_id,
            )

    def _report(
        self,
        reason: DegradationReason,
        *,
        node_id: str | None,
    ) -> None:
        try:
            self._recorder.report_degradation(
                reason,
                execution_id=self.execution_id,
                node_id=node_id,
            )
        except BaseException:
            pass

    def record_value(
        self,
        node_type: NodeType,
        value: object,
        *,
        attempt: int = 1,
        technical_metadata: dict[str, object] | None = None,
    ) -> None:
        self.call(
            node_type,
            lambda: value,
            input_value=value,
            output_value=lambda result: result,
            attempt=attempt,
            technical_metadata=technical_metadata,
        )

    def call(
        self,
        node_type: NodeType,
        call: Callable[[], T],
        *,
        input_value: object | None = None,
        output_value: Callable[[T], object] | None = None,
        attempt: int = 1,
        technical_metadata: dict[str, object] | None = None,
    ) -> T:
        ordinal = self._next_ordinal
        self._next_ordinal += 1
        try:
            input_summary: dict[str, object] = {}
            input_full: dict[str, object] | None = None
            if input_value is not None:
                input_summary, serialized_full = serialize_ops_value(input_value)
                if self._full_content:
                    input_full = serialized_full
            node = OpsNodeStart(
                execution_id=self.execution_id,
                node_type=node_type,
                ordinal=ordinal,
                attempt=attempt,
                parent_node_id=self._parent_node_id,
                started_at=datetime.now(timezone.utc),
                input_summary=input_summary,
                input_full=input_full,
                technical_metadata=technical_metadata or {},
            )
        except BaseException:
            self._report(DegradationReason.RESULT_SERIALIZATION_FAILED, node_id=None)
            return call()
        try:
            self._recorder.start_node(node)
        except BaseException:
            self._report(DegradationReason.NODE_START_FAILED, node_id=node.node_id)
        try:
            result = call()
        except BaseException:
            try:
                failed = OpsNodeFinish.from_start(
                    node,
                    status=ExecutionStatus.FAILED,
                    completed_at=datetime.now(timezone.utc),
                    error={"kind": "wrapped_call_failed"},
                )
                try:
                    self._recorder.finish_node(failed)
                except BaseException:
                    self._report(
                        DegradationReason.NODE_FINISH_FAILED,
                        node_id=node.node_id,
                    )
            except BaseException:
                self._report(DegradationReason.NODE_FINISH_FAILED, node_id=node.node_id)
            raise
        output_summary: dict[str, object] = {}
        output_full: dict[str, object] | None = None
        if output_value is not None:
            try:
                output_summary, serialized_full = serialize_ops_value(output_value(result))
                if self._full_content:
                    output_full = serialized_full
            except BaseException:
                self._report(
                    DegradationReason.RESULT_SERIALIZATION_FAILED,
                    node_id=node.node_id,
                )
        try:
            completed = OpsNodeFinish.from_start(
                node,
                status=ExecutionStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc),
                output_summary=output_summary,
                output_full=output_full,
            )
            try:
                self._recorder.finish_node(completed)
            except BaseException:
                self._report(
                    DegradationReason.NODE_FINISH_FAILED,
                    node_id=node.node_id,
                )
        except BaseException:
            self._report(DegradationReason.NODE_FINISH_FAILED, node_id=node.node_id)
        self._parent_node_id = node.node_id
        return result


__all__ = ["OpsExecutionTrace"]
