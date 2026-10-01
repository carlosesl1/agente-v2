from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, fields
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from tests.v2_ops_records_fixture import NOW, write_records_fixture
from v2_ops.records import (
    ExecutionLink,
    LeadDetail,
    LeadSummary,
    RecordsSnapshot,
    RecordsSourceError,
    SQLiteRecordsReader,
)


GENERATED_AT = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _execution_links(lead_id: str) -> tuple[ExecutionLink, ...]:
    return (
        ExecutionLink(
            execution_id="execution-records-001",
            lead_id=lead_id,
            received_at=datetime(2026, 8, 30, 11, 56, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
            status="completed",
        ),
        ExecutionLink(
            execution_id="execution-trace-only-001",
            lead_id="manychat:trace-only-001",
            received_at=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
            completed_at=None,
            status="running",
        ),
    )


def _physical_snapshot(root: Path) -> dict[str, tuple[bool, int, int, str | None]]:
    result: dict[str, tuple[bool, int, int, str | None]] = {}
    for database in sorted(root.glob("*.sqlite3")):
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{database}{suffix}")
            if not path.exists():
                result[path.name] = (False, 0, 0, None)
                continue
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result[path.name] = (True, stat.st_size, stat.st_mtime_ns, digest)
    return result


def _snapshot(tmp_path: Path):
    root = tmp_path / "records"
    identity = write_records_fixture(root)
    result = SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )
    return root, identity, result


def test_snapshot_projects_only_factual_existing_records(tmp_path: Path) -> None:
    root, identity, snapshot = _snapshot(tmp_path)
    reader = SQLiteRecordsReader(root)
    detail = reader.lead_detail(
        identity.lead_id,
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )
    assert detail is not None

    assert snapshot.generated_at == GENERATED_AT
    assert snapshot.truncated is False
    assert len(snapshot.leads) == 2
    assert sum(item.execution_count for item in snapshot.leads) == 2
    assert len(snapshot.reservations) == 1
    assert len(snapshot.payments) == 1
    assert snapshot.handoffs == ()

    lead = next(item for item in snapshot.leads if item.lead_id == identity.lead_id)
    assert lead.fact_count == 3
    assert lead.dialogue_turn_count == 1
    assert lead.inbound_count == 1
    assert lead.public_reply_count == 1
    assert lead.execution_count == 1
    assert lead.reservation_count == 1
    assert lead.payment_count == 1
    assert lead.handoff_count == 0
    assert lead.state_code == "reservation_confirmed"
    assert lead.state_label == "Reserva confirmada"

    trace_only = next(
        item for item in snapshot.leads if item.lead_id == "manychat:trace-only-001"
    )
    assert trace_only.fact_count == 0
    assert trace_only.dialogue_turn_count == 0
    assert trace_only.reservation_count == 0
    assert trace_only.state_code == "execution_only"
    assert trace_only.state_label == "Somente execução"

    assert [fact.name for fact in detail.facts] == [
        "country_code",
        "email",
        "full_name",
    ]
    assert detail.dialogue_turns[0].assistant_reply_chunks == (
        "Resposta factual da Maya.",
    )
    assert detail.inbound_events[0].payload_exposed is False
    assert detail.public_replies[0].text == "Resposta factual da Maya."

    reservation = snapshot.reservations[0]
    assert reservation.lead_id == identity.lead_id
    assert reservation.command_id == identity.command_id
    assert reservation.workflow_id == identity.workflow_id
    assert reservation.draft_id == identity.draft_id
    assert reservation.status_code == "confirmed"
    assert reservation.status_label == "Confirmada"
    assert reservation.certainty == "effect_confirmed"
    assert reservation.provider_reference == "provider-reference-generic-001"
    assert reservation.bokun_booking_id is None
    assert reservation.cloudbeds_reservation_id is None
    assert reservation.total_minor == 33495
    assert reservation.currency == "BRL"
    assert reservation.payment_method == "stripe"
    assert reservation.components[0].service == "activity"
    assert reservation.components[0].adults == 1
    assert reservation.components[0].children == 0

    payment = snapshot.payments[0]
    assert payment.lead_id == identity.lead_id
    assert payment.phase == "initiation"
    assert payment.payment_id == identity.payment_id
    assert payment.initiation_id == identity.initiation_id
    assert payment.reservation_anchor_id == identity.draft_id
    assert payment.method == "stripe"
    assert payment.amount_due_minor == 33495
    assert payment.currency == "BRL"
    assert payment.payment_link_prepared is True
    assert payment.status_code == "link_ready"
    assert payment.status_label == "Link de pagamento preparado"
    assert payment.amount_paid_minor is None
    assert payment.settled_at is None

    assert snapshot.handoffs == ()
    assert len(reader.change_token()) == 64
    assert reader.allowed_files == frozenset({
        "inbox.sqlite3",
        "v2-bokun-audit.sqlite3",
        "v2-boundary.sqlite3",
        "v2-cloudbeds-audit.sqlite3",
        "v2-execution.sqlite3",
        "v2-followup.sqlite3",
        "v2-payment-initiation.sqlite3",
        "v2-private-customer.sqlite3",
        "v2-public-outbox.sqlite3",
    })


def test_snapshot_and_lead_detail_have_closed_bounded_contracts(tmp_path: Path) -> None:
    root = tmp_path / "records"
    identity = write_records_fixture(root)
    reader = SQLiteRecordsReader(root)
    links = _execution_links(identity.lead_id)

    snapshot = reader.snapshot(
        executions=links,
        generated_at=GENERATED_AT,
        limit=1,
    )
    detail = reader.lead_detail(
        identity.lead_id,
        executions=links,
        generated_at=GENERATED_AT,
        limit=1,
    )

    assert tuple(field.name for field in fields(RecordsSnapshot)) == (
        "generated_at",
        "leads",
        "reservations",
        "payments",
        "handoffs",
        "truncated",
    )
    assert tuple(field.name for field in fields(LeadSummary)) == (
        "lead_id",
        "first_activity_at",
        "last_activity_at",
        "state_code",
        "state_label",
        "fact_count",
        "dialogue_turn_count",
        "passenger_manifest_count",
        "inbound_count",
        "public_reply_count",
        "execution_count",
        "reservation_count",
        "payment_count",
        "payment_initiation_count",
        "settled_payment_count",
        "handoff_count",
        "latest_execution_status",
    )
    assert tuple(field.name for field in fields(LeadDetail)) == (
        "generated_at",
        "summary",
        "facts",
        "dialogue_turns",
        "passenger_manifests",
        "inbound_events",
        "public_replies",
        "reservations",
        "payments",
        "handoffs",
        "executions",
        "truncated",
    )
    assert snapshot.generated_at == GENERATED_AT
    assert len(snapshot.leads) == 1
    assert snapshot.truncated is True
    assert detail is not None
    assert len(detail.facts) == 1
    assert detail.truncated is True
    assert "manifest_json" not in json.dumps(asdict(detail), default=str)
    with pytest.raises(RecordsSourceError, match="^records source unavailable$"):
        reader.snapshot(
            executions=links,
            generated_at=GENERATED_AT,
            limit=201,
        )


def test_snapshot_is_immutable_and_reader_does_not_mutate_sqlite_family(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records"
    identity = write_records_fixture(root)
    for path in root.iterdir():
        path.chmod(0o444)
    root.chmod(0o555)
    before = _physical_snapshot(root)

    snapshot = SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )

    after = _physical_snapshot(root)
    assert after == before
    with pytest.raises(FrozenInstanceError):
        snapshot.leads[0].fact_count = 99  # type: ignore[misc]


def test_reader_fails_closed_for_missing_file_schema_or_malformed_json(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records-missing"
    identity = write_records_fixture(root)
    (root / "v2-bokun-audit.sqlite3").unlink()
    with pytest.raises(RecordsSourceError) as missing:
        SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )
    assert str(missing.value) == "records source unavailable"
    assert str(root) not in str(missing.value)

    root = tmp_path / "records-schema"
    identity = write_records_fixture(root)
    connection = sqlite3.connect(root / "inbox.sqlite3")
    connection.execute("ALTER TABLE inbound_events RENAME TO inbound_events_wrong")
    connection.commit()
    connection.close()
    with pytest.raises(RecordsSourceError) as schema:
        SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )
    assert str(schema.value) == "records source unavailable"

    root = tmp_path / "records-json"
    identity = write_records_fixture(root)
    connection = sqlite3.connect(root / "v2-execution.sqlite3")
    connection.execute(
        "UPDATE reservation_commands SET command_json='{' WHERE command_id=?",
        (identity.command_id,),
    )
    connection.commit()
    connection.close()
    with pytest.raises(RecordsSourceError) as malformed:
        SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )
    assert str(malformed.value) == "records source unavailable"


def test_reader_rejects_symlinked_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real-records"
    identity = write_records_fixture(real_root)
    linked_root = tmp_path / "linked-records"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(RecordsSourceError, match="^records source unavailable$"):
        SQLiteRecordsReader(linked_root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )


def test_reader_authenticates_sidecars_real_tables_and_file_change_token(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records-sidecar"
    identity = write_records_fixture(root)
    target = tmp_path / "not-a-wal"
    target.write_bytes(b"not a wal")
    (root / "inbox.sqlite3-wal").symlink_to(target)
    with pytest.raises(RecordsSourceError, match="^records source unavailable$"):
        SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )

    root = tmp_path / "records-view"
    identity = write_records_fixture(root)
    connection = sqlite3.connect(root / "inbox.sqlite3")
    connection.execute("ALTER TABLE inbound_events RENAME TO inbound_events_base")
    connection.execute(
        "CREATE VIEW inbound_events AS SELECT event_id, lead_id, occurred_at, status, "
        "completed_at FROM inbound_events_base"
    )
    connection.commit()
    connection.close()
    with pytest.raises(RecordsSourceError, match="^records source unavailable$"):
        SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )

    root = tmp_path / "records-token"
    identity = write_records_fixture(root)
    reader = SQLiteRecordsReader(root)
    first = reader.change_token()
    (root / "ignored-file.txt").write_text("not a source", encoding="utf-8")
    assert reader.change_token() == first
    connection = sqlite3.connect(root / "inbox.sqlite3")
    connection.execute(
        "UPDATE inbound_events SET status='pending' WHERE lead_id=?",
        (identity.lead_id,),
    )
    connection.commit()
    connection.close()
    assert reader.change_token() != first


def test_reader_fd_copy_rejects_transient_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "records"
    identity = write_records_fixture(root)
    outside = tmp_path / "outside"
    write_records_fixture(outside)
    outside_connection = sqlite3.connect(outside / "inbox.sqlite3")
    outside_connection.execute(
        "UPDATE inbound_events SET lead_id='outside-lead'"
    )
    outside_connection.commit()
    outside_connection.close()
    target = root / "inbox.sqlite3"
    backup = root / "inbox.sqlite3.original"
    real_open = os.open
    swapped = False

    def swap_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        candidate = Path(path)
        if candidate == target and not swapped:
            swapped = True
            target.rename(backup)
            target.symlink_to(outside / "inbox.sqlite3")
            try:
                return real_open(path, flags, mode, dir_fd=dir_fd)
            finally:
                target.unlink()
                backup.rename(target)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_before_open)
    with pytest.raises(RecordsSourceError, match="^records source unavailable$"):
        SQLiteRecordsReader(root).snapshot(
            executions=_execution_links(identity.lead_id),
            generated_at=GENERATED_AT,
        )
    assert swapped is True


def test_reader_preserves_existing_live_wal_and_shm_sidecars(tmp_path: Path) -> None:
    root = tmp_path / "records-wal"
    identity = write_records_fixture(root)
    writer = sqlite3.connect(root / "inbox.sqlite3")
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute(
        "UPDATE inbound_events SET status='processing' WHERE lead_id=?",
        (identity.lead_id,),
    )
    writer.commit()
    assert (root / "inbox.sqlite3-wal").is_file()
    assert (root / "inbox.sqlite3-shm").is_file()
    for path in root.iterdir():
        path.chmod(0o444)
    root.chmod(0o555)
    before = _physical_snapshot(root)

    try:
        SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )
        assert _physical_snapshot(root) == before
    finally:
        root.chmod(0o755)
        for path in root.iterdir():
            path.chmod(0o644)
        writer.close()


def test_settlement_and_handoff_rows_appear_without_inferred_lead_linkage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records"
    identity = write_records_fixture(root)

    followup = sqlite3.connect(root / "v2-followup.sqlite3")
    state_json = json.dumps(
        {
            "schema_version": 1,
            "type": "payment_workflow",
            "data": {
                "subject": {
                    "payment_id": identity.payment_id,
                    "payment_version": 1,
                    "confirmed_reservation_anchor": {
                        "reservation_workflow_id": identity.workflow_id,
                        "reservation_command_id": identity.command_id,
                        "reservation_subject_signature": "subject-signature-001",
                        "reservation_outcome_hash": "8" * 64,
                        "reservation_outcome": {
                            "command_id": identity.command_id,
                            "certainty": "effect_confirmed",
                            "normalized_status": "confirmed",
                            "provider_reference": "provider-reference-generic-001",
                            "evidence": ["provider_submit_accepted"],
                        },
                        "provider_reference": "provider-reference-generic-001",
                        "service": "activity",
                        "business_unit": "chapada",
                        "payment_target_id": "target-fixture-001",
                        "amount_minor": 33495,
                        "currency": "BRL",
                        "receiver_profile_id": "stripe-fixture",
                        "confirmed_at": NOW,
                        "payment_deadline": None,
                    },
                    "amount_minor": 33495,
                    "currency": "BRL",
                    "receiver_profile_id": "stripe-fixture",
                    "business_unit": "chapada",
                    "payment_target_id": "target-fixture-001",
                    "method": "stripe",
                    "economic_signature": "9" * 64,
                },
                "policy": {
                    "customer_confirmation": "required",
                    "financial_summary": "required",
                    "settlement": "required",
                },
                "status": "settled",
                "summary": None,
                "confirmation": None,
                "evidence_record": None,
                "verified_evidence": None,
                "settlement_command": None,
                "settlement_start": None,
                "settlement_finish": None,
                "expiration": None,
                "cancellation": None,
                "history": [],
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    followup.execute(
        "INSERT INTO payment_workflows VALUES (?,?,?,?,?,?,?,?,?)",
        (
            identity.payment_id,
            1,
            1,
            "9" * 64,
            "settled",
            state_json,
            hashlib.sha256(state_json.encode()).hexdigest(),
            NOW,
            NOW,
        ),
    )
    outcome_json = json.dumps(
        {
            "schema_version": 1,
            "type": "settlement_outcome",
            "data": {
                "certainty": "settled",
                "payment_registered": True,
                "reservation_target_confirmed": True,
                "provider_reference_fingerprint": "a" * 64,
                "requires_reconciliation": False,
                "claim_evidence": ["b" * 64],
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    followup.execute(
        "INSERT INTO payment_ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "settlement-records-001",
            identity.payment_id,
            1,
            "9" * 64,
            "outcome_recorded",
            None,
            1,
            None,
            None,
            1,
            1,
            "c" * 64,
            NOW,
            "settled",
            outcome_json,
            hashlib.sha256(outcome_json.encode()).hexdigest(),
            NOW,
            NOW,
        ),
    )
    handoff_json = json.dumps(
        {
            "schema_version": 1,
            "type": "handoff_workflow",
            "data": {
                "request": {
                    "handoff_id": "handoff-records-001",
                    "lead_key_hash": "d" * 64,
                    "incident_key": "incident-records-001",
                    "reason_code": "customer_requested",
                    "source_event_id": "event-records-001",
                    "reservation_anchor": None,
                    "requested_at": NOW,
                },
                "policy": {
                    "customer_acknowledgement": "required",
                    "internal_email": "disabled",
                },
                "status": "open",
                "queue_active": True,
                "acknowledgement": None,
                "effect_failures": [],
                "cancellation": None,
                "conflicting_request": None,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    followup.execute(
        "INSERT INTO handoff_workflows VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "handoff-records-001",
            "incident-records-001",
            1,
            "open",
            "d" * 64,
            handoff_json,
            hashlib.sha256(handoff_json.encode()).hexdigest(),
            NOW,
            NOW,
        ),
    )
    followup.commit()
    followup.close()

    snapshot = SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )
    settlement = next(item for item in snapshot.payments if item.phase == "settlement")
    assert settlement.lead_id == identity.lead_id
    assert settlement.status_code == "settled"
    assert settlement.status_label == "Pagamento liquidado"
    assert settlement.settled is True
    assert settlement.workflow_status == "settled"
    assert settlement.ledger_status == "outcome_recorded"
    assert settlement.outcome_certainty == "settled"
    assert settlement.amount_due_minor == 33495
    assert settlement.amount_paid_minor is None
    assert settlement.settled_at == NOW

    followup = sqlite3.connect(root / "v2-followup.sqlite3")
    followup.execute("DELETE FROM payment_ledger")
    followup.commit()
    followup.close()
    without_outcome = next(
        item
        for item in SQLiteRecordsReader(root).snapshot(
            executions=_execution_links(identity.lead_id),
            generated_at=GENERATED_AT,
        ).payments
        if item.phase == "settlement"
    )
    assert without_outcome.settled is False
    assert without_outcome.status_code == "preparing"
    assert without_outcome.amount_paid_minor is None
    assert without_outcome.workflow_status == "settled"
    assert without_outcome.ledger_status is None
    assert without_outcome.outcome_certainty is None

    handoff = snapshot.handoffs[0]
    assert handoff.handoff_id == "handoff-records-001"
    assert handoff.lead_id is None
    assert handoff.status_code == "open"
    assert handoff.status_label == "Aberto"

    # Vincular por uma identidade exata persistida no boundary, nunca por hash/horário.
    boundary = sqlite3.connect(root / "v2-boundary.sqlite3")
    row = boundary.execute(
        "SELECT state_json FROM boundary_state WHERE lead_key=?",
        (identity.lead_id,),
    ).fetchone()
    state = json.loads(row[0])
    state["data"]["handoff"] = {"handoff_id": "handoff-records-001"}
    raw = json.dumps(state, sort_keys=True, separators=(",", ":"))
    boundary.execute(
        "UPDATE boundary_state SET state_json=?, state_hash=? WHERE lead_key=?",
        (raw, hashlib.sha256(raw.encode()).hexdigest(), identity.lead_id),
    )
    boundary.commit()
    boundary.close()

    linked = SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )
    assert linked.handoffs[0].lead_id == identity.lead_id


def test_unrelated_boundary_command_is_not_misclassified_as_reservation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records"
    identity = write_records_fixture(root)
    unrelated = "opaque-future-envelope-v2"
    connection = sqlite3.connect(root / "v2-boundary.sqlite3")
    connection.execute(
        "INSERT INTO boundary_commands VALUES (?,?,?,?,?,?,?,?)",
        (
            "command-handoff-001",
            identity.lead_id,
            "turn-records-002",
            "handoff",
            unrelated,
            hashlib.sha256(unrelated.encode()).hexdigest(),
            "e" * 64,
            NOW,
        ),
    )
    connection.commit()
    connection.close()

    snapshot = SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )

    assert len(snapshot.reservations) == 1
    assert snapshot.reservations[0].command_id == identity.command_id

    connection = sqlite3.connect(root / "v2-boundary.sqlite3")
    connection.execute(
        "UPDATE boundary_commands SET command_type='reservation' "
        "WHERE command_id='command-handoff-001'"
    )
    connection.commit()
    connection.close()
    with pytest.raises(RecordsSourceError, match="records source unavailable"):
        SQLiteRecordsReader(root).snapshot(
        executions=_execution_links(identity.lead_id),
        generated_at=GENERATED_AT,
    )


def test_public_projection_excludes_technical_and_secret_fields(tmp_path: Path) -> None:
    _root, _identity, snapshot = _snapshot(tmp_path)
    payload = json.dumps(asdict(snapshot), ensure_ascii=False, sort_keys=True, default=str)

    for forbidden in (
        "claim_token",
        "claim_owner",
        "lease_expires_at",
        "fencing_token",
        "payload_hash",
        "command_hash",
        "state_hash",
        "selection_hash",
        "result_json",
        "receipt_json",
        "ciphertext",
        "nonce",
        "excluded-webhook-payload",
        "excluded-claim-token",
        "encrypted-result-excluded",
    ):
        assert forbidden not in payload


def test_blank_final_provider_ids_are_ignored(tmp_path: Path) -> None:
    root = tmp_path / "records"
    identity = write_records_fixture(root)
    connection = sqlite3.connect(root / "v2-bokun-audit.sqlite3")
    connection.execute(
        "INSERT INTO bokun_audit_tasks "
        "(task_id, command_id, booking_id, product_id, provider_start_time_id, "
        "provider_rate_id, activity_date, start_time, adults, children, total, "
        "currency, expected_status, max_attempts, status, attempts, fencing_token) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task-bokun-blank",
            identity.command_id,
            "",
            "product-1",
            "start-1",
            "rate-1",
            "2026-09-15",
            "08:00",
            1,
            0,
            "334.95",
            "BRL",
            "confirmed",
            3,
            "pending",
            0,
            1,
        ),
    )
    connection.commit()
    connection.close()
    connection = sqlite3.connect(root / "v2-cloudbeds-audit.sqlite3")
    connection.execute(
        "INSERT INTO cloudbeds_audit_tasks "
        "(task_id, command_id, reservation_id, property_id, start_date, end_date, "
        "adults, children, amount, currency, expected_status, max_attempts, status, attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task-cloudbeds-blank",
            identity.command_id,
            "",
            "property-1",
            "2026-09-15",
            "2026-09-16",
            1,
            0,
            "334.95",
            "BRL",
            "confirmed",
            3,
            "pending",
            0,
        ),
    )
    connection.commit()
    connection.close()

    reservation = SQLiteRecordsReader(root).snapshot(generated_at=GENERATED_AT).reservations[0]

    assert reservation.bokun_booking_id is None
    assert reservation.cloudbeds_reservation_id is None


def test_reservation_status_uses_only_closed_factual_labels(tmp_path: Path) -> None:
    root = tmp_path / "records"
    write_records_fixture(root)
    connection = sqlite3.connect(root / "v2-execution.sqlite3")
    outcome = json.loads(
        connection.execute("SELECT outcome_json FROM execution_ledger").fetchone()[0]
    )
    outcome["data"]["certainty"] = "effect_not_confirmed"
    outcome["data"]["normalized_status"] = "failed"
    workflow = json.loads(
        connection.execute("SELECT state_json FROM workflows").fetchone()[0]
    )
    workflow["data"]["outcome"] = outcome["data"]
    connection.execute(
        "UPDATE workflows SET state_json=?",
        (json.dumps(workflow, sort_keys=True, separators=(",", ":")),),
    )
    connection.execute(
        "UPDATE execution_ledger SET status='outcome_recorded', outcome_json=?",
        (json.dumps(outcome, sort_keys=True, separators=(",", ":")),),
    )
    connection.commit()
    connection.close()

    terminal = SQLiteRecordsReader(root).snapshot(generated_at=GENERATED_AT).reservations[0]
    assert (terminal.status_code, terminal.status_label) == (
        "outcome_recorded",
        "Outcome registrado",
    )

    connection = sqlite3.connect(root / "v2-execution.sqlite3")
    workflow = json.loads(
        connection.execute("SELECT state_json FROM workflows").fetchone()[0]
    )
    workflow["data"]["outcome"] = None
    connection.execute(
        "UPDATE workflows SET state_json=?",
        (json.dumps(workflow, sort_keys=True, separators=(",", ":")),),
    )
    connection.execute("UPDATE execution_ledger SET status='queued', outcome_json=NULL")
    connection.commit()
    connection.close()

    preparing = SQLiteRecordsReader(root).snapshot(generated_at=GENERATED_AT).reservations[0]
    assert (preparing.status_code, preparing.status_label) == (
        "preparing",
        "Em preparação",
    )
    assert "Em processamento" not in {
        terminal.status_label,
        preparing.status_label,
    }
