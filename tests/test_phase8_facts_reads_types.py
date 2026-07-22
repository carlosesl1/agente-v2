"""Focused RED/GREEN contracts for the Phase 8 TypedFact wire."""

from __future__ import annotations

from dataclasses import fields
from datetime import date
import hashlib
import json
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
