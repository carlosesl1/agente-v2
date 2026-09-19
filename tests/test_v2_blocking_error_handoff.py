"""Offline ingress failure -> production recovery -> existing ManyChat handoff."""
from datetime import timedelta
import json
import socket
import sqlite3

import httpx
import pytest

from tests.test_v2_inbox_reliability import event, NOW
from tests.test_v2_production_composition import _settings, REAL_EFFECTS_ACK
from tests.test_v2_rejection_continuity import ScriptedModel, invalid_selection
from tests import test_v2_turn_executor as fx
from v2_application.inbox import SQLiteInbox
from v2_contracts.model import ActionRejectionUnresolved
from v2_host.composition import V2Container, V2Role
from v2_host.settings import RuntimeMode
from v2_host.worker_main import WorkerQueue
import v2_host.production as production


class BrokenExecutor:
    def __init__(self, error=TimeoutError):
        self.error = error
        self.calls = 0

    def execute(self, batch):
        self.calls += 1
        raise self.error("synthetic blocked service")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("external network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    knowledge = tmp_path / "knowledge.yaml"
    knowledge.write_text("entries: []\n")
    settings = _settings(tmp_path, runtime_mode=RuntimeMode.GENERAL_AVAILABILITY,
        allowed_subscriber_ids=(), hermes_model="openai-codex/gpt-5.6-terra",
        candidate_git_sha="a" * 40, candidate_image_digest="sha256:" + "b" * 64,
        manychat_api_key="test-key", hermes_command=("unused-model",),
        hermes_system_prompt="Maya", hermes_transcript_key=b"t" * 32,
        public_authority_hmac_key=b"a" * 32, knowledge_base_path=knowledge,
        manychat_handoff_enabled=True, manychat_handoff_tag_id=301,
        manychat_handoff_flow_ns="flow:test-handoff", real_effects_ack=REAL_EFFECTS_ACK,
        global_kill_switch_engaged=False, write_window_end=None)
    monkeypatch.setattr(production.ReconciliationStage, "_probe_reads",
        lambda self, **kwargs: {"status": "simulated"})
    # All clocks and HTTP receipts stay local; no real delivery occurs.
    monkeypatch.setattr(production.UTCClock, "now", lambda self: NOW + timedelta(seconds=30))
    seen = []
    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, request=request,
            json={"status": "success", "request_id": f"local-{len(seen)}"})
    transport = production.ManyChatHTTPTransport
    monkeypatch.setattr(production, "ManyChatHTTPTransport", lambda **kwargs: transport(
        **kwargs, client=httpx.Client(transport=httpx.MockTransport(handler))))
    opened = []
    def reopen():
        container = V2Container.open(settings=settings, role=V2Role.WORKER)
        opened.append(container)
        workers = production.build_worker_set(container=container, settings=settings)
        workers[WorkerQueue.INBOX]._quiet_window = timedelta(0)
        return container, workers
    yield reopen, seen
    for container in opened:
        container.close()


def fail_turn(container, workers, *, error=TimeoutError, lead="10001", suffix="blocked"):
    executor = BrokenExecutor(error)
    workers[WorkerQueue.INBOX]._executor = executor
    container.inbox.accept(event(suffix, lead=lead))
    attempts = 1 if error is ActionRejectionUnresolved else 3
    for attempt in range(attempts):
        with pytest.raises(error):
            workers[WorkerQueue.INBOX].run_once(now=NOW + timedelta(seconds=6 * attempt))
    return executor


def handoff_count(container):
    return container.followup._connection.execute("SELECT count(*) FROM handoff_outbox").fetchone()[0]


@pytest.mark.parametrize("error", [TimeoutError, ActionRejectionUnresolved])
def test_terminal_turn_reaches_existing_tag_flow_after_restart(runtime, error):
    reopen, seen = runtime
    container, workers = reopen()
    executor = fail_turn(container, workers, error=error)
    assert container.boundary._connection.execute("SELECT count(*) FROM boundary_state").fetchone() == (0,)
    container.close()
    container, workers = reopen()
    result = workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=20))
    assert result["manual_handoff"].created == 1
    assert handoff_count(container) == 1
    assert workers[WorkerQueue.HANDOFF].run_once(now=NOW + timedelta(seconds=21)).disposition.value == "delivered"
    assert seen == [
        ("/fb/subscriber/addTag", {"subscriber_id": "10001", "tag_id": 301}),
        ("/fb/sending/sendFlow", {"subscriber_id": "10001", "flow_ns": "flow:test-handoff"}),
    ]
    workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=31))
    assert workers[WorkerQueue.HANDOFF].run_once(now=NOW + timedelta(seconds=32)).disposition.value == "idle"
    assert len(seen) == 2
    assert executor.calls == (1 if error is ActionRejectionUnresolved else 3)
    assert container.boundary._connection.execute("SELECT count(*) FROM boundary_commands").fetchone() == (0,)
    with sqlite3.connect(container.inbox.path) as db:
        assert db.execute("SELECT status,handoff_id FROM inbound_events").fetchone()[1].startswith("handoff:")


def test_transient_error_does_not_handoff(runtime):
    reopen, seen = runtime
    container, workers = reopen()
    container.inbox.accept(event("temporary"))
    workers[WorkerQueue.INBOX]._executor = BrokenExecutor()
    with pytest.raises(TimeoutError):
        workers[WorkerQueue.INBOX].run_once(now=NOW)
    workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=1))
    assert handoff_count(container) == 0
    assert seen == []


def test_real_invalid_recovery_opens_handoff_without_repeating_maya(runtime):
    reopen, seen = runtime
    container, workers = reopen()
    model = ScriptedModel(container.boundary, [invalid_selection(), invalid_selection()])
    executor = workers[WorkerQueue.INBOX]._executor
    executor._model = model
    executor._profile = fx.FakeProfile(container.boundary)
    container.inbox.accept(event("invalid"))
    with pytest.raises(ActionRejectionUnresolved):
        workers[WorkerQueue.INBOX].run_once(now=NOW)
    workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=3))
    assert handoff_count(container) == 1
    assert workers[WorkerQueue.HANDOFF].run_once(now=NOW + timedelta(seconds=4)).disposition.value == "delivered"
    assert len(model.calls) == 2
    assert len(seen) == 2


def test_admission_then_inbox_link_failure_recovers_one_handoff(runtime, monkeypatch):
    reopen, _ = runtime
    container, workers = reopen()
    fail_turn(container, workers, error=ActionRejectionUnresolved)
    original = SQLiteInbox.record_handoff
    def fail_link(*args, **kwargs):
        raise OSError("synthetic inbox link interruption")
    monkeypatch.setattr(SQLiteInbox, "record_handoff", fail_link)
    with pytest.raises(OSError):
        workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=2))
    assert handoff_count(container) == 1
    monkeypatch.setattr(SQLiteInbox, "record_handoff", original)
    container.close()
    container, workers = reopen()
    workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=4))
    assert handoff_count(container) == 1
    with sqlite3.connect(container.inbox.path) as db:
        assert db.execute("SELECT handoff_id FROM inbound_events").fetchone()[0]


def test_two_failed_batches_same_lead_share_handoff_other_lead_isolated(runtime):
    reopen, seen = runtime
    container, workers = reopen()
    for name, lead in [("first", "10001"), ("second", "10001"), ("other", "10002")]:
        fail_turn(container, workers, error=ActionRejectionUnresolved, lead=lead, suffix=name)
    workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=2))
    assert handoff_count(container) == 2
    for _ in range(2):
        assert workers[WorkerQueue.HANDOFF].run_once(now=NOW + timedelta(seconds=3)).disposition.value == "delivered"
    assert {body["subscriber_id"] for _, body in seen} == {"10001", "10002"}
    assert len(seen) == 4


def test_committed_ack_retry_does_not_open_handoff(runtime, monkeypatch):
    reopen, _ = runtime
    container, workers = reopen()
    model = ScriptedModel(container.boundary, [fx._proposal("Resposta persistida.")])
    executor = workers[WorkerQueue.INBOX]._executor
    executor._model = model
    executor._profile = fx.FakeProfile(container.boundary)
    container.inbox.accept(event("ack"))
    def fail_ack(*args, **kwargs):
        raise OSError("ack unavailable")
    monkeypatch.setattr(SQLiteInbox, "complete_claim", fail_ack)
    for attempt in range(3):
        with pytest.raises(OSError):
            workers[WorkerQueue.INBOX].run_once(now=NOW + timedelta(seconds=6 * attempt))
    workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=20))
    assert handoff_count(container) == 0
    assert len(model.calls) == 1


@pytest.mark.parametrize("close_before_link", [False, True])
def test_admitted_handoff_is_not_reopened_after_operator_close(runtime, monkeypatch, close_before_link):
    from reservation_followup.handoff import HandoffCancelled, HandoffCancellationCode
    reopen, _ = runtime
    container, workers = reopen()
    fail_turn(container, workers, error=ActionRejectionUnresolved)
    original = SQLiteInbox.record_handoff
    if close_before_link:
        def fail_link(*args, **kwargs):
            raise OSError("link interrupted")
        monkeypatch.setattr(SQLiteInbox, "record_handoff", fail_link)
        with pytest.raises(OSError):
            workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=2))
        monkeypatch.setattr(SQLiteInbox, "record_handoff", original)
    else:
        workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=2))
    handoff_id = container.followup._connection.execute("SELECT handoff_id FROM handoff_workflows").fetchone()[0]
    request = container.followup.load_handoff(handoff_id).request
    container.followup.apply_handoff(request.handoff_id, 1, HandoffCancelled(handoff_id=request.handoff_id,
        incident_key=request.incident_key, cancellation_code=HandoffCancellationCode.OPERATOR_CANCELLED,
        cancelled_at=NOW + timedelta(seconds=3)))
    workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=4))
    assert handoff_count(container) == 1
    assert container.followup.load_handoff(handoff_id).status.value == "cancelled"
    assert container.inbox.pending_handoff_batches() == ()


def test_handoff_admission_failure_remains_recoverable(runtime, monkeypatch):
    from v2_application.recovery import HandoffCoordinator
    reopen, _ = runtime
    container, workers = reopen()
    fail_turn(container, workers, error=ActionRejectionUnresolved)
    original = HandoffCoordinator.open_exception_once
    def failed(*args, **kwargs):
        raise OSError("temporary followup store failure")
    monkeypatch.setattr(HandoffCoordinator, "open_exception_once", failed)
    with pytest.raises(OSError):
        workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=2))
    assert handoff_count(container) == 0
    assert len(container.inbox.pending_handoff_batches()) == 1
    monkeypatch.setattr(HandoffCoordinator, "open_exception_once", original)
    container.close()
    container, workers = reopen()
    workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=4))
    assert handoff_count(container) == 1


def test_failed_read_probe_does_not_prevent_handoff(runtime, monkeypatch):
    reopen, _ = runtime
    container, workers = reopen()
    fail_turn(container, workers, error=ActionRejectionUnresolved)
    def failed(*args, **kwargs):
        raise OSError("provider reads unavailable")
    monkeypatch.setattr(production.ReconciliationStage, "_probe_reads", failed)
    with pytest.raises(OSError):
        workers[WorkerQueue.RECONCILIATION].run_once(now=NOW + timedelta(seconds=2))
    assert handoff_count(container) == 1
    assert workers[WorkerQueue.HANDOFF].run_once(now=NOW + timedelta(seconds=3)).disposition.value == "delivered"


def test_previous_schema_migrates_and_does_not_erase_terminal_batch(tmp_path):
    from v2_application.inbox import _SCHEMA
    path = tmp_path / "previous.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_SCHEMA.replace(",\n  handoff_id TEXT", ""))
    inbox = SQLiteInbox(path)
    inbox.accept(event("old-terminal"))
    claim = inbox.claim_ready(now=NOW, quiet_window=timedelta(0), lease_for=timedelta(seconds=30))
    inbox.release_claim(claim, failure_reason="TimeoutError", terminal=True)
    reopened = SQLiteInbox(path)
    assert reopened.pending_handoff_batches() == ((claim.batch_id, claim.lead_id),)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT failure_count,failure_reason,handoff_id FROM inbound_events").fetchone() == (1, "TimeoutError", None)
