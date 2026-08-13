from __future__ import annotations

from datetime import date
from pathlib import Path

from v2_contracts.providers import ReadKind, ReadRequest
from v2_ops.contracts import NodeType
from v2_ops.harness import create_synthetic_harness
from v2_ops.store import SQLiteOpsTraceReader


def test_synthetic_harness_is_explicit_complete_and_decryptable(tmp_path) -> None:
    path = (tmp_path / "synthetic-harness.sqlite3").resolve()
    key = b"h" * 32

    create_synthetic_harness(path, key)

    assert Path(f"{path}-wal").exists()
    assert Path(f"{path}-shm").exists()

    reader = SQLiteOpsTraceReader(path, key)
    page = reader.list_executions()
    execution = page.executions[0]
    nodes = reader.list_nodes(execution.execution_id)
    first_full = reader.read_full(execution.execution_id, nodes[0].node_id, side="input")
    request_node = next(
        node for node in nodes if node.node_type is NodeType.PROVIDER_READ_REQUEST
    )
    response_node = next(
        node for node in nodes if node.node_type is NodeType.PROVIDER_READ_RESPONSE
    )
    request_full = reader.read_full(
        execution.execution_id, request_node.node_id, side="input"
    )
    response_full = reader.read_full(
        execution.execution_id, response_node.node_id, side="input"
    )
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
    assert request_full is not None
    assert response_full is not None
    request = ReadRequest(
        request_id=str(request_full.value["request_id"]),
        kind=ReadKind(str(request_full.value["kind"])),
        check_in=date.fromisoformat(str(request_full.value["check_in"])),
        check_out=date.fromisoformat(str(request_full.value["check_out"])),
        adults=int(request_full.value["adults"]),
        children=int(request_full.value["children"]),
    )
    assert response_full.value["request_hash"] == request.canonical_hash()
