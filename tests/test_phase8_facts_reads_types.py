"""Focused RED/GREEN contracts for the Phase 8 TypedFact wire."""

from __future__ import annotations

import base64
from dataclasses import fields
from datetime import date
import hashlib
import json
from pathlib import Path
import unittest

from reservation_boundary.conversation import (
    ConversationProjection,
    ConversationStage,
    DesiredService,
    ReservationExecutionProjection,
)
from reservation_boundary.types import DateSlot, IntegerSlot, StringSlot, TypedFact


_FIXTURE = Path(__file__).parent / "fixtures" / "phase8_facts_reads_wire_v1.json"


def _fixture() -> dict[str, object]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class Phase8TypedFactTests(unittest.TestCase):
    def test_legacy_fact_remains_internal_but_v8_requires_frame_backlink(self) -> None:
        legacy = TypedFact("guest_count", IntegerSlot(2))

        self.assertEqual(
            tuple(field.name for field in fields(TypedFact)),
            ("name", "value", "frame_commitment_hash"),
        )
        self.assertIsNone(legacy.frame_commitment_hash)
        with self.assertRaises(ValueError):
            legacy.to_canonical_bytes()
        with self.assertRaises(ValueError):
            legacy.canonical_hash()

        with self.assertRaises(ValueError):
            TypedFact("language", StringSlot("pt-BR"), None).to_canonical_bytes()

    def test_v8_known_answers_match_all_value_kinds(self) -> None:
        examples = _fixture()["examples"]
        cases = {
            "typed_fact.string": StringSlot("pt-BR"),
            "typed_fact.integer": IntegerSlot(2),
            "typed_fact.date": DateSlot(date(2026, 8, 1)),
        }

        for name, slot in cases.items():
            with self.subTest(name=name):
                item = examples[name]
                envelope = json.loads(item["canonical_utf8"])
                fact = TypedFact(
                    envelope["data"]["name"],
                    slot,
                    envelope["data"]["frame_commitment_hash"],
                )
                canonical = fact.to_canonical_bytes()
                self.assertEqual(canonical.decode("utf-8"), item["canonical_utf8"])
                self.assertEqual(fact.canonical_hash(), item["canonical_hash"])
                self.assertEqual(
                    fact.canonical_hash(),
                    hashlib.sha256(TypedFact.DOMAIN.encode("ascii") + b"\0" + canonical).hexdigest(),
                )

    def test_v8_catalog_and_slot_mapping_are_closed(self) -> None:
        frame = "a" * 64
        valid = (
            TypedFact("language", StringSlot("en"), frame),
            TypedFact("service", StringSlot("hostel"), frame),
            TypedFact("service", StringSlot("agency"), frame),
            TypedFact("start_date", DateSlot(date(2026, 8, 1)), frame),
            TypedFact("end_date", DateSlot(date(2026, 8, 2)), frame),
            TypedFact("adults", IntegerSlot(1), frame),
            TypedFact("children", IntegerSlot(0), frame),
        )
        self.assertTrue(all(fact.to_canonical_bytes() for fact in valid))

        invalid = (
            ("guest_count", IntegerSlot(2), frame),
            ("language", IntegerSlot(2), frame),
            ("service", StringSlot("tour"), frame),
            ("start_date", StringSlot("2026-08-01"), frame),
            ("adults", IntegerSlot(0), frame),
            ("children", DateSlot(date(2026, 8, 1)), frame),
            ("language", StringSlot("pt-BR"), "A" * 64),
        )
        for name, slot, backlink in invalid:
            with self.subTest(name=name, slot=slot, backlink=backlink):
                with self.assertRaises((TypeError, ValueError)):
                    TypedFact(name, slot, backlink)


class Phase8ProjectionTests(unittest.TestCase):
    def test_projection_known_answers_are_finite_and_byte_exact(self) -> None:
        examples = _fixture()["examples"]
        execution_item = examples["reservation_execution_projection.present"]
        execution_envelope = json.loads(execution_item["canonical_utf8"])
        execution_data = execution_envelope["data"]
        execution = ReservationExecutionProjection(
            base64.b64decode(
                execution_data["reservation_relay_bundle_bytes"],
                validate=True,
            ),
            execution_data["reservation_relay_bundle_hash"],
        )
        self.assertEqual(
            execution.to_canonical_bytes().decode("utf-8"),
            execution_item["canonical_utf8"],
        )
        self.assertEqual(execution.canonical_hash(), execution_item["canonical_hash"])

        projection_item = examples["conversation_projection.with_reservation"]
        projection_data = json.loads(projection_item["canonical_utf8"])["data"]
        frame = projection_data["facts"][0]["data"]["frame_commitment_hash"]
        projection = ConversationProjection(
            ConversationStage.HOSTEL,
            (DesiredService.HOSTEL,),
            "pt-BR",
            (
                TypedFact("language", StringSlot("pt-BR"), frame),
                TypedFact("start_date", DateSlot(date(2026, 8, 1)), frame),
                TypedFact("adults", IntegerSlot(2), frame),
            ),
            execution,
        )
        canonical = projection.to_canonical_bytes()
        self.assertEqual(canonical.decode("utf-8"), projection_item["canonical_utf8"])
        self.assertEqual(projection.canonical_hash(), projection_item["canonical_hash"])
        self.assertNotIn(b"phase8-conversation-projection", execution.reservation_relay_bundle_bytes)
        self.assertNotIn(b"boundary_state", execution.reservation_relay_bundle_bytes)

    def test_projection_rejects_noncanonical_bundle_and_open_or_misordered_state(self) -> None:
        examples = _fixture()["examples"]
        execution_data = json.loads(
            examples["reservation_execution_projection.present"]["canonical_utf8"]
        )["data"]
        bundle = base64.b64decode(
            execution_data["reservation_relay_bundle_bytes"],
            validate=True,
        )
        binding = execution_data["reservation_relay_bundle_hash"]
        execution = ReservationExecutionProjection(bundle, binding)
        frame = "a" * 64

        noncanonical = json.dumps(json.loads(bundle.decode("utf-8"))).encode("utf-8")
        invalid_execution = (
            (bundle + b" ", binding),
            (noncanonical, hashlib.sha256(
                ReservationExecutionProjection.BUNDLE_BINDING_DOMAIN.encode("ascii")
                + b"\0"
                + noncanonical
            ).hexdigest()),
            (bundle, "b" * 64),
        )
        for bundle_bytes, bundle_hash in invalid_execution:
            with self.subTest(bundle_hash=bundle_hash):
                with self.assertRaises((TypeError, ValueError)):
                    ReservationExecutionProjection(bundle_bytes, bundle_hash)

        invalid_projection = (
            (
                ConversationStage.HOSTEL,
                (DesiredService.HOSTEL, DesiredService.HOSTEL),
                "pt-BR",
                (),
            ),
            (
                ConversationStage.HOSTEL,
                (DesiredService.AGENCY, DesiredService.HOSTEL),
                "pt-BR",
                (),
            ),
            (
                ConversationStage.HOSTEL,
                (DesiredService.HOSTEL,),
                "pt_br",
                (),
            ),
            (
                ConversationStage.HOSTEL,
                (DesiredService.HOSTEL,),
                "pt-BR",
                (TypedFact("adults", IntegerSlot(2), frame), TypedFact("language", StringSlot("pt-BR"), frame)),
            ),
            (
                ConversationStage.HOSTEL,
                (DesiredService.HOSTEL,),
                "pt-BR",
                (TypedFact("guest_count", IntegerSlot(2)),),
            ),
        )
        for stage, services, locale, facts_value in invalid_projection:
            with self.subTest(services=services, locale=locale, facts=facts_value):
                with self.assertRaises((TypeError, ValueError)):
                    ConversationProjection(stage, services, locale, facts_value, execution)


if __name__ == "__main__":
    unittest.main()
