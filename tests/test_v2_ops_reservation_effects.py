from __future__ import annotations

from datetime import timedelta

from reservation_execution import DispatchPermit, Lease
from tests.test_v2_reservations import (
    NOW,
    FakeReservationPort,
    _authorization,
    _group_activity_command,
    _group_passengers,
    _result,
)
from v2_application.lead_identity import EffectTraceContext
from v2_application.reservations import V2ReservationExecutionAdapter
from v2_contracts.providers import ProviderCertainty
from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
)
from v2_ops.recording import SQLiteOpsRecorder
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter


def _terminal_trace(
    writer: SQLiteOpsTraceWriter,
    execution_id: str,
    *,
    terminal_at=NOW,
) -> None:
    recorder = SQLiteOpsRecorder(writer)
    received = terminal_at - timedelta(seconds=2)
    recorder.start_execution(
        OpsExecution(
            execution_id=execution_id,
            lead_id="manychat:1873018537",
            received_at=received,
            trace_completeness=TraceCompleteness.PARTIAL_TRACE,
        )
    )
    commit = OpsNodeStart(
        execution_id=execution_id,
        node_type=NodeType.TURN_COMMIT,
        ordinal=1,
        started_at=terminal_at - timedelta(seconds=1),
    )
    recorder.start_node(commit)
    recorder.finish_node(
        OpsNodeFinish.from_start(
            commit,
            status=ExecutionStatus.COMPLETED,
            completed_at=terminal_at,
        )
    )
    recorder.finish_execution(
        OpsExecution(
            execution_id=execution_id,
            lead_id="manychat:1873018537",
            received_at=received,
            status=ExecutionStatus.COMPLETED,
            trace_completeness=TraceCompleteness.PARTIAL_TRACE,
            current_node_id=commit.node_id,
            completed_at=terminal_at,
            terminal_reason="turn_committed",
        )
    )


def test_bokun_dispatch_after_fence_records_request_response_and_calls_once(
    tmp_path,
) -> None:
    execution_id = "event:reservation-effect"
    source_receipt = "c" * 64
    trace_path = (tmp_path / "ops.sqlite3").resolve()
    trace_key = b"r" * 32
    writer = SQLiteOpsTraceWriter(trace_path, trace_key)
    _terminal_trace(writer, execution_id)
    command = _group_activity_command(_group_passengers())
    port = FakeReservationPort(
        "bokun",
        _result(ProviderCertainty.EFFECT_CONFIRMED),
    )

    class Resolver:
        def effect_trace_for_command(self, command_id: str) -> EffectTraceContext:
            assert command_id == command.command_id
            return EffectTraceContext(
                execution_id,
                "manychat:1873018537",
                source_receipt,
            )

    adapter = V2ReservationExecutionAdapter(
        provider="bokun",
        port=port,
        authorization=_authorization("bokun"),
        require_private_binding=False,
        ops_recorder=SQLiteOpsRecorder(writer),
        effect_trace_resolver=Resolver(),
        ops_full_content=True,
    )
    request = adapter.prepare(command)
    permit = DispatchPermit(
        command_id=command.command_id,
        lease=Lease(
            owner="worker:ops-reservation",
            fencing_token=1,
            acquired_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
        ),
        dispatch_slot=1,
        request_hash=request.payload_hash,
        fenced_at=NOW,
    )
    try:
        outcome = adapter.dispatch_fenced(
            permit,
            request,
            idempotency_key=command.idempotency_key,
        )
    finally:
        writer.close()

    assert outcome.command_id == command.command_id
    assert len(port.calls) == 1
    with SQLiteOpsTraceReader(trace_path, trace_key) as reader:
        nodes = reader.list_nodes(execution_id)
    assert [item.node_type for item in nodes[-2:]] == [
        NodeType.BOKUN_BOOKING_REQUEST,
        NodeType.BOKUN_BOOKING_RESPONSE,
    ]
    assert nodes[-2].has_full_input is True
    assert nodes[-2].has_full_output is True
    assert nodes[-1].has_full_input is True
    assert nodes[-1].has_full_output is True
