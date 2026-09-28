"""Explicit production Stripe mode; all provider/channel HTTP is simulated."""

from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.native_stripe_helpers import close_lab, make_lab, signature
from tests.test_v2_native_stripe import NOW
from tests.test_v2_settings import _controlled_env
from tests.test_v2_stripe_test_transport import _request
from v2_adapters.stripe import (
    StripeLinkReconciliationAdapter,
    StripeTestHTTPTransport,
    StripeTestReconciliationTransport,
    WiseBRLRates,
)
from v2_host.api_main import build_api_app
from v2_host.settings import StripeEnvironment, V2Settings


def test_ga_explicit_live_is_supported_without_changing_test_default(tmp_path):
    env = _controlled_env(tmp_path)
    env.update(
        V2_RUNTIME_MODE="general_availability",
        V2_ALLOWED_SUBSCRIBER_IDS="",
        V2_STRIPE_ENVIRONMENT="live",
    )
    config = V2Settings.from_env(env)
    assert config.stripe_environment is StripeEnvironment.LIVE
    assert (
        V2Settings.from_env(_controlled_env(tmp_path)).stripe_environment
        is StripeEnvironment.TEST
    )


def test_controlled_test_remains_test_only(tmp_path):
    env = _controlled_env(tmp_path)
    env["V2_STRIPE_ENVIRONMENT"] = "live"
    with pytest.raises(ValueError):
        V2Settings.from_env(env)


@pytest.mark.parametrize("unit", ["hostel", "agency"])
@pytest.mark.parametrize("livemode", [False, True])
def test_issuance_native_http_and_reopen_are_same_mode_exactly_once(
    tmp_path, unit, livemode
):
    from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork

    lab = make_lab(tmp_path, unit, livemode=livemode)
    try:
        for expected_code, expected_status in [(202, "accepted"), (200, "duplicate")]:
            app = build_api_app(lab.settings, clock=lambda: NOW)
            app.state.native_stripe.client = lab.client
            with TestClient(app) as client:
                body = lab.body()
                response = client.post(
                    f"/webhook/payments/stripe/{unit}",
                    content=body,
                    headers=signature(body),
                )
                assert (response.status_code, response.json()) == (
                    expected_code,
                    {"status": expected_status},
                )
        with SQLiteFollowupUnitOfWork.open_v2(lab.paths["followup"]) as store:
            assert (
                store._connection.execute(
                    "SELECT count(*) FROM payment_evidence_claims"
                ).fetchone()[0]
                == 1
            )
        assert all(r.method == "GET" for r in lab.requests)
    finally:
        close_lab(lab)


@pytest.mark.parametrize(
    "boundary", ["event", "session", "intent", "link", "price", "transaction"]
)
@pytest.mark.parametrize("bad_mode", [False, None, 1])
def test_live_native_rejects_every_wrong_or_untyped_mode_before_financial_state(
    tmp_path, boundary, bad_mode
):
    lab = make_lab(tmp_path, livemode=True)
    try:
        objects = {
            "event": lab.event,
            "session": lab.session,
            "intent": lab.intent,
            "link": lab.objects["plink_Native"],
            "price": lab.objects["price_Native"],
            "transaction": lab.canonical_event,
        }
        objects[boundary]["livemode"] = bad_mode
        app = build_api_app(lab.settings, clock=lambda: NOW)
        app.state.native_stripe.client = lab.client
        with TestClient(app) as client:
            body = lab.body()
            response = client.post(
                "/webhook/payments/stripe/hostel", content=body, headers=signature(body)
            )
        assert response.status_code == 422
        assert not lab.paths["followup"].exists()
        assert all(r.method == "GET" for r in lab.requests)
    finally:
        close_lab(lab)


@pytest.mark.parametrize(
    "transport", [StripeTestHTTPTransport, StripeTestReconciliationTransport]
)
@pytest.mark.parametrize(
    "livemode,key", [(True, "rk_test_fixture"), (False, "rk_live_fixture")]
)
def test_wrong_key_mode_fails_before_http(transport, livemode, key):
    with pytest.raises(ValueError):
        transport(secret_keys={"profile": key}, livemode=livemode)


@pytest.mark.parametrize(
    "transport", [StripeTestHTTPTransport, StripeTestReconciliationTransport]
)
@pytest.mark.parametrize("invalid_mode", [1, "live", None])
def test_mode_is_exact_boolean(transport, invalid_mode):
    with pytest.raises(TypeError):
        transport(secret_keys={"profile": "rk_test_fixture"}, livemode=invalid_mode)


@pytest.mark.parametrize("bad_mode", [False, None, 1])
def test_live_writer_rejects_wrong_mode_after_one_post_without_next_step(bad_mode):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"id": "prod_WrongMode", "livemode": bad_mode})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        writer = StripeTestHTTPTransport(
            secret_keys={_request().account_profile_id: "rk_live_fixture"},
            livemode=True,
            client=client,
            wise_rates=lambda: WiseBRLRates(Decimal(5), Decimal(6)),
        )
        with pytest.raises(RuntimeError, match="mode"):
            writer(_request())
    assert len(calls) == 1 and calls[0].url.path == "/v1/products"


@pytest.mark.parametrize("livemode", [False, True])
def test_reconciler_checks_all_objects_against_explicit_mode(livemode):
    from v2_adapters.stripe_checkout import stripe_product_presentation
    from v2_contracts.payments import BusinessUnit

    reader = StripeTestReconciliationTransport(
        secret_keys={"profile": "rk_live_fixture" if livemode else "rk_test_fixture"},
        livemode=livemode,
    )
    reconciler = StripeLinkReconciliationAdapter(
        transport=reader,
        account_profiles={BusinessUnit.HOSTEL: "profile", BusinessUnit.AGENCY: "other"},
        payment_percentages={BusinessUnit.HOSTEL: 100, BusinessUnit.AGENCY: 20},
    )
    req = _request()
    presentation = stripe_product_presentation(req)
    product = {
        "id": "prod_Match",
        "livemode": livemode,
        "name": presentation.name,
        "description": presentation.description,
        "metadata": {},
    }
    price = {
        "id": "price_Match",
        "livemode": livemode,
        "active": True,
        "product": "prod_Match",
        "currency": "brl",
        "unit_amount": req.amount_minor,
    }
    link = {
        "id": "plink_Match",
        "livemode": livemode,
        "active": True,
        "url": "https://buy.stripe.com/opaque",
        "metadata": {},
        "line_items": {"data": [{"price": "price_Match"}]},
    }

    def matches():
        return (
            reconciler._product_matches(
                product, product_id="prod_Match", presentation=presentation, metadata={}
            ),
            reconciler._price_matches(
                price, price_id="price_Match", product_id="prod_Match", request=req
            ),
            reconciler._link_values(
                link, link_id="plink_Match", price_id="price_Match", metadata={}
            )
            is not None,
        )

    assert matches() == (True, True, True)
    for obj in (product, price, link):
        obj["livemode"] = not livemode
    assert matches() == (False, False, False)


def test_controlled_api_cannot_accept_live_environment(tmp_path):
    from v2_host.settings import V2ProcessRole

    env = _controlled_env(tmp_path)
    env["V2_STRIPE_ENVIRONMENT"] = "live"
    with pytest.raises(ValueError, match="controlled_write"):
        V2Settings.from_env(env, process_role=V2ProcessRole.API)


def test_live_ingress_rejects_signed_session_mode_even_if_get_is_live(tmp_path):
    from copy import deepcopy

    lab = make_lab(tmp_path, livemode=True)
    try:
        lab.event["data"]["object"] = deepcopy(lab.session)
        lab.event["data"]["object"]["livemode"] = False
        app = build_api_app(lab.settings, clock=lambda: NOW)
        app.state.native_stripe.client = lab.client
        with TestClient(app) as client:
            body = lab.body()
            response = client.post(
                "/webhook/payments/stripe/hostel", content=body, headers=signature(body)
            )
        assert response.status_code == 422
        assert lab.requests == []
        assert not lab.paths["followup"].exists()
    finally:
        close_lab(lab)
