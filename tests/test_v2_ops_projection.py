from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from reservation_domain import ExecutionCertainty, loads_command
from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.test_v2_outcome_projector import (
    RESULT_KEY as PAYMENT_KEY,
    _finish_next,
    _persist,
)
from tests.test_v2_turn_executor import (
    SequenceClock,
    _approval_expiry_fixture,
    _committed_public_store,
)
from v2_ops.contracts import NodeType, TraceCompleteness
from v2_ops.projection import LedgerOnlyProjector
from v2_ops.sources import (
    SQLiteBoundaryProjectionSource,
    SQLiteExecutionProjectionSource,
    SQLitePaymentProjectionSource,
    ProjectionSourceError,
)
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter


KEY = b"p" * 32


def _copy_boundary(path: Path) -> None:
    source = _committed_public_store()
    target = SQLiteBoundaryStore.open_path_v8(path)
    try:
        source._connection.backup(target._connection)
    finally:
        target.close()
        source.close()
    _materialize_wal_sidecars(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _materialize_wal_sidecars(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            raise RuntimeError("fixture is not in WAL mode")
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
        wal = Path(f"{path}-wal")
        shm = Path(f"{path}-shm")
        wal_bytes = wal.read_bytes()
        shm_bytes = shm.read_bytes()
    finally:
        connection.close()
    wal.write_bytes(wal_bytes)
    shm.write_bytes(shm_bytes)


def _source_hashes(path: Path) -> tuple[str, str, str]:
    return tuple(
        _sha(candidate)
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
    )


def test_boundary_source_is_strictly_read_only_and_emits_only_proven_primary_rows(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "boundary.sqlite3").resolve()
    _copy_boundary(path)
    before = _source_hashes(path)

    with SQLiteBoundaryProjectionSource(path) as source:
        rows = source.primary_executions(limit=10)
        assert source.query_only is True

    assert _source_hashes(path) == before
    assert len(rows) == 1
    row = rows[0]
    assert row.execution_id == "event:turn-executor-001"
    assert row.lead_id == "manychat:lead-executor-001"
    assert row.source_turn_receipt_hash == (
        "9a5539583eaf1dab1d46702f98d4920f1dabed59830405f04147b852a6b689e8"
    )
    assert len(row.public_rows) == 1
    assert row.public_rows[0].source_turn_receipt_hash == row.source_turn_receipt_hash


def test_boundary_source_rejects_descendant_with_divergent_receipt(tmp_path: Path) -> None:
    path = (tmp_path / "boundary.sqlite3").resolve()
    _copy_boundary(path)
    connection = SQLiteBoundaryStore.open_path_v8(path)
    try:
        connection._connection.execute("PRAGMA ignore_check_constraints=ON")
        connection._connection.execute(
            "UPDATE boundary_public_outbox SET source_turn_receipt_hash=?",
            ("f" * 64,),
        )
    finally:
        connection.close()

    with pytest.raises(ProjectionSourceError, match="projection source unavailable"):
        SQLiteBoundaryProjectionSource(path)


def test_boundary_source_never_projects_a_root_with_divergent_turn_receipt(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "boundary.sqlite3").resolve()
    _copy_boundary(path)
    connection = SQLiteBoundaryStore.open_path_v8(path)
    try:
        connection._connection.execute("PRAGMA ignore_check_constraints=ON")
        connection._connection.execute(
            "UPDATE boundary_event_sources SET source_turn_receipt_hash=?",
            ("e" * 64,),
        )
    finally:
        connection.close()

    with pytest.raises(ProjectionSourceError, match="projection source unavailable"):
        SQLiteBoundaryProjectionSource(path)


def test_projection_source_uri_opens_the_exact_percent_encoded_path(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "boundary#exact?source.sqlite3").resolve()
    _copy_boundary(path)

    with SQLiteBoundaryProjectionSource(path) as source:
        rows = source.primary_executions(limit=10)

    assert len(rows) == 1
    assert not (tmp_path / "boundary").exists()


def test_ledger_only_projector_writes_idempotent_sanitized_milestones(tmp_path: Path) -> None:
    boundary_path = (tmp_path / "boundary.sqlite3").resolve()
    trace_path = (tmp_path / "trace.sqlite3").resolve()
    _copy_boundary(boundary_path)
    boundary_before = _sha(boundary_path)

    with SQLiteBoundaryProjectionSource(boundary_path) as source, SQLiteOpsTraceWriter(
        trace_path, KEY
    ) as writer:
        projector = LedgerOnlyProjector(source=source, writer=writer)
        first = projector.run(limit=10)
        replay = projector.run(limit=10)

    assert first.projected == 1
    assert first.skipped_existing == 0
    assert replay.projected == 0
    assert replay.skipped_existing == 1
    assert _sha(boundary_path) == boundary_before
    with SQLiteOpsTraceReader(trace_path, KEY) as reader:
        execution = reader.get_execution("event:turn-executor-001")
        nodes = reader.list_nodes("event:turn-executor-001")

    assert execution.trace_completeness is TraceCompleteness.LEDGER_ONLY
    assert execution.lead_id == "manychat:lead-executor-001"
    assert [node.node_type for node in nodes] == [
        NodeType.LEDGER_TURN,
        NodeType.LEDGER_PUBLIC_OUTBOX,
    ]
    assert all(node.status == "completed" for node in nodes)
    assert all(node.has_full_input is False for node in nodes)
    assert all(node.has_full_output is False for node in nodes)
    rendered = repr(nodes)
    assert "Pessoa Teste" not in rendered
    assert "person@example.invalid" not in rendered
    assert str(boundary_path) not in rendered


def test_projector_never_overlays_an_existing_live_trace(tmp_path: Path) -> None:
    boundary_path = (tmp_path / "boundary.sqlite3").resolve()
    trace_path = (tmp_path / "trace.sqlite3").resolve()
    _copy_boundary(boundary_path)

    from tests.test_v2_ops_reservation_effects import _terminal_trace
    from tests.test_v2_turn_executor import NOW

    with SQLiteOpsTraceWriter(trace_path, KEY) as writer:
        _terminal_trace(writer, "event:turn-executor-001", terminal_at=NOW)
        with SQLiteBoundaryProjectionSource(boundary_path) as source:
            result = LedgerOnlyProjector(source=source, writer=writer).run(limit=10)

    assert result.projected == 0
    assert result.skipped_existing == 1
    with SQLiteOpsTraceReader(trace_path, KEY) as reader:
        execution = reader.get_execution("event:turn-executor-001")
    assert execution.trace_completeness is TraceCompleteness.PARTIAL_TRACE


def test_execution_and_payment_enrich_only_by_authenticated_deterministic_keys(
    tmp_path: Path,
) -> None:
    boundary_path = (tmp_path / "boundary.sqlite3").resolve()
    execution_path = (tmp_path / "execution.sqlite3").resolve()
    payment_path = (tmp_path / "payments.sqlite3").resolve()
    trace_path = (tmp_path / "trace.sqlite3").resolve()
    boundary, _model, _read_port, confirmation_batch, executor = (
        _approval_expiry_fixture(
            approval_ttl=timedelta(minutes=5),
            confirmation_clock=SequenceClock(),
        )
    )
    try:
        confirmed = executor.execute(confirmation_batch)
        assert len(confirmed.receipt.command_rows) == 1
        command_json = boundary._connection.execute(
            "SELECT command_json FROM boundary_commands"
        ).fetchone()[0]
        command = loads_command(command_json)
        target = SQLiteBoundaryStore.open_path_v8(boundary_path)
        try:
            boundary._connection.backup(target._connection)
        finally:
            target.close()
    finally:
        boundary.close()
    _materialize_wal_sidecars(boundary_path)
    execution = SQLiteUnitOfWork.open_v6(execution_path)
    from v2_application.outcome_projector import ReservationOutcomeProjector
    from v2_application.payments import SQLitePaymentInitiationStore
    from v2_contracts.payments import BusinessUnit, PaymentMethod
    payments = SQLitePaymentInitiationStore(payment_path, result_encryption_key=PAYMENT_KEY)
    try:
        _persist(execution, (command,))
        _finish_next(
            execution,
            now=command.created_at + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        ReservationOutcomeProjector(
            execution=execution,
            payment_store=payments,
            receiver_profiles={BusinessUnit.HOSTEL: "stripe-account:hostel:test", BusinessUnit.AGENCY: "stripe-account:agency:test"},
            enabled_methods=(PaymentMethod.STRIPE,),
        ).run_once(now=command.created_at + timedelta(seconds=2))
    finally:
        payments.close()
        execution.close()

    _materialize_wal_sidecars(execution_path)
    _materialize_wal_sidecars(payment_path)
    before = tuple(
        _source_hashes(path) for path in (boundary_path, execution_path, payment_path)
    )
    with (
        SQLiteBoundaryProjectionSource(boundary_path) as boundary_source,
        SQLiteExecutionProjectionSource(execution_path) as execution_source,
        SQLitePaymentProjectionSource(payment_path) as payment_source,
        SQLiteOpsTraceWriter(trace_path, KEY) as writer,
    ):
        result = LedgerOnlyProjector(
            source=boundary_source,
            execution_source=execution_source,
            payment_source=payment_source,
            writer=writer,
        ).run(limit=10)
    after = tuple(
        _source_hashes(path) for path in (boundary_path, execution_path, payment_path)
    )

    assert result.projected == 2
    assert before == after
    with SQLiteOpsTraceReader(trace_path, KEY) as reader:
        nodes = reader.list_nodes(confirmation_batch.events[0].event_id)
    assert NodeType.LEDGER_RESERVATION in [item.node_type for item in nodes]
    assert NodeType.LEDGER_PAYMENT in [item.node_type for item in nodes]
    rendered = repr(nodes)
    assert command.payload.customer.full_name not in rendered
    assert command.payload.customer.email not in rendered
    assert command.payload.customer.phone_e164 not in rendered


def test_projection_sources_reject_schema_drift_hash_drift_and_ambiguous_payment(
    tmp_path: Path,
) -> None:
    boundary_path = (tmp_path / "boundary.sqlite3").resolve()
    _copy_boundary(boundary_path)
    connection = sqlite3.connect(boundary_path)
    try:
        connection.execute("CREATE INDEX unexpected_ops_index ON boundary_state(version)")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ProjectionSourceError, match="projection source unavailable"):
        SQLiteBoundaryProjectionSource(boundary_path)

    execution_path = (tmp_path / "execution.sqlite3").resolve()
    execution = SQLiteUnitOfWork.open_v6(execution_path)
    execution.close()
    connection = sqlite3.connect(execution_path)
    try:
        connection.execute(
            "CREATE INDEX unexpected_projection_index ON execution_ledger(status)"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ProjectionSourceError, match="projection source unavailable"):
        SQLiteExecutionProjectionSource(execution_path)

    payment_path = (tmp_path / "payments.sqlite3").resolve()
    from v2_application.payments import SQLitePaymentInitiationStore
    from v2_contracts.payments import BusinessUnit, PaymentMethod, PaymentObligation, PaymentSelection, DueKind

    payment_id = "payment:test-projection-negative"
    store = SQLitePaymentInitiationStore(payment_path, result_encryption_key=PAYMENT_KEY)
    try:
        selection = PaymentSelection(
            PaymentObligation(
                payment_id=payment_id,
                reservation_anchor_id="reservation-anchor:test-projection-negative",
                business_unit=BusinessUnit.HOSTEL,
                amount_minor=100,
                currency="BRL",
                due_kind=DueKind.PREPAYMENT,
                economic_version=1,
                receiver_profile_id="stripe-account:hostel:test",
            ),
            PaymentMethod.STRIPE,
        )
        assert store.enqueue(
            selection,
            now=datetime(2026, 8, 13, 18, 0, tzinfo=timezone.utc),
        )
    finally:
        store.close()
    connection = sqlite3.connect(payment_path)
    try:
        connection.execute(
            "INSERT INTO payment_initiations "
            "(initiation_id,selection_json,selection_hash,status,dispatch_slots,updated_at) "
            "SELECT 'payment-init:duplicate',selection_json,selection_hash,status,"
            "dispatch_slots,updated_at FROM payment_initiations"
        )
        connection.commit()
    finally:
        connection.close()
    _materialize_wal_sidecars(payment_path)
    with SQLitePaymentProjectionSource(payment_path) as source:
        with pytest.raises(ProjectionSourceError, match="projection source unavailable"):
            source.for_payment(payment_id)
    connection = sqlite3.connect(payment_path)
    try:
        connection.execute("DELETE FROM payment_initiations WHERE initiation_id='payment-init:duplicate'")
        connection.execute("UPDATE payment_initiations SET selection_hash=?", ("f" * 64,))
        connection.commit()
    finally:
        connection.close()
    _materialize_wal_sidecars(payment_path)
    with SQLitePaymentProjectionSource(payment_path) as source:
        with pytest.raises(ProjectionSourceError, match="projection source unavailable"):
            source.for_payment(payment_id)


def test_projection_cli_uses_environment_key_and_writes_readable_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    boundary_path = (tmp_path / "boundary.sqlite3").resolve()
    output_path = (tmp_path / "ops.sqlite3").resolve()
    _copy_boundary(boundary_path)
    key_hex = KEY.hex()
    monkeypatch.setenv("V2_OPS_TRACE_KEY_HEX", key_hex)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "v2-ops-project",
            "--boundary",
            str(boundary_path),
            "--output",
            str(output_path),
            "--limit",
            "10",
        ],
    )

    from v2_ops.project_cli import main

    assert main() == 0
    output = capsys.readouterr().out
    assert "projected=1" in output
    assert key_hex not in output
    assert Path(f"{output_path}-wal").is_file()
    assert Path(f"{output_path}-shm").is_file()
    with SQLiteOpsTraceReader(output_path, KEY) as reader:
        execution = reader.get_execution("event:turn-executor-001")
    assert execution.trace_completeness is TraceCompleteness.LEDGER_ONLY


def test_projection_cli_rejects_output_that_aliases_a_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary_path = (tmp_path / "boundary.sqlite3").resolve()
    _copy_boundary(boundary_path)
    before = _source_hashes(boundary_path)
    monkeypatch.setenv("V2_OPS_TRACE_KEY_HEX", KEY.hex())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "v2-ops-project",
            "--boundary",
            str(boundary_path),
            "--output",
            str(boundary_path),
        ],
    )

    from v2_ops.project_cli import main

    with pytest.raises(SystemExit, match="output must be physically distinct"):
        main()
    assert _source_hashes(boundary_path) == before
