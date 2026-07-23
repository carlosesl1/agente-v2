from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from v2_adapters.manychat import (
    ManyChatDeliveryAdapter,
    ManyChatTransportNotCalled,
    ManyChatTransportResponse,
)
from v2_application.completion import (
    CompletionContext,
    CompletionPolicy,
    CompletionStatus,
    PublicDeliveryWorker,
    PublicOutboxStore,
    PublicReply,
    PublicServiceKind,
    PublicDeliveryNotCalled,
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
            raise PublicDeliveryNotCalled("before send")
        return f"receipt:{claim.message_id}"


class ManyChatTransport:
    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior
        self.calls = []

    def send_text(self, *, subscriber_id: str, text: str, idempotency_key: str):
        self.calls.append((subscriber_id, text, idempotency_key))
        if self.behavior == "not_called":
            raise ManyChatTransportNotCalled("connection refused before request")
        if self.behavior == "unknown":
            raise TimeoutError("response lost after request")
        return ManyChatTransportResponse(provider_message_id="manychat-message:001")


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


def test_manychat_adapter_uses_subscriber_and_stable_outbox_identity(tmp_path) -> None:
    store = PublicOutboxStore(tmp_path / "manychat-success.sqlite3")
    reply = PublicReply(
        release_id="release:manychat-success",
        lead_id="manychat:subscriber-001",
        message_id="message:manychat-success",
        channel="manychat",
        chunks=("Mensagem pública.",),
    )
    store.enqueue(reply, now=NOW)
    claim = store.claim(
        worker_id="worker:manychat-success",
        now=NOW + timedelta(seconds=1),
        lease_ttl=timedelta(seconds=30),
    )
    transport = ManyChatTransport()

    receipt_id = ManyChatDeliveryAdapter(transport).send(claim)

    assert receipt_id == "manychat-message:001"
    assert transport.calls == [
        ("subscriber-001", "Mensagem pública.", claim.outbox_id)
    ]


def test_manychat_pre_send_failure_requeues_but_unknown_never_resends(tmp_path) -> None:
    def run_case(name: str, behavior: str):
        store = PublicOutboxStore(tmp_path / f"{name}.sqlite3")
        reply = PublicReply(
            release_id=f"release:{name}",
            lead_id="manychat:subscriber-001",
            message_id=f"message:{name}",
            channel="manychat",
            chunks=("Mensagem pública.",),
        )
        store.enqueue(reply, now=NOW)
        transport = ManyChatTransport(behavior)
        worker = PublicDeliveryWorker(
            store=store,
            delivery=ManyChatDeliveryAdapter(transport),
            worker_id=f"worker:{name}",
            lease_ttl=timedelta(seconds=30),
        )
        first = worker.run_once(now=NOW + timedelta(seconds=1))
        second = worker.run_once(now=NOW + timedelta(seconds=2))
        return store, transport, first, second

    retry_store, retry_transport, retry_first, retry_second = run_case(
        "manychat-not-called", "not_called"
    )
    assert retry_first.value == "retryable_failure"
    assert retry_second.value == "retryable_failure"
    assert retry_store.pending_count() == 1
    assert len(retry_transport.calls) == 2

    unknown_store, unknown_transport, unknown_first, unknown_second = run_case(
        "manychat-unknown", "unknown"
    )
    assert unknown_first.value == "manual_review"
    assert unknown_second.value == "idle"
    assert unknown_store.manual_review_count() == 1
    assert len(unknown_transport.calls) == 1


def test_public_outbox_migrates_checkpoint_schema_before_unknown_outcome(tmp_path) -> None:
    path = tmp_path / "public-checkpoint.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE public_outbox (
          outbox_id TEXT PRIMARY KEY,
          release_id TEXT NOT NULL,
          lead_id TEXT NOT NULL,
          source_message_id TEXT NOT NULL,
          chunk_index INTEGER NOT NULL,
          text TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('pending','leased','delivered')),
          claim_owner TEXT,
          fencing_token INTEGER NOT NULL DEFAULT 0,
          lease_expires_at TEXT,
          receipt_id TEXT,
          updated_at TEXT NOT NULL,
          UNIQUE(release_id, chunk_index)
        ) STRICT;
        """
    )
    connection.close()
    store = PublicOutboxStore(path)
    reply = PublicReply(
        release_id="release:migrated",
        lead_id="manychat:subscriber-001",
        message_id="message:migrated",
        channel="manychat",
        chunks=("Mensagem pública.",),
    )
    store.enqueue(reply, now=NOW)
    worker = PublicDeliveryWorker(
        store=store,
        delivery=ManyChatDeliveryAdapter(ManyChatTransport("unknown")),
        worker_id="worker:migrated",
        lease_ttl=timedelta(seconds=30),
    )

    assert worker.run_once(now=NOW + timedelta(seconds=1)).value == "manual_review"
    assert store.manual_review_count() == 1
