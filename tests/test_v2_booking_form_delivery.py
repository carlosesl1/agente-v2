"""Real reservation/payment/SQLite/outbox chain; only external HTTP is simulated."""

import importlib
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_domain.signature import command_identity, subject_signature
from tests import test_v2_visual_proof_journey as journey
from tests.test_v2_booking_forms import EXPECTED
from tests.test_v2_visual_settlement import financial_worker
from v2_adapters.manychat import ManyChatFlowDeliveryAdapter
from v2_adapters.provider_http import ManyChatHTTPTransport
from v2_application.completion import PublicDeliveryWorker, PublicOutboxStore
from v2_contracts.channel import PublicDeliveryRejected, PublicMessageAuthor
from v2_host.settings import _default_payment_routes

NOW = journey.NOW
LEAD = journey.LEAD


def command_for(product, *, nonce="one", **kwargs):
    command = journey_original(**kwargs)
    components = tuple(
        replace(c, lookup_id=f"lookup:{product}:{nonce}", offer_id=f"offer:{nonce}")
        if c.service.value == "activity"
        else c
        for c in command.payload.components
    )
    signature = subject_signature(
        components=components,
        customer=command.payload.customer,
        terms=command.payload.terms,
    )
    draft = "draft:forms:" + nonce
    cid, key = command_identity(
        workflow_id=command.workflow_id,
        draft_id=draft,
        draft_version=1,
        signature=signature,
        operation=command.operation,
    )
    return replace(
        command,
        payload=replace(command.payload, components=components),
        draft_id=draft,
        command_id=cid,
        idempotency_key=key,
        subject_signature=signature,
    )


journey_original = journey._package_command


@pytest.fixture
def build(tmp_path, monkeypatch):
    resources = []

    def factory(
        *,
        product="product:buracao",
        method="pix",
        unit="agency",
        locale="pt-BR",
        cutoff=NOW,
        nonce="one",
    ):
        assert importlib.util.find_spec("v2_application.booking_forms") is not None, (
            "post-payment form owner is missing"
        )
        module = importlib.import_module("v2_application.booking_forms")
        path = tmp_path / nonce
        path.mkdir(exist_ok=True)
        with monkeypatch.context() as scope:
            scope.setattr(
                journey,
                "_package_command",
                lambda **kw: command_for(product, nonce=nonce, **kw),
            )
            f = journey.lab(path, method, unit)
        f.path = path
        f.public = PublicOutboxStore(path / "public.sqlite3")
        f.boundary = SQLiteBoundaryStore.open_path_v8(path / "boundary.sqlite3")
        # Locale-only fixture; financial facts and dispatch owners are real.
        monkeypatch.setattr(
            f.boundary,
            "load_latest_conversation_projection",
            lambda _: SimpleNamespace(locale=locale),
        )
        f.forms = module.BookingFormProjector(
            execution=f.execution,
            followup=f.followup,
            public_store=f.public,
            boundary=f.boundary,
            lead_resolver=f.leads,
            enabled_from=cutoff,
        )
        f.http = []
        f.behavior = "ok"

        def respond(request):
            body = json.loads(request.content)
            f.http.append((request.url.path, body))
            if (
                request.url.path.endswith("setCustomField")
                and f.behavior == "not_called"
            ):
                raise httpx.ConnectError("synthetic not connected", request=request)
            if request.url.path.endswith("sendFlow"):
                if f.behavior == "unknown":
                    raise httpx.ReadTimeout("synthetic response lost", request=request)
                if f.behavior == "crash":
                    raise KeyboardInterrupt("synthetic process lost")
            return httpx.Response(
                200, json={"status": "success", "request_id": f"request:{len(f.http)}"}
            )

        f.client = httpx.Client(transport=httpx.MockTransport(respond))
        f.delivery = ManyChatFlowDeliveryAdapter(
            transport=ManyChatHTTPTransport(api_key="synthetic", client=f.client),
            allowed_subscriber_id=LEAD.split(":")[1],
            reply_field_id=101,
            reply_flow_ns="synthetic:reply",
            payment_routes=_default_payment_routes(),
            booking_form_resolver=f.forms.route_for_claim,
        )
        f.worker = lambda: PublicDeliveryWorker(
            store=f.public,
            delivery=f.delivery,
            worker_id="worker:forms",
            lease_ttl=timedelta(seconds=30),
        )
        resources.append(f)
        return f

    yield factory
    for f in resources:
        for owner in (
            f.public,
            f.boundary,
            f.followup,
            f.payments,
            f.execution,
            f.client,
        ):
            owner.close()


def settle(f, **kwargs):
    f.service.accept(request=f.request, proof=f.proof)
    return financial_worker(f, **kwargs).run_once(now=f.clock())


@pytest.mark.parametrize(
    "product,category",
    [
        ("product:pati-3d", "pati"),
        ("product:buracao", "day_excursions"),
        ("product:mixila-1d", "mixila_fumaca_below"),
        ("product:fumaca-por-baixo-3d", "mixila_fumaca_below"),
    ],
)
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("method", ["pix", "wise"])
def test_after_paid_only_exact_button_http(build, product, category, locale, method):
    f = build(product=product, locale=locale, method=method)
    assert f.forms.run_once(now=f.clock()) == 0
    assert not f.http
    f.service.accept(request=f.request, proof=f.proof)
    assert f.forms.run_once(now=f.clock()) == 0  # receipt accepted != provider settled
    assert financial_worker(f).run_once(now=f.clock()).disposition.value == "settled"
    assert f.forms.run_once(now=f.clock()) == 1
    assert f.worker().run_once(now=f.clock()).value == "accepted"
    assert f.http == [
        (
            "/fb/subscriber/setCustomField",
            {
                "subscriber_id": LEAD.split(":")[1],
                "field_id": 14426643,
                "field_value": EXPECTED[category, locale],
            },
        ),
        (
            "/fb/sending/sendFlow",
            {
                "subscriber_id": LEAD.split(":")[1],
                "flow_ns": {
                    "pt-BR": "content20260327015856_644212",
                    "en": "content20260327021027_819398",
                }[locale],
            },
        ),
    ]
    assert f.forms.run_once(now=f.clock()) == 0
    assert f.worker().run_once(now=f.clock()).value == "idle"
    assert len(f.http) == 2


@pytest.mark.parametrize(
    "unit,cutoff,behavior",
    [
        ("hostel", NOW, "success"),
        ("agency", None, "success"),
        ("agency", NOW + timedelta(days=1), "success"),
        ("agency", NOW, "timeout"),
    ],
)
def test_no_form_for_hostel_disabled_historical_or_uncertain_payment(
    build, unit, cutoff, behavior
):
    f = build(unit=unit, cutoff=cutoff)
    settle(f, behavior=behavior)
    assert f.forms.run_once(now=f.clock()) == 0
    assert f.worker().run_once(now=f.clock()).value == "idle"
    assert f.http == []


def test_catalog_never_uses_public_label_for_unknown_product(build):
    f = build(product="product:unknown")
    settle(f)
    assert f.forms.run_once(now=f.clock()) == 0


@pytest.mark.parametrize("behavior", ["ok", "unknown", "crash"])
def test_restart_does_not_repeat_delivered_or_ambiguous_flow(build, behavior):
    f = build()
    settle(f)
    assert f.forms.run_once(now=f.clock()) == 1
    f.behavior = behavior
    if behavior == "crash":
        with pytest.raises(KeyboardInterrupt):
            f.worker().run_once(now=f.clock())
    else:
        f.worker().run_once(now=f.clock())
    original = list(f.http)
    f.public.close()
    f.public = PublicOutboxStore(f.path / "public.sqlite3")
    f.forms.public = f.public
    assert f.forms.run_once(now=f.clock() + timedelta(days=1)) == 0
    assert f.worker().run_once(now=f.clock() + timedelta(days=1)).value == "idle"
    assert f.http == original
    assert len([r for r in f.requests if r.method == "POST"]) == 1


def test_known_not_called_retries_but_does_not_repeat_provider_payment(build):
    f = build()
    settle(f)
    f.forms.run_once(now=f.clock())
    f.behavior = "not_called"
    assert f.worker().run_once(now=f.clock()).value == "retryable_failure"
    f.behavior = "ok"
    assert f.worker().run_once(now=f.clock()).value == "accepted"
    assert len([1 for path, _ in f.http if path.endswith("sendFlow")]) == 1
    assert len([r for r in f.requests if r.method == "POST"]) == 1


@pytest.mark.parametrize("change", ["lead", "text", "source", "author"])
def test_tampered_form_claim_cannot_mutate_contact_fields(build, change):
    f = build()
    settle(f)
    f.forms.run_once(now=f.clock())
    claim = f.public.claim(
        worker_id="worker:claim", now=f.clock(), lease_ttl=timedelta(seconds=30)
    )
    altered = {
        "lead": {"lead_id": "manychat:99999"},
        "text": {"text": "wrong"},
        "source": {"source_message_id": "message:booking-form:wrong"},
        "author": {"author": PublicMessageAuthor.MAYA},
    }[change]
    with pytest.raises(PublicDeliveryRejected):
        f.delivery.send(replace(claim, **altered))
    assert f.http == []


def test_one_form_for_two_paid_tours_even_in_separate_drafts(build):
    first = build(nonce="first")
    second = build(product="product:pati-3d", nonce="second")
    settle(first)
    settle(second)
    assert first.forms.run_once(now=first.clock()) == 1
    # Both independent canonical journeys resolve to the same lead and shared outbox.
    second.forms.public = first.public
    assert second.forms.run_once(now=second.clock()) == 0
    assert first.worker().run_once(now=first.clock()).value == "accepted"
    assert second.http == []


def test_changing_conversation_language_after_enqueue_keeps_committed_route(
    build, monkeypatch
):
    f = build(locale="en")
    settle(f)
    f.forms.run_once(now=f.clock())
    monkeypatch.setattr(
        f.boundary,
        "load_latest_conversation_projection",
        lambda _: SimpleNamespace(locale="pt-BR"),
    )
    assert f.worker().run_once(now=f.clock()).value == "accepted"
    assert f.http[0][1]["field_value"] == EXPECTED["day_excursions", "en"]
