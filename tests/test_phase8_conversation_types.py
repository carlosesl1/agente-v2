"""Focused contracts for Phase 8 conversation wire types."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import hashlib
import unittest

from reservation_boundary.conversation import SourceEventIdentity


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


if __name__ == "__main__":
    unittest.main()
