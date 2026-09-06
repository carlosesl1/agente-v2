from __future__ import annotations

import pytest

from reservation_followup import PaymentMethod
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from tests.phase6_helpers import pix_visual_evidence
from tests.test_phase6_payment_claims import prepare_payment
from v2_application.financial_webhooks import (
    FinancialEvidenceAcceptor,
    FinancialProvider,
    VerifiedFinancialWebhook,
)
from v2_application.payments import EvidenceDisposition


@pytest.mark.parametrize("schema_version", (1, 2))
def test_evidence_acceptor_opens_v2_and_upgrades_exact_v1_without_reclaim(
    tmp_path, schema_version,
) -> None:
    path = tmp_path / "followup.sqlite3"
    opener = (
        SQLiteFollowupUnitOfWork.open
        if schema_version == 1
        else SQLiteFollowupUnitOfWork.open_v2
    )
    evidence = pix_visual_evidence()
    with opener(path) as store:
        state, event = prepare_payment(
            store, suffix="opener-compat", method=PaymentMethod.PIX, evidence=evidence,
        )
    webhook = VerifiedFinancialWebhook(
        provider=FinancialProvider.PIX,
        external_event_id=evidence.normalized_e2e,
        payment_id=state.subject.payment_id,
        expected_revision=3,
        event=event,
        body_hash="a" * 64,
    )
    acceptor = FinancialEvidenceAcceptor(path)
    first = acceptor.accept(webhook)
    duplicate = acceptor.accept(webhook)
    assert first.disposition is EvidenceDisposition.ACCEPTED
    assert duplicate.disposition is EvidenceDisposition.DUPLICATE
    assert first.visual_evidence_accepted is True
    assert first.bank_settlement_confirmed is False
    with SQLiteFollowupUnitOfWork.open_v2(path) as store:
        assert store._connection.execute(
            "SELECT count(*) FROM payment_evidence_claims"
        ).fetchone() == (1,)
