from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from v2_contracts.channel import AcceptDisposition, InboundBatch, InboundEvent
from v2_contracts.model import ModelProposal, ModelRequest
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest
from v2_ops.contracts import ExecutionStatus, NodeType, OpsExecution, TraceCompleteness
from v2_ops.recording import SQLiteOpsRecorder
from v2_ops.store import SQLiteOpsTraceWriter
from v2_ops.tracing import OpsExecutionTrace


def _preserve_wal_sidecars(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            raise RuntimeError("synthetic harness store is not in WAL mode")
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
        wal_path = Path(f"{path}-wal")
        shm_path = Path(f"{path}-shm")
        wal_bytes = wal_path.read_bytes()
        shm_bytes = shm_path.read_bytes()
    finally:
        connection.close()
    wal_path.write_bytes(wal_bytes)
    shm_path.write_bytes(shm_bytes)


def create_synthetic_harness(path: Path, key: bytes) -> None:
    """Create a non-production demonstration graph with explicit synthetic IDs."""

    if not path.is_absolute():
        raise ValueError("harness path must be absolute")
    now = datetime.now(timezone.utc) - timedelta(seconds=1)
    event = InboundEvent(
        event_id="synthetic-harness:event-001",
        lead_id="synthetic-harness:lead-001",
        subscriber_id="synthetic-harness-subscriber",
        conversation_id="synthetic-harness-conversation",
        text="synthetic harness input",
        media_url=None,
        media_type=None,
        occurred_at=now,
        payload_hash="a" * 64,
    )
    batch = InboundBatch(
        batch_id="synthetic-harness:batch-001",
        lead_id=event.lead_id,
        subscriber_id=event.subscriber_id,
        events=(event,),
        combined_text=event.text,
    )
    writer = SQLiteOpsTraceWriter(path, key)
    recorder = SQLiteOpsRecorder(writer)
    recorder.start_execution(
        OpsExecution(
            execution_id=event.event_id,
            lead_id=event.lead_id,
            received_at=now,
            trace_completeness=TraceCompleteness.COMPLETE_TRACE,
        )
    )
    trace = OpsExecutionTrace(
        execution_id=event.event_id,
        recorder=recorder,
        full_content=True,
        first_ordinal=1,
        initial_parent_node_id=None,
    )
    trace.record_value(
        NodeType.MANYCHAT_WEBHOOK,
        event,
        technical_metadata={"source": "synthetic_harness"},
    )
    trace.record_value(
        NodeType.INBOX_ACCEPT,
        batch,
        technical_metadata={"status": AcceptDisposition.ACCEPTED.value},
    )
    model_request = ModelRequest(
        request_id="synthetic-harness:request-001",
        lead_id=event.lead_id,
        source_event_id=batch.batch_id,
        message=event.text,
        locale="pt-BR",
        state_version=0,
    )
    trace.record_value(NodeType.MAYA_REQUEST, model_request)
    proposal = ModelProposal(
        source_event_id=batch.batch_id,
        intent="inform",
        reply_chunks=("synthetic harness reply",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    trace.record_value(NodeType.MAYA_RESPONSE, proposal)
    read_request = ReadRequest(
        request_id="synthetic-harness:read-001",
        kind=ReadKind.LODGING,
        check_in=now.date() + timedelta(days=7),
        check_out=now.date() + timedelta(days=8),
        adults=1,
        children=0,
    )
    trace.record_value(NodeType.MAYA_READ_REQUEST, read_request)
    trace.record_value(NodeType.PROVIDER_READ_REQUEST, read_request)
    observation = ReadObservation(
        request_hash=read_request.canonical_hash(),
        provider="cloudbeds",
        observed_at=now,
        expires_at=now + timedelta(minutes=5),
        public_payload={"status": "available", "option_count": 1},
        private_binding_hash="c" * 64,
    )
    trace.record_value(NodeType.PROVIDER_READ_RESPONSE, observation)
    trace.record_value(NodeType.MAYA_OBSERVATION, observation)
    trace.record_value(NodeType.CONVERSATION_REDUCER, proposal)
    trace.record_value(NodeType.TURN_COMMIT, proposal)
    trace.finish_execution(
        lead_id=event.lead_id,
        received_at=now,
        status=ExecutionStatus.COMPLETED,
        terminal_reason="synthetic_harness_completed",
        trace_completeness=TraceCompleteness.COMPLETE_TRACE,
    )
    writer.close()
    _preserve_wal_sidecars(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("key_hex")
    args = parser.parse_args()
    key = bytes.fromhex(args.key_hex)
    if len(key) != 32:
        raise SystemExit("key must be 32 bytes")
    create_synthetic_harness(args.path.resolve(), key)
    print("synthetic-harness: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
