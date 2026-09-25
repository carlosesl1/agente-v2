"""Offline connected owner/provider lab. All HTTP is synthetic MockTransport."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import base64
import hashlib
import io

import pytest
from PIL import Image
from reservation_execution import DispatchRequest
from reservation_domain import ExecutionCertainty, dumps_command
from tests.test_v2_outcome_projector import _package_command, _persist, NOW
from v2_application.reservations import ReservationAllocator

from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_contracts.payments import PaymentMethod
from v2_contracts.payments import BusinessUnit
from v2_application.payments import (
    SQLitePaymentInitiationStore,
    PaymentInitiationWorker,
    PaymentService,
)
from v2_application.outcome_projector import ReservationOutcomeProjector
from v2_application.visual_proofs import VisualProofService
from v2_adapters.proof_media import retain_bytes
from v2_adapters.wise import WiseInstructionAdapter
from v2_contracts.model import ModelRequest, ModelAttachment, AttachmentContentStatus
from v2_contracts.payment_proof import PaymentProofObservation

LEAD = "manychat:1873018537"


def image():
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, "PNG")
    raw = stream.getvalue()
    return ModelAttachment(
        "image/png",
        AttachmentContentStatus.IMAGE_READY,
        "event:receipt",
        hashlib.sha256(raw).hexdigest(),
        "data:image/png;base64," + base64.b64encode(raw).decode(),
    )


def lab(tmp_path, method="pix", unit="agency"):
    command = next(
        c
        for c in ReservationAllocator()
        .allocate(_package_command(payment_method=method))
        .commands
        if c.payload.components[0].service.value
        == ("lodging" if unit == "hostel" else "activity")
    )
    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
    _persist(execution, (command,))
    claim = execution.claim_command(
        worker_id="worker:reservation", now=NOW, lease_ttl=timedelta(seconds=30)
    )
    dispatch = DispatchRequest.from_command(command, dumps_command(command))
    permit = execution.fence_dispatch(claim, dispatch, now=NOW)
    execution.record_outcome(
        permit,
        command.outcome(
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
            normalized_status="accepted",
            provider_reference="provider:cloudbeds:123"
            if unit == "hostel"
            else "provider:bokun:id:456",
            evidence=(dispatch.payload_hash,),
        ),
        now=NOW,
    )
    boundary = None
    followup = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
    payments = SQLitePaymentInitiationStore(
        tmp_path / "payments.sqlite3", result_encryption_key=b"p" * 32
    )
    leads = SimpleNamespace(
        lead_id_for_command=lambda cid: LEAD if cid == command.command_id else None
    )
    profiles = {
        "receiver:hostel:primary": "Transfer details",
        "receiver:agency:primary": "Transfer details",
    }
    percentages = {p: 20 for p in profiles}
    wise = WiseInstructionAdapter(
        instructions=profiles, payment_percentages=percentages
    )

    class Pix:
        def instruction(self, obligation):
            return replace(wise.instruction(obligation), method=PaymentMethod.PIX)

    class NoStripe:
        def create_link(self, *args):
            raise AssertionError("card not allowed")

    payservice = PaymentService(stripe=NoStripe(), wise=wise, pix=Pix())
    projector = ReservationOutcomeProjector(
        execution=execution,
        payment_store=payments,
        receiver_profiles={
            BusinessUnit.HOSTEL: "receiver:hostel:primary",
            BusinessUnit.AGENCY: "receiver:agency:primary",
        },
        enabled_methods=(PaymentMethod(method),),
    )
    projector.run_once(now=NOW + timedelta(seconds=2))
    worker = PaymentInitiationWorker(
        store=payments,
        payments=payservice,
        worker_id="worker:proof",
        lease_ttl=timedelta(minutes=1),
    )
    worker.run_once(now=NOW + timedelta(seconds=3))
    clock = lambda: NOW + timedelta(seconds=10)
    receivers = {
        unit + ":" + method: {
            "profile_id": "receiver:" + unit + ":primary",
            "identifier_kind": "tax_id",
            "identifier": "12.345.678/0001-90",
            "recipient_names": ["Receiver Ltd"],
        }
    }
    service = VisualProofService(
        execution=execution,
        payments=payments,
        followup=followup,
        lead_resolver=leads,
        receivers=receivers,
        archive=tmp_path / "proofs",
        retain=retain_bytes,
        clock=clock,
    )
    attachment = image()
    request = ModelRequest(
        "request:proof",
        LEAD,
        "event:receipt",
        "Segue comprovante",
        "pt-BR",
        0,
        attachments=(attachment,),
    )
    candidate = service.context(LEAD)[0]
    proof = PaymentProofObservation(
        attachment.source_event_id,
        attachment.source_sha256,
        candidate["payment_id"],
        method,
        "completed",
        candidate["amount_minor"],
        candidate["currency"],
        "12345678000190",
        "Receiver Ltd",
        "Third Party Payer",
        "E12345678202607241200A1B2C3D4E5F" if method == "pix" else "987654321",
        (NOW + timedelta(seconds=5)).isoformat(),
        (),
    )
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("method", ("pix", "wise"))
@pytest.mark.parametrize("unit", ("hostel", "agency"))
def test_visual_owner_accepts_then_restart_reexport_is_no_new_command(
    tmp_path, method, unit
):
    f = lab(tmp_path, method, unit)
    result = f.service.accept(request=f.request, proof=f.proof)
    assert result["analysis"] == "accepted", result
    assert result["human_review"] == "pending"
    assert result["bank_settlement_confirmed"] is False
    assert result["settlement"] == "settlement_queued"
    state = f.followup.load_payment(result["financial_payment_id"])
    assert state.subject.amount_minor == f.proof.amount_minor
    f.followup.close()
    f.followup = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
    f.service.followup = f.followup
    again = f.service.accept(
        request=f.request, proof=replace(f.proof, payer_name="Third Party Payer 2")
    )
    assert again["analysis"] == "duplicate"
    assert f.followup._connection.execute(
        "select count(*) from payment_commands"
    ).fetchone() == (1,)
    assert (
        f.followup.find_active_handoff_by_lead_hash(
            hashlib.sha256(LEAD.encode()).hexdigest()
        )
        is None
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"status": "scheduled"}, "transfer_not_completed"),
        ({"status": "pending"}, "transfer_not_completed"),
        ({"currency": "USD"}, "currency_mismatch"),
        ({"recipient_identifier": "wrong"}, "recipient_identifier_mismatch"),
        ({"recipient_name": "Other receiver"}, "beneficiary_name_mismatch"),
        ({"amount_minor": 1}, "amount_mismatch"),
        ({"payer_name": None}, "missing:payer_name"),
        ({"transaction_id": None}, "missing:transaction_id"),
        (
            {"transferred_at": (NOW - timedelta(days=1)).isoformat()},
            "transaction_time_mismatch",
        ),
        (
            {"transferred_at": (NOW + timedelta(days=1)).isoformat()},
            "transaction_time_mismatch",
        ),
        ({"payment_id": "other:payment"}, "payment_target_unresolved"),
        ({"issues": ("illegible",)}, "illegible"),
    ],
)
def test_visual_comparison_never_queues_mismatch(tmp_path, changes, reason):
    f = lab(tmp_path)
    result = f.service.accept(request=f.request, proof=replace(f.proof, **changes))
    assert reason in result["reasons"], result
    assert f.followup._connection.execute(
        "select count(*) from payment_commands"
    ).fetchone() == (0,)
    assert tuple((tmp_path / "proofs" / "assessments").iterdir())


def test_other_lead_cannot_target_original_receipt(tmp_path):
    f = lab(tmp_path)
    result = f.service.accept(
        request=replace(f.request, lead_id="manychat:99999"), proof=f.proof
    )
    assert result["reasons"] == ["payment_target_unresolved"]


def test_hash_or_event_from_another_attachment_is_rejected_before_financial_owner(
    tmp_path,
):
    f = lab(tmp_path)
    with pytest.raises(ValueError, match="origin"):
        f.service.accept(
            request=f.request, proof=replace(f.proof, source_sha256="a" * 64)
        )
