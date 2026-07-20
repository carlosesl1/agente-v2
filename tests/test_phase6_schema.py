from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from reservation_followup.handoff import HandoffEffectKind
from reservation_followup.payment import SettlementOperation
from reservation_followup.projection import PaymentEffectKind
from reservation_followup.schema import (
    SCHEMA_VERSION,
    ColumnContract,
    TableContract,
    render_postgresql,
    render_sqlite,
    schema_contract,
    schema_hash,
)
from reservation_followup.types import (
    HandoffStatus,
    PaymentMethod,
    PaymentStatus,
    SettlementCertainty,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = "2027-01-01T00:00:00+00:00"
LATER = "2027-01-01T00:01:00+00:00"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64

EXPECTED_COLUMNS = {
    "handoff_workflows": (
        "handoff_id",
        "incident_key",
        "revision",
        "status",
        "lead_key_hash",
        "state_json",
        "state_hash",
        "created_at",
        "updated_at",
    ),
    "handoff_events": (
        "event_id",
        "handoff_id",
        "revision",
        "event_type",
        "event_json",
        "event_hash",
        "occurred_at",
    ),
    "handoff_outbox": (
        "message_id",
        "idempotency_key",
        "effect_id",
        "handoff_id",
        "kind",
        "template_id",
        "payload_json",
        "payload_hash",
        "status",
        "claim_owner",
        "fencing_token",
        "lease_acquired_at",
        "lease_expires_at",
        "delivery_attempts",
        "delivered_at",
        "receipt_hash",
        "created_at",
        "updated_at",
    ),
    "handoff_receipts": (
        "receipt_id",
        "idempotency_key",
        "message_id",
        "receipt_json",
        "receipt_hash",
        "delivered_at",
    ),
    "payment_workflows": (
        "payment_id",
        "revision",
        "payment_version",
        "economic_signature",
        "status",
        "state_json",
        "state_hash",
        "created_at",
        "updated_at",
    ),
    "payment_events": (
        "event_id",
        "payment_id",
        "revision",
        "payment_version",
        "economic_signature",
        "event_type",
        "event_json",
        "event_hash",
        "occurred_at",
    ),
    "payment_evidence_claims": (
        "claim_key",
        "payment_id",
        "payment_version",
        "economic_signature",
        "method",
        "evidence_json",
        "evidence_hash",
        "status",
        "claimed_at",
        "consumed_at",
    ),
    "payment_commands": (
        "settlement_command_id",
        "idempotency_key",
        "payment_id",
        "payment_version",
        "economic_signature",
        "evidence_claim_key",
        "operation",
        "command_json",
        "command_hash",
        "created_at",
    ),
    "payment_ledger": (
        "settlement_command_id",
        "payment_id",
        "payment_version",
        "economic_signature",
        "status",
        "claim_owner",
        "fencing_token",
        "lease_acquired_at",
        "lease_expires_at",
        "claim_count",
        "dispatch_slots_consumed",
        "dispatch_request_hash",
        "dispatch_fenced_at",
        "outcome_certainty",
        "outcome_json",
        "outcome_hash",
        "outcome_recorded_at",
        "updated_at",
    ),
    "payment_outbox": (
        "message_id",
        "idempotency_key",
        "effect_id",
        "payment_id",
        "payment_version",
        "economic_signature",
        "settlement_command_id",
        "kind",
        "template_id",
        "payload_json",
        "payload_hash",
        "status",
        "claim_owner",
        "fencing_token",
        "lease_acquired_at",
        "lease_expires_at",
        "delivery_attempts",
        "delivered_at",
        "receipt_hash",
        "created_at",
        "updated_at",
    ),
    "payment_receipts": (
        "receipt_id",
        "idempotency_key",
        "message_id",
        "receipt_json",
        "receipt_hash",
        "delivered_at",
    ),
}

EXPECTED_NULLABLE = {
    "handoff_workflows": set(),
    "handoff_events": set(),
    "handoff_outbox": {
        "claim_owner",
        "lease_acquired_at",
        "lease_expires_at",
        "delivered_at",
        "receipt_hash",
    },
    "handoff_receipts": set(),
    "payment_workflows": set(),
    "payment_events": set(),
    "payment_evidence_claims": {"consumed_at"},
    "payment_commands": set(),
    "payment_ledger": {
        "claim_owner",
        "lease_acquired_at",
        "lease_expires_at",
        "dispatch_request_hash",
        "dispatch_fenced_at",
        "outcome_certainty",
        "outcome_json",
        "outcome_hash",
        "outcome_recorded_at",
    },
    "payment_outbox": {
        "claim_owner",
        "lease_acquired_at",
        "lease_expires_at",
        "delivered_at",
        "receipt_hash",
    },
    "payment_receipts": set(),
}

EXPECTED_PRIMARY_KEYS = {
    "handoff_workflows": ("handoff_id",),
    "handoff_events": ("event_id",),
    "handoff_outbox": ("message_id",),
    "handoff_receipts": ("receipt_id",),
    "payment_workflows": ("payment_id",),
    "payment_events": ("event_id",),
    "payment_evidence_claims": ("claim_key",),
    "payment_commands": ("settlement_command_id",),
    "payment_ledger": ("settlement_command_id",),
    "payment_outbox": ("message_id",),
    "payment_receipts": ("receipt_id",),
}

EXPECTED_FOREIGN_KEYS = {
    "handoff_workflows": set(),
    "handoff_events": {("handoff_id", "handoff_workflows", "handoff_id")},
    "handoff_outbox": {("handoff_id", "handoff_workflows", "handoff_id")},
    "handoff_receipts": {
        ("message_id", "handoff_outbox", "message_id"),
        ("receipt_hash", "handoff_outbox", "receipt_hash"),
        ("delivered_at", "handoff_outbox", "delivered_at"),
    },
    "payment_workflows": set(),
    "payment_events": {("payment_id", "payment_workflows", "payment_id")},
    "payment_evidence_claims": {
        ("payment_id", "payment_workflows", "payment_id")
    },
    "payment_commands": {
        ("payment_id", "payment_workflows", "payment_id"),
        ("evidence_claim_key", "payment_evidence_claims", "claim_key"),
        ("payment_id", "payment_evidence_claims", "payment_id"),
        ("payment_version", "payment_evidence_claims", "payment_version"),
        ("economic_signature", "payment_evidence_claims", "economic_signature"),
    },
    "payment_ledger": {
        ("settlement_command_id", "payment_commands", "settlement_command_id"),
        ("payment_id", "payment_commands", "payment_id"),
        ("payment_version", "payment_commands", "payment_version"),
        ("economic_signature", "payment_commands", "economic_signature"),
    },
    "payment_outbox": {
        ("payment_id", "payment_workflows", "payment_id"),
        ("settlement_command_id", "payment_commands", "settlement_command_id"),
        ("payment_id", "payment_commands", "payment_id"),
        ("payment_version", "payment_commands", "payment_version"),
        ("economic_signature", "payment_commands", "economic_signature"),
    },
    "payment_receipts": {
        ("message_id", "payment_outbox", "message_id"),
        ("receipt_hash", "payment_outbox", "receipt_hash"),
        ("delivered_at", "payment_outbox", "delivered_at"),
    },
}

EXPECTED_FOREIGN_KEY_GROUPS = {
    "handoff_workflows": set(),
    "handoff_events": {
        (("handoff_id",), "handoff_workflows", ("handoff_id",))
    },
    "handoff_outbox": {
        (("handoff_id",), "handoff_workflows", ("handoff_id",))
    },
    "handoff_receipts": {
        (
            ("message_id", "receipt_hash", "delivered_at"),
            "handoff_outbox",
            ("message_id", "receipt_hash", "delivered_at"),
        )
    },
    "payment_workflows": set(),
    "payment_events": {
        (("payment_id",), "payment_workflows", ("payment_id",))
    },
    "payment_evidence_claims": {
        (("payment_id",), "payment_workflows", ("payment_id",))
    },
    "payment_commands": {
        (("payment_id",), "payment_workflows", ("payment_id",)),
        (
            (
                "evidence_claim_key",
                "payment_id",
                "payment_version",
                "economic_signature",
            ),
            "payment_evidence_claims",
            ("claim_key", "payment_id", "payment_version", "economic_signature"),
        ),
    },
    "payment_ledger": {
        (
            (
                "settlement_command_id",
                "payment_id",
                "payment_version",
                "economic_signature",
            ),
            "payment_commands",
            (
                "settlement_command_id",
                "payment_id",
                "payment_version",
                "economic_signature",
            ),
        )
    },
    "payment_outbox": {
        (("payment_id",), "payment_workflows", ("payment_id",)),
        (
            (
                "settlement_command_id",
                "payment_id",
                "payment_version",
                "economic_signature",
            ),
            "payment_commands",
            (
                "settlement_command_id",
                "payment_id",
                "payment_version",
                "economic_signature",
            ),
        ),
    },
    "payment_receipts": {
        (
            ("message_id", "receipt_hash", "delivered_at"),
            "payment_outbox",
            ("message_id", "receipt_hash", "delivered_at"),
        )
    },
}

EXPECTED_UNIQUES = {
    "handoff_workflows": {("incident_key",)},
    "handoff_events": {("handoff_id", "revision")},
    "handoff_outbox": {
        ("idempotency_key",),
        ("effect_id",),
        ("message_id", "receipt_hash", "delivered_at"),
    },
    "handoff_receipts": {("idempotency_key",), ("message_id",)},
    "payment_workflows": set(),
    "payment_events": {("payment_id", "revision")},
    "payment_evidence_claims": {
        ("claim_key", "payment_id", "payment_version", "economic_signature")
    },
    "payment_commands": {
        ("idempotency_key",),
        ("payment_id", "payment_version", "economic_signature"),
        (
            "settlement_command_id",
            "payment_id",
            "payment_version",
            "economic_signature",
        ),
    },
    "payment_ledger": {("payment_id", "payment_version", "economic_signature")},
    "payment_outbox": {
        ("idempotency_key",),
        ("effect_id",),
        ("message_id", "receipt_hash", "delivered_at"),
    },
    "payment_receipts": {("idempotency_key",), ("message_id",)},
}

INTEGER_COLUMNS = {
    "handoff_workflows": {"revision"},
    "handoff_events": {"revision"},
    "handoff_outbox": {"fencing_token", "delivery_attempts"},
    "handoff_receipts": set(),
    "payment_workflows": {"revision", "payment_version"},
    "payment_events": {"revision", "payment_version"},
    "payment_evidence_claims": {"payment_version"},
    "payment_commands": {"payment_version"},
    "payment_ledger": {
        "payment_version",
        "fencing_token",
        "claim_count",
        "dispatch_slots_consumed",
    },
    "payment_outbox": {
        "payment_version",
        "fencing_token",
        "delivery_attempts",
    },
    "payment_receipts": set(),
}

EXPECTED_POSTGRESQL_DDL_SHA256 = (
    "761ad4e6121b48ec9833b8b77b0bea4c762cf392102bee9b639106af40869273"
)

EXPECTED_POSTGRESQL_BLOCK_SHA256 = {
    "handoff_workflows": "1806f243640f15e8d483a7112f2aeb0af4104213321fa7b8f251847575442ce3",
    "handoff_events": "04364593ac89ae6cc3a4343e235696d7d610752e80032f138926711341f8d8f9",
    "handoff_outbox": "c19cb68a1fb65dba90874546158f87d6609939c866b0f587c1f6e65c3c65168f",
    "handoff_receipts": "e0f5d23ba80f667b6bd06c1644e9c5644e13e9aa38f95f789f793f7d0e0468b6",
    "payment_workflows": "f4c1f38bd6b7269cda779aa9f6f3aa1674345fda5b8c9b41c12e4a15f3282a66",
    "payment_events": "7945ae415cde0c8eb8e77499e93a0f9813380aff5fb817bea36e260126ab97c1",
    "payment_evidence_claims": "7fc64fa56d374f27e2b68c415aabf2ebaf86d5a7fabf9a453c5e9b0d0d590b13",
    "payment_commands": "4b64d1eccc930ac67a01465796ca8c12714a4e9e64d763858d454582ed7edcbe",
    "payment_ledger": "d6ab8fbfad492d0990745f6308c074a9283ba7245c65127048be3cc7671c6bd8",
    "payment_outbox": "0d4be1b283692f7714cf12a448567930170603350bc2498601f62bda140fafdf",
    "payment_receipts": "53407389f3c21e5aee00d4466fcc28533bcc8f96282a87de00318fcf8918f53d",
}

NONEMPTY_COLUMNS = {
    "handoff_workflows": {"handoff_id", "incident_key", "state_json"},
    "handoff_events": {"event_id", "handoff_id", "event_type", "event_json"},
    "handoff_outbox": {
        "message_id",
        "idempotency_key",
        "effect_id",
        "handoff_id",
        "template_id",
        "payload_json",
        "claim_owner",
    },
    "handoff_receipts": {
        "receipt_id",
        "idempotency_key",
        "message_id",
        "receipt_json",
    },
    "payment_workflows": {"payment_id", "state_json"},
    "payment_events": {"event_id", "payment_id", "event_type", "event_json"},
    "payment_evidence_claims": {"claim_key", "payment_id", "evidence_json"},
    "payment_commands": {
        "settlement_command_id",
        "idempotency_key",
        "payment_id",
        "evidence_claim_key",
        "command_json",
    },
    "payment_ledger": {
        "settlement_command_id",
        "payment_id",
        "claim_owner",
        "outcome_json",
    },
    "payment_outbox": {
        "message_id",
        "idempotency_key",
        "effect_id",
        "payment_id",
        "settlement_command_id",
        "template_id",
        "payload_json",
        "claim_owner",
    },
    "payment_receipts": {
        "receipt_id",
        "idempotency_key",
        "message_id",
        "receipt_json",
    },
}

HASH_COLUMNS = {
    "handoff_workflows": {"lead_key_hash", "state_hash"},
    "handoff_events": {"event_hash"},
    "handoff_outbox": {"payload_hash", "receipt_hash"},
    "handoff_receipts": {"receipt_hash"},
    "payment_workflows": {"economic_signature", "state_hash"},
    "payment_events": {"economic_signature", "event_hash"},
    "payment_evidence_claims": {"economic_signature", "evidence_hash"},
    "payment_commands": {"economic_signature", "command_hash"},
    "payment_ledger": {
        "economic_signature",
        "dispatch_request_hash",
        "outcome_hash",
    },
    "payment_outbox": {
        "economic_signature",
        "payload_hash",
        "receipt_hash",
    },
    "payment_receipts": {"receipt_hash"},
}

TIMESTAMP_COLUMNS = {
    "handoff_workflows": {"created_at", "updated_at"},
    "handoff_events": {"occurred_at"},
    "handoff_outbox": {
        "lease_acquired_at",
        "lease_expires_at",
        "delivered_at",
        "created_at",
        "updated_at",
    },
    "handoff_receipts": {"delivered_at"},
    "payment_workflows": {"created_at", "updated_at"},
    "payment_events": {"occurred_at"},
    "payment_evidence_claims": {"claimed_at", "consumed_at"},
    "payment_commands": {"created_at"},
    "payment_ledger": {
        "lease_acquired_at",
        "lease_expires_at",
        "dispatch_fenced_at",
        "outcome_recorded_at",
        "updated_at",
    },
    "payment_outbox": {
        "lease_acquired_at",
        "lease_expires_at",
        "delivered_at",
        "created_at",
        "updated_at",
    },
    "payment_receipts": {"delivered_at"},
}


class Phase6SchemaTests(unittest.TestCase):
    def assert_postgresql_contract_exact(self, sql: str) -> None:
        matches = tuple(
            re.finditer(
                r"(?ms)^CREATE TABLE ([a-z][a-z0-9_]*) \(\n.*?^\);$",
                sql,
            )
        )
        names = tuple(match.group(1) for match in matches)
        self.assertEqual(names, tuple(EXPECTED_POSTGRESQL_BLOCK_SHA256))
        actual_block_hashes = {
            match.group(1): hashlib.sha256(match.group(0).encode("utf-8")).hexdigest()
            for match in matches
        }
        self.assertEqual(actual_block_hashes, EXPECTED_POSTGRESQL_BLOCK_SHA256)
        self.assertEqual(
            hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            EXPECTED_POSTGRESQL_DDL_SHA256,
        )

    def open_database(self) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(render_sqlite())
        return connection

    def sqlite_column_definition(self, table_name: str, column_name: str) -> str:
        sql = render_sqlite()
        start = sql.index(f"CREATE TABLE {table_name} (")
        end = sql.index(") STRICT;", start)
        prefix = f"    {column_name} "
        matches = [
            line.strip().removesuffix(",")
            for line in sql[start:end].splitlines()
            if line.startswith(prefix)
        ]
        self.assertEqual(len(matches), 1, (table_name, column_name, matches))
        return matches[0]

    def assert_isolated_sqlite_column_rejects(
        self,
        table_name: str,
        column_name: str,
        value: object,
    ) -> None:
        self.assert_isolated_sqlite_column_rejects_all(
            table_name,
            column_name,
            (value,),
        )

    def assert_isolated_sqlite_column_rejects_all(
        self,
        table_name: str,
        column_name: str,
        values: tuple[object, ...] | list[object],
    ) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        definition = self.sqlite_column_definition(table_name, column_name)
        connection.execute(f"CREATE TABLE probe ({definition}) STRICT")
        for value in values:
            with self.subTest(table=table_name, column=column_name, isolated=value):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        f"INSERT INTO probe ({column_name}) VALUES (?)",
                        (value,),
                    )

    def insert_handoff_workflow(
        self,
        connection: sqlite3.Connection,
        suffix: str,
        **overrides: object,
    ) -> str:
        values: dict[str, object] = {
            "handoff_id": f"handoff:schema:{suffix}",
            "incident_key": f"incident:schema:{suffix}",
            "revision": 0,
            "status": HandoffStatus.REQUESTED.value,
            "lead_key_hash": HASH_A,
            "state_json": "{}",
            "state_hash": HASH_B,
            "created_at": NOW,
            "updated_at": NOW,
        }
        values.update(overrides)
        connection.execute(
            "INSERT INTO handoff_workflows "
            "(handoff_id, incident_key, revision, status, lead_key_hash, state_json, "
            "state_hash, created_at, updated_at) VALUES "
            "(:handoff_id, :incident_key, :revision, :status, :lead_key_hash, "
            ":state_json, :state_hash, :created_at, :updated_at)",
            values,
        )
        return str(values["handoff_id"])

    def insert_handoff_outbox(
        self,
        connection: sqlite3.Connection,
        handoff_id: str,
        suffix: str,
        **overrides: object,
    ) -> str:
        values: dict[str, object] = {
            "message_id": f"handoff:message:{suffix}",
            "idempotency_key": f"handoff:idem:{suffix}",
            "effect_id": f"handoff:effect:{suffix}",
            "handoff_id": handoff_id,
            "kind": HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT.value,
            "template_id": "handoff.acknowledgement.v1",
            "payload_json": "{}",
            "payload_hash": HASH_A,
            "status": "pending",
            "claim_owner": None,
            "fencing_token": 0,
            "lease_acquired_at": None,
            "lease_expires_at": None,
            "delivery_attempts": 0,
            "delivered_at": None,
            "receipt_hash": None,
            "created_at": NOW,
            "updated_at": NOW,
        }
        values.update(overrides)
        if values["status"] == "delivered" and "claim_owner" not in overrides:
            values["claim_owner"] = "handoff-claim:" + hashlib.sha256(
                str(values["message_id"]).encode("utf-8")
            ).hexdigest()
        connection.execute(
            "INSERT INTO handoff_outbox "
            "(message_id, idempotency_key, effect_id, handoff_id, kind, template_id, "
            "payload_json, payload_hash, status, claim_owner, fencing_token, "
            "lease_acquired_at, lease_expires_at, delivery_attempts, delivered_at, "
            "receipt_hash, created_at, updated_at) VALUES "
            "(:message_id, :idempotency_key, :effect_id, :handoff_id, :kind, "
            ":template_id, :payload_json, :payload_hash, :status, :claim_owner, "
            ":fencing_token, :lease_acquired_at, :lease_expires_at, "
            ":delivery_attempts, :delivered_at, :receipt_hash, :created_at, :updated_at)",
            values,
        )
        return str(values["message_id"])

    def insert_payment_workflow(
        self,
        connection: sqlite3.Connection,
        suffix: str,
        **overrides: object,
    ) -> str:
        values: dict[str, object] = {
            "payment_id": f"payment:schema:{suffix}",
            "revision": 0,
            "payment_version": 1,
            "economic_signature": HASH_A,
            "status": PaymentStatus.AWAITING_METHOD.value,
            "state_json": "{}",
            "state_hash": HASH_B,
            "created_at": NOW,
            "updated_at": NOW,
        }
        values.update(overrides)
        connection.execute(
            "INSERT INTO payment_workflows "
            "(payment_id, revision, payment_version, economic_signature, status, "
            "state_json, state_hash, created_at, updated_at) VALUES "
            "(:payment_id, :revision, :payment_version, :economic_signature, :status, "
            ":state_json, :state_hash, :created_at, :updated_at)",
            values,
        )
        return str(values["payment_id"])

    def insert_claim(
        self,
        connection: sqlite3.Connection,
        default_payment_id: str,
        suffix: str,
        **overrides: object,
    ) -> str:
        values: dict[str, object] = {
            "claim_key": (
                "pix:schema:" + hashlib.sha256(suffix.encode("utf-8")).hexdigest()
            ),
            "payment_id": default_payment_id,
            "payment_version": 1,
            "economic_signature": HASH_A,
            "method": PaymentMethod.PIX.value,
            "evidence_json": "{}",
            "evidence_hash": HASH_B,
            "status": "completed",
            "claimed_at": NOW,
            "consumed_at": LATER,
        }
        values.update(overrides)
        connection.execute(
            "INSERT INTO payment_evidence_claims "
            "(claim_key, payment_id, payment_version, economic_signature, method, "
            "evidence_json, evidence_hash, status, claimed_at, consumed_at) VALUES "
            "(:claim_key, :payment_id, :payment_version, :economic_signature, :method, "
            ":evidence_json, :evidence_hash, :status, :claimed_at, :consumed_at)",
            values,
        )
        return str(values["claim_key"])

    def insert_command(
        self,
        connection: sqlite3.Connection,
        default_payment_id: str,
        claim_key: str,
        suffix: str,
        **overrides: object,
    ) -> str:
        values: dict[str, object] = {
            "settlement_command_id": f"payment:command:{suffix}",
            "idempotency_key": f"payment:command:idem:{suffix}",
            "payment_id": default_payment_id,
            "payment_version": 1,
            "economic_signature": HASH_A,
            "evidence_claim_key": claim_key,
            "operation": SettlementOperation.REGISTER_AND_CONFIRM.value,
            "command_json": "{}",
            "command_hash": HASH_C,
            "created_at": NOW,
        }
        values.update(overrides)
        connection.execute(
            "INSERT INTO payment_commands "
            "(settlement_command_id, idempotency_key, payment_id, payment_version, "
            "economic_signature, evidence_claim_key, operation, command_json, "
            "command_hash, created_at) VALUES "
            "(:settlement_command_id, :idempotency_key, :payment_id, "
            ":payment_version, :economic_signature, :evidence_claim_key, :operation, "
            ":command_json, :command_hash, :created_at)",
            values,
        )
        return str(values["settlement_command_id"])

    def create_payment_command_graph(
        self,
        connection: sqlite3.Connection,
        suffix: str,
    ) -> tuple[str, str, str]:
        payment_id = self.insert_payment_workflow(connection, suffix)
        claim_key = self.insert_claim(connection, payment_id, suffix)
        command_id = self.insert_command(
            connection, payment_id, claim_key, suffix
        )
        return payment_id, claim_key, command_id

    def insert_ledger(
        self,
        connection: sqlite3.Connection,
        payment_id: str,
        command_id: str,
        **overrides: object,
    ) -> None:
        values: dict[str, object] = {
            "settlement_command_id": command_id,
            "payment_id": payment_id,
            "payment_version": 1,
            "economic_signature": HASH_A,
            "status": "queued",
            "claim_owner": None,
            "fencing_token": 0,
            "lease_acquired_at": None,
            "lease_expires_at": None,
            "claim_count": 0,
            "dispatch_slots_consumed": 0,
            "dispatch_request_hash": None,
            "dispatch_fenced_at": None,
            "outcome_certainty": None,
            "outcome_json": None,
            "outcome_hash": None,
            "outcome_recorded_at": None,
            "updated_at": NOW,
        }
        values.update(overrides)
        connection.execute(
            "INSERT INTO payment_ledger "
            "(settlement_command_id, payment_id, payment_version, economic_signature, "
            "status, claim_owner, fencing_token, lease_acquired_at, lease_expires_at, "
            "claim_count, dispatch_slots_consumed, dispatch_request_hash, "
            "dispatch_fenced_at, outcome_certainty, outcome_json, outcome_hash, "
            "outcome_recorded_at, updated_at) VALUES "
            "(:settlement_command_id, :payment_id, :payment_version, "
            ":economic_signature, :status, :claim_owner, :fencing_token, "
            ":lease_acquired_at, :lease_expires_at, :claim_count, "
            ":dispatch_slots_consumed, :dispatch_request_hash, :dispatch_fenced_at, "
            ":outcome_certainty, :outcome_json, :outcome_hash, "
            ":outcome_recorded_at, :updated_at)",
            values,
        )

    def insert_payment_outbox(
        self,
        connection: sqlite3.Connection,
        payment_id: str,
        command_id: str,
        suffix: str,
        **overrides: object,
    ) -> str:
        values: dict[str, object] = {
            "message_id": f"payment:message:{suffix}",
            "idempotency_key": f"payment:outbox:idem:{suffix}",
            "effect_id": f"payment:effect:{suffix}",
            "payment_id": payment_id,
            "payment_version": 1,
            "economic_signature": HASH_A,
            "settlement_command_id": command_id,
            "kind": "customer_payment_confirmation",
            "template_id": "payment.confirmation.v1",
            "payload_json": "{}",
            "payload_hash": HASH_A,
            "status": "pending",
            "claim_owner": None,
            "fencing_token": 0,
            "lease_acquired_at": None,
            "lease_expires_at": None,
            "delivery_attempts": 0,
            "delivered_at": None,
            "receipt_hash": None,
            "created_at": NOW,
            "updated_at": NOW,
        }
        values.update(overrides)
        connection.execute(
            "INSERT INTO payment_outbox "
            "(message_id, idempotency_key, effect_id, payment_id, payment_version, "
            "economic_signature, settlement_command_id, kind, template_id, "
            "payload_json, payload_hash, status, claim_owner, fencing_token, "
            "lease_acquired_at, lease_expires_at, delivery_attempts, delivered_at, "
            "receipt_hash, created_at, updated_at) VALUES "
            "(:message_id, :idempotency_key, :effect_id, :payment_id, "
            ":payment_version, :economic_signature, :settlement_command_id, :kind, "
            ":template_id, :payload_json, :payload_hash, :status, :claim_owner, "
            ":fencing_token, :lease_acquired_at, :lease_expires_at, "
            ":delivery_attempts, :delivered_at, :receipt_hash, :created_at, :updated_at)",
            values,
        )
        return str(values["message_id"])

    def populate_all_tables(self, connection: sqlite3.Connection) -> dict[str, str]:
        handoff_id = self.insert_handoff_workflow(connection, "all")
        connection.execute(
            "INSERT INTO handoff_events "
            "(event_id, handoff_id, revision, event_type, event_json, event_hash, "
            "occurred_at) VALUES ('handoff:event:all', ?, 1, 'HandoffRequested', "
            "'{}', ?, ?)",
            (handoff_id, HASH_C, NOW),
        )
        handoff_message = self.insert_handoff_outbox(
            connection,
            handoff_id,
            "all",
            status="delivered",
            fencing_token=1,
            delivery_attempts=1,
            delivered_at=LATER,
            receipt_hash=HASH_D,
        )
        connection.execute(
            "INSERT INTO handoff_receipts "
            "(receipt_id, idempotency_key, message_id, receipt_json, receipt_hash, "
            "delivered_at) VALUES ('handoff:receipt:all', 'handoff:receipt:idem:all', "
            "?, '{}', ?, ?)",
            (handoff_message, HASH_D, LATER),
        )

        payment_id, claim_key, command_id = self.create_payment_command_graph(
            connection, "all"
        )
        connection.execute(
            "INSERT INTO payment_events "
            "(event_id, payment_id, revision, payment_version, economic_signature, "
            "event_type, event_json, event_hash, occurred_at) VALUES "
            "('payment:event:all', ?, 1, 1, ?, 'PaymentEvidenceRecorded', '{}', ?, ?)",
            (payment_id, HASH_A, HASH_C, NOW),
        )
        self.insert_ledger(connection, payment_id, command_id)
        payment_message = self.insert_payment_outbox(
            connection,
            payment_id,
            command_id,
            "all",
            status="delivered",
            fencing_token=1,
            delivery_attempts=1,
            delivered_at=LATER,
            receipt_hash=HASH_D,
        )
        connection.execute(
            "INSERT INTO payment_receipts "
            "(receipt_id, idempotency_key, message_id, receipt_json, receipt_hash, "
            "delivered_at) VALUES ('payment:receipt:all', 'payment:receipt:idem:all', "
            "?, '{}', ?, ?)",
            (payment_message, HASH_D, LATER),
        )
        return {
            "handoff_workflows": handoff_id,
            "handoff_events": "handoff:event:all",
            "handoff_outbox": handoff_message,
            "handoff_receipts": "handoff:receipt:all",
            "payment_workflows": payment_id,
            "payment_events": "payment:event:all",
            "payment_evidence_claims": claim_key,
            "payment_commands": command_id,
            "payment_ledger": command_id,
            "payment_outbox": payment_message,
            "payment_receipts": "payment:receipt:all",
        }

    def test_contract_classes_and_exact_ordered_table_column_universe(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(ColumnContract)),
            ("name", "sqlite_type", "postgresql_type", "nullable", "check"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(TableContract)),
            ("name", "columns", "table_constraints"),
        )
        sample = ColumnContract("sample", "TEXT", "text")
        with self.assertRaises(FrozenInstanceError):
            sample.name = "changed"  # type: ignore[misc]
        self.assertFalse(hasattr(sample, "__dict__"))
        table_sample = TableContract("sample", (sample,), ())
        with self.assertRaises(FrozenInstanceError):
            table_sample.name = "changed"  # type: ignore[misc]
        self.assertFalse(hasattr(table_sample, "__dict__"))

        contract = schema_contract()
        self.assertEqual(len(contract), 11)
        self.assertEqual(tuple(table.name for table in contract), tuple(EXPECTED_COLUMNS))
        self.assertEqual(
            {
                table.name: tuple(column.name for column in table.columns)
                for table in contract
            },
            EXPECTED_COLUMNS,
        )
        for table in contract:
            for column in table.columns:
                expected_sqlite = (
                    "INTEGER"
                    if column.name in INTEGER_COLUMNS[table.name]
                    else "TEXT"
                )
                expected_postgresql = (
                    "bigint"
                    if column.name in INTEGER_COLUMNS[table.name]
                    else (
                        "timestamptz"
                        if column.name in TIMESTAMP_COLUMNS[table.name]
                        else "text"
                    )
                )
                with self.subTest(table=table.name, column=column.name):
                    self.assertEqual(column.sqlite_type, expected_sqlite)
                    self.assertEqual(column.postgresql_type, expected_postgresql)

    def test_render_is_deterministic_tracked_and_contains_only_create_tables(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 1)
        sqlite_sql = render_sqlite()
        postgresql_sql = render_postgresql()
        self.assertEqual(
            (ROOT / "schemas/phase6/sqlite.sql").read_text(encoding="utf-8"),
            sqlite_sql,
        )
        self.assertEqual(
            (ROOT / "schemas/phase6/postgresql.sql").read_text(encoding="utf-8"),
            postgresql_sql,
        )
        self.assertEqual(render_sqlite(), sqlite_sql)
        self.assertEqual(render_postgresql(), postgresql_sql)
        self.assertTrue(sqlite_sql.startswith("PRAGMA foreign_keys = ON;\n\n"))
        self.assertNotIn("PRAGMA", postgresql_sql.upper())
        for dialect, sql in (("sqlite", sqlite_sql), ("postgresql", postgresql_sql)):
            with self.subTest(dialect=dialect):
                self.assertTrue(sql.endswith("\n"))
                self.assertEqual(len(re.findall(r"(?m)^CREATE TABLE ", sql)), 11)
                self.assertEqual(sql.count(";"), 12 if dialect == "sqlite" else 11)
                self.assertIsNone(
                    re.search(
                        r"\b(?:INSERT|UPDATE|DELETE|MERGE|CREATE\s+TRIGGER|"
                        r"CREATE\s+EXTENSION|CREATE\s+FUNCTION)\b",
                        sql,
                        re.IGNORECASE,
                    )
                )
                self.assertNotRegex(sql, r"(?i)\bCASCADE\b")
                digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                self.assertEqual(schema_hash(dialect), digest)
                self.assertRegex(digest, r"\A[0-9a-f]{64}\Z")
        with self.assertRaises(ValueError):
            schema_hash("mysql")

    def test_postgresql_contains_exact_contract_constraints_columns_and_nullability(self) -> None:
        self.assert_postgresql_contract_exact(render_postgresql())

    def test_postgresql_exact_oracle_rejects_additional_restrictions(self) -> None:
        sql = render_postgresql()
        expected = "    method text NOT NULL CHECK (method IN ('pix', 'wise', 'stripe'))"
        mutated = sql.replace(
            expected,
            expected + " CHECK (method = 'pix')",
            1,
        )
        self.assertNotEqual(mutated, sql)
        with self.assertRaises(AssertionError):
            self.assert_postgresql_contract_exact(mutated)

    def test_sqlite_executes_strict_with_exact_columns_types_and_nullability(self) -> None:
        connection = self.open_database()
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone(), (1,))
        names = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid"
            )
        )
        self.assertEqual(names, tuple(EXPECTED_COLUMNS))
        self.assertEqual(render_sqlite().count(") STRICT;"), 11)
        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            with self.subTest(table=table_name):
                info = list(connection.execute(f"PRAGMA table_info('{table_name}')"))
                self.assertEqual(tuple(row[1] for row in info), expected_columns)
                self.assertEqual(
                    {row[1]: row[2] for row in info},
                    {
                        column_name: (
                            "INTEGER"
                            if column_name in INTEGER_COLUMNS[table_name]
                            else "TEXT"
                        )
                        for column_name in expected_columns
                    },
                )
                nullable = {row[1] for row in info if row[3] == 0}
                self.assertEqual(nullable, EXPECTED_NULLABLE[table_name])

    def test_primary_foreign_unique_keys_and_fk_groups_are_exact(self) -> None:
        connection = self.open_database()
        domains = {
            "handoff": set(tuple(EXPECTED_COLUMNS)[:4]),
            "payment": set(tuple(EXPECTED_COLUMNS)[4:]),
        }
        for table_name in EXPECTED_COLUMNS:
            with self.subTest(table=table_name):
                info = list(connection.execute(f"PRAGMA table_info('{table_name}')"))
                primary_key = tuple(
                    row[1]
                    for row in sorted(info, key=lambda row: row[5])
                    if row[5]
                )
                self.assertEqual(primary_key, EXPECTED_PRIMARY_KEYS[table_name])
                by_name = {row[1]: row for row in info}
                self.assertTrue(all(by_name[name][3] == 1 for name in primary_key))

                fk_rows = list(
                    connection.execute(f"PRAGMA foreign_key_list('{table_name}')")
                )
                foreign_keys = {(row[3], row[2], row[4]) for row in fk_rows}
                self.assertEqual(foreign_keys, EXPECTED_FOREIGN_KEYS[table_name])
                grouped: dict[int, list[tuple[int, str, str, str]]] = {}
                for row in fk_rows:
                    grouped.setdefault(row[0], []).append(
                        (row[1], row[2], row[3], row[4])
                    )
                groups = {
                    (
                        tuple(item[2] for item in sorted(items)),
                        items[0][1],
                        tuple(item[3] for item in sorted(items)),
                    )
                    for items in grouped.values()
                }
                self.assertEqual(groups, EXPECTED_FOREIGN_KEY_GROUPS[table_name])
                self.assertTrue(
                    all(row[5] == "NO ACTION" and row[6] == "NO ACTION" for row in fk_rows)
                )
                own_domain = domains["handoff"] if table_name.startswith("handoff_") else domains["payment"]
                self.assertTrue(all(row[2] in own_domain for row in fk_rows))

                unique_columns: set[tuple[str, ...]] = set()
                for index_row in connection.execute(
                    f"PRAGMA index_list('{table_name}')"
                ):
                    if index_row[2] and index_row[3] == "u":
                        unique_columns.add(
                            tuple(
                                row[2]
                                for row in connection.execute(
                                    f"PRAGMA index_info('{index_row[1]}')"
                                )
                            )
                        )
                self.assertEqual(unique_columns, EXPECTED_UNIQUES[table_name])

        self.populate_all_tables(connection)
        self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
        self.assertEqual(
            list(connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")),
            [],
        )

    def test_fk_closure_rejects_orphans_in_each_domain(self) -> None:
        connection = self.open_database()
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO handoff_events "
                "(event_id, handoff_id, revision, event_type, event_json, event_hash, "
                "occurred_at) VALUES ('handoff:event:orphan', 'handoff:missing', 1, "
                "'HandoffRequested', '{}', ?, ?)",
                (HASH_A, NOW),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO payment_events "
                "(event_id, payment_id, revision, payment_version, economic_signature, "
                "event_type, event_json, event_hash, occurred_at) VALUES "
                "('payment:event:orphan', 'payment:missing', 1, 1, ?, "
                "'PaymentMethodSelected', '{}', ?, ?)",
                (HASH_A, HASH_B, NOW),
            )

    def test_named_incident_event_outbox_receipt_uniques_fail_closed(self) -> None:
        connection = self.open_database()
        first = self.insert_handoff_workflow(
            connection, "unique-1", incident_key="incident:shared"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_handoff_workflow(
                connection, "unique-2", incident_key="incident:shared"
            )
        connection.execute(
            "INSERT INTO handoff_events "
            "(event_id, handoff_id, revision, event_type, event_json, event_hash, "
            "occurred_at) VALUES ('handoff:event:unique', ?, 1, 'HandoffRequested', "
            "'{}', ?, ?)",
            (first, HASH_A, NOW),
        )
        for event_id in ("handoff:event:unique", "handoff:event:other"):
            with self.subTest(event_id=event_id), self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO handoff_events "
                    "(event_id, handoff_id, revision, event_type, event_json, event_hash, "
                    "occurred_at) VALUES (?, ?, 1, 'HandoffRequested', '{}', ?, ?)",
                    (event_id, first, HASH_B, NOW),
                )

        self.insert_handoff_outbox(
            connection,
            first,
            "unique-1",
            idempotency_key="handoff:idem:shared",
            effect_id="handoff:effect:shared",
            status="delivered",
            fencing_token=1,
            delivery_attempts=1,
            delivered_at=NOW,
            receipt_hash=HASH_A,
        )
        for suffix, override in (
            ("unique-2", {"idempotency_key": "handoff:idem:shared"}),
            ("unique-3", {"effect_id": "handoff:effect:shared"}),
        ):
            with self.subTest(override=override), self.assertRaises(sqlite3.IntegrityError):
                self.insert_handoff_outbox(connection, first, suffix, **override)

        message_one = "handoff:message:unique-1"
        connection.execute(
            "INSERT INTO handoff_receipts "
            "(receipt_id, idempotency_key, message_id, receipt_json, receipt_hash, "
            "delivered_at) VALUES ('handoff:receipt:1', 'handoff:receipt:idem:shared', "
            "?, '{}', ?, ?)",
            (message_one, HASH_A, NOW),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO handoff_receipts "
                "(receipt_id, idempotency_key, message_id, receipt_json, "
                "receipt_hash, delivered_at) VALUES (?, ?, ?, '{}', ?, ?)",
                ("handoff:receipt:2", "handoff:receipt:idem:other", message_one, HASH_A, NOW),
            )
        message_two = self.insert_handoff_outbox(
            connection,
            first,
            "unique-receipt-2",
            status="delivered",
            fencing_token=1,
            delivery_attempts=1,
            delivered_at=LATER,
            receipt_hash=HASH_B,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO handoff_receipts "
                "(receipt_id, idempotency_key, message_id, receipt_json, "
                "receipt_hash, delivered_at) VALUES (?, ?, ?, '{}', ?, ?)",
                (
                    "handoff:receipt:3",
                    "handoff:receipt:idem:shared",
                    message_two,
                    HASH_B,
                    LATER,
                ),
            )

    def test_payment_claim_command_subject_outbox_receipt_uniques_fail_closed(self) -> None:
        connection = self.open_database()
        payment_one = self.insert_payment_workflow(connection, "unique-payment-1")
        payment_two = self.insert_payment_workflow(connection, "unique-payment-2")
        claim = self.insert_claim(
            connection,
            payment_one,
            "unique-payment-1",
            claim_key="pix:global:evidence:shared",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_claim(
                connection,
                payment_two,
                "unique-payment-2",
                claim_key="pix:global:evidence:shared",
            )
        command = self.insert_command(
            connection,
            payment_one,
            claim,
            "unique-command-1",
            idempotency_key="payment:command:idem:shared",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_command(
                connection,
                payment_one,
                claim,
                "unique-command-subject",
                settlement_command_id="payment:command:subject-other",
                idempotency_key="payment:command:idem:subject-other",
            )

        claim_two = self.insert_claim(
            connection,
            payment_two,
            "unique-payment-2-valid-claim",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_command(
                connection,
                payment_two,
                claim_two,
                "unique-command-idempotency",
                settlement_command_id="payment:command:idem-other",
                idempotency_key="payment:command:idem:shared",
            )

        payment_three = self.insert_payment_workflow(connection, "unique-payment-3")
        claim_three = self.insert_claim(
            connection,
            payment_three,
            "unique-payment-3-valid-claim",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_command(
                connection,
                payment_three,
                claim_three,
                "unique-command-id",
                settlement_command_id=command,
                idempotency_key="payment:command:idem:id-other",
            )

        self.insert_payment_outbox(
            connection,
            payment_one,
            command,
            "unique-outbox-1",
            idempotency_key="payment:outbox:idem:shared",
            effect_id="payment:effect:shared",
            status="delivered",
            fencing_token=1,
            delivery_attempts=1,
            delivered_at=NOW,
            receipt_hash=HASH_A,
        )
        for suffix, overrides in (
            ("unique-outbox-2", {"idempotency_key": "payment:outbox:idem:shared"}),
            ("unique-outbox-3", {"effect_id": "payment:effect:shared"}),
        ):
            with self.subTest(overrides=overrides), self.assertRaises(sqlite3.IntegrityError):
                self.insert_payment_outbox(
                    connection, payment_one, command, suffix, **overrides
                )

        message = "payment:message:unique-outbox-1"
        connection.execute(
            "INSERT INTO payment_receipts "
            "(receipt_id, idempotency_key, message_id, receipt_json, receipt_hash, "
            "delivered_at) VALUES ('payment:receipt:1', 'payment:receipt:idem:shared', "
            "?, '{}', ?, ?)",
            (message, HASH_A, NOW),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO payment_receipts "
                "(receipt_id, idempotency_key, message_id, receipt_json, "
                "receipt_hash, delivered_at) VALUES (?, ?, ?, '{}', ?, ?)",
                ("payment:receipt:2", "payment:receipt:idem:other", message, HASH_A, NOW),
            )
        message_two = self.insert_payment_outbox(
            connection,
            payment_one,
            command,
            "unique-receipt-2",
            status="delivered",
            fencing_token=1,
            delivery_attempts=1,
            delivered_at=LATER,
            receipt_hash=HASH_B,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO payment_receipts "
                "(receipt_id, idempotency_key, message_id, receipt_json, "
                "receipt_hash, delivered_at) VALUES (?, ?, ?, '{}', ?, ?)",
                (
                    "payment:receipt:3",
                    "payment:receipt:idem:shared",
                    message_two,
                    HASH_B,
                    LATER,
                ),
            )

    def test_receipts_require_an_exact_delivered_outbox_snapshot(self) -> None:
        connection = self.open_database()

        handoff_id = self.insert_handoff_workflow(connection, "receipt-binding")
        pending_handoff = self.insert_handoff_outbox(
            connection,
            handoff_id,
            "receipt-binding-pending",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO handoff_receipts "
                "(receipt_id, idempotency_key, message_id, receipt_json, receipt_hash, "
                "delivered_at) VALUES ('handoff:receipt:pending', "
                "'handoff:receipt:pending:idem', ?, '{}', ?, ?)",
                (pending_handoff, HASH_A, NOW),
            )
        delivered_handoff = self.insert_handoff_outbox(
            connection,
            handoff_id,
            "receipt-binding-delivered",
            status="delivered",
            fencing_token=1,
            delivery_attempts=1,
            delivered_at=LATER,
            receipt_hash=HASH_B,
        )
        for suffix, receipt_hash, delivered_at in (
            ("wrong-hash", HASH_A, LATER),
            ("wrong-time", HASH_B, NOW),
        ):
            with self.subTest(domain="handoff", suffix=suffix):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO handoff_receipts "
                        "(receipt_id, idempotency_key, message_id, receipt_json, "
                        "receipt_hash, delivered_at) VALUES (?, ?, ?, '{}', ?, ?)",
                        (
                            f"handoff:receipt:{suffix}",
                            f"handoff:receipt:{suffix}:idem",
                            delivered_handoff,
                            receipt_hash,
                            delivered_at,
                        ),
                    )
        connection.execute(
            "INSERT INTO handoff_receipts "
            "(receipt_id, idempotency_key, message_id, receipt_json, receipt_hash, "
            "delivered_at) VALUES ('handoff:receipt:matching', "
            "'handoff:receipt:matching:idem', ?, '{}', ?, ?)",
            (delivered_handoff, HASH_B, LATER),
        )

        payment_id, _, command_id = self.create_payment_command_graph(
            connection,
            "receipt-binding",
        )
        pending_payment = self.insert_payment_outbox(
            connection,
            payment_id,
            command_id,
            "receipt-binding-pending",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO payment_receipts "
                "(receipt_id, idempotency_key, message_id, receipt_json, receipt_hash, "
                "delivered_at) VALUES ('payment:receipt:pending', "
                "'payment:receipt:pending:idem', ?, '{}', ?, ?)",
                (pending_payment, HASH_A, NOW),
            )
        delivered_payment = self.insert_payment_outbox(
            connection,
            payment_id,
            command_id,
            "receipt-binding-delivered",
            status="delivered",
            fencing_token=1,
            delivery_attempts=1,
            delivered_at=LATER,
            receipt_hash=HASH_B,
        )
        for suffix, receipt_hash, delivered_at in (
            ("wrong-hash", HASH_A, LATER),
            ("wrong-time", HASH_B, NOW),
        ):
            with self.subTest(domain="payment", suffix=suffix):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO payment_receipts "
                        "(receipt_id, idempotency_key, message_id, receipt_json, "
                        "receipt_hash, delivered_at) VALUES (?, ?, ?, '{}', ?, ?)",
                        (
                            f"payment:receipt:{suffix}",
                            f"payment:receipt:{suffix}:idem",
                            delivered_payment,
                            receipt_hash,
                            delivered_at,
                        ),
                    )
        connection.execute(
            "INSERT INTO payment_receipts "
            "(receipt_id, idempotency_key, message_id, receipt_json, receipt_hash, "
            "delivered_at) VALUES ('payment:receipt:matching', "
            "'payment:receipt:matching:idem', ?, '{}', ?, ?)",
            (delivered_payment, HASH_B, LATER),
        )

    def test_closed_status_kind_method_operation_and_certainty_checks_match_enums(self) -> None:
        checks = {
            ("handoff_workflows", "status"): {item.value for item in HandoffStatus},
            ("handoff_outbox", "kind"): {item.value for item in HandoffEffectKind},
            ("handoff_outbox", "status"): {"pending", "leased", "delivered"},
            ("payment_workflows", "status"): {item.value for item in PaymentStatus},
            ("payment_evidence_claims", "method"): {
                item.value for item in PaymentMethod
            },
            ("payment_evidence_claims", "status"): {
                "in_progress",
                "completed",
                "retryable",
                "manual_review",
            },
            ("payment_commands", "operation"): {
                item.value for item in SettlementOperation
            },
            ("payment_ledger", "status"): {
                "queued",
                "leased",
                "dispatch_fenced",
                "outcome_recorded",
                "manual_review",
            },
            ("payment_ledger", "outcome_certainty"): {
                item.value for item in SettlementCertainty
            },
            ("payment_outbox", "kind"): {
                item.value for item in PaymentEffectKind
            },
            ("payment_outbox", "status"): {"pending", "leased", "delivered"},
        }
        for (table_name, column_name), expected in checks.items():
            table = next(t for t in schema_contract() if t.name == table_name)
            column = next(c for c in table.columns if c.name == column_name)
            with self.subTest(table=table_name, column=column_name):
                self.assertEqual(
                    set(re.findall(r"'([^']+)'", column.check or "")), expected
                )

        connection = self.open_database()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_handoff_workflow(connection, "unknown", status="unknown")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_payment_workflow(connection, "unknown", status="unknown")
        payment_id = self.insert_payment_workflow(connection, "bad-method")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_claim(
                connection, payment_id, "bad-method", method="cash"
            )

    def test_every_workflow_status_and_outbox_kind_has_a_valid_row(self) -> None:
        connection = self.open_database()
        for index, status in enumerate(HandoffStatus):
            self.insert_handoff_workflow(
                connection, f"handoff-status-{index}", status=status.value
            )
        handoff_id = self.insert_handoff_workflow(connection, "handoff-kinds")
        for index, kind in enumerate(HandoffEffectKind):
            self.insert_handoff_outbox(
                connection, handoff_id, f"handoff-kind-{index}", kind=kind.value
            )
        for index, status in enumerate(PaymentStatus):
            self.insert_payment_workflow(
                connection, f"payment-status-{index}", status=status.value
            )
        method_payment = self.insert_payment_workflow(connection, "payment-methods")
        method_claim_keys = {
            PaymentMethod.PIX: "pix:E1234567820270201ABCDEFGHIJK",
            PaymentMethod.WISE: "wise:1234567890abcdef1234567890abcdef",
            PaymentMethod.STRIPE: "stripe:account-profile:evt_1234567890abcdef",
        }
        for index, method in enumerate(PaymentMethod):
            self.insert_claim(
                connection,
                method_payment,
                f"payment-method-{index}",
                method=method.value,
                claim_key=method_claim_keys[method],
            )
        payment_id, _, command_id = self.create_payment_command_graph(
            connection, "payment-kinds"
        )
        for index, kind in enumerate(PaymentEffectKind):
            self.insert_payment_outbox(
                connection,
                payment_id,
                command_id,
                f"payment-kind-{index}",
                kind=kind.value,
            )

    def test_claim_lifecycle_is_closed(self) -> None:
        connection = self.open_database()
        payment_id = self.insert_payment_workflow(connection, "claim-life")
        for index, (status, consumed_at) in enumerate(
            (
                ("in_progress", None),
                ("retryable", None),
                ("completed", LATER),
                ("manual_review", LATER),
            )
        ):
            self.insert_claim(
                connection,
                payment_id,
                f"valid-{index}",
                status=status,
                consumed_at=consumed_at,
            )
        for index, overrides in enumerate(
            (
                {"status": "in_progress", "consumed_at": LATER},
                {"status": "retryable", "consumed_at": LATER},
                {"status": "completed", "consumed_at": None},
                {"status": "manual_review", "consumed_at": None},
                {
                    "status": "completed",
                    "consumed_at": "2026-12-31T23:59:59+00:00",
                },
                {"status": "unknown", "consumed_at": None},
            )
        ):
            with self.subTest(overrides=overrides), self.assertRaises(sqlite3.IntegrityError):
                self.insert_claim(
                    connection,
                    payment_id,
                    f"claim-life-invalid-{index}",
                    **overrides,
                )

    def test_delivered_handoff_retains_historical_claim_owner_without_live_lease(self) -> None:
        connection = self.open_database()
        handoff_id = self.insert_handoff_workflow(connection, "delivered-owner")
        try:
            self.insert_handoff_outbox(
                connection,
                handoff_id,
                "delivered-owner-valid",
                status="delivered",
                claim_owner="handoff-claim:" + "a" * 64,
                fencing_token=1,
                delivery_attempts=1,
                delivered_at=LATER,
                receipt_hash=HASH_D,
            )
        except sqlite3.IntegrityError as exc:
            self.fail(f"historical delivered owner was rejected: {exc}")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_handoff_outbox(
                connection,
                handoff_id,
                "delivered-owner-missing",
                status="delivered",
                claim_owner=None,
                fencing_token=1,
                delivery_attempts=1,
                delivered_at=LATER,
                receipt_hash=HASH_D,
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_handoff_outbox(
                connection,
                handoff_id,
                "delivered-owner-live-lease",
                status="delivered",
                claim_owner="handoff-claim:" + "b" * 64,
                fencing_token=1,
                lease_acquired_at=NOW,
                lease_expires_at=LATER,
                delivery_attempts=1,
                delivered_at=LATER,
                receipt_hash=HASH_D,
            )

    def test_handoff_fencing_token_cannot_advance_without_delivery_attempt(self) -> None:
        connection = self.open_database()
        handoff_id = self.insert_handoff_workflow(connection, "equal-counters")
        invalid_rows = (
            {
                "status": "leased",
                "claim_owner": "handoff-claim:" + "d" * 64,
                "fencing_token": 2,
                "lease_acquired_at": NOW,
                "lease_expires_at": LATER,
                "delivery_attempts": 1,
            },
            {
                "status": "delivered",
                "claim_owner": "handoff-claim:" + "e" * 64,
                "fencing_token": 2,
                "delivery_attempts": 1,
                "delivered_at": LATER,
                "receipt_hash": HASH_D,
            },
        )
        for index, overrides in enumerate(invalid_rows):
            with self.subTest(status=overrides["status"]), self.assertRaises(
                sqlite3.IntegrityError
            ):
                self.insert_handoff_outbox(
                    connection,
                    handoff_id,
                    f"unequal-counters-{index}",
                    **overrides,
                )

    def test_handoff_and_payment_outbox_lease_receipt_status_matrices(self) -> None:
        connection = self.open_database()
        handoff_id = self.insert_handoff_workflow(connection, "outbox-matrix")
        payment_id, _, command_id = self.create_payment_command_graph(
            connection, "outbox-matrix"
        )
        valid = (
            {},
            {
                "status": "leased",
                "claim_owner": "worker:schema:leased",
                "fencing_token": 1,
                "lease_acquired_at": NOW,
                "lease_expires_at": LATER,
                "delivery_attempts": 1,
            },
            {
                "status": "delivered",
                "fencing_token": 1,
                "delivery_attempts": 1,
                "delivered_at": LATER,
                "receipt_hash": HASH_D,
            },
        )
        for index, overrides in enumerate(valid):
            handoff_overrides = dict(overrides)
            if handoff_overrides.get("status") == "delivered":
                handoff_overrides["claim_owner"] = "handoff-claim:" + "c" * 64
            self.insert_handoff_outbox(
                connection,
                handoff_id,
                f"handoff-valid-{index}",
                **handoff_overrides,
            )
            self.insert_payment_outbox(
                connection,
                payment_id,
                command_id,
                f"payment-valid-{index}",
                **overrides,
            )

        invalid = (
            {"status": "leased", "claim_owner": "worker:partial"},
            {
                "status": "leased",
                "claim_owner": "worker:zero",
                "fencing_token": 0,
                "lease_acquired_at": NOW,
                "lease_expires_at": LATER,
            },
            {
                "status": "leased",
                "claim_owner": "worker:reverse",
                "fencing_token": 1,
                "lease_acquired_at": LATER,
                "lease_expires_at": NOW,
            },
            {
                "status": "pending",
                "claim_owner": "worker:pending",
                "fencing_token": 1,
                "lease_acquired_at": NOW,
                "lease_expires_at": LATER,
            },
            {"status": "delivered", "delivery_attempts": 1, "delivered_at": LATER},
            {"status": "delivered", "delivery_attempts": 1, "receipt_hash": HASH_D},
            {
                "status": "delivered",
                "fencing_token": 0,
                "delivery_attempts": 1,
                "delivered_at": LATER,
                "receipt_hash": HASH_D,
            },
            {
                "status": "delivered",
                "fencing_token": 1,
                "delivery_attempts": 0,
                "delivered_at": LATER,
                "receipt_hash": HASH_D,
            },
        )
        for index, overrides in enumerate(invalid):
            with self.subTest(domain="handoff", overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_handoff_outbox(
                        connection,
                        handoff_id,
                        f"handoff-invalid-{index}",
                        **overrides,
                    )
            with self.subTest(domain="payment", overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_payment_outbox(
                        connection,
                        payment_id,
                        command_id,
                        f"payment-invalid-{index}",
                        **overrides,
                    )

    def test_payment_ledger_accepts_closed_one_slot_outcome_fence_matrix(self) -> None:
        connection = self.open_database()
        lease = {
            "claim_owner": "worker:payment:1",
            "fencing_token": 1,
            "lease_acquired_at": NOW,
            "lease_expires_at": LATER,
            "claim_count": 1,
        }
        dispatch = {
            "fencing_token": 1,
            "claim_count": 1,
            "dispatch_slots_consumed": 1,
            "dispatch_request_hash": HASH_B,
            "dispatch_fenced_at": NOW,
        }
        outcome = {
            "outcome_json": "{}",
            "outcome_hash": HASH_C,
            "outcome_recorded_at": LATER,
        }
        cases = (
            ("queued", {}),
            ("leased", lease),
            ("dispatch_fenced", {**lease, **dispatch}),
            (
                "outcome_recorded",
                {"outcome_certainty": "not_dispatched", **outcome},
            ),
            (
                "outcome_recorded",
                {**dispatch, "outcome_certainty": "settled", **outcome},
            ),
            (
                "manual_review",
                {**dispatch, "outcome_certainty": "dispatched_no_effect", **outcome},
            ),
            (
                "manual_review",
                {**dispatch, "outcome_certainty": "partial_settlement", **outcome},
            ),
            (
                "manual_review",
                {**dispatch, "outcome_certainty": "dispatched_unknown", **outcome},
            ),
        )
        for index, (status, overrides) in enumerate(cases):
            payment_id, _, command_id = self.create_payment_command_graph(
                connection, f"ledger-valid-{index}"
            )
            self.insert_ledger(
                connection, payment_id, command_id, status=status, **overrides
            )

    def test_payment_ledger_rejects_invalid_lease_slot_outcome_fence_matrix(self) -> None:
        connection = self.open_database()
        lease = {
            "claim_owner": "worker:payment:1",
            "fencing_token": 1,
            "lease_acquired_at": NOW,
            "lease_expires_at": LATER,
            "claim_count": 1,
        }
        dispatch = {
            "fencing_token": 1,
            "claim_count": 1,
            "dispatch_slots_consumed": 1,
            "dispatch_request_hash": HASH_B,
            "dispatch_fenced_at": NOW,
        }
        outcome = {
            "outcome_json": "{}",
            "outcome_hash": HASH_C,
            "outcome_recorded_at": LATER,
        }
        invalid = (
            {"status": "leased", "claim_owner": "worker:partial", "claim_count": 1},
            {"status": "leased", **lease, "fencing_token": 0},
            {
                "status": "leased",
                **lease,
                "lease_acquired_at": LATER,
                "lease_expires_at": NOW,
            },
            {"dispatch_slots_consumed": 1},
            {"dispatch_request_hash": HASH_B},
            {"dispatch_fenced_at": NOW},
            {"status": "dispatch_fenced", **lease, "dispatch_slots_consumed": 1},
            {"status": "dispatch_fenced", **lease, **dispatch, "fencing_token": 0},
            {"status": "outcome_recorded"},
            {"status": "outcome_recorded", "outcome_certainty": "not_dispatched"},
            {"status": "outcome_recorded", "outcome_json": "{}"},
            {"status": "outcome_recorded", "outcome_hash": HASH_C},
            {"status": "outcome_recorded", "outcome_recorded_at": LATER},
            {
                "status": "outcome_recorded",
                "outcome_certainty": "settled",
                **outcome,
            },
            {
                "status": "outcome_recorded",
                **dispatch,
                "outcome_certainty": "not_dispatched",
                **outcome,
            },
            {
                "status": "manual_review",
                "outcome_certainty": "dispatched_unknown",
                **outcome,
            },
            {"status": "manual_review", **dispatch},
            {
                "status": "manual_review",
                **dispatch,
                "outcome_certainty": "settled",
                **outcome,
            },
            {"status": "queued", **lease},
            {"status": "queued", **dispatch},
            {
                "status": "queued",
                "outcome_certainty": "not_dispatched",
                **outcome,
            },
        )
        for index, overrides in enumerate(invalid):
            payment_id, _, command_id = self.create_payment_command_graph(
                connection, f"ledger-invalid-{index}"
            )
            with self.subTest(index=index, overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_ledger(
                        connection, payment_id, command_id, **overrides
                    )

    def test_fencing_tokens_allow_zero_at_rest_but_require_positive_active_or_fenced(self) -> None:
        connection = self.open_database()
        handoff_id = self.insert_handoff_workflow(connection, "fence-rest")
        self.insert_handoff_outbox(
            connection, handoff_id, "fence-rest", fencing_token=0
        )
        payment_id, _, command_id = self.create_payment_command_graph(
            connection, "fence-rest"
        )
        self.insert_ledger(
            connection, payment_id, command_id, fencing_token=0
        )
        self.insert_payment_outbox(
            connection, payment_id, command_id, "fence-rest", fencing_token=0
        )
        for table_name in ("handoff_outbox", "payment_ledger", "payment_outbox"):
            with self.subTest(table=table_name), self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    f"UPDATE {table_name} SET fencing_token = -1"
                )

    def test_fencing_tokens_preserve_claim_and_delivery_history(self) -> None:
        connection = self.open_database()
        for index, overrides in enumerate(
            (
                {"claim_count": 2, "fencing_token": 0},
                {"claim_count": 0, "fencing_token": 2},
            )
        ):
            payment_id, _, command_id = self.create_payment_command_graph(
                connection,
                f"fence-history-invalid-{index}",
            )
            with self.subTest(ledger=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_ledger(
                        connection,
                        payment_id,
                        command_id,
                        **overrides,
                    )
        payment_id, _, command_id = self.create_payment_command_graph(
            connection,
            "fence-history-valid",
        )
        self.insert_ledger(
            connection,
            payment_id,
            command_id,
            claim_count=2,
            fencing_token=2,
        )

        handoff_id = self.insert_handoff_workflow(connection, "delivery-history")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_handoff_outbox(
                connection,
                handoff_id,
                "delivery-history-invalid",
                delivery_attempts=2,
                fencing_token=0,
            )
        self.insert_handoff_outbox(
            connection,
            handoff_id,
            "delivery-history-valid",
            delivery_attempts=2,
            fencing_token=2,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_payment_outbox(
                connection,
                payment_id,
                command_id,
                "delivery-history-invalid",
                delivery_attempts=2,
                fencing_token=0,
            )
        self.insert_payment_outbox(
            connection,
            payment_id,
            command_id,
            "delivery-history-valid",
            delivery_attempts=2,
            fencing_token=2,
        )

    def test_every_integer_column_rejects_fractional_affinity(self) -> None:
        connection = self.open_database()
        identities = self.populate_all_tables(connection)
        for table_name, integer_columns in INTEGER_COLUMNS.items():
            primary = EXPECTED_PRIMARY_KEYS[table_name][0]
            for column_name in sorted(integer_columns):
                with self.subTest(table=table_name, column=column_name):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            f"UPDATE {table_name} SET {column_name} = 0.5 "
                            f"WHERE {primary} = ?",
                            (identities[table_name],),
                        )

    def test_all_hash_columns_are_closed_and_reject_null_or_embedded_nul(self) -> None:
        contract = schema_contract()
        self.assertEqual(
            {
                table.name: {
                    column.name
                    for column in table.columns
                    if column.check is not None
                    and f"length({column.name}) = 64" in column.check
                }
                for table in contract
            },
            HASH_COLUMNS,
        )
        for table in contract:
            for column in table.columns:
                if column.name in HASH_COLUMNS[table.name]:
                    with self.subTest(table=table.name, column=column.name):
                        check = column.check or ""
                        self.assertIn("lower(", check)
                        self.assertIn("replace(", check)
                        self.assertNotIn("GLOB", check.upper())
                        self.assertNotIn("REGEXP", check.upper())

        connection = self.open_database()
        identities = self.populate_all_tables(connection)
        for table in contract:
            primary = EXPECTED_PRIMARY_KEYS[table.name][0]
            nullable = EXPECTED_NULLABLE[table.name]
            for column_name in HASH_COLUMNS[table.name]:
                invalid = ["x" * 64, "A" * 64, HASH_A + "\x00junk"]
                if column_name not in nullable:
                    invalid.append(None)
                self.assert_isolated_sqlite_column_rejects_all(
                    table.name,
                    column_name,
                    invalid,
                )
                for value in invalid:
                    with self.subTest(
                        table=table.name, column=column_name, value=value
                    ):
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(
                                f"UPDATE {table.name} SET {column_name} = ? "
                                f"WHERE {primary} = ?",
                                (value, identities[table.name]),
                            )

    def test_all_sqlite_text_rejects_embedded_nul_and_payloads_reject_empty(self) -> None:
        contract = schema_contract()
        text_count = sum(
            column.sqlite_type == "TEXT"
            for table in contract
            for column in table.columns
        )
        self.assertEqual(render_sqlite().count("CHECK (instr("), text_count)
        connection = self.open_database()
        identities = self.populate_all_tables(connection)
        for table in contract:
            primary = EXPECTED_PRIMARY_KEYS[table.name][0]
            for column in table.columns:
                if column.sqlite_type != "TEXT":
                    continue
                with self.subTest(table=table.name, column=column.name):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            f"UPDATE {table.name} SET {column.name} = ? "
                            f"WHERE {primary} = ?",
                            ("safe\x00unsafe", identities[table.name]),
                        )
        self.assertEqual(
            {
                table.name: {
                    column.name
                    for column in table.columns
                    if column.check == f"length({column.name}) >= 1"
                }
                for table in contract
            },
            NONEMPTY_COLUMNS,
        )
        for table_name, column_names in NONEMPTY_COLUMNS.items():
            for column_name in sorted(column_names):
                self.assert_isolated_sqlite_column_rejects(
                    table_name,
                    column_name,
                    "",
                )

    def test_all_timestamps_have_closed_sqlite_shape_calendar_and_nul_guards(self) -> None:
        contract = schema_contract()
        self.assertEqual(
            {
                table.name: {
                    column.name
                    for column in table.columns
                    if column.postgresql_type == "timestamptz"
                }
                for table in contract
            },
            TIMESTAMP_COLUMNS,
        )
        connection = self.open_database()
        identities = self.populate_all_tables(connection)
        malformed = (
            "2027-01-01T00:00:00Z",
            "junk+00:00",
            "2027-01-01 00:00:00+00:00",
            "0000-01-01T00:00:00+00:00",
            "2027-13-01T00:00:00+00:00",
            "2027-04-31T00:00:00+00:00",
            "2027-02-29T00:00:00+00:00",
            "2027-01-01T24:00:00+00:00",
            "2027-01-01T23:60:00+00:00",
            "2027-01-01T23:59:60+00:00",
            "2027-01-01T00:00:00.000000+00:00",
            NOW + "\x00junk",
        )
        for table in contract:
            primary = EXPECTED_PRIMARY_KEYS[table.name][0]
            for column_name in TIMESTAMP_COLUMNS[table.name]:
                self.assert_isolated_sqlite_column_rejects_all(
                    table.name,
                    column_name,
                    list(malformed),
                )
                for value in malformed:
                    with self.subTest(
                        table=table.name, column=column_name, value=value
                    ):
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(
                                f"UPDATE {table.name} SET {column_name} = ? "
                                f"WHERE {primary} = ?",
                                (value, identities[table.name]),
                            )
        connection.execute(
            "UPDATE handoff_workflows SET created_at = ?",
            ("2028-02-29T23:59:59+00:00",),
        )
        connection.execute(
            "UPDATE handoff_workflows SET updated_at = ?",
            ("2027-01-01T00:00:00.000001+00:00",),
        )

    def test_command_payload_hash_signature_and_composite_claim_binding_fail_closed(self) -> None:
        connection = self.open_database()
        payment_one = self.insert_payment_workflow(connection, "binding-one")
        payment_two = self.insert_payment_workflow(connection, "binding-two")
        claim = self.insert_claim(connection, payment_one, "binding-one")
        for index, overrides in enumerate(
            (
                {"command_json": ""},
                {"command_json": None},
                {"command_hash": None},
                {"economic_signature": None},
                {"operation": "unknown"},
                {"payment_id": payment_two},
                {"payment_version": 2},
                {"economic_signature": HASH_D},
            )
        ):
            with self.subTest(overrides=overrides), self.assertRaises(sqlite3.IntegrityError):
                self.insert_command(
                    connection,
                    payment_one,
                    claim,
                    f"binding-invalid-{index}",
                    **overrides,
                )

    def test_separation_scanner_finds_no_cross_ledger_or_live_capability(self) -> None:
        sqlite_sql = render_sqlite()
        postgresql_sql = render_postgresql()
        forbidden_sql = (
            "reservation_commands",
            "execution_ledger",
            "outbox_messages",
            "schema_migrations",
            "CREATE TRIGGER",
        )
        for sql in (sqlite_sql, postgresql_sql):
            for forbidden in forbidden_sql:
                self.assertNotIn(forbidden.casefold(), sql.casefold())
            self.assertNotRegex(
                sql,
                r"(?i)\b(?:postgres(?:ql)?://|redis://|https?://|ATTACH)\b",
            )
        self.assertEqual(sqlite_sql.upper().count("PRAGMA"), 1)
        self.assertTrue(sqlite_sql.startswith("PRAGMA foreign_keys = ON;\n\n"))
        self.assertNotIn("PRAGMA", postgresql_sql.upper())

        forbidden_imports = {
            "sqlite3",
            "psycopg",
            "psycopg2",
            "sqlalchemy",
            "requests",
            "httpx",
            "socket",
            "subprocess",
        }
        for relative_path in (
            "reservation_followup/schema.py",
            "scripts/generate_phase6_schema.py",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            tree = ast.parse(source)
            imports: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.add((node.module or "").split(".")[0])
            with self.subTest(source=relative_path):
                self.assertTrue(imports.isdisjoint(forbidden_imports), imports)
        for table in schema_contract():
            for column in table.columns:
                self.assertFalse(column.name.startswith("reservation_"))

    def test_postgresql_is_static_text_only_and_never_claims_execution(self) -> None:
        sql = render_postgresql()
        self.assertIn(" bigint NOT NULL", sql)
        self.assertIn(" timestamptz NOT NULL", sql)
        self.assertNotRegex(
            sql,
            r"(?i)\b(?:PRAGMA|AUTOINCREMENT|GLOB|STRICT|INSTR)\b|CHAR\s*\(\s*0\s*\)",
        )
        self.assertNotIn("CREATE TYPE", sql.upper())
        self.assertNotIn("JSONB", sql.upper())
        generator = (ROOT / "scripts/generate_phase6_schema.py").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(
            generator,
            r"(?i)\b(?:psycopg|postgresql://|create_connection|connect\s*\()",
        )

    def test_generator_cli_writes_distinct_deterministic_targets_and_rejects_collision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-schema-") as directory:
            root = Path(directory)
            sqlite_target = root / "sqlite.sql"
            postgresql_target = root / "postgresql.sql"
            command = [
                sys.executable,
                str(ROOT / "scripts/generate_phase6_schema.py"),
                "--sqlite",
                str(sqlite_target),
                "--postgresql",
                str(postgresql_target),
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(sqlite_target.read_text(encoding="utf-8"), render_sqlite())
            self.assertEqual(
                postgresql_target.read_text(encoding="utf-8"), render_postgresql()
            )
            self.assertEqual(payload["sqlite"]["path"], str(sqlite_target))
            self.assertEqual(payload["postgresql"]["path"], str(postgresql_target))
            self.assertEqual(payload["sqlite"]["sha256"], schema_hash("sqlite"))
            self.assertEqual(
                payload["postgresql"]["sha256"], schema_hash("postgresql")
            )
            self.assertNotIn("postgresql_executed", payload)

            collision = root / "collision.sql"
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/generate_phase6_schema.py"),
                    "--sqlite",
                    str(collision),
                    "--postgresql",
                    str(root / "." / "collision.sql"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse(collision.exists())
            self.assertIn("distinct", rejected.stderr.lower())


if __name__ == "__main__":
    unittest.main()
