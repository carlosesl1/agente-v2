from __future__ import annotations

from datetime import timedelta

import pytest

from reservation_followup import PaymentMethod
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from reservation_followup.workers import (
    PaymentSettlementWorker,
    SettlementWorkerDisposition,
)
from tests.phase6_helpers import pix_visual_evidence
from tests.test_phase6_payment_claims import (
    LEASE_TTL,
    NOW,
    alternate_anchor,
    prepare_payment,
)
from v2_application.payments import (
    EvidenceConflict,
    EvidenceDisposition,
    V2PaymentEvidenceGateway,
)


class TimeoutSettlement:
    settlement_id = "settlement:v2-timeout"
    settlement_version = 1

    def __init__(self) -> None:
        self.calls = 0

    def prepare(self, command):
        return command.canonical_payload

    def dispatch(self, permit):
        self.calls += 1
        raise TimeoutError("after dispatch")


def test_pix_global_claim_duplicate_conflict_and_nonbank_flags(tmp_path) -> None:
    store = SQLiteFollowupUnitOfWork.open(tmp_path / "evidence.sqlite3")
    gateway = V2PaymentEvidenceGateway(store)
    shared = pix_visual_evidence()
    state, event = prepare_payment(
        store,
        suffix="v2-pix-first",
        method=PaymentMethod.PIX,
        evidence=shared,
    )
    try:
        first = gateway.accept(
            payment_id=state.subject.payment_id,
            expected_revision=3,
            event=event,
        )
        duplicate = gateway.accept(
            payment_id=state.subject.payment_id,
            expected_revision=3,
            event=event,
        )

        assert first.disposition is EvidenceDisposition.ACCEPTED
        assert duplicate.disposition is EvidenceDisposition.DUPLICATE
        assert first.visual_evidence_accepted is True
        assert first.bank_settlement_confirmed is False
        assert store._connection.execute(
            "SELECT count(*) FROM payment_evidence_claims"
        ).fetchone() == (1,)

        other_state, other_event = prepare_payment(
            store,
            suffix="v2-pix-other",
            method=PaymentMethod.PIX,
            evidence=shared,
            anchor=alternate_anchor("v2-pix-other"),
        )
        with pytest.raises(EvidenceConflict):
            gateway.accept(
                payment_id=other_state.subject.payment_id,
                expected_revision=3,
                event=other_event,
            )
        assert store._connection.execute(
            "SELECT count(*) FROM payment_evidence_claims"
        ).fetchone() == (1,)
    finally:
        store.close()


def test_settlement_timeout_after_fence_never_redispatches(tmp_path) -> None:
    store = SQLiteFollowupUnitOfWork.open(tmp_path / "settlement.sqlite3")
    gateway = V2PaymentEvidenceGateway(store)
    state, event = prepare_payment(
        store,
        suffix="v2-settlement-timeout",
        method=PaymentMethod.PIX,
        evidence=pix_visual_evidence(),
    )
    gateway.accept(
        payment_id=state.subject.payment_id,
        expected_revision=3,
        event=event,
    )
    settlement = TimeoutSettlement()
    worker = PaymentSettlementWorker(
        store=store,
        settlement=settlement,
        worker_id="worker:v2-settlement-timeout",
        lease_ttl=LEASE_TTL,
    )
    try:
        first = worker.run_once(now=NOW)
        second = worker.run_once(now=NOW + timedelta(seconds=1))

        assert first.disposition is SettlementWorkerDisposition.MANUAL_REVIEW
        assert second.disposition is SettlementWorkerDisposition.IDLE
        assert settlement.calls == 1
    finally:
        store.close()
