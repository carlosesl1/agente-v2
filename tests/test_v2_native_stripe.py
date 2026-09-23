"""Native Stripe HTTP boundary. All external calls are synthetic, never real payments."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from v2_host.api_main import build_api_app
from v2_host.settings import V2ProcessRole, V2Settings

NOW = datetime(2026, 7, 24, 12, 1, tzinfo=UTC)
KEY = b"n" * 32
ACCOUNTS = {
    "hostel": {
        "profile_id": "stripe-account:hostel:test",
        "account_id": "acct_Hostel",
        "api_key": "rk_test_native",
        "webhook_secret": "whsec_native",
    }
}


def settings(tmp_path, **changes):
    env = {
        "V2_SQLITE_PATH": str(tmp_path / "inbox.sqlite3"),
        "V2_MANYCHAT_WEBHOOK_SECRET": "fixture",
        "V2_STRIPE_NATIVE_ACCOUNTS_JSON": json.dumps(ACCOUNTS),
        "V2_STRIPE_NATIVE_RESULT_KEY_HEX": KEY.hex(),
        "V2_ALLOWED_SUBSCRIBER_IDS": "12345",
    }
    env.update(changes)
    return V2Settings.from_env(env, process_role=V2ProcessRole.API)


def test_default_factory_exposes_native_account_route_and_authenticates_first(tmp_path):
    with TestClient(build_api_app(settings(tmp_path), clock=lambda: NOW)) as client:
        response = client.post("/webhook/payments/stripe/hostel", content=b"not json")
    assert response.status_code == 401
    assert not list(tmp_path.glob("*followup*"))


def test_native_ingress_requires_complete_account_and_result_key(tmp_path):
    with pytest.raises(ValueError):
        settings(tmp_path, V2_STRIPE_NATIVE_RESULT_KEY_HEX="")


def test_unconfigured_native_route_is_explicitly_closed(tmp_path):
    config = V2Settings(
        webhook_secret="fixture", sqlite_path=tmp_path / "inbox.sqlite3"
    )
    with TestClient(build_api_app(config, clock=lambda: NOW)) as client:
        response = client.post("/webhook/payments/stripe/hostel", content=b"{}")
    assert response.status_code == 503


@pytest.fixture
def lab(tmp_path):
    from tests.native_stripe_helpers import close_lab, make_lab

    value = make_lab(tmp_path)
    try:
        yield value
    finally:
        close_lab(value)


def accept(lab, body=None, headers=None):
    from tests.native_stripe_helpers import signature
    from v2_application.native_stripe import NativeStripeIngress

    body = lab.body() if body is None else body
    ingress = NativeStripeIngress(
        paths=lab.paths,
        accounts=lab.settings.stripe_native_accounts,
        result_key=KEY,
        allowed_subscribers=("12345",),
        client=lab.client,
    )
    return ingress.accept(
        lab.unit, body, signature(body) if headers is None else headers, received_at=NOW
    )


def test_checkout_ahead_of_offer_publication_retries_instead_of_dropping(
    lab, monkeypatch
):
    from v2_application.native_stripe import NativeStripeUnresolved
    from v2_application.payments import SQLitePaymentInitiationStore

    monkeypatch.setattr(SQLitePaymentInitiationStore, "completed_offers", lambda _: ())
    with pytest.raises(NativeStripeUnresolved):
        accept(lab)
    assert not lab.paths["followup"].exists()


def test_native_http_post_commits_and_duplicate_is_http_200(lab):
    from tests.native_stripe_helpers import signature

    app = build_api_app(lab.settings, clock=lambda: NOW)
    app.state.native_stripe.client = lab.client
    try:
        with TestClient(app) as client:
            body = lab.body()
            first = client.post(
                "/webhook/payments/stripe/hostel", content=body, headers=signature(body)
            )
            second = client.post(
                "/webhook/payments/stripe/hostel", content=body, headers=signature(body)
            )
            assert (first.status_code, first.json()) == (202, {"status": "accepted"})
            assert (second.status_code, second.json()) == (200, {"status": "duplicate"})
    finally:
        app.state.v2_container.close()


def test_issued_checkout_creates_existing_workflow_and_global_claim_then_reopens(lab):
    from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
    from reservation_followup.types import PaymentStatus

    result = accept(lab)
    assert result.disposition.value == "accepted"
    with SQLiteFollowupUnitOfWork.open_v2(lab.paths["followup"]) as store:
        workflow = store.load_payment(result.payment_id)
        assert workflow.status is PaymentStatus.SETTLEMENT_QUEUED
        assert workflow.evidence_record.evidence.event_id == "evt_1QNative8aBcD23eFgH45"
        assert workflow.subject.amount_minor == lab.session["amount_total"]
        assert (
            store._connection.execute(
                "SELECT count(*) FROM payment_evidence_claims"
            ).fetchone()[0]
            == 1
        )
    lab.event["id"] = "evt_Redelivered"
    lab.event["type"] = "checkout.session.async_payment_succeeded"
    assert accept(lab).disposition.value == "duplicate"
    assert all(r.method == "GET" for r in lab.requests)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda l: l.session.update(livemode=True),
        lambda l: l.session.update(amount_total=l.session["amount_total"] + 1),
        lambda l: l.intent.update(status="processing"),
        lambda l: l.intent.update(amount_received=1),
        lambda l: l.objects["plink_Native"]["metadata"].update(business_unit="agency"),
        lambda l: l.objects["price_Native"].update(unit_amount=1),
        lambda l: l.canonical_event.update(livemode=True),
    ],
)
def test_authenticated_but_mismatched_payment_never_opens_financial_state(lab, mutate):
    from v2_application.financial_webhooks import FinancialWebhookInvalid

    mutate(lab)
    with pytest.raises(FinancialWebhookInvalid):
        accept(lab)
    assert not lab.paths["followup"].exists()


def test_invalid_or_old_signature_does_not_read_stripe(lab):
    from datetime import timedelta

    from tests.native_stripe_helpers import signature
    from v2_application.financial_webhooks import FinancialWebhookUnauthorized

    for headers in (
        {},
        signature(lab.body(), secret="wrong"),
        signature(lab.body(), when=NOW - timedelta(seconds=301)),
    ):
        with pytest.raises(FinancialWebhookUnauthorized):
            accept(lab, headers=headers)
    assert lab.requests == []


def test_paid_foreign_link_is_ignored_without_claiming_or_creating_state(lab):
    from v2_application.native_stripe import NativeStripeIgnored

    lab.session["payment_link"] = "plink_Foreign"
    lab.objects["plink_Foreign"] = {
        "id": "plink_Foreign",
        "livemode": False,
        "metadata": {"legacy": "true"},
    }
    with pytest.raises(NativeStripeIgnored):
        accept(lab)
    assert not lab.paths["followup"].exists()


def test_paid_eur_uses_authenticated_price_and_keeps_brl_provider_amount(lab):
    from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork

    lab.session.update(
        currency="eur",
        amount_total=lab.objects["price_Native"]["currency_options"]["eur"][
            "unit_amount"
        ],
    )
    lab.intent.update(currency="eur", amount_received=lab.session["amount_total"])
    result = accept(lab)
    with SQLiteFollowupUnitOfWork.open_v2(lab.paths["followup"]) as store:
        workflow = store.load_payment(result.payment_id)
        assert workflow.subject.currency == "BRL"
        assert (
            workflow.subject.amount_minor == lab.objects["price_Native"]["unit_amount"]
        )
