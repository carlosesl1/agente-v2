from __future__ import annotations

from datetime import datetime, timedelta, timezone

from v2_application.completion import (
    CompletionContext,
    CompletionPolicy,
    CompletionStatus,
    PublicDeliveryWorker,
    PublicOutboxStore,
    PublicReply,
    PublicServiceKind,
)


NOW = datetime(2026, 7, 23, 17, 0, tzinfo=timezone.utc)


class Delivery:
    delivery_id = "manychat:fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def send(self, claim):
        self.calls.append(claim)
        if self.fail:
            raise ConnectionError("before send")
        return f"receipt:{claim.message_id}"


def context(
    *,
    kind: PublicServiceKind = PublicServiceKind.LODGING,
    receipts=frozenset(("reservation", "settlement", "public_delivery")),
    manual_review: bool = False,
):
    return CompletionContext(
        workflow_id="workflow:completion:001",
        service_kind=kind,
        requires_payment=True,
        manual_review=manual_review,
        receipts=receipts,
    )


def test_completion_requires_only_declared_receipts_and_ignores_optional_email() -> None:
    policy = CompletionPolicy()

    assert policy.evaluate(context()) is CompletionStatus.COMPLETED
    assert policy.evaluate(
        context(receipts=frozenset(("reservation", "settlement", "public_delivery", "optional_email_failed")))
    ) is CompletionStatus.COMPLETED
    assert policy.evaluate(
        context(receipts=frozenset(("reservation", "settlement")))
    ) is CompletionStatus.PENDING
    assert policy.evaluate(context(manual_review=True)) is CompletionStatus.MANUAL_REVIEW


def test_bokun_form_is_required_only_for_activity() -> None:
    policy = CompletionPolicy()

    assert "bokun_form" not in policy.required_receipts(context())
    activity = context(kind=PublicServiceKind.ACTIVITY)
    assert "bokun_form" in policy.required_receipts(activity)
    assert policy.evaluate(activity) is CompletionStatus.PENDING


def test_public_outbox_delivery_receipt_is_idempotent(tmp_path) -> None:
    store = PublicOutboxStore(tmp_path / "public-outbox.sqlite3")
    reply = PublicReply(
        release_id="release:001",
        lead_id="manychat:lead-001",
        message_id="message:001",
        channel="manychat",
        chunks=("Reserva confirmada.", "Confira os próximos passos."),
    )
    assert store.enqueue(reply, now=NOW) == 2
    assert store.enqueue(reply, now=NOW) == 0
    delivery = Delivery()
    worker = PublicDeliveryWorker(
        store=store,
        delivery=delivery,
        worker_id="worker:public-delivery",
        lease_ttl=timedelta(seconds=30),
    )

    first = worker.run_once(now=NOW + timedelta(seconds=1))
    second = worker.run_once(now=NOW + timedelta(seconds=2))
    idle = worker.run_once(now=NOW + timedelta(seconds=3))

    assert first.value == "delivered"
    assert second.value == "delivered"
    assert idle.value == "idle"
    assert store.delivered_count(reply.release_id) == 2
    assert len(delivery.calls) == 2


def test_delivery_failure_requeues_without_touching_upstream_effects(tmp_path) -> None:
    store = PublicOutboxStore(tmp_path / "public-failure.sqlite3")
    reply = PublicReply(
        release_id="release:failure",
        lead_id="manychat:lead-001",
        message_id="message:failure",
        channel="manychat",
        chunks=("Mensagem segura.",),
    )
    store.enqueue(reply, now=NOW)
    delivery = Delivery(fail=True)
    worker = PublicDeliveryWorker(
        store=store,
        delivery=delivery,
        worker_id="worker:public-failure",
        lease_ttl=timedelta(seconds=30),
    )
    reservation_calls = 1
    settlement_calls = 1

    assert worker.run_once(now=NOW + timedelta(seconds=1)).value == "retryable_failure"
    assert store.pending_count() == 1
    assert (reservation_calls, settlement_calls) == (1, 1)
