from __future__ import annotations

from datetime import timedelta

from tests.test_v2_turn_executor import (
    BATCH,
    EVENT,
    NOW,
    FakeAuditedModel,
    FakeProfile,
    FixedAuthority,
    FixedClock,
    _enabled_reducer,
    _install_public_authority,
    _proposal,
)
from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
from v2_application.reads import V2ReadService
from v2_application.turn_executor import V2TurnExecutor
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


def test_executor_persists_causal_maya_reducer_commit_graph(tmp_path) -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [_proposal()])
    _install_public_authority(store)
    trace_path = tmp_path / "ops.sqlite3"
    trace_key = b"o" * 32
    writer = SQLiteOpsTraceWriter(trace_path, trace_key)
    recorder = SQLiteOpsRecorder(writer)
    recorder.start_execution(
        OpsExecution(
            execution_id=EVENT.event_id,
            lead_id=BATCH.lead_id,
            received_at=EVENT.occurred_at,
            trace_completeness=TraceCompleteness.PARTIAL_TRACE,
        )
    )
    claim = OpsNodeStart(
        execution_id=EVENT.event_id,
        node_type=NodeType.INBOX_CLAIM,
        ordinal=1,
        started_at=NOW,
    )
    recorder.start_node(claim)
    recorder.finish_node(
        OpsNodeFinish.from_start(
            claim,
            status=ExecutionStatus.COMPLETED,
            completed_at=NOW,
        )
    )
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=FakeProfile(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
        ops_recorder=recorder,
        ops_full_content=True,
    )
    try:
        result = executor.execute(BATCH)
        assert result.replayed is False
    finally:
        private_store.close()
        writer.close()
        store.close()

    reader = SQLiteOpsTraceReader(trace_path, trace_key)
    persisted = reader.get_execution(EVENT.event_id)
    nodes = reader.list_nodes(EVENT.event_id)
    reader.close()

    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.terminal_reason == "turn_committed"
    assert [item.node_type for item in nodes] == [
        NodeType.INBOX_CLAIM,
        NodeType.MAYA_REQUEST,
        NodeType.MAYA_RESPONSE,
        NodeType.CONVERSATION_REDUCER,
        NodeType.TURN_COMMIT,
    ]
    assert [item.ordinal for item in nodes] == [1, 2, 3, 4, 5]
    assert all(
        node.parent_node_id == nodes[index - 1].node_id
        for index, node in enumerate(nodes[1:], start=1)
    )
    assert persisted.current_node_id == nodes[-1].node_id
