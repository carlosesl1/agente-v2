from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import timedelta, timezone
import hashlib
import json
import unittest

from reservation_domain import ExecutionCertainty, ExecutionOutcome, ServiceKind
from reservation_followup import (
    BusinessUnit,
    ConfirmedReservationAnchor,
    EffectRequirement,
    HandoffEffectPolicy,
    HandoffStatus,
    PaymentEffectPolicy,
    PaymentMethod,
    PaymentStatus,
    PaymentSubject,
    SettlementCertainty,
    from_wire_json,
    semantic_hash,
    to_wire_json,
)
from tests.phase6_helpers import (
    T0,
    confirmed_anchor,
    economic_signature,
    outcome,
    payment_subject,
)


class Phase6SharedTypeTests(unittest.TestCase):
    def test_closed_enums_have_exact_values_without_aliases(self) -> None:
        expected = {
            BusinessUnit: ("hostel", "agency"),
            PaymentMethod: ("pix", "wise", "stripe"),
            EffectRequirement: ("required", "optional", "disabled"),
            HandoffStatus: (
                "requested",
                "active",
                "acknowledgement_pending",
                "acknowledged",
                "manual_review",
                "completed",
                "cancelled",
            ),
            PaymentStatus: (
                "awaiting_method",
                "awaiting_financial_confirmation",
                "awaiting_evidence",
                "evidence_verified",
                "settlement_queued",
                "settling",
                "paid",
                "retryable",
                "manual_review",
                "expired",
                "cancelled",
            ),
            SettlementCertainty: (
                "not_dispatched",
                "dispatched_no_effect",
                "settled",
                "partial_settlement",
                "dispatched_unknown",
            ),
        }
        for enum_type, values in expected.items():
            with self.subTest(enum_type=enum_type.__name__):
                self.assertEqual(tuple(item.value for item in enum_type), values)
                self.assertEqual(len(enum_type.__members__), len(values))

    def test_shared_dataclasses_have_exact_closed_field_universes(self) -> None:
        expected = {
            ConfirmedReservationAnchor: (
                "reservation_workflow_id",
                "reservation_command_id",
                "reservation_subject_signature",
                "reservation_outcome_hash",
                "reservation_outcome",
                "provider_reference",
                "service",
                "business_unit",
                "payment_target_id",
                "amount_minor",
                "currency",
                "receiver_profile_id",
                "confirmed_at",
                "payment_deadline",
            ),
            HandoffEffectPolicy: (
                "queue_state",
                "customer_acknowledgement",
                "internal_email",
            ),
            PaymentEffectPolicy: (
                "paid_state_transition",
                "customer_payment_confirmation",
                "internal_payment_email",
                "booking_form",
            ),
            PaymentSubject: (
                "payment_id",
                "payment_version",
                "confirmed_reservation_anchor",
                "amount_minor",
                "currency",
                "receiver_profile_id",
                "business_unit",
                "payment_target_id",
                "method",
                "economic_signature",
            ),
        }
        for dto, names in expected.items():
            with self.subTest(dto=dto.__name__):
                self.assertEqual(tuple(field.name for field in fields(dto)), names)

    def test_anchor_requires_exact_effect_confirmed_outcome(self) -> None:
        for certainty in (
            ExecutionCertainty.NOT_CALLED,
            ExecutionCertainty.CALLED_NO_EFFECT,
            ExecutionCertainty.CALLED_UNKNOWN,
        ):
            with self.subTest(certainty=certainty), self.assertRaises(ValueError):
                confirmed_anchor(outcome=outcome(certainty=certainty))

    def test_anchor_rejects_outcome_subclass(self) -> None:
        valid = outcome()

        class InventedOutcome(ExecutionOutcome):
            pass

        invented = InventedOutcome(
            command_id=valid.command_id,
            certainty=valid.certainty,
            normalized_status=valid.normalized_status,
            provider_reference=valid.provider_reference,
            evidence=valid.evidence,
        )
        anchor = confirmed_anchor()
        values = {field.name: getattr(anchor, field.name) for field in fields(anchor)}
        values["reservation_outcome"] = invented
        with self.assertRaises(ValueError):
            ConfirmedReservationAnchor(**values)

    def test_anchor_recomputes_outcome_hash_and_binds_command_and_provider(self) -> None:
        valid = confirmed_anchor()
        self.assertEqual(valid.reservation_command_id, valid.reservation_outcome.command_id)
        self.assertEqual(valid.provider_reference, valid.reservation_outcome.provider_reference)
        for changes in (
            {"reservation_outcome_hash": "0" * 64},
            {"reservation_command_id": "command:reservation:synthetic:other"},
            {"provider_reference": "provider:reservation:synthetic:other"},
            {"provider_reference": "   "},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                confirmed_anchor(**changes)

    def test_anchor_validates_exact_identifiers_hashes_economics_and_enums(self) -> None:
        invalid_changes = (
            {"reservation_workflow_id": 123},
            {"reservation_workflow_id": "x"},
            {"reservation_subject_signature": "A" * 64},
            {"reservation_subject_signature": "a" * 63},
            {"service": ServiceKind.LODGING.value},
            {"business_unit": BusinessUnit.HOSTEL.value},
            {"payment_target_id": "x"},
            {"receiver_profile_id": object()},
            {"amount_minor": True},
            {"amount_minor": 1.0},
            {"amount_minor": 0},
            {"amount_minor": -1},
            {"currency": "brl"},
            {"currency": "USDT"},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                confirmed_anchor(**changes)

    def test_anchor_normalizes_aware_timestamps_to_utc_and_orders_deadline(self) -> None:
        offset = timezone(timedelta(hours=-3))
        value = confirmed_anchor(
            confirmed_at=T0.astimezone(offset),
            payment_deadline=(T0 + timedelta(days=1)).astimezone(offset),
        )
        self.assertEqual(value.confirmed_at, T0)
        self.assertIs(value.confirmed_at.tzinfo, timezone.utc)
        self.assertEqual(value.payment_deadline, T0 + timedelta(days=1))
        self.assertIs(value.payment_deadline.tzinfo, timezone.utc)
        for changes in (
            {"confirmed_at": T0.replace(tzinfo=None)},
            {"payment_deadline": T0.replace(tzinfo=None)},
            {"payment_deadline": T0},
            {"payment_deadline": T0 - timedelta(microseconds=1)},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                confirmed_anchor(**changes)
        self.assertIsNone(confirmed_anchor(payment_deadline=None).payment_deadline)

    def test_handoff_policy_requires_queue_and_customer_ack(self) -> None:
        for changes in (
            {"queue_state": EffectRequirement.DISABLED},
            {"queue_state": EffectRequirement.OPTIONAL},
            {"customer_acknowledgement": EffectRequirement.DISABLED},
            {"customer_acknowledgement": EffectRequirement.OPTIONAL},
        ):
            values = {
                "queue_state": EffectRequirement.REQUIRED,
                "customer_acknowledgement": EffectRequirement.REQUIRED,
                "internal_email": EffectRequirement.OPTIONAL,
            }
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                HandoffEffectPolicy(**values)

    def test_handoff_policy_internal_email_is_only_optional_or_disabled(self) -> None:
        for requirement in (
            EffectRequirement.OPTIONAL,
            EffectRequirement.DISABLED,
        ):
            with self.subTest(requirement=requirement):
                policy = HandoffEffectPolicy(
                    queue_state=EffectRequirement.REQUIRED,
                    customer_acknowledgement=EffectRequirement.REQUIRED,
                    internal_email=requirement,
                )
                self.assertIs(policy.internal_email, requirement)
        self.assertEqual(
            HandoffEffectPolicy.default_email_disabled(),
            HandoffEffectPolicy(
                queue_state=EffectRequirement.REQUIRED,
                customer_acknowledgement=EffectRequirement.REQUIRED,
                internal_email=EffectRequirement.DISABLED,
            ),
        )
        with self.assertRaises(ValueError):
            HandoffEffectPolicy(
                queue_state=EffectRequirement.REQUIRED,
                customer_acknowledgement=EffectRequirement.REQUIRED,
                internal_email=EffectRequirement.REQUIRED,
            )

    def test_policies_require_exact_effect_requirement_members(self) -> None:
        with self.assertRaises(ValueError):
            HandoffEffectPolicy(
                queue_state="required",
                customer_acknowledgement=EffectRequirement.REQUIRED,
                internal_email=EffectRequirement.DISABLED,
            )
        with self.assertRaises(ValueError):
            HandoffEffectPolicy(
                queue_state=EffectRequirement.REQUIRED,
                customer_acknowledgement=EffectRequirement.REQUIRED,
                internal_email="optional",
            )
        with self.assertRaises(ValueError):
            PaymentEffectPolicy(
                paid_state_transition=EffectRequirement.REQUIRED,
                customer_payment_confirmation=EffectRequirement.REQUIRED,
                internal_payment_email="optional",
                booking_form=EffectRequirement.DISABLED,
            )

    def test_payment_policy_requires_explicit_booking_form_classification(self) -> None:
        with self.assertRaises(ValueError):
            PaymentEffectPolicy(
                paid_state_transition=EffectRequirement.REQUIRED,
                customer_payment_confirmation=EffectRequirement.REQUIRED,
                internal_payment_email=EffectRequirement.OPTIONAL,
                booking_form=None,
            )
        for requirement in EffectRequirement:
            with self.subTest(requirement=requirement):
                policy = PaymentEffectPolicy(
                    paid_state_transition=EffectRequirement.REQUIRED,
                    customer_payment_confirmation=EffectRequirement.REQUIRED,
                    internal_payment_email=EffectRequirement.DISABLED,
                    booking_form=requirement,
                )
                self.assertIs(policy.booking_form, requirement)

    def test_payment_policy_requires_paid_state_and_customer_confirmation(self) -> None:
        base = {
            "paid_state_transition": EffectRequirement.REQUIRED,
            "customer_payment_confirmation": EffectRequirement.REQUIRED,
            "internal_payment_email": EffectRequirement.OPTIONAL,
            "booking_form": EffectRequirement.DISABLED,
        }
        for field_name in (
            "paid_state_transition",
            "customer_payment_confirmation",
        ):
            for requirement in (
                EffectRequirement.OPTIONAL,
                EffectRequirement.DISABLED,
            ):
                values = dict(base)
                values[field_name] = requirement
                with self.subTest(field=field_name, requirement=requirement):
                    with self.assertRaises(ValueError):
                        PaymentEffectPolicy(**values)
        values = dict(base)
        values["internal_payment_email"] = EffectRequirement.REQUIRED
        with self.assertRaises(ValueError):
            PaymentEffectPolicy(**values)

    def test_payment_subject_validates_closed_identity_and_economics(self) -> None:
        invalid_changes = (
            {"payment_id": "x"},
            {"payment_version": True},
            {"payment_version": 1.0},
            {"payment_version": 0},
            {"confirmed_reservation_anchor": object()},
            {"amount_minor": True},
            {"amount_minor": 0},
            {"currency": "brl"},
            {"receiver_profile_id": "x"},
            {"business_unit": BusinessUnit.HOSTEL.value},
            {"payment_target_id": 123},
            {"method": PaymentMethod.PIX.value},
            {"economic_signature": "0" * 64},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                payment_subject(**changes)

    def test_payment_subject_economic_signature_excludes_method_but_binds_economics(self) -> None:
        pix = payment_subject(method=PaymentMethod.PIX)
        wise = payment_subject(method=PaymentMethod.WISE)
        without_method = payment_subject(method=None)
        self.assertEqual(pix.economic_signature, wise.economic_signature)
        self.assertEqual(pix.economic_signature, without_method.economic_signature)
        changed_amount = payment_subject(
            amount_minor=pix.amount_minor + 1,
            payment_version=2,
        )
        changed_receiver = payment_subject(
            receiver_profile_id="receiver:profile:synthetic:2",
            payment_version=2,
        )
        self.assertNotEqual(pix.economic_signature, changed_amount.economic_signature)
        self.assertNotEqual(pix.economic_signature, changed_receiver.economic_signature)
        self.assertEqual(
            changed_amount.economic_signature,
            economic_signature(
                amount_minor=changed_amount.amount_minor,
                currency=changed_amount.currency,
                receiver_profile_id=changed_amount.receiver_profile_id,
                business_unit=changed_amount.business_unit,
                payment_target_id=changed_amount.payment_target_id,
            ),
        )

    def test_all_shared_dtos_are_frozen_and_slotted(self) -> None:
        instances = (
            confirmed_anchor(),
            HandoffEffectPolicy.default_email_disabled(),
            PaymentEffectPolicy(
                paid_state_transition=EffectRequirement.REQUIRED,
                customer_payment_confirmation=EffectRequirement.REQUIRED,
                internal_payment_email=EffectRequirement.OPTIONAL,
                booking_form=EffectRequirement.DISABLED,
            ),
            payment_subject(),
        )
        for instance in instances:
            with self.subTest(dto=type(instance).__name__):
                self.assertFalse(hasattr(instance, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(instance, fields(instance)[0].name, "changed")


class Phase6WireSerializationTests(unittest.TestCase):
    def policy(self) -> PaymentEffectPolicy:
        return PaymentEffectPolicy(
            paid_state_transition=EffectRequirement.REQUIRED,
            customer_payment_confirmation=EffectRequirement.REQUIRED,
            internal_payment_email=EffectRequirement.OPTIONAL,
            booking_form=EffectRequirement.DISABLED,
        )

    def test_every_shared_dto_round_trips_with_exact_type(self) -> None:
        values = (
            confirmed_anchor(),
            confirmed_anchor(payment_deadline=None),
            HandoffEffectPolicy.default_email_disabled(),
            self.policy(),
            payment_subject(),
            payment_subject(method=None),
        )
        for value in values:
            with self.subTest(dto=type(value).__name__):
                decoded = from_wire_json(to_wire_json(value), type(value))
                self.assertEqual(decoded, value)
                self.assertIs(type(decoded), type(value))

    def test_wire_json_is_canonical_utf8_and_semantic_hash_is_sha256(self) -> None:
        value = payment_subject()
        raw = to_wire_json(value)
        parsed = json.loads(raw)
        self.assertEqual(
            raw,
            json.dumps(
                parsed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
        self.assertEqual(
            semantic_hash(value),
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(len(semantic_hash(value)), 64)

    def test_duplicate_keys_fail_closed_at_envelope_and_nested_depths(self) -> None:
        raw = to_wire_json(payment_subject())
        duplicate_envelope = raw.replace(
            '"schema_version":1',
            '"schema_version":1,"schema_version":1',
            1,
        )
        duplicate_nested = raw.replace(
            '"amount_minor":12500',
            '"amount_minor":12500,"amount_minor":12500',
            1,
        )
        for mutation in (duplicate_envelope, duplicate_nested):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                from_wire_json(mutation, PaymentSubject)

    def test_unknown_and_missing_fields_fail_closed_at_every_depth(self) -> None:
        original = json.loads(to_wire_json(payment_subject()))
        mutations = []
        unknown_top = json.loads(json.dumps(original))
        unknown_top["unexpected"] = True
        mutations.append(unknown_top)
        missing_top = json.loads(json.dumps(original))
        missing_top.pop("type")
        mutations.append(missing_top)
        unknown_data = json.loads(json.dumps(original))
        unknown_data["data"]["unexpected"] = True
        mutations.append(unknown_data)
        missing_data = json.loads(json.dumps(original))
        missing_data["data"].pop("currency")
        mutations.append(missing_data)
        unknown_nested = json.loads(json.dumps(original))
        unknown_nested["data"]["confirmed_reservation_anchor"]["unexpected"] = True
        mutations.append(unknown_nested)
        missing_nested = json.loads(json.dumps(original))
        missing_nested["data"]["confirmed_reservation_anchor"].pop("service")
        mutations.append(missing_nested)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                from_wire_json(json.dumps(mutation), PaymentSubject)

    def test_schema_version_requires_exact_supported_json_integer(self) -> None:
        original = json.loads(to_wire_json(payment_subject()))
        for value in (True, False, 1.0, "1", 0, 2, None):
            mutation = json.loads(json.dumps(original))
            mutation["schema_version"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                from_wire_json(json.dumps(mutation), PaymentSubject)

    def test_type_tag_and_expected_type_fail_closed(self) -> None:
        original = json.loads(to_wire_json(payment_subject()))
        for value in ("invented", "payment_subject_v2", 1, None):
            mutation = json.loads(json.dumps(original))
            mutation["type"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                from_wire_json(json.dumps(mutation), PaymentSubject)
        with self.assertRaises(ValueError):
            from_wire_json(to_wire_json(payment_subject()), PaymentEffectPolicy)
        with self.assertRaises(TypeError):
            from_wire_json(to_wire_json(payment_subject()), ExecutionOutcome)

    def test_bool_and_float_as_integer_fail_closed(self) -> None:
        original = json.loads(to_wire_json(payment_subject()))
        paths = (
            ("payment_version",),
            ("amount_minor",),
            ("confirmed_reservation_anchor", "amount_minor"),
        )
        for path in paths:
            for invalid in (True, False, 1.0, 12500.0):
                mutation = json.loads(json.dumps(original))
                target = mutation["data"]
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = invalid
                with self.subTest(path=path, invalid=invalid):
                    with self.assertRaises(ValueError):
                        from_wire_json(json.dumps(mutation), PaymentSubject)

    def test_invalid_and_noncanonical_enum_strings_fail_closed(self) -> None:
        original = json.loads(to_wire_json(payment_subject()))
        mutations = []
        invalid_method = json.loads(json.dumps(original))
        invalid_method["data"]["method"] = "cash"
        mutations.append(invalid_method)
        noncanonical_method = json.loads(json.dumps(original))
        noncanonical_method["data"]["method"] = "PIX"
        mutations.append(noncanonical_method)
        invalid_business = json.loads(json.dumps(original))
        invalid_business["data"]["business_unit"] = "HOSTEL"
        mutations.append(invalid_business)
        invalid_service = json.loads(json.dumps(original))
        invalid_service["data"]["confirmed_reservation_anchor"]["service"] = "package"
        mutations.append(invalid_service)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                from_wire_json(json.dumps(mutation), PaymentSubject)

    def test_naive_compact_and_non_utc_wire_timestamps_fail_closed(self) -> None:
        original = json.loads(to_wire_json(confirmed_anchor()))
        invalid_values = (
            "2027-02-01T12:00:00",
            "20270201T120000+0000",
            "2027-02-01T09:00:00-03:00",
            "2027-02-01T12:00:00Z",
            0,
        )
        for field_name in ("confirmed_at", "payment_deadline"):
            for invalid in invalid_values:
                mutation = json.loads(json.dumps(original))
                mutation["data"][field_name] = invalid
                with self.subTest(field=field_name, invalid=invalid):
                    with self.assertRaises(ValueError):
                        from_wire_json(json.dumps(mutation), ConfirmedReservationAnchor)

    def test_nonfinite_numbers_fail_closed(self) -> None:
        raw = to_wire_json(payment_subject())
        for token in ("NaN", "Infinity", "-Infinity"):
            mutation = raw.replace('"amount_minor":12500', f'"amount_minor":{token}', 1)
            with self.subTest(token=token), self.assertRaises(ValueError):
                from_wire_json(mutation, PaymentSubject)

    def test_malformed_nonobject_and_wrong_shape_payloads_fail_closed(self) -> None:
        original = json.loads(to_wire_json(payment_subject()))
        wrong_data = dict(original)
        wrong_data["data"] = []
        for raw in ("{", "[]", "null", '"text"', "1", json.dumps(wrong_data)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                from_wire_json(raw, PaymentSubject)
        with self.assertRaises(ValueError):
            from_wire_json(b"{}", PaymentSubject)

    def test_encoder_and_decoder_reject_types_outside_closed_universe(self) -> None:
        class InventedSubject(PaymentSubject):
            pass

        subject = payment_subject()
        invented = InventedSubject(
            **{field.name: getattr(subject, field.name) for field in fields(subject)}
        )
        for value in (invented, subject.confirmed_reservation_anchor.reservation_outcome, {}):
            with self.subTest(value=type(value).__name__), self.assertRaises(TypeError):
                to_wire_json(value)
        with self.assertRaises(TypeError):
            from_wire_json(to_wire_json(subject), InventedSubject)

    def test_encoder_and_semantic_hash_reject_mutated_invalid_schema(self) -> None:
        mutations = []

        bool_as_int = payment_subject()
        object.__setattr__(bool_as_int, "payment_version", True)
        mutations.append(bool_as_int)

        raw_enum = HandoffEffectPolicy.default_email_disabled()
        object.__setattr__(raw_enum, "internal_email", "optional")
        mutations.append(raw_enum)

        invalid_binding = confirmed_anchor()
        object.__setattr__(
            invalid_binding,
            "reservation_command_id",
            "command:reservation:synthetic:other",
        )
        mutations.append(invalid_binding)

        for mutation in mutations:
            for operation in (to_wire_json, semantic_hash):
                with self.subTest(
                    dto=type(mutation).__name__,
                    operation=operation.__name__,
                ):
                    with self.assertRaises(ValueError):
                        operation(mutation)

    def test_separate_decodes_do_not_share_nested_objects(self) -> None:
        raw = to_wire_json(payment_subject())
        first = from_wire_json(raw, PaymentSubject)
        second = from_wire_json(raw, PaymentSubject)
        self.assertIsNot(first, second)
        self.assertIsNot(
            first.confirmed_reservation_anchor,
            second.confirmed_reservation_anchor,
        )
        self.assertIsNot(
            first.confirmed_reservation_anchor.reservation_outcome,
            second.confirmed_reservation_anchor.reservation_outcome,
        )
        self.assertIsNot(
            first.confirmed_reservation_anchor.reservation_outcome.evidence,
            second.confirmed_reservation_anchor.reservation_outcome.evidence,
        )


if __name__ == "__main__":
    unittest.main()
