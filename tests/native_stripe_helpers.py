"""Causal producer-to-ingress lab. No external network or seeded financial workflow."""

import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx

from reservation_domain import ExecutionCertainty, dumps_command
from reservation_execution import DispatchRequest
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.test_v2_native_stripe import ACCOUNTS, KEY, NOW, settings
from tests.test_v2_outcome_projector import _package_command, _persist
from v2_adapters.stripe import StripeLinkAdapter, StripeTestHTTPTransport, WiseBRLRates
from v2_application.outcome_projector import ReservationOutcomeProjector
from v2_application.payments import (
    PaymentInitiationWorker,
    PaymentService,
    SQLitePaymentInitiationStore,
)
from v2_application.reservations import ReservationAllocator
from v2_contracts.payments import BusinessUnit


def signature(body, when=NOW, secret="whsec_native"):
    stamp = str(int(when.timestamp()))
    digest = hmac.new(
        secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return {"Stripe-Signature": f"t={stamp},v1={digest}"}


def make_lab(tmp_path, unit="hostel"):
    cfg = settings(tmp_path)
    if unit == "agency":
        from dataclasses import replace

        cfg = replace(
            cfg,
            stripe_native_accounts={
                "agency": {
                    **ACCOUNTS["hostel"],
                    "profile_id": "stripe-account:agency:test",
                }
            },
        )
    paths = cfg.sqlite_paths
    execution = SQLiteUnitOfWork.open_v6(paths["execution"])
    payments = SQLitePaymentInitiationStore(
        paths["payment_initiation"], result_encryption_key=KEY
    )
    command = next(
        c
        for c in ReservationAllocator().allocate(_package_command()).commands
        if c.payload.components[0].service.value
        == {"hostel": "lodging", "agency": "activity"}[unit]
    )
    _persist(execution, (command,))
    claim = execution.claim_command(
        worker_id="worker:test",
        now=NOW - timedelta(seconds=40),
        lease_ttl=timedelta(seconds=30),
    )
    request = DispatchRequest.from_command(command, dumps_command(command))
    permit = execution.fence_dispatch(claim, request, now=NOW - timedelta(seconds=40))
    outcome = command.outcome(
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        normalized_status="accepted",
        provider_reference={
            "hostel": "provider:cloudbeds:123",
            "agency": "provider:bokun:id:456",
        }[unit],
        evidence=(request.payload_hash,),
    )
    execution.record_outcome(permit, outcome, now=NOW - timedelta(seconds=40))
    profiles = {
        BusinessUnit.HOSTEL: "stripe-account:hostel:test",
        BusinessUnit.AGENCY: "stripe-account:agency:test",
    }
    ReservationOutcomeProjector(
        execution=execution, payment_store=payments, receiver_profiles=profiles
    ).run_once(now=NOW - timedelta(seconds=30))
    lab = SimpleNamespace(
        settings=cfg,
        paths=paths,
        execution=execution,
        payments=payments,
        command=command,
        objects={},
        requests=[],
        unit=unit,
    )

    def handler(req):
        lab.requests.append(req)
        if req.method == "POST":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            if req.url.path == "/v1/products":
                obj = {
                    "id": "prod_Native",
                    "livemode": False,
                    "name": form["name"],
                    "description": form["description"],
                    "metadata": {
                        k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")
                    },
                }
            elif req.url.path == "/v1/prices":
                obj = {
                    "id": "price_Native",
                    "livemode": False,
                    "product": form["product"],
                    "currency": "brl",
                    "unit_amount": int(form["unit_amount"]),
                    "currency_options": {
                        "eur": {
                            "unit_amount": int(
                                form["currency_options[eur][unit_amount]"]
                            )
                        },
                        "usd": {
                            "unit_amount": int(
                                form["currency_options[usd][unit_amount]"]
                            )
                        },
                    },
                }
            else:
                assert req.url.path == "/v1/payment_links"
                obj = {
                    "id": "plink_Native",
                    "livemode": False,
                    "active": True,
                    "url": "https://buy.stripe.com/test_Native",
                    "metadata": {
                        k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")
                    },
                }
            lab.objects[obj["id"]] = obj
        else:
            assert req.method == "GET"
            if req.url.path == "/v1/account":
                obj = {"id": ACCOUNTS["hostel"]["account_id"]}
            elif req.url.path == "/v1/events":
                obj = {"data": [lab.canonical_event], "has_more": False}
            elif req.url.path.endswith("/line_items"):
                obj = {
                    "data": [{"quantity": 1, "price": lab.objects["price_Native"]}],
                    "has_more": False,
                }
            else:
                obj = lab.objects[req.url.path.rsplit("/", 1)[1]]
        return httpx.Response(200, json=obj)

    lab.client = httpx.Client(transport=httpx.MockTransport(handler))
    stripe = StripeLinkAdapter(
        transport=StripeTestHTTPTransport(
            secret_keys={profiles[BusinessUnit(unit)]: "rk_test_native"},
            base_url="https://api.stripe.com",
            client=lab.client,
            wise_rates=lambda: WiseBRLRates(usd_brl=Decimal(5), eur_brl=Decimal(6)),
            journal=payments,
            clock=lambda: NOW - timedelta(seconds=20),
        ),
        account_profiles=profiles,
        enabled=True,
        subscriber_id="12345",
        payment_percentages={BusinessUnit.HOSTEL: 100, BusinessUnit.AGENCY: 20},
    )
    service = PaymentService(
        stripe=stripe,
        wise=SimpleNamespace(instruction=lambda _: None),
        pix=SimpleNamespace(instruction=lambda _: None),
    )
    result = PaymentInitiationWorker(
        store=payments,
        payments=service,
        worker_id="worker:init",
        lease_ttl=timedelta(seconds=30),
    ).run_once(now=NOW - timedelta(seconds=20))
    assert result.disposition.value == "completed"
    amount = lab.objects["price_Native"]["unit_amount"]
    lab.session = {
        "id": "cs_test_Native",
        "object": "checkout.session",
        "status": "complete",
        "payment_status": "paid",
        "livemode": False,
        "mode": "payment",
        "expires_at": int((NOW + timedelta(hours=24)).timestamp()),
        "payment_link": "plink_Native",
        "payment_intent": "pi_Native",
        "currency": "brl",
        "amount_total": amount,
        "created": int((NOW - timedelta(seconds=10)).timestamp()),
    }
    lab.intent = {
        "id": "pi_Native",
        "object": "payment_intent",
        "status": "succeeded",
        "livemode": False,
        "amount_received": amount,
        "currency": "brl",
    }
    lab.canonical_event = {
        "id": "evt_1QNative8aBcD23eFgH45",
        "object": "event",
        "livemode": False,
        "type": "payment_intent.succeeded",
        "created": int((NOW - timedelta(seconds=1)).timestamp()),
        "data": {"object": lab.intent},
    }
    lab.event = {
        "id": "evt_CheckoutNative",
        "object": "event",
        "livemode": False,
        "type": "checkout.session.completed",
        "created": int(NOW.timestamp()),
        "data": {"object": lab.session},
    }
    lab.objects.update({"cs_test_Native": lab.session, "pi_Native": lab.intent})
    lab.body = lambda: json.dumps(lab.event, separators=(",", ":")).encode()
    lab.requests.clear()
    return lab


def close_lab(lab):
    lab.execution.close()
    lab.payments.close()
    lab.client.close()
