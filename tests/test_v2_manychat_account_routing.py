"""Account/locale routing through real durable completion and HTTP serialization."""

# ruff: noqa: F811 -- imported pytest fixture intentionally injected as a parameter
from datetime import timedelta

import pytest

from tests.test_v2_outcome_projector import NOW, US_PHONE
from tests.test_v2_payment_button_completion import (  # noqa: F401
    drain,
    lab,
    prepare,
    reopen,
)

ROUTES = {
    ("hostel", "pt-BR"): (14385975, 14389398, "content20260317150732_004280"),
    ("hostel", "en"): (14385975, 14389398, "content20260317175037_863148"),
    ("agency", "pt-BR"): (14385973, 14389400, "content20260316210707_801647"),
    ("agency", "en"): (14385973, 14389400, "content20260317181131_597245"),
}


@pytest.mark.parametrize("phone,locale", [(None, "pt-BR"), (US_PHONE, "en")])
@pytest.mark.parametrize("service", ["reserve_lodging", "book_activity", "package"])
def test_each_authenticated_link_uses_its_account_fields_and_locale_flow(
    lab, phone, locale, service, monkeypatch
):
    # Deliberately opaque: neither account nor locale can be inferred from the URL.
    def opaque_link(self, request):
        self.calls.append(request)
        token = "a4cF01" if request.business_unit.value == "hostel" else "z8bY92"
        return {
            "link_id": "plink_" + token,
            "url": "https://buy.stripe.com/test_" + token,
        }

    monkeypatch.setattr(type(lab.stripe), "__call__", opaque_link)
    commands = prepare(lab, service=service, phone=phone)
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    drain(lab)
    fields = [
        (i, b) for i, (p, b, _) in enumerate(lab.http) if p.endswith("setCustomFields")
    ]
    assert len(fields) == len(commands)
    for i, body in fields:
        url = body["fields"][0]["field_value"]
        offer = next(o for o in lab.payments.completed_offers() if o.public_url == url)
        (context,) = lab.payments.context_for_payment(offer.payment_id)
        unit = context.selection.obligation.business_unit.value
        link_field, description_field, flow = ROUTES[(unit, locale)]
        assert [f["field_id"] for f in body["fields"]] == [
            link_field,
            description_field,
        ]
        assert lab.http[i + 1][0].endswith("sendFlow")
        assert lab.http[i + 1][1]["flow_ns"] == flow
    prior = list(lab.http)
    reopen(lab)
    lab.projector.run_once(now=NOW + timedelta(days=1))
    drain(lab)
    assert lab.http == prior
    assert len(lab.stripe.calls) == len(commands)


def test_tampered_payment_url_is_never_sent_under_another_account(lab):
    prepare(lab)
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    # Local test database, not active state: swap URLs while retaining message IDs.
    offers = lab.payments.completed_offers()
    a, b = (o.public_url for o in offers)
    lab.public._connection.execute(
        "UPDATE public_outbox SET text=replace(text,?,?) WHERE text LIKE ?",
        (a, b, "%" + a),
    )
    drain(lab)
    sent = [body for path, body, _ in lab.http if path.endswith("setCustomFields")]
    assert len(sent) == 1
    assert lab.public.manual_review_count() == 1


@pytest.mark.parametrize("change", ["lead", "source", "description", "author", "chunk"])
def test_outbox_provenance_is_verified_before_any_field_write(lab, change):
    from dataclasses import replace

    from v2_contracts.channel import PublicDeliveryRejected, PublicMessageAuthor

    prepare(lab, service="reserve_lodging")
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    claim = lab.public.claim(
        worker_id="claim:test",
        now=NOW + timedelta(seconds=6),
        lease_ttl=timedelta(seconds=20),
    )
    changes = {
        "lead": {"lead_id": "manychat:67890"},
        "source": {"source_message_id": "message:payment-link:unknown"},
        "description": {"text": claim.text.replace("hospedagem", "passeio")},
        "author": {"author": PublicMessageAuthor.MAYA},
        "chunk": {"chunk_index": 1},
    }
    with pytest.raises(PublicDeliveryRejected):
        lab.delivery.send(replace(claim, **changes[change]))
    assert lab.http == []


@pytest.mark.parametrize("change", ["account", "language", "method"])
def test_mismatched_authenticated_offer_is_not_routed(lab, monkeypatch, change):
    from dataclasses import replace

    from v2_contracts.payments import PaymentMethod

    prepare(lab, service="reserve_lodging")
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    (original,) = lab.payments.completed_offers()
    (context,) = lab.payments.context_for_payment(original.payment_id)
    if change == "account":
        offer = replace(original, account_profile_id="stripe-account:agency:test")
    elif change == "language":
        offer = replace(original, customer_language=None)
    else:
        offer = original
        context = replace(
            context, selection=replace(context.selection, method=PaymentMethod.PIX)
        )
    context = replace(context, offer=offer)
    monkeypatch.setattr(lab.payments, "completed_offers", lambda: (offer,))
    monkeypatch.setattr(lab.payments, "context_for_payment", lambda _id: (context,))
    drain(lab)
    # The independent authored reply still delivers; no payment field/flow is called.
    assert [
        body.get("flow_ns") for path, body, _ in lab.http if path.endswith("sendFlow")
    ] == ["fixture:reply"]
    assert not any(path.endswith("setCustomFields") for path, _, _ in lab.http)
    assert lab.public.manual_review_count() == 1


def test_missing_owner_never_falls_back_to_a_default_flow(lab):
    prepare(lab, service="reserve_lodging")
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    lab.delivery._payment_context_resolver = None
    drain(lab)
    # The independent authored reply still delivers; no payment field/flow is called.
    assert [
        body.get("flow_ns") for path, body, _ in lab.http if path.endswith("sendFlow")
    ] == ["fixture:reply"]
    assert not any(path.endswith("setCustomFields") for path, _, _ in lab.http)
    assert lab.public.manual_review_count() == 1


def test_package_keeps_both_destination_fields_after_all_actions(lab):
    prepare(lab)
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    drain(lab)
    state = {}
    dispatched = []
    for path, body, _ in lab.http:
        if path.endswith("setCustomFields"):
            state.update({f["field_id"]: f["field_value"] for f in body["fields"]})
        elif path.endswith("sendFlow") and body["flow_ns"] != "fixture:reply":
            route = next(r for r in ROUTES.values() if r[2] == body["flow_ns"])
            dispatched.append((route[0], state[route[0]]))
    assert len(dispatched) == 2
    assert dispatched[0][1] != dispatched[1][1]
    for field, url in dispatched:
        assert state[field] == url


def test_route_settings_ignore_legacy_single_account_flow(tmp_path):
    from tests.test_v2_settings import _controlled_env
    from v2_host.settings import V2Settings

    env = _controlled_env(tmp_path)
    env.update(
        V2_MANYCHAT_PAYMENT_FLOW_NS="wrong-legacy-flow",
        V2_MANYCHAT_PAYMENT_LINK_FIELD_ID="11",
        V2_MANYCHAT_PAYMENT_DESCRIPTION_FIELD_ID="12",
    )
    settings = V2Settings.from_env(env)
    assert {
        (r.business_unit.value, r.customer_language.value): (
            r.link_field_id,
            r.description_field_id,
            r.flow_ns,
        )
        for r in settings.manychat_payment_routes
    } == ROUTES


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "flow_alias",
        "cross_account_field",
        "bad_locale",
        "bad_unit",
        "field_bool",
        "field_zero",
        "same_field",
        "bad_flow",
    ],
)
def test_invalid_routing_configuration_is_rejected(tmp_path, change):
    import json
    from dataclasses import asdict

    from tests.test_v2_settings import _controlled_env
    from v2_host.settings import V2Settings, _default_payment_routes

    rows = [asdict(r) for r in _default_payment_routes()]
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows[1] = rows[0]
    elif change == "flow_alias":
        rows[1]["flow_ns"] = rows[0]["flow_ns"]
    elif change == "cross_account_field":
        rows[2]["link_field_id"] = rows[0]["link_field_id"]
    elif change == "bad_locale":
        rows[0]["customer_language"] = "es"
    elif change == "bad_unit":
        rows[0]["business_unit"] = "other"
    elif change == "field_bool":
        rows[0]["link_field_id"] = True
    elif change == "field_zero":
        rows[0]["link_field_id"] = 0
    elif change == "same_field":
        rows[0]["link_field_id"] = rows[0]["description_field_id"]
    elif change == "bad_flow":
        rows[0]["flow_ns"] = " "
    env = _controlled_env(tmp_path)
    env["V2_MANYCHAT_PAYMENT_ROUTES_JSON"] = json.dumps(rows)
    with pytest.raises((ValueError, TypeError)):
        V2Settings.from_env(env)


def test_explicit_route_configuration_roundtrips(tmp_path):
    import json
    from dataclasses import asdict

    from tests.test_v2_settings import _controlled_env
    from v2_host.settings import V2Settings, _default_payment_routes

    env = _controlled_env(tmp_path)
    env["V2_MANYCHAT_PAYMENT_ROUTES_JSON"] = json.dumps(
        [asdict(r) for r in _default_payment_routes()]
    )
    assert V2Settings.from_env(env).manychat_payment_routes == _default_payment_routes()
