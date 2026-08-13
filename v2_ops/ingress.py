from __future__ import annotations

from datetime import timedelta

from v2_contracts.channel import AcceptDisposition, InboundEvent
from v2_ops.contracts import ExecutionStatus, NodeType, OpsExecution, OpsNodeFinish, OpsNodeStart, TraceCompleteness
from v2_ops.recording import DegradationReason, NullOpsRecorder, OpsRecorder
from v2_ops.serialization import serialize_ops_value


def record_ingress(
    recorder: OpsRecorder | None,
    *,
    event: InboundEvent,
    disposition: AcceptDisposition,
    full_content: bool = False,
) -> None:
    """Best-effort ingress graph emitted only after canonical parse and inbox result."""

    if type(event) is not InboundEvent:
        raise TypeError("event must be an exact InboundEvent")
    if type(disposition) is not AcceptDisposition:
        raise TypeError("disposition must be an exact AcceptDisposition")
    if type(full_content) is not bool:
        raise TypeError("full_content must be an exact bool")
    sink = NullOpsRecorder() if recorder is None else recorder
    execution = OpsExecution(
        execution_id=event.event_id,
        lead_id=event.lead_id,
        received_at=event.occurred_at,
        trace_completeness=TraceCompleteness.PARTIAL_TRACE,
    )
    try:
        sink.start_execution(execution)
    except BaseException:
        try:
            sink.report_degradation(
                DegradationReason.EXECUTION_START_FAILED,
                execution_id=event.event_id,
                node_id=None,
            )
        except BaseException:
            pass
    try:
        event_summary, event_full = serialize_ops_value(event)
        summaries = (
            {
                "payload_hash": event.payload_hash,
                "has_text": event_summary["has_text"],
                "has_media": event_summary["has_media"],
                "media_type": event_summary["media_type"],
            },
            {
                "payload_hash": event.payload_hash,
                "subscriber_fingerprint": event_summary["subscriber_fingerprint"],
                "conversation_fingerprint": event_summary["conversation_fingerprint"],
            },
            {
                "status": disposition.value,
                "payload_hash": event.payload_hash,
            },
        )
        node_types = (
            NodeType.MANYCHAT_WEBHOOK,
            NodeType.ROUTER_VALIDATION,
            NodeType.INBOX_ACCEPT,
        )
        parent: str | None = None
        for ordinal, (node_type, summary) in enumerate(zip(node_types, summaries), 1):
            node = OpsNodeStart(
                execution_id=event.event_id,
                node_type=node_type,
                ordinal=ordinal,
                parent_node_id=parent,
                started_at=event.occurred_at + timedelta(microseconds=ordinal),
                input_summary=summary,
                input_full=(event_full if full_content and ordinal == 1 else None),
                technical_metadata={"trace_stage": node_type.value},
            )
            try:
                sink.start_node(node)
            except BaseException:
                try:
                    sink.report_degradation(
                        DegradationReason.NODE_START_FAILED,
                        execution_id=event.event_id,
                        node_id=node.node_id,
                    )
                except BaseException:
                    pass
            finished = OpsNodeFinish.from_start(
                node,
                status=ExecutionStatus.COMPLETED,
                completed_at=event.occurred_at + timedelta(microseconds=ordinal * 2),
                output_summary={"status": disposition.value} if ordinal == 3 else {},
            )
            try:
                sink.finish_node(finished)
            except BaseException:
                try:
                    sink.report_degradation(
                        DegradationReason.NODE_FINISH_FAILED,
                        execution_id=event.event_id,
                        node_id=node.node_id,
                    )
                except BaseException:
                    pass
            parent = node.node_id
    except BaseException:
        try:
            sink.report_degradation(
                DegradationReason.RESULT_SERIALIZATION_FAILED,
                execution_id=event.event_id,
                node_id=None,
            )
        except BaseException:
            pass


__all__ = ["record_ingress"]
