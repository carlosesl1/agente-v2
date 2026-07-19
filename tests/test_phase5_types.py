from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import inspect
import json
import unittest

from reservation_domain import (
    CommandPayload,
    CustomerFacts,
    EconomicTerms,
    Money,
    OfferSnapshot,
    Party,
    ReservationCommand,
    ReservationOperation,
    ServiceKind,
    command_identity,
    subject_signature,
)
from reservation_execution import (
    CommandClaim,
    DeliveryReceipt,
    DispatchPermit,
    DispatchRequest,
    ExecutionAdapter,
    Lease,
    LedgerStatus,
    OutboxKind,
    OutboxMessage,
    OutboxStatus,
    PreparationDisposition,
    PreparationFailure,
)

T0 = datetime(2027, 1, 1, tzinfo=timezone.utc)
CANONICAL_PAYLOAD = (
    '{"customer_ref":"customer:synthetic:1","offer_id":"offer:synthetic:1"}'
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def reservation_command() -> ReservationCommand:
    component = OfferSnapshot(
        offer_id="offer:synthetic:1",
        lookup_id="lookup:synthetic:1",
        service=ServiceKind.LODGING,
        provider_ref="provider:synthetic:room:1",
        public_label="Synthetic lodging",
        start_date=date(2027, 1, 10),
        end_date=date(2027, 1, 12),
        start_time=None,
        party=Party(adults=1, children=0),
        total=Money(amount=Decimal("100.00"), currency="BRL"),
        available=True,
    )
    customer = CustomerFacts(
        customer_ref="customer:synthetic:1",
        full_name="Synthetic Test Person",
        email="synthetic.person" + chr(64) + "example.invalid",
        phone_e164="+99900000000",
        country_code="ZZ",
    )
    terms = EconomicTerms(payment_method="card")
    payload = CommandPayload(
        components=(component,),
        customer=customer,
        terms=terms,
    )
    signature = subject_signature(
        components=payload.components,
        customer=payload.customer,
        terms=payload.terms,
    )
    operation = ReservationOperation.RESERVE_LODGING
    command_id, idempotency_key = command_identity(
        workflow_id="workflow:synthetic:1",
        draft_id="draft:synthetic:1",
        draft_version=1,
        signature=signature,
        operation=operation,
    )
    return ReservationCommand(
        command_id=command_id,
        idempotency_key=idempotency_key,
        workflow_id="workflow:synthetic:1",
        draft_id="draft:synthetic:1",
        draft_version=1,
        subject_signature=signature,
        operation=operation,
        payload=payload,
        created_at=T0,
    )


def lease() -> Lease:
    return Lease(
        owner="worker:phase5:a",
        fencing_token=1,
        acquired_at=T0,
        expires_at=T0 + timedelta(seconds=30),
    )


def outbox_message(**changes: object) -> OutboxMessage:
    values: dict[str, object] = {
        "message_id": "message:synthetic:1",
        "idempotency_key": "delivery:synthetic:1",
        "workflow_id": "workflow:synthetic:1",
        "command_id": "command:synthetic:1",
        "kind": OutboxKind.EXECUTION_SUCCEEDED,
        "template_id": "reservation.execution.succeeded.v1",
        "canonical_payload": CANONICAL_PAYLOAD,
        "payload_hash": sha256_text(CANONICAL_PAYLOAD),
        "created_at": T0,
    }
    values.update(changes)
    return OutboxMessage(**values)


def receipt_hash(
    *,
    message_id: str,
    delivery_reference: str,
    delivered_at: datetime,
) -> str:
    material = json.dumps(
        {
            "message_id": message_id,
            "delivery_reference": delivery_reference,
            "delivered_at": delivered_at.astimezone(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(material)


class Phase5ExecutionTypeTests(unittest.TestCase):
    def test_closed_enums_have_exact_values_without_aliases(self) -> None:
        expected = {
            LedgerStatus: (
                "queued",
                "preparing",
                "dispatch_fenced",
                "outcome_recorded",
                "manual_review",
            ),
            OutboxStatus: ("pending", "leased", "delivered"),
            OutboxKind: (
                "summary_presented",
                "execution_succeeded",
                "execution_failed_no_effect",
                "execution_not_called",
                "execution_manual_review",
            ),
            PreparationDisposition: ("requeued", "terminal_not_called"),
        }
        for enum_type, values in expected.items():
            with self.subTest(enum_type=enum_type.__name__):
                self.assertEqual(tuple(item.value for item in enum_type), values)
                self.assertEqual(len(enum_type.__members__), len(values))

    def test_operational_dataclasses_have_exact_closed_field_universes(self) -> None:
        expected = {
            CommandClaim: (
                "command",
                "workflow_revision",
                "lease",
                "claim_count",
                "preparation_failures",
            ),
            DispatchRequest: (
                "command_id",
                "idempotency_key",
                "operation",
                "canonical_payload",
                "payload_hash",
            ),
            DispatchPermit: (
                "command_id",
                "lease",
                "dispatch_slot",
                "request_hash",
                "fenced_at",
            ),
            OutboxMessage: (
                "message_id",
                "idempotency_key",
                "workflow_id",
                "command_id",
                "kind",
                "template_id",
                "canonical_payload",
                "payload_hash",
                "created_at",
            ),
            DeliveryReceipt: (
                "message_id",
                "delivery_reference",
                "receipt_hash",
                "delivered_at",
            ),
        }
        for dto, names in expected.items():
            with self.subTest(dto=dto.__name__):
                self.assertEqual(tuple(field.name for field in fields(dto)), names)
        self.assertNotIn("provider_ref", inspect.signature(DispatchPermit).parameters)
        self.assertNotIn("offer_id", inspect.signature(DispatchPermit).parameters)

    def test_lease_requires_opaque_owner_exact_positive_token_and_positive_ttl(self) -> None:
        valid = lease()
        self.assertEqual(valid.expires_at, T0 + timedelta(seconds=30))
        for kwargs in (
            {"owner": "x"},
            {"owner": 123},
            {"fencing_token": True},
            {"fencing_token": 0},
            {"fencing_token": 1.0},
            {"expires_at": T0},
            {"expires_at": T0 - timedelta(microseconds=1)},
            {"acquired_at": T0.replace(tzinfo=None)},
            {"expires_at": (T0 + timedelta(seconds=30)).replace(tzinfo=None)},
        ):
            values = {
                "owner": "worker:phase5:a",
                "fencing_token": 1,
                "acquired_at": T0,
                "expires_at": T0 + timedelta(seconds=30),
            }
            values.update(kwargs)
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    Lease(**values)

    def test_datetime_inputs_are_normalized_to_canonical_utc(self) -> None:
        offset = timezone(timedelta(hours=-3))
        acquired = T0.astimezone(offset)
        expires = (T0 + timedelta(seconds=30)).astimezone(offset)
        value = Lease(
            owner="worker:phase5:a",
            fencing_token=1,
            acquired_at=acquired,
            expires_at=expires,
        )
        self.assertEqual(value.acquired_at, T0)
        self.assertIs(value.acquired_at.tzinfo, timezone.utc)
        self.assertEqual(value.expires_at, T0 + timedelta(seconds=30))
        self.assertIs(value.expires_at.tzinfo, timezone.utc)

    def test_command_claim_requires_exact_domain_command_and_integer_counters(self) -> None:
        command = reservation_command()
        claim = CommandClaim(
            command=command,
            workflow_revision=7,
            lease=lease(),
            claim_count=1,
            preparation_failures=0,
        )
        self.assertIs(claim.command, command)
        invalid_changes = (
            {"command": object()},
            {"workflow_revision": True},
            {"workflow_revision": -1},
            {"lease": object()},
            {"claim_count": True},
            {"claim_count": 0},
            {"preparation_failures": True},
            {"preparation_failures": -1},
            {"preparation_failures": 4},
        )
        for changes in invalid_changes:
            values = {
                "command": command,
                "workflow_revision": 7,
                "lease": lease(),
                "claim_count": 1,
                "preparation_failures": 0,
            }
            values.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    CommandClaim(**values)

    def test_dispatch_request_from_command_copies_only_identity_and_operation(self) -> None:
        command = reservation_command()
        request = DispatchRequest.from_command(command, CANONICAL_PAYLOAD)
        self.assertEqual(request.command_id, command.command_id)
        self.assertEqual(request.idempotency_key, command.idempotency_key)
        self.assertIs(request.operation, command.operation)
        self.assertEqual(request.canonical_payload, CANONICAL_PAYLOAD)
        self.assertEqual(request.payload_hash, sha256_text(CANONICAL_PAYLOAD))
        self.assertFalse(hasattr(request, "workflow_id"))
        self.assertFalse(hasattr(request, "provider_ref"))
        with self.assertRaises(ValueError):
            DispatchRequest.from_command(object(), CANONICAL_PAYLOAD)

    def test_dispatch_request_rejects_tampered_payload_hash_and_wrong_types(self) -> None:
        command = reservation_command()
        values = {
            "command_id": command.command_id,
            "idempotency_key": command.idempotency_key,
            "operation": command.operation,
            "canonical_payload": CANONICAL_PAYLOAD,
            "payload_hash": sha256_text(CANONICAL_PAYLOAD),
        }
        for changes in (
            {"command_id": 123},
            {"operation": command.operation.value},
            {"canonical_payload": {"offer_id": "offer:synthetic:1"}},
            {"payload_hash": "0" * 64},
            {"payload_hash": sha256_text(CANONICAL_PAYLOAD).upper()},
        ):
            invalid = dict(values)
            invalid.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    DispatchRequest(**invalid)

    def test_hash_fields_reject_surrounding_whitespace_without_normalization(self) -> None:
        command = reservation_command()
        digest = sha256_text(CANONICAL_PAYLOAD)
        for padded in (" " + digest, digest + " ", "\t" + digest + "\n"):
            with self.subTest(dto="DispatchRequest", padded=repr(padded)):
                with self.assertRaises(ValueError):
                    DispatchRequest(
                        command_id=command.command_id,
                        idempotency_key=command.idempotency_key,
                        operation=command.operation,
                        canonical_payload=CANONICAL_PAYLOAD,
                        payload_hash=padded,
                    )
            with self.subTest(dto="DispatchPermit", padded=repr(padded)):
                with self.assertRaises(ValueError):
                    DispatchPermit(
                        command_id=command.command_id,
                        lease=lease(),
                        dispatch_slot=1,
                        request_hash=padded,
                        fenced_at=T0,
                    )
            with self.subTest(dto="OutboxMessage", padded=repr(padded)):
                with self.assertRaises(ValueError):
                    outbox_message(payload_hash=padded)

        message_id = "message:synthetic:1"
        reference = "delivery:synthetic:reference:1"
        receipt_digest = receipt_hash(
            message_id=message_id,
            delivery_reference=reference,
            delivered_at=T0,
        )
        with self.assertRaises(ValueError):
            DeliveryReceipt(
                message_id=message_id,
                delivery_reference=reference,
                receipt_hash=" " + receipt_digest,
                delivered_at=T0,
            )
        with self.assertRaises(ValueError):
            PreparationFailure(
                reason="synthetic_timeout",
                retryable=True,
                evidence=(receipt_digest + " ",),
            )

    def test_canonical_payload_rejects_noncanonical_duplicate_nonfinite_and_nonobject_json(self) -> None:
        command = reservation_command()
        invalid_payloads = (
            '{"offer_id":"offer:synthetic:1", "customer_ref":"customer:synthetic:1"}',
            '{"offer_id":"offer:synthetic:1","customer_ref":"customer:synthetic:1"}',
            '{"label":"\\u00e1"}',
            '{"a":1,"a":1}',
            '{"nested":{"a":1,"a":2}}',
            '{"value":NaN}',
            '{"value":Infinity}',
            '{"value":-Infinity}',
            "[]",
            '"text"',
            "null",
            "1",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    DispatchRequest.from_command(command, payload)

    def test_dispatch_request_rejects_escaped_and_materialized_lone_surrogates_uniformly(self) -> None:
        command = reservation_command()
        invalid_payloads = (
            r'{"x":"\ud800"}',
            r'{"x":"\udc00"}',
            '{"x":"' + chr(0xD800) + '"}',
            '{"x":"' + chr(0xDC00) + '"}',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=ascii(payload)):
                with self.assertRaisesRegex(
                    ValueError,
                    "canonical_payload must be valid canonical JSON",
                ):
                    DispatchRequest.from_command(command, payload)

    def test_dispatch_permit_requires_exact_lease_slot_hash_and_utc(self) -> None:
        command = reservation_command()
        request = DispatchRequest.from_command(command, CANONICAL_PAYLOAD)
        permit = DispatchPermit(
            command_id=command.command_id,
            lease=lease(),
            dispatch_slot=1,
            request_hash=request.payload_hash,
            fenced_at=T0 + timedelta(seconds=1),
        )
        self.assertEqual(permit.dispatch_slot, 1)
        for changes in (
            {"command_id": 123},
            {"lease": object()},
            {"dispatch_slot": True},
            {"dispatch_slot": 0},
            {"dispatch_slot": 2},
            {"request_hash": "A" * 64},
            {"request_hash": "not-a-hash"},
            {"fenced_at": T0.replace(tzinfo=None)},
        ):
            values = {
                "command_id": command.command_id,
                "lease": lease(),
                "dispatch_slot": 1,
                "request_hash": request.payload_hash,
                "fenced_at": T0 + timedelta(seconds=1),
            }
            values.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    DispatchPermit(**values)

    def test_outbox_message_validates_exact_fields_payload_hash_and_optional_command(self) -> None:
        message = outbox_message()
        self.assertEqual(message.payload_hash, sha256_text(CANONICAL_PAYLOAD))
        without_command = outbox_message(command_id=None)
        self.assertIsNone(without_command.command_id)
        for changes in (
            {"message_id": 123},
            {"command_id": "x"},
            {"kind": OutboxKind.EXECUTION_SUCCEEDED.value},
            {"template_id": "x"},
            {"canonical_payload": '{"b":2,"a":1}'},
            {"canonical_payload": "[]", "payload_hash": sha256_text("[]")},
            {"payload_hash": "f" * 64},
            {"created_at": T0.replace(tzinfo=None)},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    outbox_message(**changes)

    def test_delivery_receipt_recomputes_canonical_material_and_rejects_divergence(self) -> None:
        message_id = "message:synthetic:1"
        reference = "delivery:synthetic:reference:1"
        digest = receipt_hash(
            message_id=message_id,
            delivery_reference=reference,
            delivered_at=T0,
        )
        receipt = DeliveryReceipt(
            message_id=message_id,
            delivery_reference=reference,
            receipt_hash=digest,
            delivered_at=T0,
        )
        self.assertEqual(receipt.receipt_hash, digest)
        for changes in (
            {"message_id": "message:synthetic:2"},
            {"delivery_reference": "delivery:synthetic:reference:2"},
            {"receipt_hash": "0" * 64},
            {"receipt_hash": digest.upper()},
            {"delivered_at": T0 + timedelta(microseconds=1)},
            {"delivered_at": T0.replace(tzinfo=None)},
        ):
            values = {
                "message_id": message_id,
                "delivery_reference": reference,
                "receipt_hash": digest,
                "delivered_at": T0,
            }
            values.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    DeliveryReceipt(**values)

    def test_preparation_failure_is_typed_immutable_and_hash_validated(self) -> None:
        failure = PreparationFailure(
            reason="synthetic_timeout",
            retryable=True,
            evidence=("a" * 64,),
        )
        self.assertIsInstance(failure, Exception)
        self.assertEqual(failure.evidence, ("a" * 64,))
        with self.assertRaises(FrozenInstanceError):
            failure.reason = "changed"
        for changes in (
            {"reason": "x"},
            {"retryable": 1},
            {"evidence": ["a" * 64]},
            {"evidence": ("A" * 64,)},
        ):
            values = {
                "reason": "synthetic_timeout",
                "retryable": True,
                "evidence": ("a" * 64,),
            }
            values.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    PreparationFailure(**values)

    def test_all_operational_dtos_are_frozen_and_slotted(self) -> None:
        command = reservation_command()
        request = DispatchRequest.from_command(command, CANONICAL_PAYLOAD)
        message_id = "message:synthetic:1"
        reference = "delivery:synthetic:reference:1"
        instances = (
            lease(),
            CommandClaim(command, 7, lease(), 1, 0),
            request,
            DispatchPermit(command.command_id, lease(), 1, request.payload_hash, T0),
            outbox_message(),
            DeliveryReceipt(
                message_id,
                reference,
                receipt_hash(
                    message_id=message_id,
                    delivery_reference=reference,
                    delivered_at=T0,
                ),
                T0,
            ),
        )
        for instance in instances:
            with self.subTest(dto=type(instance).__name__):
                self.assertFalse(hasattr(instance, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(instance, fields(instance)[0].name, "changed")

    def test_execution_adapter_is_runtime_protocol_only_with_closed_methods(self) -> None:
        self.assertTrue(getattr(ExecutionAdapter, "_is_protocol", False))
        self.assertTrue(getattr(ExecutionAdapter, "_is_runtime_protocol", False))
        self.assertEqual(
            set(ExecutionAdapter.__dict__).intersection({"prepare", "dispatch"}),
            {"prepare", "dispatch"},
        )
        self.assertEqual(
            tuple(inspect.signature(ExecutionAdapter.prepare).parameters),
            ("self", "command"),
        )
        dispatch = inspect.signature(ExecutionAdapter.dispatch)
        self.assertEqual(tuple(dispatch.parameters), ("self", "request", "idempotency_key"))
        self.assertIs(
            dispatch.parameters["idempotency_key"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )


if __name__ == "__main__":
    unittest.main()
