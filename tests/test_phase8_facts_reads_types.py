"""Focused RED/GREEN contracts for the Phase 8 TypedFact wire."""

from __future__ import annotations

import base64
from dataclasses import fields, replace
from datetime import date, datetime
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
from reservation_boundary.types import (
    ActivityDescriptionArguments,
    ActivityReadArguments,
    FaqReadArguments,
    LodgingReadArguments,
    RoomDescriptionArguments,
)
from reservation_boundary.reads import (
    GenesisStatus,
    LegacyGenesisEvidenceRecord,
    LegacyGenesisReadRequest,
    LegacyGenesisReceipt,
    KnowledgeSource,
    Phase8ToolReadRequest,
    PUBLIC_READ_POLICY_HASH,
    ReadEvidenceDisposition,
    ReadEvidenceReceipt,
    SanitizedKnowledgeResult,
    validate_public_text,
)


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


class Phase8ReadRequestTests(unittest.TestCase):
    @staticmethod
    def _source_event(data: dict[str, object]):
        from reservation_boundary.conversation import SourceEventIdentity

        event_data = data["source_event"]["data"]
        return SourceEventIdentity(
            event_data["source_event_id"],
            event_data["source_event_hash"],
        )

    def test_all_five_tool_requests_and_genesis_match_known_answers(self) -> None:
        examples = _fixture()["examples"]
        argument_types = {
            "FaqReadArguments": lambda data: FaqReadArguments(
                data["query"], data["locale"]
            ),
            "LodgingReadArguments": lambda data: LodgingReadArguments(
                date.fromisoformat(data["check_in"]),
                date.fromisoformat(data["check_out"]),
                data["adults"],
                data["children"],
            ),
            "RoomDescriptionArguments": lambda data: RoomDescriptionArguments(
                data["room_offer_id"]
            ),
            "ActivityReadArguments": lambda data: ActivityReadArguments(
                data["activity_id"],
                date.fromisoformat(data["activity_date"]),
                data["participants"],
            ),
            "ActivityDescriptionArguments": lambda data: ActivityDescriptionArguments(
                data["activity_id"]
            ),
        }
        labels = (
            "faq",
            "lodging_availability",
            "room_description",
            "activity_availability",
            "activity_description",
        )
        for label in labels:
            with self.subTest(label=label):
                item = examples[f"read_request.{label}"]
                data = json.loads(item["canonical_utf8"])["data"]
                argument = argument_types[data["arguments"]["type"]](
                    data["arguments"]["data"]
                )
                request = Phase8ToolReadRequest(
                    data["tool_name"],
                    argument,
                    data["lead_key_hash"],
                    data["aggregate_turn_id"],
                    self._source_event(data),
                    datetime.fromisoformat(data["deadline_at"]),
                    data["locale"],
                    data["projection_hash"],
                )
                self.assertEqual(
                    request.to_canonical_bytes().decode("utf-8"),
                    item["canonical_utf8"],
                )
                self.assertEqual(request.canonical_hash(), item["canonical_hash"])
                self.assertEqual(request.read_request_hash(), item["request_hash"])

        genesis_item = examples["read_request.legacy_genesis"]
        genesis_data = json.loads(genesis_item["canonical_utf8"])["data"]
        genesis = LegacyGenesisReadRequest(
            genesis_data["lead_key_hash"],
            genesis_data["aggregate_turn_id"],
            self._source_event(genesis_data),
            datetime.fromisoformat(genesis_data["deadline_at"]),
            genesis_data["legacy_source"],
        )
        self.assertEqual(
            genesis.to_canonical_bytes().decode("utf-8"),
            genesis_item["canonical_utf8"],
        )
        self.assertEqual(genesis.canonical_hash(), genesis_item["canonical_hash"])
        self.assertEqual(genesis.read_request_hash(), genesis_item["request_hash"])

    def test_read_request_tool_pairs_and_turn_bindings_fail_closed(self) -> None:
        examples = _fixture()["examples"]
        data = json.loads(examples["read_request.faq"]["canonical_utf8"])["data"]
        event = self._source_event(data)
        deadline = datetime.fromisoformat(data["deadline_at"])
        common = (
            data["lead_key_hash"],
            data["aggregate_turn_id"],
            event,
            deadline,
            data["locale"],
            data["projection_hash"],
        )
        invalid = (
            ("unknown_read", FaqReadArguments("Question?", "pt-BR"), common),
            (
                "cerebro_consultar",
                LodgingReadArguments(date(2026, 8, 1), date(2026, 8, 2), 1, 0),
                common,
            ),
            (
                "cerebro_consultar",
                FaqReadArguments("Question?", "en"),
                common,
            ),
            (
                "cerebro_consultar",
                FaqReadArguments("Question?", "pt-BR"),
                ("x" * 64, *common[1:]),
            ),
            (
                "cerebro_consultar",
                FaqReadArguments("Question?", "pt-BR"),
                (*common[:3], deadline.replace(tzinfo=None), *common[4:]),
            ),
        )
        for tool_name, arguments, bindings in invalid:
            with self.subTest(tool_name=tool_name, arguments=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    Phase8ToolReadRequest(tool_name, arguments, *bindings)

        with self.assertRaises(ValueError):
            LegacyGenesisReadRequest(
                data["lead_key_hash"],
                data["aggregate_turn_id"],
                event,
                deadline,
                "another_source",
            )


class Phase8GenesisEvidenceTests(unittest.TestCase):
    def test_genesis_receipts_and_owner_records_match_all_known_answers(self) -> None:
        examples = _fixture()["examples"]
        for status in ("found", "proven_absent", "unavailable"):
            with self.subTest(status=status):
                receipt_item = examples[f"legacy_genesis_receipt.{status}"]
                receipt = LegacyGenesisReceipt.from_canonical_bytes(
                    receipt_item["canonical_utf8"].encode("utf-8")
                )
                self.assertEqual(receipt.status.value, status)
                self.assertEqual(
                    receipt.to_canonical_bytes().decode("utf-8"),
                    receipt_item["canonical_utf8"],
                )
                self.assertEqual(receipt.canonical_hash(), receipt_item["canonical_hash"])

                record_item = examples[f"legacy_genesis_evidence_record.{status}"]
                record = LegacyGenesisEvidenceRecord.from_canonical_bytes(
                    record_item["canonical_utf8"].encode("utf-8")
                )
                self.assertEqual(record.receipt_bytes, receipt.to_canonical_bytes())
                self.assertEqual(
                    record.to_canonical_bytes().decode("utf-8"),
                    record_item["canonical_utf8"],
                )
                self.assertEqual(record.canonical_hash(), record_item["canonical_hash"])

    def test_genesis_evidence_rejects_status_confusion_and_preimage_swaps(self) -> None:
        examples = _fixture()["examples"]
        found = LegacyGenesisReceipt.from_canonical_bytes(
            examples["legacy_genesis_receipt.found"]["canonical_utf8"].encode("utf-8")
        )
        absent = LegacyGenesisReceipt.from_canonical_bytes(
            examples["legacy_genesis_receipt.proven_absent"]["canonical_utf8"].encode(
                "utf-8"
            )
        )
        unavailable = LegacyGenesisReceipt.from_canonical_bytes(
            examples["legacy_genesis_receipt.unavailable"]["canonical_utf8"].encode(
                "utf-8"
            )
        )
        self.assertIs(found.status, GenesisStatus.FOUND)
        self.assertIs(absent.status, GenesisStatus.PROVEN_ABSENT)
        self.assertIs(unavailable.status, GenesisStatus.UNAVAILABLE)
        self.assertNotEqual(absent, unavailable)

        with self.assertRaises(ValueError):
            replace(found, receipt_id="genesis:" + "0" * 64)
        with self.assertRaises(ValueError):
            replace(absent, matched_row_count=1)
        with self.assertRaises((TypeError, ValueError)):
            replace(unavailable, failure_evidence_hash=None)

        found_record = LegacyGenesisEvidenceRecord.from_canonical_bytes(
            examples["legacy_genesis_evidence_record.found"]["canonical_utf8"].encode(
                "utf-8"
            )
        )
        unavailable_record = LegacyGenesisEvidenceRecord.from_canonical_bytes(
            examples["legacy_genesis_evidence_record.unavailable"]["canonical_utf8"].encode(
                "utf-8"
            )
        )
        with self.assertRaises(ValueError):
            replace(
                found_record,
                source_snapshot_bytes=found_record.source_snapshot_bytes + b" ",
            )
        with self.assertRaises(ValueError):
            replace(
                found_record,
                failure_evidence_bytes=unavailable_record.failure_evidence_bytes,
            )
        with self.assertRaises(ValueError):
            replace(
                unavailable_record,
                receipt_bytes=absent.to_canonical_bytes(),
            )


class Phase8KnowledgeEvidenceTests(unittest.TestCase):
    def test_public_policy_probes_and_knowledge_known_answers_are_exact(self) -> None:
        fixture = _fixture()
        probes = fixture["policy_probes"]
        for text in probes["accepted"]:
            with self.subTest(accepted=text):
                self.assertEqual(validate_public_text(text, limit=4096), text)
        for probe in probes["rejected"]:
            with self.subTest(rejected=probe):
                with self.assertRaises(ValueError):
                    validate_public_text(probe["text"], limit=4096)

        self.assertEqual(
            PUBLIC_READ_POLICY_HASH,
            fixture["auxiliary_preimages"]["public_read_policy"]["domain_hash"],
        )
        examples = fixture["examples"]
        for disposition in ("public_safe", "private_only"):
            with self.subTest(disposition=disposition):
                receipt_item = examples[f"read_evidence_receipt.knowledge_{disposition}"]
                receipt = ReadEvidenceReceipt.from_canonical_bytes(
                    receipt_item["canonical_utf8"].encode("utf-8")
                )
                self.assertEqual(receipt.disposition.value, disposition)
                self.assertEqual(receipt.canonical_hash(), receipt_item["canonical_hash"])

                result_item = examples[f"result.knowledge_{disposition}"]
                result = SanitizedKnowledgeResult.from_canonical_bytes(
                    result_item["canonical_utf8"].encode("utf-8")
                )
                self.assertIs(result.source, KnowledgeSource.FAQ)
                self.assertEqual(result.evidence_receipt, receipt)
                self.assertEqual(
                    result.to_canonical_bytes().decode("utf-8"),
                    result_item["canonical_utf8"],
                )
                self.assertEqual(result.canonical_hash(), result_item["canonical_hash"])

    def test_knowledge_result_rejects_pii_subject_and_evidence_swaps(self) -> None:
        examples = _fixture()["examples"]
        public_result = SanitizedKnowledgeResult.from_canonical_bytes(
            examples["result.knowledge_public_safe"]["canonical_utf8"].encode("utf-8")
        )
        private_result = SanitizedKnowledgeResult.from_canonical_bytes(
            examples["result.knowledge_private_only"]["canonical_utf8"].encode("utf-8")
        )
        self.assertIs(
            public_result.evidence_receipt.disposition,
            ReadEvidenceDisposition.PUBLIC_SAFE,
        )
        self.assertIs(
            private_result.evidence_receipt.disposition,
            ReadEvidenceDisposition.PRIVATE_ONLY,
        )

        with self.assertRaises(ValueError):
            replace(public_result, answer_text="Telefone pessoal: (75) 99999-9999")
        with self.assertRaises(ValueError):
            replace(public_result, subject_id="offer:" + "a" * 64)
        self.assertEqual(
            replace(public_result, evidence_receipt=private_result.evidence_receipt),
            private_result,
        )
        lookup_receipt = ReadEvidenceReceipt.from_canonical_bytes(
            examples["read_evidence_receipt.lookup_positive_public_safe"][
                "canonical_utf8"
            ].encode("utf-8")
        )
        with self.assertRaises(ValueError):
            replace(public_result, evidence_receipt=lookup_receipt)
        with self.assertRaises(ValueError):
            replace(
                public_result.evidence_receipt,
                result_content_hash="0" * 64,
            )


if __name__ == "__main__":
    unittest.main()
