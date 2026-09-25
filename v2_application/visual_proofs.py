"""Bind Maya's visual facts to issued instructions and the canonical financial owner.

No provider writes. Only accepted documents enqueue the existing settlement. The
archive is evidence, not a second payment ledger; financial status stays in UoW.
"""

from datetime import UTC, timedelta, datetime
import hashlib
import json
from pathlib import Path
import unicodedata

from reservation_domain import ExecutionCertainty, loads_outcome
from reservation_followup.payment import (
    VisualTransferEvidence,
    evidence_claim_key,
    FinancialSummaryRecorded,
    FinancialConfirmationReceived,
    PaymentEvidenceRecorded,
    PaymentMethodSelected,
    PaymentEvidenceTrust,
    financial_summary_hash,
)
from reservation_followup.types import (
    BusinessUnit,
    ConfirmedReservationAnchor,
    PaymentSubject,
    PaymentMethod,
    PaymentEffectPolicy,
    EffectRequirement,
)
from v2_contracts.payments import PaymentInstruction
from v2_contracts.payment_proof import PaymentProofObservation
from v2_contracts.model import AttachmentContentStatus
from v2_application.lead_identity import payment_id_for_command
from v2_application.outcome_projector import _opaque, _minor_units
from v2_application.payments import V2PaymentEvidenceGateway, EvidenceConflict
from v2_application.recovery import HandoffCoordinator, HandoffEffectGuard
from reservation_followup.handoff import HandoffReasonCode


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def normalize_identifier(value, kind):
    value = unicodedata.normalize("NFKC", value).strip()
    if kind == "tax_id":
        return value.translate(str.maketrans("", "", ".-/ "))
    if kind == "iban":
        return "".join(value.split()).upper()
    return value.casefold()


def normalized_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def validate_receivers(receivers):
    if type(receivers) is not dict or not receivers:
        raise ValueError("structured receipt receivers required")
    for key, row in receivers.items():
        if key not in {
            f"{unit}:{method}"
            for unit in ("hostel", "agency")
            for method in ("pix", "wise")
        }:
            raise ValueError("unknown receipt receiver scope")
        if type(row) is not dict or set(row) != {
            "profile_id",
            "identifier",
            "identifier_kind",
            "recipient_names",
        }:
            raise ValueError("receipt receiver fields mismatch")
        if row["identifier_kind"] not in (
            "tax_id",
            "email",
            "phone",
            "iban",
            "account",
            "random_key",
        ):
            raise ValueError("receiver identifier kind invalid")
        if any(
            type(row[f]) is not str or not row[f].strip()
            for f in ("profile_id", "identifier")
        ):
            raise ValueError("receiver identity required")
        if (
            type(row["recipient_names"]) is not list
            or not row["recipient_names"]
            or any(type(n) is not str or not n.strip() for n in row["recipient_names"])
        ):
            raise ValueError("receiver beneficiary names required")
    return json.loads(canonical(receivers))


class VisualProofService:
    def __init__(
        self,
        *,
        execution,
        payments,
        followup,
        lead_resolver,
        receivers,
        archive,
        retain,
        clock,
    ):
        self.execution, self.payments, self.followup = execution, payments, followup
        self.leads, self.receivers = lead_resolver, validate_receivers(receivers)
        self.archive, self.clock, self.retain = Path(archive), clock, retain

    def _candidates(self, lead):
        result = []
        for command, ledger in self.execution.list_outcome_projection_inputs():
            if self.leads.lead_id_for_command(command.command_id) != lead:
                continue
            outcome = loads_outcome(ledger.outcome_json)
            if outcome.certainty is not ExecutionCertainty.EFFECT_CONFIRMED:
                continue
            if (
                ledger.outcome_hash
                != hashlib.sha256(ledger.outcome_json.encode()).hexdigest()
            ):
                raise RuntimeError("reservation outcome integrity mismatch")
            for offer, _, issued_at in self.payments.completed_offer_events():
                if (
                    type(offer) is not PaymentInstruction
                    or offer.payment_id != payment_id_for_command(command)
                ):
                    continue
                records = [
                    r
                    for r in self.payments.context_for_payment(offer.payment_id)
                    if r.offer == offer
                ]
                if len(records) != 1:
                    raise RuntimeError("issued instruction ownership ambiguous")
                obligation = records[0].selection.obligation
                if (
                    offer.reservation_anchor_id
                    != _opaque("reservation-anchor", command.command_id)
                    or offer.economic_version != command.draft_version
                    or command.payload.terms.payment_method != offer.method.value
                    or obligation.amount_minor
                    != _minor_units(command.payload.components[0].total.amount)
                    or offer.receiver_profile_id != obligation.receiver_profile_id
                    or offer.currency not in (None, obligation.currency)
                ):
                    raise RuntimeError("issued instruction binding mismatch")
                if offer.requested_amount_minor is None:
                    continue  # Legacy prose is not mechanically recoverable authority.
                config = self.receivers.get(
                    obligation.business_unit.value + ":" + offer.method.value
                )
                if config is None or config["profile_id"] != offer.receiver_profile_id:
                    continue
                anchor = ConfirmedReservationAnchor(
                    command.workflow_id,
                    command.command_id,
                    command.subject_signature,
                    ledger.outcome_hash,
                    outcome,
                    outcome.provider_reference,
                    command.payload.components[0].service,
                    BusinessUnit(obligation.business_unit.value),
                    _opaque("payment-target", command.command_id),
                    obligation.amount_minor,
                    obligation.currency,
                    obligation.receiver_profile_id,
                    ledger.updated_at,
                    ledger.updated_at + timedelta(hours=24),
                )
                result.append((offer, issued_at, anchor, config))
        return result

    def context(self, lead):
        return tuple(
            {
                "payment_id": o.payment_id,
                "method": o.method.value,
                "business_unit": a.business_unit.value,
                "amount_minor": o.requested_amount_minor,
                "currency": o.currency,
                "issued_at": issued.isoformat(),
                "deadline": a.payment_deadline.isoformat(),
                "reservation": a.provider_reference,
            }
            for o, issued, a, _ in self._candidates(lead)
        )

    def _handoff(self, lead, anchor, event_id):
        return (
            HandoffCoordinator(store=self.followup)
            .open_exception_once(
                lead_id=lead,
                workflow_id=anchor.reservation_workflow_id,
                source_event_id=_opaque("proof-incident", event_id),
                reason_code=HandoffReasonCode.OPERATIONAL_REVIEW,
                now=self.clock(),
            )
            .workflow.request.handoff_id
        )

    def accept(self, *, request, proof):
        result = self._accept(request=request, proof=proof)
        admission = {
            "schema": "v2-visual-admission-v1",
            "lead_id": request.lead_id,
            "source_event_id": request.source_event_id,
            "at": self.clock().isoformat(),
            "observed": proof.to_dict(),
            "result": result,
        }
        self.retain(self.archive / "admissions", canonical(admission))
        return result

    def _accept(self, *, request, proof):
        if type(proof) is not PaymentProofObservation:
            raise TypeError("exact proof observation required")
        matches = [
            a
            for a in request.attachments
            if a.content_status is AttachmentContentStatus.IMAGE_READY
            and (a.source_event_id, a.source_sha256)
            == (proof.source_event_id, proof.source_sha256)
        ]
        if not matches or (len(matches) != 1 and not (
            matches[0].document_sha256 and len({a.document_sha256 for a in matches}) == 1
        )):
            raise ValueError("proof origin does not bind one received image")
        from v2_contracts.model_wire import validate_image_data_url

        self.retain(
            self.archive / "images", validate_image_data_url(matches[0].image_data_url)
        )
        document = None
        if matches[0].document_sha256:
            pages = [a for a in request.attachments if
                     (a.source_event_id, a.document_sha256) ==
                     (proof.source_event_id, matches[0].document_sha256)]
            original = (self.archive / "images" / matches[0].document_sha256).read_bytes()
            if hashlib.sha256(original).hexdigest() != matches[0].document_sha256:
                raise ValueError("document origin archive mismatch")
            document = {"sha256": matches[0].document_sha256, "media_type": "application/pdf",
                        "pages": [{"page_number": a.page_number, "source_sha256": a.source_sha256} for a in pages]}
            for page in pages:
                self.retain(self.archive / "images", validate_image_data_url(page.image_data_url))
        now = self.clock()
        rows = [
            r
            for r in self._candidates(request.lead_id)
            if r[0].payment_id == proof.payment_id
        ]
        result = {
            "analysis": "incomplete",
            "reasons": [],
            "payment_id": proof.payment_id,
            "bank_settlement_confirmed": False,
            "human_review": None,
            "settlement": "not_called",
        }
        expected = None
        if len(rows) != 1:
            result["reasons"] = ["payment_target_unresolved"]
        else:
            offer, issued, anchor, config = rows[0]
            expected = {
                "receiver": config,
                "amount_minor": offer.requested_amount_minor,
                "currency": offer.currency,
                "method": offer.method.value,
                "issued_at": issued.isoformat(),
                "deadline": anchor.payment_deadline.isoformat(),
                "reservation": anchor.provider_reference,
            }
            missing = [name for name, value in proof.to_dict().items() if value is None]
            reasons = list(proof.issues) + ["missing:" + name for name in missing]
            if not reasons:
                if proof.method != offer.method.value:
                    reasons.append("method_mismatch")
                if proof.status != "completed":
                    reasons.append("transfer_not_completed")
                if proof.amount_minor != offer.requested_amount_minor:
                    reasons.append("amount_mismatch")
                if proof.currency != offer.currency:
                    reasons.append("currency_mismatch")
                if normalize_identifier(
                    proof.recipient_identifier, config["identifier_kind"]
                ) != normalize_identifier(
                    config["identifier"], config["identifier_kind"]
                ):
                    reasons.append("recipient_identifier_mismatch")
                if normalized_name(proof.recipient_name) not in {
                    normalized_name(n) for n in config["recipient_names"]
                }:
                    reasons.append("beneficiary_name_mismatch")
                observed = datetime.fromisoformat(proof.transferred_at).astimezone(UTC)
                if not issued <= observed <= now:
                    reasons.append("transaction_time_mismatch")
                if observed > anchor.payment_deadline:
                    reasons.append("transaction_after_deadline")
            result["reasons"] = reasons
            result["analysis"] = (
                "incomplete"
                if missing or proof.issues
                else "divergent"
                if reasons
                else "accepted"
            )
        assessment = {
            "schema": "v2-visual-assessment-v1",
            "stage": "document_comparison",
            "lead_id": request.lead_id,
            "observed": proof.to_dict(),
            **({"document": document} if document is not None else {}),
            "expected": expected,
            "analysis": result["analysis"],
            "reasons": result["reasons"],
        }
        assessment_hash = self.retain(
            self.archive / "assessments", canonical(assessment)
        )
        result["assessment_hash"] = assessment_hash
        if result["analysis"] != "accepted":
            return result
        try:
            ev = VisualTransferEvidence(
                PaymentMethod(proof.method),
                proof.amount_minor,
                proof.currency,
                anchor.receiver_profile_id,
                proof.transaction_id,
                observed,
                now,
                proof.source_event_id,
                proof.source_sha256,
                proof.recipient_identifier,
                proof.recipient_name,
                proof.payer_name,
                assessment_hash,
            )
        except ValueError:
            return {
                **result,
                "analysis": "divergent",
                "reasons": ["transaction_identity_invalid"],
            }
        policy = PaymentEffectPolicy(
            EffectRequirement.REQUIRED,
            EffectRequirement.REQUIRED,
            EffectRequirement.DISABLED,
            EffectRequirement.DISABLED,
        )
        state = self.followup.open_payment(anchor, policy).state
        payment_id = state.subject.payment_id
        # Reexport/resend uses transaction identity, never the document hash.
        if state.evidence_record is not None:
            old = state.evidence_record
            if evidence_claim_key(old.evidence) != evidence_claim_key(ev):
                return {
                    **result,
                    "analysis": "divergent",
                    "reasons": ["payment_already_has_other_evidence"],
                    "handoff_id": self._handoff(
                        request.lead_id, anchor, proof.source_event_id
                    ),
                }
            return {
                **result,
                "analysis": "duplicate",
                "human_review": "pending",
                "settlement": state.status.value,
                "financial_payment_id": payment_id,
            }
        if now > anchor.payment_deadline:
            return {
                **result,
                "analysis": "divergent",
                "reasons": ["payment_expired"],
                "handoff_id": self._handoff(
                    request.lead_id, anchor, proof.source_event_id
                ),
            }
        HandoffEffectGuard(store=self.followup).assert_allowed(lead_id=request.lead_id)

        def apply(event):
            self.followup.apply_payment(
                payment_id, self.followup._load_payment(payment_id)[1], event
            )

        apply(
            PaymentMethodSelected(
                _opaque("visual-method", payment_id), payment_id, ev.method, issued
            )
        )
        subject = PaymentSubject.from_anchor(
            anchor,
            payment_id=payment_id,
            method=ev.method,
            amount_minor=offer.requested_amount_minor,
        )
        summary = financial_summary_hash(subject)
        apply(
            FinancialSummaryRecorded(
                _opaque("visual-summary", payment_id), subject, summary, issued
            )
        )
        apply(
            FinancialConfirmationReceived(
                _opaque("visual-confirm", payment_id),
                payment_id,
                subject.payment_version,
                subject.economic_signature,
                summary,
                _opaque("visual-transfer", ev.method.value, ev.transaction_id),
                observed,
            )
        )
        event = PaymentEvidenceRecorded(
            _opaque("visual-evidence", ev.method.value, ev.transaction_id),
            payment_id,
            subject.payment_version,
            subject.economic_signature,
            ev,
            PaymentEvidenceTrust(
                anchor.receiver_profile_id,
                "closed:wise-signer",
                anchor.receiver_profile_id,
                "closed:stripe",
            ),
            now,
        )
        try:
            V2PaymentEvidenceGateway(self.followup).accept(
                payment_id=payment_id,
                expected_revision=self.followup._load_payment(payment_id)[1],
                event=event,
            )
        except EvidenceConflict:
            return {
                **result,
                "analysis": "divergent",
                "reasons": ["transaction_already_used"],
                "handoff_id": self._handoff(
                    request.lead_id, anchor, proof.source_event_id
                ),
            }
        return {
            **result,
            "human_review": "pending",
            "financial_payment_id": payment_id,
            "settlement": self.followup.load_payment(payment_id).status.value,
        }
