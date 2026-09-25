from types import SimpleNamespace
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs
import json
import httpx
import pytest
from tests.test_v2_visual_proof_journey import lab, LEAD
from reservation_followup.workers import PaymentSettlementWorker
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_host.stripe_settlement import StripeSettlementAdapter


def financial_worker(f, *, status="active", behavior="success", enabled=True):
    f.requests = []
    amount = Decimal(f.proof.amount_minor) / 100

    def respond(req):
        f.requests.append(req)
        if req.method == "GET":
            if req.url.path.endswith("getReservation"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {
                            "reservationID": "123",
                            "propertyID": "789",
                            "status": "confirmed" if status == "active" else "canceled",
                            "currency": "BRL",
                            "balance": "10000.00",
                            "balanceDetailed": {"paid": "0.00"},
                        },
                    },
                )
            if req.url.path.endswith("getPaymentMethods"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {
                            "propertyID": "789",
                            "methods": [{"method": "PIX"}, {"method": "Wise"}],
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "bookingId": 456,
                    "status": "CONFIRMED" if status == "active" else "CANCELLED",
                    "currency": "BRL",
                    "totalPaid": 0,
                    "totalDue": 10000,
                },
            )
        assert req.method == "POST"
        if behavior == "timeout":
            raise httpx.ReadTimeout("synthetic lost response", request=req)
        if f.unit == "hostel":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "paymentID": "pay:fixture",
                    "transactionID": "txn:fixture",
                },
            )
        payload = json.loads(req.content)["payment"]
        return httpx.Response(
            200,
            json={
                "bookingId": 456,
                "status": "CONFIRMED",
                "currency": "BRL",
                "totalPaid": float(amount),
                "customerPayments": [
                    {
                        "id": 789,
                        "amount": float(amount),
                        "currency": "BRL",
                        "paymentReferenceId": payload["paymentReferenceId"],
                    }
                ],
            },
        )

    adapter = StripeSettlementAdapter(
        store=f.followup,
        execution=f.execution,
        cloudbeds_api_key="synthetic",
        cloudbeds_property_id="789",
        bokun_access_key="synthetic",
        bokun_secret_key="synthetic",
        effect_guard=SimpleNamespace(allows_workflow=lambda _: True),
        lead_resolver=f.leads,
        allowed_subscribers=(LEAD.split(":")[1],),
        clock=f.clock,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        enabled_methods=("pix", "wise") if enabled else ("stripe",),
    )
    return PaymentSettlementWorker(
        store=f.followup,
        settlement=adapter,
        worker_id="worker:visual-settlement",
        lease_ttl=timedelta(seconds=60),
        clock=f.clock,
    )


@pytest.mark.parametrize("method", ("pix", "wise"))
@pytest.mark.parametrize("unit", ("hostel", "agency"))
def test_visual_provider_payment_once_with_correct_method_and_review_pending(
    tmp_path, method, unit
):
    f = lab(tmp_path, method, unit)
    accepted = f.service.accept(request=f.request, proof=f.proof)
    worker = financial_worker(f)
    assert worker.run_once(now=f.clock()).disposition.value == "settled"
    state = f.followup.load_payment(accepted["financial_payment_id"])
    assert state.status.value == "paid"
    assert state.evidence_record.evidence.human_review_status == "pending"
    assert state.evidence_record.evidence.bank_settlement_confirmed is False
    posts = [r for r in f.requests if r.method == "POST"]
    assert len(posts) == 1
    if unit == "hostel":
        payload = parse_qs(posts[0].content.decode())
        assert payload["type"] == ["PIX" if method == "pix" else "Wise"]
        assert payload["amount"] == [format(Decimal(f.proof.amount_minor) / 100, ".2f")]
    else:
        payload = json.loads(posts[0].content)["payment"]
        assert payload["paymentType"] == "POINT_OF_SALE"
        assert payload["comment"].startswith(method.capitalize())
    f.followup.close()
    f.followup = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
    assert financial_worker(f).run_once(now=f.clock()).disposition.value == "idle"
    assert not f.requests


@pytest.mark.parametrize("method", ("pix", "wise"))
@pytest.mark.parametrize("unit", ("hostel", "agency"))
@pytest.mark.parametrize(
    "status,behavior,enabled,expected",
    [
        ("cancelled", "success", True, "preparation_terminal"),
        ("active", "timeout", True, "manual_review"),
        ("active", "success", False, "preparation_terminal"),
    ],
)
def test_cancelled_closed_and_unknown_never_repeat_or_reactivate(
    tmp_path, method, unit, status, behavior, enabled, expected
):
    f = lab(tmp_path, method, unit)
    f.service.accept(request=f.request, proof=f.proof)
    worker = financial_worker(f, status=status, behavior=behavior, enabled=enabled)
    assert worker.run_once(now=f.clock()).disposition.value == expected
    posts = [r for r in f.requests if r.method == "POST"]
    assert len(posts) == (1 if behavior == "timeout" else 0)
    assert worker.run_once(now=f.clock()).disposition.value == "idle"
    assert [r for r in f.requests if r.method == "POST"] == posts
