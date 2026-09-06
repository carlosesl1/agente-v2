"""Offline B2/B6 witnesses: durable inbox, real commit/replay, denied network."""
from datetime import timedelta
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from tests import test_v2_turn_executor as fx
from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from v2_adapters.manychat import parse_manychat_payload
from v2_application.inbox import SQLiteInbox
from v2_application.inbox_worker import InboxTurnWorker, InboxWorkerDisposition
from v2_contracts.channel import AcceptDisposition
from v2_contracts.model import ModelProposal
from v2_host.public_authority import GeneralAvailabilityPublicAuthorityResolver

NOW = fx.NOW
LEASE = timedelta(seconds=30)


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network forbidden in inbox reliability tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def event(name, *, lead="10001", at=None):
    return parse_manychat_payload({
        "event_id": name, "subscriber_id": lead, "text": "Synthetic request",
        "timestamp": (at or NOW - timedelta(seconds=10)).isoformat(),
    }, NOW)


def worker(inbox, executor):
    return InboxTurnWorker(inbox=inbox, executor=executor,
                           quiet_window=timedelta(0), lease_ttl=LEASE)


def claim(inbox, now=NOW, quiet=timedelta(0)):
    return inbox.claim_ready(now=now, quiet_window=quiet, lease_for=LEASE)


class SelectiveExecutor:
    def __init__(self):
        self.seen = []
        self.fail = True

    def execute(self, batch):
        self.seen.append(batch)
        if self.fail and batch.lead_id == "manychat:10001":
            raise ValueError("synthetic deterministic failure")
        return SimpleNamespace(receipt=SimpleNamespace(artifact_hash="a" * 64),
                               replayed=False)


@pytest.mark.parametrize("next_seconds", [1, 31])
def test_poison_lead_yields_to_healthy_after_worker_restart(tmp_path, next_seconds):
    path = tmp_path / "inbox.sqlite3"
    inbox = SQLiteInbox(path)
    first = event("poison")
    healthy = event("healthy", lead="10002", at=NOW - timedelta(seconds=5))
    inbox.accept(first)
    inbox.accept(healthy)
    executor = SelectiveExecutor()
    with pytest.raises(ValueError, match="synthetic deterministic"):
        worker(inbox, executor).run_once(now=NOW)
    restarted = SQLiteInbox(path)
    result = worker(restarted, executor).run_once(now=NOW + timedelta(seconds=next_seconds))
    assert result.disposition is InboxWorkerDisposition.COMMITTED
    assert [b.lead_id for b in executor.seen] == [first.lead_id, healthy.lead_id]
    assert restarted.processed_count() == 1
    assert restarted.pending_count() == 1
    executor.fail = False
    assert worker(restarted, executor).run_once(now=NOW + timedelta(seconds=60)).disposition is InboxWorkerDisposition.COMMITTED
    assert executor.seen[-1] == executor.seen[0]
    assert restarted.processed_count() == 2


def test_failed_batch_cannot_be_bypassed_by_new_same_lead_event(tmp_path):
    path = tmp_path / "inbox.sqlite3"
    inbox = SQLiteInbox(path)
    inbox.accept(event("first"))
    executor = SelectiveExecutor()
    with pytest.raises(ValueError):
        worker(inbox, executor).run_once(now=NOW)
    inbox.accept(event("new", at=NOW))
    restarted = SQLiteInbox(path)
    assert claim(restarted, NOW + timedelta(microseconds=1)) is None
    retried = claim(restarted, NOW + timedelta(seconds=60))
    assert retried.batch == executor.seen[0]
    assert restarted.pending_count() == 1


@pytest.mark.parametrize("recovery", ["release", "expiry"])
def test_original_membership_survives_restart_and_newer_quiet_window(tmp_path, recovery):
    path = tmp_path / "inbox.sqlite3"
    inbox = SQLiteInbox(path)
    first, second = event("a"), event("b", at=NOW - timedelta(seconds=9))
    inbox.accept(second)
    inbox.accept(first)
    original = claim(inbox)
    assert original.events == (first, second)
    if recovery == "release":
        inbox.release_claim(original)
    reopened = SQLiteInbox(path)
    newer = event("new", at=NOW + timedelta(seconds=30))
    assert reopened.accept(newer) is AcceptDisposition.ACCEPTED
    assert reopened.accept(first) is AcceptDisposition.DUPLICATE
    recovered = claim(reopened, NOW + LEASE, quiet=timedelta(seconds=5))
    assert recovered is not None
    assert recovered.batch == original.batch
    assert recovered.claim_token != original.claim_token
    with pytest.raises(RuntimeError):
        reopened.complete_claim(original, turn_receipt_hash="b" * 64, now=NOW + timedelta(seconds=1))
    reopened.complete_claim(recovered, turn_receipt_hash="a" * 64, now=NOW + LEASE)
    assert claim(reopened, NOW + LEASE, quiet=timedelta(seconds=5)) is None
    following = claim(reopened, NOW + timedelta(seconds=35), quiet=timedelta(seconds=5))
    assert following.events == (newer,)
    reopened.complete_claim(following, turn_receipt_hash="b" * 64, now=NOW + timedelta(seconds=35))
    assert reopened.processed_count() == 3
    assert reopened.accept(newer) is AcceptDisposition.DUPLICATE


def test_live_claim_blocks_same_lead_but_not_other_lead(tmp_path):
    inbox = SQLiteInbox(tmp_path / "inbox.sqlite3")
    inbox.accept(event("first"))
    original = claim(inbox)
    inbox.accept(event("new", at=NOW - timedelta(seconds=2)))
    healthy = event("healthy", lead="10002", at=NOW - timedelta(seconds=1))
    inbox.accept(healthy)
    other = claim(inbox)
    assert other.events == (healthy,)
    inbox.complete_claim(other, turn_receipt_hash="a" * 64, now=NOW)
    assert claim(inbox) is None
    inbox.complete_claim(original, turn_receipt_hash="b" * 64, now=NOW)
    assert claim(inbox).events[0].event_id == "new"


def test_legacy_live_claim_migrates_without_rebatching(tmp_path):
    from v2_application.inbox import _batch_id, _event_bytes

    path = tmp_path / "legacy.sqlite3"
    first = event("legacy-first")
    with sqlite3.connect(path) as connection:
        # Pre-reliability inbox schema, including the older optional ACK columns.
        connection.executescript("""
            CREATE TABLE inbound_events (
              event_id TEXT PRIMARY KEY, lead_id TEXT NOT NULL,
              subscriber_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
              occurred_at TEXT NOT NULL, payload BLOB NOT NULL,
              payload_hash TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN
                ('pending','claimed','processed','manual_review')),
              claim_token TEXT, claim_expires_at TEXT
            ) STRICT;
        """)
        connection.execute(
            "INSERT INTO inbound_events VALUES (?,?,?,?,?,?,?,'claimed',?,?)",
            (first.event_id, first.lead_id, first.subscriber_id,
             first.conversation_id, first.occurred_at.isoformat(timespec="microseconds"),
             _event_bytes(first), first.payload_hash, "a" * 32,
             (NOW + LEASE).isoformat(timespec="microseconds")),
        )
    migrated = SQLiteInbox(path)
    migrated.accept(event("new-after-upgrade", at=NOW))
    restarted = SQLiteInbox(path)
    recovered = claim(restarted, NOW + LEASE)
    assert recovered is not None
    assert recovered.events == (first,)
    assert recovered.batch_id == _batch_id((first,))
    restarted.complete_claim(recovered, turn_receipt_hash="a" * 64, now=NOW + LEASE)
    assert restarted.processed_count() == 1
    assert restarted.pending_count() == 1


class DynamicModel(fx.FakeAuditedModel):
    def __init__(self, store):
        super().__init__(store, [])

    def complete_audited(self, request):
        self.proposals.append(ModelProposal(
            source_event_id=request.source_event_id, intent="inform",
            reply_chunks=("Qual data deseja consultar?",),
            clarification_question="Qual data deseja consultar?",
            facts=(), read_requests=(), effect_proposals=(),
        ))
        return super().complete_audited(request)


def real_executor(store):
    model = DynamicModel(store)
    executor = fx._executor(
        store=store, model=model, profile=fx.FakeProfile(store),
        public_authority=GeneralAvailabilityPublicAuthorityResolver(store=store, hmac_key=b"a" * 32),
    )
    return executor, model


@pytest.mark.parametrize("recovery", ["release", "expiry"])
def test_commit_before_ack_replays_exact_batch_after_all_stores_restart(tmp_path, recovery):
    inbox_path, boundary_path = tmp_path / "inbox.sqlite3", tmp_path / "boundary.sqlite3"
    inbox = SQLiteInbox(inbox_path)
    inbox.accept(event("committed-first"))
    store = SQLiteBoundaryStore.open_path_v8(boundary_path)
    executor, model = real_executor(store)
    original = claim(inbox)
    try:
        committed = executor.execute(original.batch)
        assert len(model.calls) == 1
        assert not committed.replayed
        # Crash window: the real boundary transaction committed; inbox ACK did not.
        if recovery == "release":
            inbox.release_claim(original)
    finally:
        executor._private_customer_facts.close()
        store.close()
    restarted = SQLiteInbox(inbox_path)
    restarted.accept(event("new-after-commit", at=NOW - timedelta(seconds=1)))
    store = SQLiteBoundaryStore.open_path_v8(boundary_path)
    executor, model = real_executor(store)
    try:
        replay = worker(restarted, executor).run_once(now=NOW + LEASE)
        assert replay.disposition is InboxWorkerDisposition.REPLAYED
        assert replay.batch_id == original.batch_id
        assert replay.turn_receipt_hash == committed.receipt.artifact_hash
        assert len(model.calls) == 0
        assert restarted.processed_count() == 1
        assert restarted.pending_count() == 1
        following = worker(restarted, executor).run_once(now=NOW + LEASE + timedelta(seconds=1))
        assert following.disposition is InboxWorkerDisposition.COMMITTED
        assert following.batch_id != original.batch_id
        assert len(model.calls) == 1
        assert restarted.processed_count() == 2
        assert store._connection.execute("SELECT count(*) FROM boundary_events").fetchone()[0] == 2
    finally:
        executor._private_customer_facts.close()
        store.close()
