"""Connected qualification for completion, Stripe, fencing and two-owner races."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest

from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from tests import test_v2_booking_form_delivery as form_lab
from tests.test_v2_booking_form_delivery import LEAD, NOW, command_for, settle
from tests.v2_completion_helpers import CompletionMaya, authored_chunks, make_completion
from v2_application.completion import PublicOutboxStore
from v2_application.public_delivery import CombinedPublicDeliveryWorker

build = form_lab.build


def test_settlement_completion_enqueues_button_without_rewriting_maya(build, tmp_path):
    f = build()
    settle(f)
    projector = make_completion(
        tmp_path,
        f.execution,
        f.payments,
        f.public,
        lead_id=LEAD,
        followup=f.followup,
        booking_forms_from=NOW,
    )
    try:
        assert projector.run_once(now=f.clock()).inserted == 1
        assert [(c.author, c.text) for c in authored_chunks(projector)] == [
            ("maya", CompletionMaya.text)
        ]
        assert len(f.public.conversation_messages(LEAD)) == 1
        f.delivery._booking_form_resolver = projector.booking_forms.route_for_claim
        gate = SimpleNamespace(allows_workflow=lambda _: False)
        worker = CombinedPublicDeliveryWorker(
            boundary=SQLiteBoundaryWorkerStore(projector._boundary),
            completion=f.public,
            delivery=f.delivery,
            effect_guard=gate,
            worker_id="worker:joined",
            lease_ttl=timedelta(seconds=30),
        )
        assert worker.run_once(now=f.clock()).value == "idle"
        assert f.http == []
        gate.allows_workflow = lambda _: True
        from datetime import datetime

        created = projector._boundary._connection.execute(
            "SELECT max(created_at) FROM boundary_public_outbox"
        ).fetchone()[0]
        tick = datetime.fromisoformat(created) + timedelta(seconds=1)
        assert worker.run_once(now=tick).value == "accepted"
        assert worker.run_once(now=tick).value == "accepted"
        assert [b["field_id"] for p, b in f.http if p.endswith("setCustomField")] == [
            101,
            14426643,
        ]
        before = list(f.http)
        projector.run_once(now=tick)
        assert worker.run_once(now=tick).value == "idle"
        assert f.http == before
    finally:
        projector._boundary.close()


def test_concurrent_enqueues_choose_only_one_form_per_lead(build):
    f = build()
    settle(f)
    candidate = f.forms._candidates()[0]
    from v2_contracts.localization import CustomerLanguage

    reply = f.forms._reply(candidate, CustomerLanguage.PT_BR)
    other = replace(
        reply, message_id="message:booking-form:other-paid-tour", chunks=("other form",)
    )
    barrier = Barrier(2)

    def insert(row):
        store = PublicOutboxStore(f.path / "public.sqlite3")
        try:
            barrier.wait(timeout=5)
            return store.enqueue(row, now=f.clock(), once_per_release=True)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(insert, (reply, other))) == [0, 1]
    assert len(f.public.conversation_messages(LEAD)) == 1


@pytest.mark.parametrize("behavior", ["unknown", "crash"])
def test_terminal_form_delivery_problem_opens_one_real_handoff_not_retry(
    build, behavior
):
    f = build()
    settle(f)
    f.forms.run_once(now=f.clock())
    f.behavior = behavior
    if behavior == "crash":
        with pytest.raises(KeyboardInterrupt):
            f.worker().run_once(now=f.clock())
    else:
        assert f.worker().run_once(now=f.clock()).value == "manual_review"
    tick = f.clock() + timedelta(seconds=60)
    f.forms.run_once(now=tick)
    handoff = f.followup.find_active_handoff_by_lead_hash(
        hashlib.sha256(LEAD.encode()).hexdigest()
    )
    assert handoff is not None
    assert handoff.request.reason_code.value == "operational_review"
    f.forms.run_once(now=tick)
    assert (
        f.followup.find_active_handoff_by_lead_hash(
            hashlib.sha256(LEAD.encode()).hexdigest()
        )
        == handoff
    )
    assert f.worker().run_once(now=tick).value == "idle"
    assert len([1 for p, b in f.http if p.endswith("sendFlow")]) == 1


def test_live_fence_during_inflight_send_does_not_open_false_handoff(build):
    f = build()
    settle(f)
    f.forms.run_once(now=f.clock())
    claim = f.public.claim(
        worker_id="worker:pending", now=f.clock(), lease_ttl=timedelta(seconds=30)
    )
    f.public.fence(claim, now=f.clock())
    f.forms.run_once(now=f.clock())
    assert (
        f.followup.find_active_handoff_by_lead_hash(
            hashlib.sha256(LEAD.encode()).hexdigest()
        )
        is None
    )


def test_separate_lead_gets_their_own_form(build):
    first = build(nonce="first")
    second = build(nonce="second")
    settle(first)
    settle(second)
    second.forms.leads = SimpleNamespace(lead_id_for_command=lambda _: "manychat:99999")
    second.forms.public = first.public
    assert first.forms.run_once(now=first.clock()) == 1
    assert second.forms.run_once(now=second.clock()) == 1
    assert len(first.public.conversation_messages(LEAD)) == 1
    assert len(first.public.conversation_messages("manychat:99999")) == 1


@pytest.mark.parametrize(
    "livemode", [False, True], ids=["Stripe TEST fixture", "Stripe LIVE fixture"]
)
def test_stripe_signed_event_then_provider_settlement_emits_form(
    build, tmp_path, monkeypatch, livemode
):
    from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
    from tests import native_stripe_helpers as native
    from tests.test_v2_native_stripe import accept
    from tests.test_v2_stripe_settlement import worker as card_worker
    from v2_application.booking_forms import BookingFormProjector

    f = build()
    (tmp_path / "card").mkdir()
    with monkeypatch.context() as scope:
        scope.setattr(
            native,
            "_package_command",
            lambda **kw: command_for("product:pati-3d", **kw),
        )
        card = native.make_lab(tmp_path / "card", "agency", livemode=livemode)
    card.now = native.NOW + timedelta(seconds=1)
    card.guard = True
    card.receipt = accept(card)
    card.followup = SQLiteFollowupUnitOfWork.open_v2(card.paths["followup"])
    calls = []

    def respond(request):
        calls.append(request)
        row = {
            "bookingId": 456,
            "status": "RESERVED",
            "currency": "BRL",
            "totalPaid": 0,
            "totalDue": 10000,
        }
        if request.method == "POST":
            payment = json.loads(request.content)["payment"]
            row.update(
                status="CONFIRMED",
                totalPaid=payment["amount"],
                customerPayments=[{"id": 789, **payment}],
            )
        return httpx.Response(200, json=row)

    card.provider_client = httpx.Client(transport=httpx.MockTransport(respond))
    forms = BookingFormProjector(
        execution=card.execution,
        followup=card.followup,
        public_store=f.public,
        boundary=f.boundary,
        lead_resolver=SimpleNamespace(lead_id_for_command=lambda _: LEAD),
        enabled_from=NOW,
    )
    f.delivery._booking_form_resolver = forms.route_for_claim
    try:
        assert forms.run_once(now=card.now) == 0
        assert card_worker(card).run_once(now=card.now).disposition.value == "settled"
        assert forms.run_once(now=card.now) == 1
        assert f.worker().run_once(now=card.now).value == "accepted"
        assert f.http[0][1]["field_value"] == "https://forms.gle/bXEoQUBcwzBaMQ8t7"
        assert forms.run_once(now=card.now) == 0
        assert len([r for r in calls if r.method == "POST"]) == 1
    finally:
        card.followup.close()
        card.provider_client.close()
        native.close_lab(card)
