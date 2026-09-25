"""Documentary acceptance is not bank verification. Synthetic, offline fixtures."""

from dataclasses import replace
from datetime import timedelta
import hashlib
import json

import pytest

from tests.phase6_helpers import (
    T0,
    payment_evidence_trust as base_trust,
    payment_subject,
)


def payment_evidence_trust():
    return replace(base_trust(), wise_account_profile_id="receiver:profile:synthetic:1")


from tests.test_phase6_payment_claims import prepare_payment, alternate_anchor
from reservation_followup.payment import validate_evidence, evidence_claim_key
from reservation_followup.types import PaymentMethod
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_application.payments import V2PaymentEvidenceGateway, EvidenceConflict


def evidence(method=PaymentMethod.PIX, **changes):
    from reservation_followup.payment import VisualTransferEvidence

    values = dict(
        method=method,
        amount_minor=12500,
        currency="BRL",
        receiver_profile_id="receiver:profile:synthetic:1",
        transaction_id="E12345678202607241200A1B2C3D4E5F"
        if method is PaymentMethod.PIX
        else "7364910285",
        observed_at=T0 + timedelta(seconds=4),
        validated_at=T0 + timedelta(seconds=5),
        source_event_id="message:proof:1",
        source_sha256=hashlib.sha256(b"synthetic image").hexdigest(),
        recipient_identifier="account:fixture:1",
        recipient_name="Synthetic Hostel",
        payer_name="Acompanhante",
        assessment_hash=hashlib.sha256(b"comparison result").hexdigest(),
    )
    values.update(changes)
    return VisualTransferEvidence(**values)


def test_v9_document_observation_is_not_a_payment_or_customer_confirmation():
    from v2_adapters.hermes_model import _proposal
    from v2_contracts.model import ModelRequest

    request = ModelRequest(
        "request:proof", "manychat:12345", "event:proof", "Segue", "pt-BR", 0
    )
    raw = {
        "schema": "v2-model-proposal-v9",
        "intent": "inform",
        "reply_chunks": [{"text": "Vou conferir.", "expects_reply": False}],
        "facts": [],
        "read_requests": [],
        "selected_choice_refs": [],
        "selection_requested": False,
        "pending_action_disposition": None,
        "passengers": [],
        "payment_proof": None,
    }
    result = _proposal(json.dumps(raw).encode(), request)
    assert result.payment_proof is None
    assert result.effect_proposals == () and result.confirmed_summary_version is None
    assert result.reply_chunks == ("Vou conferir.",)


@pytest.mark.parametrize("method", [PaymentMethod.PIX, PaymentMethod.WISE])
def test_document_is_method_specific_and_nonbank(method):
    ev = evidence(method)
    subject = payment_subject(method=method)
    result = validate_evidence(subject, ev, payment_evidence_trust())
    assert result.method is method
    assert ev.human_review_status == "pending"
    assert ev.bank_settlement_confirmed is False
    assert not hasattr(ev, "signature_verified")
    assert evidence_claim_key(
        replace(ev, source_sha256=hashlib.sha256(b"reexport").hexdigest())
    ) == evidence_claim_key(ev)


@pytest.mark.parametrize("method", [PaymentMethod.PIX, PaymentMethod.WISE])
@pytest.mark.parametrize(
    "change",
    [
        dict(amount_minor=12501),
        dict(currency="USD"),
        dict(receiver_profile_id="other:account"),
        dict(observed_at=T0 - timedelta(seconds=1)),
        dict(validated_at=T0 + timedelta(days=3)),
    ],
)
def test_document_divergence_never_validates(method, change):
    with pytest.raises(ValueError):
        validate_evidence(
            payment_subject(method=method),
            evidence(method, **change),
            payment_evidence_trust(),
        )


@pytest.mark.parametrize("method", [PaymentMethod.PIX, PaymentMethod.WISE])
def test_document_persists_reopens_deduplicates_and_cannot_pay_another_reservation(
    tmp_path, method
):
    path = tmp_path / "followup.sqlite3"
    store = SQLiteFollowupUnitOfWork.open_v2(path)
    state, event = prepare_payment(
        store, suffix="visual", method=method, evidence=evidence(method)
    )
    event = replace(event, trust=payment_evidence_trust())
    first = V2PaymentEvidenceGateway(store).accept(
        payment_id=state.subject.payment_id, expected_revision=3, event=event
    )
    assert first.visual_evidence_accepted and not first.bank_settlement_confirmed
    store.close()
    with SQLiteFollowupUnitOfWork.open_v2(path) as reopened:
        saved = reopened.load_payment(first.payment_id)
        assert saved.evidence_record.evidence == evidence(method)
        assert saved.evidence_record.evidence.human_review_status == "pending"
        assert (
            V2PaymentEvidenceGateway(reopened)
            .accept(payment_id=first.payment_id, expected_revision=3, event=event)
            .disposition.value
            == "duplicate"
        )
        other, other_event = prepare_payment(
            reopened,
            suffix="other",
            method=method,
            evidence=evidence(method),
            anchor=alternate_anchor("visual-other"),
        )
        other_event = replace(other_event, trust=payment_evidence_trust())
        with pytest.raises(EvidenceConflict):
            V2PaymentEvidenceGateway(reopened).accept(
                payment_id=other.subject.payment_id,
                expected_revision=3,
                event=other_event,
            )
        assert reopened._connection.execute(
            "SELECT count(*) FROM payment_commands"
        ).fetchone() == (1,)
