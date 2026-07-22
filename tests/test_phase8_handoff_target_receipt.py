"""Phase 8 handoff relay and target-operation receipt owner contracts."""

from __future__ import annotations

import base64
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
import unittest

from reservation_boundary import effects
from reservation_followup.serialization import to_wire_json
from tests.phase6_helpers import handoff_requested, optional_email_policy


def _canonical_envelope(schema: str, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": schema, "version": 1, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _bundle_values() -> dict[str, object]:
    request_bytes = to_wire_json(handoff_requested()).encode("utf-8")
    policy_bytes = to_wire_json(optional_email_policy()).encode("utf-8")
    data: dict[str, object] = {
        "request_bytes": request_bytes,
        "policy_bytes": policy_bytes,
        "history_bytes": (),
        "expected_final_state_hash": "2" * 64,
    }
    preimage = _canonical_envelope(
        "phase8-handoff-relay-bundle-preimage",
        {
            "request_bytes": base64.b64encode(request_bytes).decode("ascii"),
            "policy_bytes": base64.b64encode(policy_bytes).decode("ascii"),
            "history_bytes": [],
            "expected_final_state_hash": "2" * 64,
        },
    )
    data["artifact_hash"] = hashlib.sha256(
        b"phase8-handoff-relay-bundle-v1\x00" + preimage
    ).hexdigest()
    return data


class HandoffRelayBundleTests(unittest.TestCase):
    def test_fields_strict_phase6_decode_and_complete_hash_are_closed(self) -> None:
        bundle_type = getattr(effects, "HandoffRelayBundle", None)
        self.assertIsNotNone(bundle_type, "HandoffRelayBundle must have an owner")
        assert bundle_type is not None
        self.assertEqual(
            tuple(field.name for field in fields(bundle_type)),
            (
                "request_bytes",
                "policy_bytes",
                "history_bytes",
                "expected_final_state_hash",
                "artifact_hash",
            ),
        )
        values = _bundle_values()
        bundle = bundle_type(**values)
        self.assertEqual(
            bundle.artifact_hash,
            hashlib.sha256(
                b"phase8-handoff-relay-bundle-v1\x00"
                + bundle.artifact_preimage_bytes()
            ).hexdigest(),
        )
        wire = json.loads(bundle.to_canonical_bytes())
        self.assertNotIn("source_turn_receipt_hash", wire["data"])
        self.assertEqual(
            effects.HANDOFF_RELAY_DOMAIN,
            "phase8-handoff-relay-bundle-v1",
        )

    def test_noncanonical_wrong_contract_duplicate_request_and_hash_fail_closed(self) -> None:
        bundle_type = getattr(effects, "HandoffRelayBundle", None)
        self.assertIsNotNone(bundle_type)
        assert bundle_type is not None
        values = _bundle_values()
        for change in (
            {"request_bytes": values["request_bytes"] + b" "},
            {"request_bytes": values["policy_bytes"]},
            {"policy_bytes": values["request_bytes"]},
            {"history_bytes": (values["request_bytes"],)},
            {"artifact_hash": "f" * 64},
        ):
            with self.subTest(change=next(iter(change))):
                with self.assertRaises((TypeError, ValueError)):
                    bundle_type(**(values | change))
        with self.assertRaises(TypeError):
            bundle_type(**(values | {"history_bytes": []}))
        with self.assertRaises(TypeError):
            bundle_type(**(values | {"source_turn_receipt_hash": "a" * 64}))


class TargetOperationReceiptTests(unittest.TestCase):
    def test_fields_enum_utc_canonical_bytes_and_known_answer_are_exact(self) -> None:
        receipt_type = getattr(effects, "TargetOperationReceipt", None)
        job_kind = getattr(effects, "InternalJobKind", None)
        self.assertIsNotNone(receipt_type, "TargetOperationReceipt must have an owner")
        self.assertIsNotNone(job_kind, "InternalJobKind must have an owner")
        assert receipt_type is not None and job_kind is not None
        self.assertEqual(tuple(item.value for item in job_kind), ("handoff", "learning"))
        self.assertEqual(
            tuple(field.name for field in fields(receipt_type)),
            (
                "operation_id",
                "job_kind",
                "artifact_hash",
                "source_turn_receipt_hash",
                "target_commit_hash",
                "target_result_hash",
                "committed_at",
            ),
        )
        receipt = receipt_type(
            operation_id="6" * 64,
            job_kind=job_kind.HANDOFF,
            artifact_hash="82fb707addf117045bc03c18cc247041c53acb551ee83a9fa47cf31e42971e70",
            source_turn_receipt_hash="5" * 64,
            target_commit_hash="7" * 64,
            target_result_hash="8" * 64,
            committed_at=datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )
        expected = (
            b'{"data":{"artifact_hash":"82fb707addf117045bc03c18cc247041c53acb551ee83a9fa47cf31e42971e70",'
            b'"committed_at":"1970-01-01T00:00:01.000000Z","job_kind":"handoff",'
            b'"operation_id":"6666666666666666666666666666666666666666666666666666666666666666",'
            b'"source_turn_receipt_hash":"5555555555555555555555555555555555555555555555555555555555555555",'
            b'"target_commit_hash":"7777777777777777777777777777777777777777777777777777777777777777",'
            b'"target_result_hash":"8888888888888888888888888888888888888888888888888888888888888888"},'
            b'"schema":"phase8-target-operation-receipt","version":1}'
        )
        self.assertEqual(receipt.to_canonical_bytes(), expected)
        self.assertEqual(
            receipt.canonical_hash(),
            "6c7972e3889458032216bd8d64d32a882edeabaf9385f7bfed005b9afb994fb8",
        )
        self.assertEqual(receipt_type.from_canonical_bytes(expected), receipt)

    def test_wrong_types_non_utc_noncanonical_and_unknown_fields_fail_closed(self) -> None:
        receipt_type = getattr(effects, "TargetOperationReceipt", None)
        job_kind = getattr(effects, "InternalJobKind", None)
        self.assertIsNotNone(receipt_type)
        self.assertIsNotNone(job_kind)
        assert receipt_type is not None and job_kind is not None
        values = {
            "operation_id": "6" * 64,
            "job_kind": job_kind.HANDOFF,
            "artifact_hash": "a" * 64,
            "source_turn_receipt_hash": "5" * 64,
            "target_commit_hash": "7" * 64,
            "target_result_hash": "8" * 64,
            "committed_at": datetime(2026, 7, 22, tzinfo=timezone.utc),
        }
        for change in (
            {"operation_id": "x" * 64},
            {"job_kind": "handoff"},
            {"committed_at": datetime(2026, 7, 22)},
            {"committed_at": True},
        ):
            with self.subTest(change=next(iter(change))):
                with self.assertRaises((TypeError, ValueError)):
                    receipt_type(**(values | change))
        receipt = receipt_type(**values)
        with self.assertRaises(ValueError):
            receipt_type.from_canonical_bytes(receipt.to_canonical_bytes() + b" ")
        envelope = json.loads(receipt.to_canonical_bytes())
        envelope["data"]["provider_reference"] = "forbidden"
        with self.assertRaises(ValueError):
            receipt_type.from_canonical_bytes(
                json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
            )


if __name__ == "__main__":
    unittest.main()
