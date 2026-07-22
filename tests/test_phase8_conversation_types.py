"""Focused contracts for Phase 8 conversation wire types."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

import reservation_boundary.conversation as conversation
from reservation_boundary.conversation import SourceEventIdentity
from reservation_boundary.types import NormalizedMessage


DEADLINE = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _request_kwargs() -> dict[str, object]:
    boundary_state_bytes = b'{"state":"canonical"}'
    return {
        "boundary_state_bytes": boundary_state_bytes,
        "state_version": 7,
        "state_hash": hashlib.sha256(boundary_state_bytes).hexdigest(),
        "normalized_message": NormalizedMessage("hello", "en"),
        "aggregate_turn_id": "turn-001",
        "source_events": (
            SourceEventIdentity("manychat:event-001", "a" * 64),
            SourceEventIdentity("manychat:event-002", "b" * 64),
        ),
        "lead_key_hash": "c" * 64,
        "private_delivery_binding_hash": "d" * 64,
        "deadline_at": DEADLINE,
        "behavior_profile_fingerprint": "e" * 64,
    }


class Phase8ConversationTypeTests(unittest.TestCase):
    def test_source_event_identity_fields_and_canonical_hash_are_closed(self) -> None:
        identity = SourceEventIdentity(
            source_event_id="manychat:event-001",
            source_event_hash="a" * 64,
        )

        self.assertEqual(
            tuple(field.name for field in fields(SourceEventIdentity)),
            ("source_event_id", "source_event_hash"),
        )
        self.assertEqual(SourceEventIdentity.SCHEMA, "phase8-source-event-identity")
        self.assertEqual(SourceEventIdentity.VERSION, 1)
        self.assertEqual(SourceEventIdentity.DOMAIN, "phase8-source-event-identity-v1")
        self.assertEqual(
            identity.to_canonical_bytes(),
            b'{"data":{"source_event_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source_event_id":"manychat:event-001"},"schema":"phase8-source-event-identity","version":1}',
        )
        self.assertEqual(
            identity.canonical_hash(),
            hashlib.sha256(
                b"phase8-source-event-identity-v1\x00" + identity.to_canonical_bytes()
            ).hexdigest(),
        )

    def test_source_event_identity_rejects_noncanonical_identity_and_hash(self) -> None:
        invalid_values = (
            ("", "a" * 64),
            (" event-001", "a" * 64),
            ("event 001", "a" * 64),
            ("event-001", "A" * 64),
            ("event-001", "a" * 63),
            (1, "a" * 64),
            ("event-001", b"a" * 64),
        )
        for source_event_id, source_event_hash in invalid_values:
            with self.subTest(
                source_event_id=source_event_id,
                source_event_hash=source_event_hash,
            ):
                with self.assertRaises((TypeError, ValueError)):
                    SourceEventIdentity(
                        source_event_id=source_event_id,  # type: ignore[arg-type]
                        source_event_hash=source_event_hash,  # type: ignore[arg-type]
                    )

    def test_source_event_identity_is_frozen(self) -> None:
        identity = SourceEventIdentity("event-001", "b" * 64)

        with self.assertRaises(FrozenInstanceError):
            identity.source_event_id = "event-002"  # type: ignore[misc]

    def test_maya_turn_request_fields_and_canonical_hash_are_closed(self) -> None:
        request_type = getattr(conversation, "MayaTurnRequest", None)
        self.assertIsNotNone(request_type, "MayaTurnRequest must have an owner")
        assert request_type is not None
        request = request_type(**_request_kwargs())

        self.assertEqual(
            tuple(field.name for field in fields(request_type)),
            (
                "boundary_state_bytes",
                "state_version",
                "state_hash",
                "normalized_message",
                "aggregate_turn_id",
                "source_events",
                "lead_key_hash",
                "private_delivery_binding_hash",
                "deadline_at",
                "behavior_profile_fingerprint",
            ),
        )
        self.assertEqual(request_type.SCHEMA, "phase8-maya-turn-request")
        self.assertEqual(request_type.VERSION, 1)
        self.assertEqual(request_type.DOMAIN, "phase8-maya-turn-request-v1")
        expected = {
            "schema": "phase8-maya-turn-request",
            "version": 1,
            "data": {
                "aggregate_turn_id": "turn-001",
                "behavior_profile_fingerprint": "e" * 64,
                "boundary_state_bytes": "eyJzdGF0ZSI6ImNhbm9uaWNhbCJ9",
                "deadline_at": "2026-07-22T12:00:00+00:00",
                "lead_key_hash": "c" * 64,
                "normalized_message": {"locale": "en", "text": "hello"},
                "private_delivery_binding_hash": "d" * 64,
                "source_events": [
                    {
                        "schema": "phase8-source-event-identity",
                        "version": 1,
                        "data": {
                            "source_event_hash": "a" * 64,
                            "source_event_id": "manychat:event-001",
                        },
                    },
                    {
                        "schema": "phase8-source-event-identity",
                        "version": 1,
                        "data": {
                            "source_event_hash": "b" * 64,
                            "source_event_id": "manychat:event-002",
                        },
                    },
                ],
                "state_hash": hashlib.sha256(b'{"state":"canonical"}').hexdigest(),
                "state_version": 7,
            },
        }
        expected_bytes = json.dumps(
            expected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(request.to_canonical_bytes(), expected_bytes)
        self.assertEqual(
            request.canonical_hash(),
            hashlib.sha256(
                b"phase8-maya-turn-request-v1\x00" + expected_bytes
            ).hexdigest(),
        )
        with self.assertRaises(FrozenInstanceError):
            request.state_version = 8

    def test_maya_turn_request_rejects_open_or_inconsistent_values(self) -> None:
        request_type = getattr(conversation, "MayaTurnRequest", None)
        self.assertIsNotNone(request_type, "MayaTurnRequest must have an owner")
        assert request_type is not None
        valid = _request_kwargs()
        invalid_overrides = (
            {"boundary_state_bytes": bytearray(b"mutable")},
            {"boundary_state_bytes": b""},
            {"state_version": True},
            {"state_hash": "f" * 64},
            {"normalized_message": object()},
            {"aggregate_turn_id": "turn 001"},
            {"source_events": list(valid["source_events"])},
            {"source_events": ()},
            {"source_events": (valid["source_events"][0], valid["source_events"][0])},
            {"lead_key_hash": "C" * 64},
            {"private_delivery_binding_hash": "short"},
            {"deadline_at": DEADLINE.replace(tzinfo=None)},
            {"deadline_at": DEADLINE.astimezone(timezone(timedelta(hours=-3)))},
            {"behavior_profile_fingerprint": ""},
        )
        for override in invalid_overrides:
            with self.subTest(override=override):
                with self.assertRaises((TypeError, ValueError)):
                    request_type(**(valid | override))


if __name__ == "__main__":
    unittest.main()
