"""Regression witnesses for B4/B5/B8; no external network or live state."""

from datetime import timedelta
import pytest

from reservation_followup import HandoffEffectPolicy
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from reservation_followup.workers import HandoffOutboxWorker
from tests.phase6_helpers import T0, handoff_requested
from tests.test_v2_manychat_handoff_delivery import _adapter, _transport
from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2Settings
import httpx

NOW = T0 + timedelta(minutes=1)


def test_handoff_records_receipt_after_transport_time_advances(tmp_path):
    calls = []

    class Clock:
        def now(self):
            return NOW + timedelta(seconds=len(calls))

    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(200, request=request, json={"status": "success"})

    store = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
    try:
        store.open_handoff(
            handoff_requested(), HandoffEffectPolicy.default_email_disabled()
        )
        adapter = _adapter(_transport(transport))
        adapter._clock = Clock()
        worker = HandoffOutboxWorker(
            store=store,
            delivery=adapter,
            worker_id="worker:time",
            lease_ttl=timedelta(seconds=30),
            clock=Clock().now,
        )
        assert worker.run_once(now=NOW).disposition.value == "delivered"
        assert (
            worker.run_once(now=NOW + timedelta(seconds=3)).disposition.value == "idle"
        )
        assert len(calls) == 2
        assert store._connection.execute(
            "SELECT delivered_at FROM handoff_receipts"
        ).fetchone() == ((NOW + timedelta(seconds=2)).isoformat(),)
    finally:
        store.close()


def test_worker_composition_can_persist_partial_handoff_without_retry(tmp_path):
    settings = V2Settings(
        webhook_secret="synthetic", sqlite_path=tmp_path / "inbox.sqlite3"
    )
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    calls = []

    def transport(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(200, request=request, json={"status": "success"})
        raise httpx.ConnectError("synthetic failure", request=request)

    try:
        store = container.followup
        store.open_handoff(
            handoff_requested(), HandoffEffectPolicy.default_email_disabled()
        )
        worker = HandoffOutboxWorker(
            store=store,
            delivery=_adapter(_transport(transport)),
            worker_id="worker:partial",
            lease_ttl=timedelta(seconds=30),
        )
        assert worker.run_once(now=NOW).disposition.value == "manual_review"
        assert (
            worker.run_once(now=NOW + timedelta(seconds=31)).disposition.value == "idle"
        )
        assert len(calls) == 2
    finally:
        container.close()


def test_followup_v1_upgrade_preserves_pending_and_delivered_then_reopens(tmp_path):
    from tests.test_phase6_handoff_worker import ScriptedDelivery

    path = tmp_path / "followup.sqlite3"
    with SQLiteFollowupUnitOfWork.open(path) as old:
        old.open_handoff(
            handoff_requested(), HandoffEffectPolicy.default_email_disabled()
        )
        HandoffOutboxWorker(
            store=old,
            delivery=ScriptedDelivery([NOW]),
            worker_id="worker:old",
            lease_ttl=timedelta(seconds=30),
        ).run_once(now=NOW)
        columns = {
            name: tuple(
                row[1] for row in old._connection.execute(f"PRAGMA table_info({name})")
            )
            for name, _ in old._table_rows()
        }
        rows = {
            name: old._connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for name in columns
        }
    for _ in range(2):
        with SQLiteFollowupUnitOfWork.open_v2(path, migrate_v1=True) as new:
            for name, names in columns.items():
                assert (
                    new._connection.execute(
                        f"SELECT {','.join(names)} FROM {name} ORDER BY 1"
                    ).fetchall()
                    == rows[name]
                )
            assert not new._connection.execute("PRAGMA foreign_key_check").fetchall()


def test_public_reply_uses_commit_sequence_and_blocks_later_turns(tmp_path):
    from reservation_boundary.sqlite_store import SQLiteBoundaryStore
    from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
    from tests import test_v2_turn_executor as fx
    from v2_adapters.manychat import parse_manychat_payload
    from v2_host.public_authority import GeneralAvailabilityPublicAuthorityResolver
    from v2_contracts.model import ModelProposal
    from v2_application.inbox import SQLiteInbox, _batch_id
    from v2_application.inbox_worker import InboxTurnWorker

    now = fx.NOW

    def event(name):
        return parse_manychat_payload(
            {
                "event_id": name,
                "subscriber_id": "10001",
                "text": "Synthetic request",
                "timestamp": (now - timedelta(seconds=10)).isoformat(),
            },
            now,
        )

    class DynamicModel(fx.FakeAuditedModel):
        def complete_audited(self, request):
            self.proposals.append(
                ModelProposal(
                    source_event_id=request.source_event_id,
                    intent="inform",
                    reply_chunks=("Qual data deseja consultar?",),
                    clarification_question="Qual data deseja consultar?",
                    facts=(),
                    read_requests=(),
                    effect_proposals=(),
                )
            )
            return super().complete_audited(request)

    store = SQLiteBoundaryStore.open_memory_v8()
    model = DynamicModel(store, [])
    ex = fx._executor(
        store=store,
        model=model,
        profile=fx.FakeProfile(store),
        public_authority=GeneralAvailabilityPublicAuthorityResolver(
            store=store, hmac_key=b"a" * 32
        ),
    )
    inbox = SQLiteInbox(tmp_path / "order.sqlite3")
    worker = InboxTurnWorker(
        inbox=inbox,
        executor=ex,
        quiet_window=timedelta(0),
        lease_ttl=timedelta(seconds=30),
    )
    options = sorted(
        (event("order-" + str(i)) for i in range(20)), key=lambda e: _batch_id((e,))
    )
    try:
        inbox.accept(options[-1])
        first = worker.run_once(now=now)
        inbox.accept(options[0])
        second = worker.run_once(now=now + timedelta(seconds=1))
        operational = SQLiteBoundaryWorkerStore(store)
        claim = operational.claim_public_delivery(
            worker_id="worker:one",
            now=now + timedelta(seconds=2),
            lease_ttl=timedelta(seconds=30),
        )
        assert claim.aggregate_turn_id == first.batch_id
        assert claim.aggregate_turn_id != second.batch_id
        assert (
            operational.claim_public_delivery(
                worker_id="worker:two",
                now=now + timedelta(seconds=3),
                lease_ttl=timedelta(seconds=30),
            )
            is None
        )
    finally:
        ex._private_customer_facts.close()
        store.close()


def test_handoff_known_pre_call_failure_retries_after_restart(tmp_path):
    settings = V2Settings(
        webhook_secret="synthetic", sqlite_path=tmp_path / "inbox.sqlite3"
    )
    calls = []

    def transport(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ConnectError("not sent", request=request)
        return httpx.Response(200, request=request, json={"status": "success"})

    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        container.followup.open_handoff(
            handoff_requested(), HandoffEffectPolicy.default_email_disabled()
        )
        worker = HandoffOutboxWorker(
            store=container.followup,
            delivery=_adapter(_transport(transport)),
            worker_id="worker:retry",
            lease_ttl=timedelta(seconds=30),
        )
        assert worker.run_once(now=NOW).disposition.value == "retryable_failure"
    finally:
        container.close()
    container = V2Container.open(settings=settings, role=V2Role.WORKER)

    class Clock:
        def now(self):
            return NOW + timedelta(seconds=5)

    try:
        adapter = _adapter(_transport(transport))
        adapter._clock = Clock()
        worker = HandoffOutboxWorker(
            store=container.followup,
            delivery=adapter,
            worker_id="worker:retry",
            lease_ttl=timedelta(seconds=30),
            clock=Clock().now,
        )
        assert worker.run_once(now=Clock().now()).disposition.value == "delivered"
    finally:
        container.close()
    assert len(calls) == 3


@pytest.mark.parametrize("outcome", ["success", "unknown", "crash"])
def test_slow_or_interrupted_handoff_never_resends_after_possible_effect(
    tmp_path, outcome
):
    from tests.test_phase6_handoff_worker import ScriptedDelivery
    from reservation_followup.workers import HandoffDeliveryUnknown

    path = tmp_path / "late.sqlite3"
    store = SQLiteFollowupUnitOfWork.open_v2(path)
    store.open_handoff(
        handoff_requested(), HandoffEffectPolicy.default_email_disabled()
    )
    late = NOW + timedelta(seconds=31)
    effect = (
        late
        if outcome == "success"
        else HandoffDeliveryUnknown("effect possible")
        if outcome == "unknown"
        else KeyboardInterrupt()
    )
    delivery = ScriptedDelivery([effect])
    worker = HandoffOutboxWorker(
        store=store,
        delivery=delivery,
        worker_id="worker:slow",
        lease_ttl=timedelta(seconds=30),
        clock=lambda: late,
    )
    try:
        if outcome == "crash":
            with pytest.raises(KeyboardInterrupt):
                worker.run_once(now=NOW)
        else:
            assert worker.run_once(now=NOW).disposition.value == (
                "delivered" if outcome == "success" else "manual_review"
            )
    finally:
        store.close()
    with SQLiteFollowupUnitOfWork.open_v2(path) as reopened:
        retry = HandoffOutboxWorker(
            store=reopened,
            delivery=delivery,
            worker_id="worker:restart",
            lease_ttl=timedelta(seconds=30),
            clock=lambda: late,
        )
        assert retry.run_once(now=late).disposition.value == "idle"
        status = reopened._connection.execute(
            "SELECT status FROM handoff_outbox"
        ).fetchone()[0]
        assert status == ("delivered" if outcome == "success" else "manual_review")
    assert len(delivery.messages) == 1


def test_followup_upgrade_failure_rolls_back_exact_legacy_schema(tmp_path, monkeypatch):
    import reservation_followup.sqlite_store as module

    path = tmp_path / "rollback.sqlite3"
    with SQLiteFollowupUnitOfWork.open(path) as old:
        old.open_handoff(
            handoff_requested(), HandoffEffectPolicy.default_email_disabled()
        )
        schema = old._table_rows()
        rows = {
            name: old._connection.execute(f"SELECT * FROM {name}").fetchall()
            for name, _ in schema
        }
    monkeypatch.setattr(
        module,
        "_schema_statements_v2",
        lambda: ("PRAGMA foreign_keys=ON", "INVALID SQL"),
    )
    with pytest.raises(Exception):
        SQLiteFollowupUnitOfWork.open_v2(path, migrate_v1=True)
    with SQLiteFollowupUnitOfWork.open(path) as old:
        assert old._table_rows() == schema
        for name, before in rows.items():
            assert old._connection.execute(f"SELECT * FROM {name}").fetchall() == before


def test_upgrade_does_not_replay_legacy_inflight_effect(tmp_path):
    path = tmp_path / "legacy-inflight.sqlite3"
    legacy = SQLiteFollowupUnitOfWork.open(path)
    legacy.open_handoff(
        handoff_requested(), HandoffEffectPolicy.default_email_disabled()
    )
    claim = legacy.claim_handoff_outbox(
        worker_id="old-process",
        now=NOW,
        lease_ttl=timedelta(seconds=1),
        delivery_id="scripted-channel",
        delivery_version=1,
    )
    assert claim is not None  # Legacy has no durable before/after-call evidence.
    legacy.close()
    current = SQLiteFollowupUnitOfWork.open_v2(path, migrate_v1=True)
    from tests.test_phase6_handoff_worker import ScriptedDelivery

    adapter = ScriptedDelivery([NOW + timedelta(seconds=2)])
    try:
        result = HandoffOutboxWorker(
            store=current,
            delivery=adapter,
            worker_id="new-process",
            lease_ttl=timedelta(seconds=30),
        ).run_once(now=NOW + timedelta(seconds=2))
        assert result.disposition.value == "idle"
        assert adapter.messages == []
        assert (
            current._connection.execute("SELECT status FROM handoff_outbox").fetchone()[
                0
            ]
            == "manual_review"
        )
    finally:
        current.close()


def test_upgrade_rejection_rolls_back_original_schema_and_payloads(tmp_path):
    path = tmp_path / "reject-unknown.sqlite3"
    legacy = SQLiteFollowupUnitOfWork.open(path)
    legacy.open_handoff(
        handoff_requested(), HandoffEffectPolicy.default_email_disabled()
    )
    before_schema = legacy._connection.execute(
        "SELECT type,name,sql FROM sqlite_master ORDER BY name"
    ).fetchall()
    before_rows = legacy._connection.execute("SELECT * FROM handoff_outbox").fetchall()
    legacy._connection.execute("CREATE VIEW foreign_view AS SELECT 1 AS value")
    legacy.close()
    with pytest.raises(Exception, match="unexpected objects"):
        SQLiteFollowupUnitOfWork.open_v2(path, migrate_v1=True)
    reopened = SQLiteFollowupUnitOfWork.open(path)
    try:
        after_schema = reopened._connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name != 'foreign_view' ORDER BY name"
        ).fetchall()
        assert after_schema == before_schema
        assert (
            reopened._connection.execute("SELECT * FROM handoff_outbox").fetchall()
            == before_rows
        )
    finally:
        reopened.close()
