from __future__ import annotations

from v2_ops.contracts import NodeType
from v2_ops.harness import create_synthetic_harness
from v2_ops.store import SQLiteOpsTraceReader


def test_synthetic_harness_is_explicit_complete_and_decryptable(tmp_path) -> None:
    path = (tmp_path / "synthetic-harness.sqlite3").resolve()
    key = b"h" * 32

    create_synthetic_harness(path, key)

    reader = SQLiteOpsTraceReader(path, key)
    page = reader.list_executions()
    execution = page.executions[0]
    nodes = reader.list_nodes(execution.execution_id)
    first_full = reader.read_full(execution.execution_id, nodes[0].node_id, side="input")
    reader.close()

    assert execution.execution_id.startswith("synthetic-harness:")
    assert execution.lead_id.startswith("synthetic-harness:")
    assert execution.status == "completed"
    assert execution.terminal_reason == "synthetic_harness_completed"
    assert [node.node_type for node in nodes] == [
        NodeType.MANYCHAT_WEBHOOK,
        NodeType.INBOX_ACCEPT,
        NodeType.MAYA_REQUEST,
        NodeType.MAYA_RESPONSE,
        NodeType.MAYA_READ_REQUEST,
        NodeType.PROVIDER_READ_REQUEST,
        NodeType.PROVIDER_READ_RESPONSE,
        NodeType.MAYA_OBSERVATION,
        NodeType.CONVERSATION_REDUCER,
        NodeType.TURN_COMMIT,
    ]
    assert first_full is not None
    assert first_full.value["event_id"].startswith("synthetic-harness:")
