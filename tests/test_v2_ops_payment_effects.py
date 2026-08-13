from __future__ import annotations

from datetime import timedelta

from tests.test_v2_payment_initiation import (
    HOSTEL,
    NOW,
    RESULT_KEY,
    service,
)
from tests.test_v2_ops_reservation_effects import _terminal_trace
from v2_application.lead_identity import EffectTraceContext
from v2_application.payments import (
    PaymentInitiationDisposition,
    PaymentInitiationWorker,
    SQLitePaymentInitiationStore,
)
from v2_contracts.payments import PaymentMethod, PaymentSelection
from v2_ops.contracts import NodeType
from v2_ops.recording import SQLiteOpsRecorder
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter


def test_stripe_initiation_after_fence_records_once_and_calls_transport_once(
    tmp_path,
) -> None:
    execution_id = "event:payment-effect"
    trace_path = (tmp_path / "ops-payment.sqlite3").resolve()
    trace_key = b"p" * 32
    writer = SQLiteOpsTraceWriter(trace_path, trace_key)
    _terminal_trace(writer, execution_id, terminal_at=NOW)
    payments, transport, _ = service()
    selection = PaymentSelection(HOSTEL, PaymentMethod.STRIPE)
    store = SQLitePaymentInitiationStore(
        tmp_path / "payment.sqlite3",
        result_encryption_key=RESULT_KEY,
    )
    assert store.enqueue(selection, now=NOW) is True

    class Resolver:
        def effect_trace_for_payment(self, payment_id: str) -> EffectTraceContext:
            assert payment_id == HOSTEL.payment_id
            return EffectTraceContext(
                execution_id,
                "manychat:1873018537",
                "d" * 64,
            )

    worker = PaymentInitiationWorker(
        store=store,
        payments=payments,
        worker_id="worker:payment-ops",
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

    assert first.disposition is PaymentInitiationDisposition.COMPLETED
    assert second.disposition is PaymentInitiationDisposition.IDLE
    assert len(transport.requests) == 1
    with SQLiteOpsTraceReader(trace_path, trace_key) as reader:
        nodes = reader.list_nodes(execution_id)
    assert nodes[-1].node_type is NodeType.STRIPE_PAYMENT_LINK
    assert nodes[-1].status == "completed"
    assert nodes[-1].has_full_input is True
    assert nodes[-1].has_full_output is True
