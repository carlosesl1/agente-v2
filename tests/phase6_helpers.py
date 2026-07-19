"""Synthetic, capability-free fixtures for Phase 6 shared contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from reservation_domain import (
    ExecutionCertainty,
    ExecutionOutcome,
    ServiceKind,
    dumps_outcome,
)
from reservation_followup import (
    BusinessUnit,
    ConfirmedReservationAnchor,
    EffectRequirement,
    HandoffEffectPolicy,
    HandoffReasonCode,
    HandoffRequested,
    PaymentEffectPolicy,
    PaymentMethod,
    PaymentEvidenceTrust,
    PaymentSubject,
    PixProofStatus,
    PixVisualEvidence,
)

UTC = timezone.utc
T0 = datetime(2027, 2, 1, 12, 0, tzinfo=UTC)


def outcome(
    *,
    certainty: ExecutionCertainty = ExecutionCertainty.EFFECT_CONFIRMED,
) -> ExecutionOutcome:
    provider_reference = (
        None
        if certainty is ExecutionCertainty.NOT_CALLED
        else "provider:reservation:synthetic:1"
    )
    evidence = ("e" * 64,) if certainty is ExecutionCertainty.EFFECT_CONFIRMED else ()
    return ExecutionOutcome(
        command_id="command:reservation:synthetic:1",
        certainty=certainty,
        normalized_status={
            ExecutionCertainty.NOT_CALLED: "provider_not_called",
            ExecutionCertainty.CALLED_NO_EFFECT: "called_without_effect",
            ExecutionCertainty.EFFECT_CONFIRMED: "reservation_created",
            ExecutionCertainty.CALLED_UNKNOWN: "provider_result_unknown",
        }[certainty],
        provider_reference=provider_reference,
        evidence=evidence,
    )


def outcome_hash(value: ExecutionOutcome) -> str:
    return hashlib.sha256(dumps_outcome(value).encode("utf-8")).hexdigest()


def confirmed_anchor(
    *,
    outcome: ExecutionOutcome | None = None,
    **changes: object,
) -> ConfirmedReservationAnchor:
    selected_outcome = outcome or globals()["outcome"]()
    values: dict[str, object] = {
        "reservation_workflow_id": "workflow:reservation:synthetic:1",
        "reservation_command_id": selected_outcome.command_id,
        "reservation_subject_signature": "a" * 64,
        "reservation_outcome_hash": outcome_hash(selected_outcome),
        "reservation_outcome": selected_outcome,
        "provider_reference": selected_outcome.provider_reference,
        "service": ServiceKind.LODGING,
        "business_unit": BusinessUnit.HOSTEL,
        "payment_target_id": "target:reservation:synthetic:1",
        "amount_minor": 12500,
        "currency": "BRL",
        "receiver_profile_id": "receiver:profile:synthetic:1",
        "confirmed_at": T0,
        "payment_deadline": T0 + timedelta(days=2),
    }
    values.update(changes)
    return ConfirmedReservationAnchor(**values)


def economic_signature(
    *,
    amount_minor: int,
    currency: str,
    receiver_profile_id: str,
    business_unit: BusinessUnit,
    payment_target_id: str,
) -> str:
    material = json.dumps(
        {
            "amount_minor": amount_minor,
            "business_unit": business_unit.value,
            "currency": currency,
            "payment_target_id": payment_target_id,
            "receiver_profile_id": receiver_profile_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def payment_subject(**changes: object) -> PaymentSubject:
    baseline_anchor = confirmed_anchor()
    anchor = changes.pop("confirmed_reservation_anchor", baseline_anchor)
    values: dict[str, object] = {
        "payment_id": "payment:synthetic:1",
        "payment_version": 1,
        "confirmed_reservation_anchor": anchor,
        "amount_minor": baseline_anchor.amount_minor,
        "currency": baseline_anchor.currency,
        "receiver_profile_id": baseline_anchor.receiver_profile_id,
        "business_unit": baseline_anchor.business_unit,
        "payment_target_id": baseline_anchor.payment_target_id,
        "method": PaymentMethod.PIX,
    }
    values.update(changes)
    if "economic_signature" not in values:
        economic_values = (
            values["amount_minor"],
            values["currency"],
            values["receiver_profile_id"],
            values["business_unit"],
            values["payment_target_id"],
        )
        if (
            type(economic_values[0]) is int
            and type(economic_values[1]) is str
            and type(economic_values[2]) is str
            and type(economic_values[3]) is BusinessUnit
            and type(economic_values[4]) is str
        ):
            values["economic_signature"] = economic_signature(
                amount_minor=values["amount_minor"],
                currency=values["currency"],
                receiver_profile_id=values["receiver_profile_id"],
                business_unit=values["business_unit"],
                payment_target_id=values["payment_target_id"],
            )
        else:
            values["economic_signature"] = "0" * 64
    return PaymentSubject(**values)


def handoff_requested(**changes: object) -> HandoffRequested:
    values: dict[str, object] = {
        "handoff_id": "handoff:synthetic:store:1",
        "lead_key_hash": "b" * 64,
        "incident_key": "incident:synthetic:store:1",
        "reason_code": HandoffReasonCode.CUSTOMER_REQUESTED,
        "source_event_id": "source:event:synthetic:store:1",
        "reservation_anchor": None,
        "requested_at": T0,
    }
    values.update(changes)
    return HandoffRequested(**values)


def optional_email_policy() -> HandoffEffectPolicy:
    return HandoffEffectPolicy(
        queue_state=EffectRequirement.REQUIRED,
        customer_acknowledgement=EffectRequirement.REQUIRED,
        internal_email=EffectRequirement.OPTIONAL,
    )


def payment_effect_policy() -> PaymentEffectPolicy:
    return PaymentEffectPolicy(
        paid_state_transition=EffectRequirement.REQUIRED,
        customer_payment_confirmation=EffectRequirement.REQUIRED,
        internal_payment_email=EffectRequirement.DISABLED,
        booking_form=EffectRequirement.DISABLED,
    )


def _digest(payload: dict[str, object]) -> str:
    material = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def pix_visual_evidence(**changes: object) -> PixVisualEvidence:
    values: dict[str, object] = {
        "proof_amount_minor": 12500,
        "proof_currency": "BRL",
        "proof_receiver_profile_id": "receiver:profile:synthetic:1",
        "proof_status": PixProofStatus.PAID,
        "normalized_e2e": "E1234567820270201ABCDEF12345",
        "observed_at": T0,
        "extractor_id": "extractor:synthetic:pix:store:1",
        "extractor_version": "extractor-version:synthetic:store:1",
    }
    values.update(changes)
    if "evidence_hash" not in values:
        payload = {
            "type": "pix_visual_evidence",
            **{
                key: (
                    value.value
                    if hasattr(value, "value")
                    else value.isoformat()
                    if hasattr(value, "isoformat")
                    else value
                )
                for key, value in values.items()
            },
        }
        values["evidence_hash"] = _digest(payload)
    return PixVisualEvidence(**values)


def payment_evidence_trust() -> PaymentEvidenceTrust:
    return PaymentEvidenceTrust(
        pix_receiver_profile_id="receiver:profile:synthetic:1",
        wise_signer_profile_id="wise-signer:profile:synthetic:1",
        wise_account_profile_id="wise-account:profile:synthetic:1",
        stripe_account_profile_id="stripe-account:profile:synthetic:1",
    )
