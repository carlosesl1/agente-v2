from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from tests.v2_ops_records_fixture import NOW, write_records_fixture
from v2_ops.records import (
    ExecutionLink,
    RecordsSourceError,
    SQLiteRecordsReader,
)


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
    result = SQLiteRecordsReader(root).snapshot(_execution_links(identity.lead_id))
    return root, identity, result


def test_snapshot_projects_only_factual_existing_records(tmp_path: Path) -> None:
    _root, identity, snapshot = _snapshot(tmp_path)

    assert snapshot.summary.lead_count == 2
    assert snapshot.summary.execution_count == 2
    assert snapshot.summary.reservation_count == 1
    assert snapshot.summary.confirmed_reservation_count == 1
    assert snapshot.summary.payment_initiation_count == 1
    assert snapshot.summary.settled_payment_count == 0
    assert snapshot.summary.handoff_count == 0

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

    assert [fact.name for fact in snapshot.facts] == [
        "country_code",
        "email",
        "full_name",
    ]
    assert snapshot.dialogue_turns[0].assistant_reply_chunks == (
        "Resposta factual da Maya.",
    )
    assert snapshot.inbound_events[0].payload_exposed is False
    assert snapshot.public_replies[0].text == "Resposta factual da Maya."

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
    assert len(snapshot.change_token) == 64
    assert set(snapshot.source_names) == {
        "inbox.sqlite3",
        "v2-bokun-audit.sqlite3",
        "v2-boundary.sqlite3",
        "v2-cloudbeds-audit.sqlite3",
        "v2-execution.sqlite3",
        "v2-followup.sqlite3",
        "v2-payment-initiation.sqlite3",
        "v2-private-customer.sqlite3",
        "v2-public-outbox.sqlite3",
    }


def test_snapshot_is_immutable_and_reader_does_not_mutate_sqlite_family(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records"
    identity = write_records_fixture(root)
    for path in root.iterdir():
        path.chmod(0o444)
    root.chmod(0o555)
    before = _physical_snapshot(root)

    snapshot = SQLiteRecordsReader(root).snapshot(_execution_links(identity.lead_id))

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
        SQLiteRecordsReader(root).snapshot(_execution_links(identity.lead_id))
    assert str(missing.value) == "records source unavailable"
    assert str(root) not in str(missing.value)

    root = tmp_path / "records-schema"
    identity = write_records_fixture(root)
    connection = sqlite3.connect(root / "inbox.sqlite3")
    connection.execute("ALTER TABLE inbound_events RENAME TO inbound_events_wrong")
    connection.commit()
    connection.close()
    with pytest.raises(RecordsSourceError) as schema:
        SQLiteRecordsReader(root).snapshot(_execution_links(identity.lead_id))
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
        SQLiteRecordsReader(root).snapshot(_execution_links(identity.lead_id))
    assert str(malformed.value) == "records source unavailable"


def test_reader_rejects_symlinked_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real-records"
    identity = write_records_fixture(real_root)
    linked_root = tmp_path / "linked-records"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(RecordsSourceError, match="^records source unavailable$"):
        SQLiteRecordsReader(linked_root).snapshot(_execution_links(identity.lead_id))


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

    snapshot = SQLiteRecordsReader(root).snapshot(_execution_links(identity.lead_id))
    settlement = next(item for item in snapshot.payments if item.phase == "settlement")
    assert settlement.lead_id == identity.lead_id
    assert settlement.status_code == "settled"
    assert settlement.status_label == "Pagamento liquidado"
    assert settlement.amount_due_minor == 33495
    assert settlement.amount_paid_minor == 33495
    assert settlement.settled_at == NOW

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

    linked = SQLiteRecordsReader(root).snapshot(_execution_links(identity.lead_id))
    assert linked.handoffs[0].lead_id == identity.lead_id


def test_unrelated_boundary_command_is_not_misclassified_as_reservation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records"
    identity = write_records_fixture(root)
    unrelated = json.dumps(
        {
            "schema_version": 1,
            "type": "handoff_command",
            "data": {"command_id": "command-handoff-001"},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
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

    snapshot = SQLiteRecordsReader(root).snapshot(_execution_links(identity.lead_id))

    assert snapshot.summary.reservation_count == 1
    assert snapshot.reservations[0].command_id == identity.command_id


def test_public_projection_excludes_technical_and_secret_fields(tmp_path: Path) -> None:
    _root, _identity, snapshot = _snapshot(tmp_path)
    payload = json.dumps(asdict(snapshot), ensure_ascii=False, sort_keys=True)

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
