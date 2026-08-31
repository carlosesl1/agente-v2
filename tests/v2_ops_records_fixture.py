from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable


NOW = "2026-08-30T12:00:00+00:00"
EARLIER = "2026-08-30T11:55:00+00:00"


@dataclass(frozen=True, slots=True)
class RecordsFixtureIdentity:
    lead_id: str
    execution_id: str
    command_id: str
    workflow_id: str
    draft_id: str
    payment_id: str
    initiation_id: str


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _database(path: Path, statements: Iterable[str]) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in statements:
        connection.execute(statement)
    return connection


def _reservation_command(identity: RecordsFixtureIdentity) -> dict[str, object]:
    return {
        "schema_version": 1,
        "type": "reservation_command",
        "data": {
            "command_id": identity.command_id,
            "created_at": EARLIER,
            "draft_id": identity.draft_id,
            "draft_version": 1,
            "idempotency_key": "idem-reservation-001",
            "operation": "book_activity",
            "payload": {
                "components": [
                    {
                        "available": True,
                        "end_date": None,
                        "lookup_id": "lookup-activity-001",
                        "offer_id": "offer-activity-001",
                        "party": {"adults": 1, "children": 0},
                        "provider_ref": "provider-offer-001",
                        "public_label": "Passeio de teste",
                        "service": "activity",
                        "start_date": "2026-09-15",
                        "start_time": "08:00",
                        "total": {"amount": "334.95", "currency": "BRL"},
                    }
                ],
                "customer": {
                    "birth_date": "1990-01-01",
                    "country_code": "BR",
                    "customer_ref": "customer-fixture-001",
                    "email": "fixture@example.invalid",
                    "full_name": "Cliente Fixture",
                    "gender": "unspecified",
                    "phone_e164": "+5500000000000",
                },
                "terms": {"add_ons": [], "payment_method": "stripe"},
            },
            "subject_signature": "subject-signature-001",
            "workflow_id": identity.workflow_id,
        },
    }


def write_records_fixture(root: Path) -> RecordsFixtureIdentity:
    root.mkdir(parents=True, exist_ok=True)
    identity = RecordsFixtureIdentity(
        lead_id="manychat:lead-records-001",
        execution_id="event-records-001",
        command_id="command-records-001",
        workflow_id="workflow-records-001",
        draft_id="draft-records-001",
        payment_id="payment-records-001",
        initiation_id="initiation-records-001",
    )

    inbound = _database(
        root / "inbox.sqlite3",
        (
            """CREATE TABLE inbound_events (
                event_id TEXT PRIMARY KEY, lead_id TEXT NOT NULL,
                subscriber_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, payload BLOB NOT NULL,
                payload_hash TEXT NOT NULL, status TEXT NOT NULL,
                claim_token TEXT, claim_expires_at TEXT,
                turn_receipt_hash TEXT, completed_at TEXT
            )""",
        ),
    )
    inbound.execute(
        "INSERT INTO inbound_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "inbound-records-001",
            identity.lead_id,
            "subscriber-fixture-001",
            "conversation-fixture-001",
            EARLIER,
            sqlite3.Binary(b"excluded-webhook-payload"),
            "1" * 64,
            "processed",
            "excluded-claim-token",
            None,
            "2" * 64,
            NOW,
        ),
    )
    inbound.commit()
    inbound.close()

    private = _database(
        root / "v2-private-customer.sqlite3",
        (
            """CREATE TABLE private_customer_facts (
                lead_id TEXT NOT NULL, fact_name TEXT NOT NULL,
                private_value TEXT NOT NULL, value_hash TEXT NOT NULL,
                source_turn_id TEXT NOT NULL, source_event_hash TEXT NOT NULL,
                revision INTEGER NOT NULL, persisted_at TEXT NOT NULL,
                PRIMARY KEY (lead_id, fact_name)
            )""",
            """CREATE TABLE private_dialogue_turns (
                lead_id TEXT NOT NULL, source_turn_id TEXT NOT NULL,
                source_event_hash TEXT NOT NULL, customer_message TEXT NOT NULL,
                assistant_reply_chunks_json TEXT NOT NULL,
                private_content_hash TEXT NOT NULL, committed_at TEXT NOT NULL,
                PRIMARY KEY (lead_id, source_turn_id)
            )""",
            """CREATE TABLE private_passenger_manifests (
                lead_id TEXT PRIMARY KEY, fact_json TEXT NOT NULL,
                fact_hash TEXT NOT NULL, source_turn_id TEXT NOT NULL,
                source_event_hash TEXT NOT NULL, revision INTEGER NOT NULL,
                persisted_at TEXT NOT NULL
            )""",
        ),
    )
    facts = (
        ("full_name", "Cliente Fixture"),
        ("email", "fixture@example.invalid"),
        ("country_code", "BR"),
    )
    private.executemany(
        "INSERT INTO private_customer_facts VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                identity.lead_id,
                name,
                value,
                _hash(value),
                "turn-records-001",
                "3" * 64,
                1,
                NOW,
            )
            for name, value in facts
        ],
    )
    chunks = _json(["Resposta factual da Maya."])
    private.execute(
        "INSERT INTO private_dialogue_turns VALUES (?,?,?,?,?,?,?)",
        (
            identity.lead_id,
            "turn-records-001",
            "3" * 64,
            "Quero confirmar os dados do passeio.",
            chunks,
            _hash(chunks),
            NOW,
        ),
    )
    private.commit()
    private.close()

    command = _reservation_command(identity)
    command_json = _json(command)
    state_json = _json(
        {
            "schema_version": 1,
            "type": "boundary_state",
            "data": {
                "lead_key": identity.lead_id,
                "version": 1,
                "processed_event_ids": ["inbound-records-001"],
                "workflow": {
                    "workflow_id": identity.workflow_id,
                    "draft_id": identity.draft_id,
                },
                "payments": [
                    {
                        "payment_id": identity.payment_id,
                        "reservation_anchor_id": identity.draft_id,
                    }
                ],
                "handoff": None,
                "schema_version": 7,
            },
        }
    )
    boundary = _database(
        root / "v2-boundary.sqlite3",
        (
            """CREATE TABLE boundary_state (
                lead_key TEXT PRIMARY KEY, version INTEGER NOT NULL,
                state_json TEXT NOT NULL, state_hash TEXT NOT NULL,
                fencing_token INTEGER NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE boundary_commands (
                command_id TEXT PRIMARY KEY, lead_key TEXT NOT NULL,
                aggregate_turn_id TEXT NOT NULL, command_type TEXT NOT NULL,
                command_json TEXT NOT NULL, command_hash TEXT NOT NULL,
                source_turn_receipt_hash TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE boundary_public_outbox (
                public_row_id TEXT PRIMARY KEY, lead_key TEXT NOT NULL,
                aggregate_turn_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL, target_binding_hash TEXT NOT NULL,
                channel_id TEXT NOT NULL, channel_scope TEXT NOT NULL,
                chunk_json TEXT NOT NULL, chunk_hash TEXT NOT NULL,
                predecessor_chunk_hash TEXT, status TEXT NOT NULL, owner TEXT,
                fencing_token INTEGER NOT NULL, lease_acquired_at TEXT,
                lease_expires_at TEXT, claim_count INTEGER NOT NULL,
                preparation_failures INTEGER NOT NULL,
                dispatch_slots_consumed INTEGER NOT NULL,
                authorization_kind TEXT NOT NULL, authorization_id TEXT NOT NULL,
                scope_subject_id TEXT NOT NULL, allocation_id TEXT NOT NULL,
                immutable_generation INTEGER NOT NULL, qualification_id TEXT,
                scenario_id TEXT, capability_policy_digest TEXT NOT NULL,
                effect_authorization_binding_digest TEXT NOT NULL,
                effective_turn_binding_digest TEXT NOT NULL,
                source_turn_receipt_hash TEXT NOT NULL,
                delivery_receipt_json TEXT, delivery_receipt_hash TEXT,
                deadline_at TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
        ),
    )
    boundary.execute(
        "INSERT INTO boundary_state VALUES (?,?,?,?,?,?,?)",
        (identity.lead_id, 1, state_json, _hash(state_json), 1, EARLIER, NOW),
    )
    boundary.execute(
        "INSERT INTO boundary_commands VALUES (?,?,?,?,?,?,?,?)",
        (
            identity.command_id,
            identity.lead_id,
            "turn-records-001",
            "reservation",
            command_json,
            _hash(command_json),
            "4" * 64,
            EARLIER,
        ),
    )
    boundary.commit()
    boundary.close()

    outcome = {
        "schema_version": 1,
        "type": "execution_outcome",
        "data": {
            "certainty": "effect_confirmed",
            "command_id": identity.command_id,
            "evidence": ["provider_submit_accepted"],
            "normalized_status": "confirmed",
            "provider_reference": "provider-reference-generic-001",
        },
    }
    outcome_json = _json(outcome)
    workflow_state = _json(
        {
            "schema_version": 1,
            "type": "succeeded",
            "data": {
                "command": command["data"],
                "meta": {
                    "command_ids": [identity.command_id],
                    "last_event_at": NOW,
                    "revision": 4,
                    "seen_event_hashes": ["5" * 64],
                    "seen_event_ids": ["execution-event-001"],
                    "workflow_id": identity.workflow_id,
                },
                "outcome": outcome["data"],
            },
        }
    )
    execution = _database(
        root / "v2-execution.sqlite3",
        (
            """CREATE TABLE reservation_commands (
                command_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL,
                workflow_id TEXT NOT NULL, draft_id TEXT NOT NULL,
                draft_version INTEGER NOT NULL, subject_signature TEXT NOT NULL,
                operation TEXT NOT NULL, command_json TEXT NOT NULL,
                command_hash TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE workflows (
                workflow_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                state_type TEXT NOT NULL, state_json TEXT NOT NULL,
                state_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE execution_ledger (
                command_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                claim_owner TEXT, fencing_token INTEGER NOT NULL,
                lease_acquired_at TEXT, lease_expires_at TEXT,
                claim_count INTEGER NOT NULL, preparation_failures INTEGER NOT NULL,
                dispatch_slots_consumed INTEGER NOT NULL,
                dispatch_request_hash TEXT, dispatch_fenced_at TEXT,
                outcome_json TEXT, outcome_hash TEXT, updated_at TEXT NOT NULL
            )""",
        ),
    )
    execution.execute(
        "INSERT INTO reservation_commands VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            identity.command_id,
            "idem-reservation-001",
            identity.workflow_id,
            identity.draft_id,
            1,
            "subject-signature-001",
            "book_activity",
            command_json,
            _hash(command_json),
            EARLIER,
        ),
    )
    execution.execute(
        "INSERT INTO workflows VALUES (?,?,?,?,?,?,?)",
        (
            identity.workflow_id,
            4,
            "succeeded",
            workflow_state,
            _hash(workflow_state),
            EARLIER,
            NOW,
        ),
    )
    execution.execute(
        "INSERT INTO execution_ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identity.command_id,
            "completed",
            None,
            1,
            None,
            None,
            1,
            0,
            1,
            "6" * 64,
            NOW,
            outcome_json,
            _hash(outcome_json),
            NOW,
        ),
    )
    execution.commit()
    execution.close()

    selection = _json(
        {
            "method": "stripe",
            "obligation": {
                "amount_minor": 33495,
                "business_unit": "chapada",
                "currency": "BRL",
                "display_details": {
                    "adults": 1,
                    "children": 0,
                    "customer_language": "pt-BR",
                    "end_date": None,
                    "package_component": False,
                    "public_label": "Passeio de teste",
                    "reservation_total_minor": 33495,
                    "service": "activity",
                    "start_date": "2026-09-15",
                    "start_time": "08:00",
                },
                "due_kind": "prepayment",
                "economic_version": 1,
                "payment_id": identity.payment_id,
                "receiver_profile_id": "stripe-fixture",
                "reservation_anchor_id": identity.draft_id,
            },
        }
    )
    payment = _database(
        root / "v2-payment-initiation.sqlite3",
        (
            """CREATE TABLE payment_initiations (
                initiation_id TEXT PRIMARY KEY, selection_json BLOB NOT NULL,
                selection_hash TEXT NOT NULL, status TEXT NOT NULL,
                claim_owner TEXT, fencing_token INTEGER NOT NULL,
                lease_expires_at TEXT, dispatch_slots INTEGER NOT NULL,
                result_json BLOB, result_hash TEXT, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE stripe_reconciliations (
                initiation_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                claim_owner TEXT, fencing_token INTEGER NOT NULL,
                lease_expires_at TEXT, attempts INTEGER NOT NULL,
                recovery_pending INTEGER NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE stripe_step_receipts (
                initiation_id TEXT NOT NULL, step TEXT NOT NULL,
                status TEXT NOT NULL, journal_owner TEXT NOT NULL,
                journal_fencing_token INTEGER NOT NULL,
                receipt_json BLOB NOT NULL, receipt_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (initiation_id, step)
            )""",
        ),
    )
    payment.execute(
        "INSERT INTO payment_initiations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            identity.initiation_id,
            sqlite3.Binary(selection.encode("utf-8")),
            _hash(selection),
            "completed",
            None,
            1,
            None,
            3,
            sqlite3.Binary(b"encrypted-result-excluded"),
            "7" * 64,
            NOW,
        ),
    )
    payment.execute(
        "INSERT INTO stripe_reconciliations VALUES (?,?,?,?,?,?,?,?)",
        (identity.initiation_id, "matched", None, 1, None, 1, 0, NOW),
    )
    payment.executemany(
        "INSERT INTO stripe_step_receipts VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                identity.initiation_id,
                step,
                "accepted",
                "fixture-journal",
                1,
                sqlite3.Binary(f"excluded-{step}-receipt".encode("utf-8")),
                _hash(step),
                NOW,
            )
            for step in ("product", "price", "payment_link")
        ],
    )
    payment.commit()
    payment.close()

    followup = _database(
        root / "v2-followup.sqlite3",
        (
            """CREATE TABLE payment_workflows (
                payment_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                payment_version INTEGER NOT NULL, economic_signature TEXT NOT NULL,
                status TEXT NOT NULL, state_json TEXT NOT NULL,
                state_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE payment_events (
                event_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL,
                revision INTEGER NOT NULL, payment_version INTEGER NOT NULL,
                economic_signature TEXT NOT NULL, event_type TEXT NOT NULL,
                event_json TEXT NOT NULL, event_hash TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )""",
            """CREATE TABLE payment_commands (
                settlement_command_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL,
                payment_id TEXT NOT NULL, payment_version INTEGER NOT NULL,
                economic_signature TEXT NOT NULL, evidence_claim_key TEXT NOT NULL,
                operation TEXT NOT NULL, command_json TEXT NOT NULL,
                command_hash TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE payment_ledger (
                settlement_command_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL,
                payment_version INTEGER NOT NULL, economic_signature TEXT NOT NULL,
                status TEXT NOT NULL, claim_owner TEXT, fencing_token INTEGER NOT NULL,
                lease_acquired_at TEXT, lease_expires_at TEXT,
                claim_count INTEGER NOT NULL, dispatch_slots_consumed INTEGER NOT NULL,
                dispatch_request_hash TEXT, dispatch_fenced_at TEXT,
                outcome_certainty TEXT, outcome_json TEXT, outcome_hash TEXT,
                outcome_recorded_at TEXT, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE payment_receipts (
                receipt_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL,
                message_id TEXT NOT NULL, receipt_json TEXT NOT NULL,
                receipt_hash TEXT NOT NULL, delivered_at TEXT NOT NULL
            )""",
            """CREATE TABLE handoff_workflows (
                handoff_id TEXT PRIMARY KEY, incident_key TEXT NOT NULL,
                revision INTEGER NOT NULL, status TEXT NOT NULL,
                lead_key_hash TEXT NOT NULL, state_json TEXT NOT NULL,
                state_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE handoff_events (
                event_id TEXT PRIMARY KEY, handoff_id TEXT NOT NULL,
                revision INTEGER NOT NULL, event_type TEXT NOT NULL,
                event_json TEXT NOT NULL, event_hash TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )""",
            """CREATE TABLE handoff_receipts (
                receipt_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL,
                message_id TEXT NOT NULL, receipt_json TEXT NOT NULL,
                receipt_hash TEXT NOT NULL, delivered_at TEXT NOT NULL
            )""",
        ),
    )
    followup.commit()
    followup.close()

    bokun = _database(
        root / "v2-bokun-audit.sqlite3",
        (
            """CREATE TABLE bokun_audit_tasks (
                task_id TEXT PRIMARY KEY, command_id TEXT NOT NULL,
                booking_id TEXT NOT NULL, product_id TEXT NOT NULL,
                provider_start_time_id TEXT NOT NULL, provider_rate_id TEXT NOT NULL,
                activity_date TEXT NOT NULL, start_time TEXT NOT NULL,
                adults INTEGER NOT NULL, children INTEGER NOT NULL,
                total TEXT NOT NULL, currency TEXT NOT NULL,
                expected_status TEXT NOT NULL, max_attempts INTEGER NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL,
                lease_owner TEXT, lease_acquired_at TEXT, lease_expires_at TEXT,
                fencing_token INTEGER NOT NULL
            )""",
        ),
    )
    bokun.commit()
    bokun.close()

    cloudbeds = _database(
        root / "v2-cloudbeds-audit.sqlite3",
        (
            """CREATE TABLE cloudbeds_audit_tasks (
                task_id TEXT PRIMARY KEY, command_id TEXT NOT NULL,
                reservation_id TEXT NOT NULL, property_id TEXT NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                adults INTEGER NOT NULL, children INTEGER NOT NULL,
                amount TEXT NOT NULL, currency TEXT NOT NULL,
                expected_status TEXT NOT NULL, max_attempts INTEGER NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL,
                lease_owner TEXT, lease_acquired_at TEXT, lease_expires_at TEXT,
                fencing_token INTEGER
            )""",
        ),
    )
    cloudbeds.commit()
    cloudbeds.close()

    public = _database(
        root / "v2-public-outbox.sqlite3",
        (
            """CREATE TABLE public_outbox (
                outbox_id TEXT PRIMARY KEY, release_id TEXT NOT NULL,
                lead_id TEXT NOT NULL, source_message_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL, text TEXT NOT NULL,
                status TEXT NOT NULL, claim_owner TEXT,
                fencing_token INTEGER NOT NULL, lease_expires_at TEXT,
                receipt_id TEXT, acceptance_json TEXT, acceptance_hash TEXT,
                updated_at TEXT NOT NULL, author TEXT NOT NULL
            )""",
        ),
    )
    public.execute(
        "INSERT INTO public_outbox VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "outbox-records-001",
            "release-fixture-001",
            identity.lead_id,
            "message-records-001",
            0,
            "Resposta factual da Maya.",
            "delivered",
            None,
            1,
            None,
            "receipt-records-001",
            None,
            None,
            NOW,
            "maya",
        ),
    )
    public.commit()
    public.close()

    return identity
