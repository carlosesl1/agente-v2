"""Real completion/SQLite/ManyChat HTTP chain; model and external HTTP are fixtures."""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from reservation_domain import ExecutionCertainty
from tests.test_v2_completion_projector import _payment_worker, _StripeTransport
from tests.test_v2_outcome_projector import (
    NOW,
    US_PHONE,
    _finish_next,
    _package_command,
    _persist,
    _stores,
)
from tests.test_v2_turn_executor import BATCH, EVENT
from tests.v2_completion_helpers import CompletionMaya, authored_chunks, make_completion
from v2_adapters.manychat import ManyChatFlowDeliveryAdapter
from v2_adapters.provider_http import ManyChatHTTPTransport
from v2_application.completion import PublicOutboxStore, PublicReply
from v2_application.completion_projector import _opaque
from v2_application.public_delivery import CombinedPublicDeliveryWorker
from v2_application.reservations import ReservationAllocator
from v2_contracts.channel import PublicAcceptanceOperation, PublicMessageAuthor

from v2_host.settings import _default_payment_routes

PAYMENT_FLOWS = {r.flow_ns for r in _default_payment_routes()}
LEAD = "manychat:12345"


@pytest.fixture
def lab(tmp_path):
    execution, payments, outcome = _stores(tmp_path)
    public = PublicOutboxStore(tmp_path / "public.sqlite3")
    stripe = _StripeTransport()
    projector = make_completion(tmp_path, execution, payments, public, lead_id=LEAD)
    value = SimpleNamespace(
        path=tmp_path,
        execution=execution,
        payments=payments,
        outcome=outcome,
        public=public,
        projector=projector,
        stripe=stripe,
        http=[],
        behavior="ok",
    )

    def respond(request):
        body = json.loads(request.content)
        value.http.append((request.url.path, body, request.headers["Idempotency-Key"]))
        if (
            request.url.path.endswith("setCustomFields")
            and value.behavior == "not_called"
        ):
            raise httpx.ConnectError("fixture: no connection", request=request)
        if body.get("flow_ns") in PAYMENT_FLOWS:
            if value.behavior == "unknown":
                raise httpx.ReadTimeout("fixture: response lost", request=request)
            if value.behavior == "crash":
                raise KeyboardInterrupt("fixture: process lost after request")
        return httpx.Response(
            200, json={"status": "success", "request_id": f"request:{len(value.http)}"}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        value.delivery = ManyChatFlowDeliveryAdapter(
            transport=ManyChatHTTPTransport(
                api_key="fixture", base_url="https://manychat.invalid", client=client
            ),
            allowed_subscriber_id=None,
            reply_field_id=101,
            reply_flow_ns="fixture:reply",
            payment_routes=_default_payment_routes(),
            payment_context_resolver=projector.payment_context_for_claim,
        )
        try:
            yield value
        finally:
            value.projector._boundary.close()
            value.public.close()
            payments.close()
            execution.close()


def prepare(lab, *, service="package", phone=None, payment_failure=False):
    kwargs = {} if phone is None else {"phone_e164": phone}
    commands = ReservationAllocator().allocate(_package_command(**kwargs)).commands
    if service != "package":
        commands = tuple(c for c in commands if c.operation.value == service)
    _persist(lab.execution, commands)
    for i in range(len(commands)):
        _finish_next(
            lab.execution,
            now=NOW + timedelta(seconds=i),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
    lab.outcome.run_once(now=NOW + timedelta(seconds=2))
    lab.stripe.fail = payment_failure
    worker = _payment_worker(lab.payments, lab.stripe)
    for i in range(len(commands)):
        worker.run_once(now=NOW + timedelta(seconds=3 + i))
    return commands


def worker(lab, *, enabled=True):
    return CombinedPublicDeliveryWorker(
        boundary=SQLiteBoundaryWorkerStore(lab.projector._boundary),
        completion=lab.public,
        delivery=lab.delivery,
        effect_guard=SimpleNamespace(allows_workflow=lambda _: enabled),
        worker_id="worker:buttons",
        lease_ttl=timedelta(seconds=30),
    )


def drain(lab, *, enabled=True):
    timestamp = lab.projector._boundary._connection.execute(
        "SELECT max(created_at) FROM boundary_public_outbox"
    ).fetchone()[0]
    tick = (
        datetime.fromisoformat(timestamp) + timedelta(seconds=1) if timestamp else NOW
    )
    delivery = worker(lab, enabled=enabled)
    results = [
        delivery.run_once(now=tick + timedelta(seconds=i)).value for i in range(6)
    ]
    return results, tick


def buttons(lab):
    return [
        b
        for p, b, _ in lab.http
        if p.endswith("sendFlow") and b["flow_ns"] in PAYMENT_FLOWS
    ]


def reopen(lab):
    lab.projector._boundary.close()
    lab.public.close()
    lab.public = PublicOutboxStore(lab.path / "public.sqlite3")
    lab.projector._public_store = lab.public
    lab.projector._boundary = SQLiteBoundaryStore.open_path_v8(
        lab.path / "communication-boundary.sqlite3"
    )
    # No further model call is allowed: committed source coverage owns recovery.
    lab.projector.executor = SimpleNamespace(
        execute=lambda _: pytest.fail("completion was reauthored")
    )


@pytest.mark.parametrize(
    "service,phone,labels",
    [
        (
            "package",
            None,
            {"Link de pagamento da hospedagem", "Link de pagamento do passeio"},
        ),
        ("reserve_lodging", US_PHONE, {"Accommodation payment link"}),
        ("book_activity", None, {"Link de pagamento do passeio"}),
    ],
)
def test_completed_offers_reach_payment_button_http_without_rewriting_maya(
    lab, service, phone, labels
):
    commands = prepare(lab, service=service, phone=phone)
    before = lab.execution.list_outcome_projection_inputs()
    assert lab.projector.run_once(now=NOW + timedelta(seconds=5)).inserted == 1
    assert [(c.author, c.text) for c in authored_chunks(lab.projector)] == [
        ("maya", CompletionMaya.text)
    ]
    drain(lab)
    assert len(buttons(lab)) == len(commands)
    field_posts = [b for p, b, _ in lab.http if p.endswith("setCustomFields")]
    expected = {o.public_url for o in lab.payments.completed_offers()}
    assert {b["fields"][0]["field_value"] for b in field_posts} == expected
    assert {b["fields"][1]["field_value"] for b in field_posts} == labels
    assert all(b["subscriber_id"] == "12345" for _, b, _ in lab.http)
    assert [
        b["field_value"] for p, b, _ in lab.http if p.endswith("setCustomField")
    ] == [CompletionMaya.text]
    rows = lab.public.conversation_messages(LEAD)
    assert len(rows) == len(commands)
    assert all(r.author is PublicMessageAuthor.AUTHENTICATED_SYSTEM for r in rows)
    for row in rows:
        (receipt,) = lab.public.acceptance_evidence(row.release_id)
        assert receipt.operations == (
            PublicAcceptanceOperation.SET_CUSTOM_FIELDS,
            PublicAcceptanceOperation.TRIGGER_FLOW,
        )
    original_http = list(lab.http)
    reopen(lab)
    lab.projector.run_once(now=NOW + timedelta(days=1))
    drain(lab)
    assert lab.http == original_http
    assert lab.execution.list_outcome_projection_inputs() == before
    assert len(lab.stripe.calls) == len(commands)


def test_customer_turn_consumes_offer_and_still_delivers_button(lab):
    prepare(lab)
    event = replace(
        EVENT, lead_id=LEAD, subscriber_id="12345", text="Pode enviar o pagamento?"
    )
    batch = replace(
        BATCH,
        lead_id=LEAD,
        subscriber_id="12345",
        events=(event,),
        combined_text=event.text,
    )
    lab.projector.executor.execute(batch)
    assert len(lab.projector.executor._model.calls[0].completion_events) == 3
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    drain(lab)
    assert len(buttons(lab)) == 2
    assert len(lab.projector.executor._model.calls) == 1


def test_projection_interruption_recovers_missing_buttons_not_business_effects(
    lab, monkeypatch
):
    prepare(lab)
    original = lab.public.enqueue
    calls = []

    def interrupted(reply, *, now):
        calls.append(reply)
        if len(calls) == 2:
            raise OSError("fixture: interrupted local enqueue")
        return original(reply, now=now)

    monkeypatch.setattr(lab.public, "enqueue", interrupted)
    with pytest.raises(OSError, match="local enqueue"):
        lab.projector.run_once(now=NOW + timedelta(seconds=5))
    assert lab.public.pending_count() == 1
    assert len(lab.projector.executor._model.calls) == 1
    reopen(lab)
    lab.projector.run_once(now=NOW + timedelta(seconds=6))
    drain(lab)
    assert len(buttons(lab)) == 2
    assert len(lab.stripe.calls) == 2


def test_button_does_not_suppress_pending_completion_consolidation(lab):
    prepare(lab)
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    assert lab.public.pending_count() == 2
    context = lab.projector.context(LEAD)
    assert sum(e.kind == "payment_offer" for e in context.events) == 2
    assert len(context.superseded_turns) == 1


@pytest.mark.parametrize("condition", ["disabled", "unknown", "generation_failed"])
def test_no_button_without_enabled_completed_and_committed_offer(
    lab, condition, monkeypatch
):
    prepare(lab, payment_failure=condition == "unknown")
    if condition == "disabled":
        lab.projector._include_payment_offers = False
    if condition == "generation_failed":

        def fail(_):
            raise TimeoutError("fixture: model failure")

        monkeypatch.setattr(lab.projector.executor, "execute", fail)
        with pytest.raises(TimeoutError):
            lab.projector.run_once(now=NOW + timedelta(seconds=5))
    else:
        lab.projector.run_once(now=NOW + timedelta(seconds=5))
    drain(lab)
    assert lab.public.pending_count() == 0
    assert not buttons(lab)


def test_legacy_payment_release_is_not_resent(lab):
    prepare(lab, service="reserve_lodging")
    (offer,) = lab.payments.completed_offers()
    lab.public.enqueue(
        PublicReply(
            release_id=_opaque("release:10-payment", offer.payment_id),
            lead_id=LEAD,
            message_id=_opaque("message:payment-link", offer.payment_id),
            channel="manychat",
            chunks=("Link de pagamento da hospedagem: " + offer.public_url,),
            author=PublicMessageAuthor.AUTHENTICATED_SYSTEM,
        ),
        now=NOW,
    )
    drain(lab)
    assert len(buttons(lab)) == 1
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    drain(lab)
    assert len(buttons(lab)) == 1


@pytest.mark.parametrize("behavior", ["unknown", "crash"])
def test_uncertain_button_never_retries_even_after_process_loss(lab, behavior):
    prepare(lab, service="reserve_lodging")
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    assert lab.public.pending_count() == 1
    lab.behavior = behavior
    if behavior == "crash":
        with pytest.raises(KeyboardInterrupt, match="process lost"):
            drain(lab)
    else:
        results, _ = drain(lab)
        assert "manual_review" in results
    assert len(buttons(lab)) == 1
    original_http = list(lab.http)
    lab.behavior = (
        "ok"  # Any forbidden retry is captured, not another process interruption.
    )
    reopen(lab)
    lab.projector.run_once(now=NOW + timedelta(days=1))
    worker(lab).run_once(now=NOW + timedelta(days=365))
    assert lab.http == original_http
    assert lab.public.manual_review_count() == 1


def test_not_called_button_can_retry_with_same_identity(lab):
    prepare(lab, service="reserve_lodging")
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    lab.behavior = "not_called"
    drain(lab)
    assert lab.public.pending_count() == 1
    assert not buttons(lab)
    failed_keys = {k for p, _, k in lab.http if p.endswith("setCustomFields")}
    lab.behavior = "ok"
    drain(lab)
    assert len(buttons(lab)) == 1
    assert {k for p, _, k in lab.http if p.endswith("setCustomFields")} == failed_keys


def test_live_gate_still_blocks_both_message_paths(lab):
    prepare(lab)
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    assert lab.public.pending_count() == 2
    drain(lab, enabled=False)
    assert lab.http == []
    drain(lab)
    assert len(buttons(lab)) == 2


@pytest.mark.parametrize("phase", ["before_send", "after_acceptance"])
def test_button_fence_survives_local_failure_without_reclaim(lab, monkeypatch, phase):
    prepare(lab, service="reserve_lodging")
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    assert lab.public.pending_count() == 1
    if phase == "before_send":
        claim = lab.public.claim(
            worker_id="worker:interrupted", now=NOW, lease_ttl=timedelta(seconds=30)
        )
        lab.public.fence(claim, now=NOW)
        with pytest.raises(RuntimeError, match="already fenced"):
            lab.public.fence(claim, now=NOW)
    else:

        def fail(*args, **kwargs):
            raise OSError("fixture: acceptance persistence interrupted")

        monkeypatch.setattr(lab.public, "complete", fail)
        with pytest.raises(OSError, match="acceptance persistence"):
            drain(lab)
        assert len(buttons(lab)) == 1
    original_http = list(lab.http)
    reopen(lab)
    lab.projector.run_once(now=NOW + timedelta(days=1))
    # For before_send, the independent Maya reply is still pending; drain it first.
    if phase == "before_send":
        drain(lab)
        original_http = list(lab.http)
    worker(lab).run_once(now=NOW + timedelta(days=365))
    assert lab.http == original_http
    assert lab.public.manual_review_count() == 1


def test_expired_unfenced_claim_is_recoverable_but_old_owner_cannot_fence(lab):
    prepare(lab, service="reserve_lodging")
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    assert lab.public.pending_count() == 1
    old = lab.public.claim(
        worker_id="worker:old", now=NOW, lease_ttl=timedelta(seconds=1)
    )
    with pytest.raises(RuntimeError, match="stale"):
        lab.public.fence(old, now=NOW + timedelta(seconds=1))
    recovered = lab.public.claim(
        worker_id="worker:new",
        now=NOW + timedelta(seconds=1),
        lease_ttl=timedelta(seconds=30),
    )
    assert recovered.outbox_id == old.outbox_id
    assert recovered.fencing_token > old.fencing_token
    with pytest.raises(RuntimeError, match="stale"):
        lab.public.fence(old, now=NOW)
    lab.public.fence(recovered, now=NOW + timedelta(seconds=1))
