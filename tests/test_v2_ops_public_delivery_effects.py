from __future__ import annotations

from datetime import timedelta

from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from tests.test_v2_ops_reservation_effects import _terminal_trace
from tests.test_v2_turn_executor import NOW, RecordingPublicDelivery, _committed_public_store
from v2_application.lead_identity import EffectTraceContext
from v2_application.public_delivery import (
    BoundaryPublicDeliveryWorker,
    BoundaryPublicDisposition,
)
from v2_ops.contracts import NodeType
from v2_ops.recording import SQLiteOpsRecorder
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter


def test_public_delivery_after_fence_records_and_calls_channel_once(tmp_path) -> None:
    store = _committed_public_store()
    queue = SQLiteBoundaryWorkerStore(store)
    delivery = RecordingPublicDelivery()
    execution_id = "event:turn-executor-001"
    trace_path = (tmp_path / "ops-public.sqlite3").resolve()
    trace_key = b"d" * 32
    writer = SQLiteOpsTraceWriter(trace_path, trace_key)
    _terminal_trace(writer, execution_id, terminal_at=NOW)

    class Resolver:
        def effect_trace_for_public_row(
            self, public_row_id: str
        ) -> EffectTraceContext:
            assert public_row_id.startswith("public-row:")
            return EffectTraceContext(
                execution_id,
                "manychat:1873018537",
                "9" * 64,
            )

    worker = BoundaryPublicDeliveryWorker(
        boundary=queue,
        delivery=delivery,
        worker_id="worker:ops-public",
        lease_ttl=timedelta(seconds=30),
        ops_recorder=SQLiteOpsRecorder(writer),
        effect_trace_resolver=Resolver(),
        ops_full_content=True,
    )
    try:
        first = worker.run_once(now=NOW + timedelta(seconds=1))
        second = worker.run_once(now=NOW + timedelta(seconds=2))
    finally:
        writer.close()
        store.close()

    assert first is BoundaryPublicDisposition.ACCEPTED
    assert second is BoundaryPublicDisposition.IDLE
    assert len(delivery.calls) == 1
    with SQLiteOpsTraceReader(trace_path, trace_key) as reader:
        nodes = reader.list_nodes(execution_id)
    assert [item.node_type for item in nodes[-2:]] == [
        NodeType.MANYCHAT_DELIVERY_REQUEST,
        NodeType.MANYCHAT_DELIVERY_RESPONSE,
    ]
    assert nodes[-1].status == "completed"
    assert nodes[-1].has_full_input is True
    assert nodes[-1].has_full_output is True
