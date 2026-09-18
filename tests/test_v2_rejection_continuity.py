"""Rejected actions: real inbox/executor/stores, scripted model, denied network."""
from dataclasses import replace
from datetime import date, timedelta
import json
import socket
import sqlite3

import pytest

from tests import test_v2_turn_executor as fx
from tests.test_v2_inbox_reliability import event, worker
from v2_adapters.hermes_model import _request_wire
from v2_application.inbox import SQLiteInbox
from v2_application.inbox_worker import InboxWorkerDisposition
from v2_application.reads import V2ReadService
from v2_application.turn_executor import _reply_from_receipt
from v2_contracts.critical_actions import ApprovalBasis, CriticalActionKind
from v2_contracts.model import ModelFact
from v2_contracts.providers import ReadKind, ReadRequest
from v2_host.public_authority import GeneralAvailabilityPublicAuthorityResolver


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


class ScriptedModel(fx.FakeAuditedModel):
    def __init__(self, store, steps):
        super().__init__(store, [])
        self.steps = list(steps)

    def complete_audited(self, request):
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            self.calls.append(request)
            raise step
        self.proposals.append(replace(step, source_event_id=request.source_event_id))
        return super().complete_audited(request)


def invalid_selection():
    return replace(fx._proposal("Vou preparar essa opção."), intent="select",
                   target_offer_id="offer:" + "7" * 64,
                   facts=(ModelFact("service", "hostel"),))


@pytest.fixture
def setup(tmp_path):
    store = fx.SQLiteBoundaryStore.open_memory_v8()
    inbox = SQLiteInbox(tmp_path / "inbox.sqlite3")
    created = []

    def build(steps, reads=None):
        model = ScriptedModel(store, steps)
        executor = fx._executor(store=store, model=model, profile=fx.FakeProfile(store),
            reads=reads, public_authority=GeneralAvailabilityPublicAuthorityResolver(
                store=store, hmac_key=b"a" * 32))
        created.append(executor)
        return model, executor

    yield inbox, store, build
    for executor in created:
        executor._private_customer_facts.close()
    store.close()


def statuses(inbox):
    with sqlite3.connect(inbox.path) as connection:
        return connection.execute("SELECT event_id,status FROM inbound_events ORDER BY event_id").fetchall()


def assert_no_effect(store):
    for table in ("boundary_commands", "boundary_command_relays"):
        assert store._connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_rejection_returns_to_maya_commits_exact_reply_and_releases_next_message(setup):
    inbox, store, build = setup
    recovery = fx._proposal("Quais são as datas da hospedagem?")
    model, executor = build([invalid_selection(), recovery, fx._proposal("Obrigada pelas datas.")])
    inbox.accept(event("first"))
    result = worker(inbox, executor).run_once(now=fx.NOW)
    assert result.disposition is InboxWorkerDisposition.COMMITTED
    assert len(model.calls) == 2
    initial, returned = model.calls
    assert returned.message == initial.message
    assert returned.source_event_id == initial.source_event_id
    assert returned.action_rejection == "selection requires one uniquely bound read"
    assert {f.name: f.value for f in returned.state_facts}["service"] == "hostel"
    wire = json.loads(_request_wire(returned, "Maya"))
    payload = json.loads(wire["messages"][-1][1])
    assert payload["action_rejection"] == returned.action_rejection
    receipt = store.load_turn_receipt(result.batch_id)
    assert _reply_from_receipt(receipt) == recovery.reply_chunks
    assert_no_effect(store)
    assert store._connection.execute("SELECT count(*) FROM boundary_public_outbox").fetchone() == (1,)
    # A duplicate is replayed without another model call or second outbox row.
    from v2_contracts.channel import InboundBatch
    original = event("first")
    replay = executor.execute(InboundBatch(result.batch_id, original.lead_id,
        original.subscriber_id, (original,), original.text))
    assert replay.replayed and replay.reply_chunks == recovery.reply_chunks
    assert len(model.calls) == 2
    inbox.accept(event("next", at=fx.NOW))
    worker(inbox, executor).run_once(now=fx.NOW + timedelta(seconds=1))
    assert statuses(inbox) == [("first", "processed"), ("next", "processed")]
    assert model.calls[-1].recent_dialogue[-1].assistant_reply_chunks == recovery.reply_chunks
    assert {f.name: f.value for f in model.calls[-1].state_facts}["service"] == "hostel"
    assert_no_effect(store)


def test_post_read_rejection_retains_observations_and_does_not_repeat_read(setup):
    inbox, store, build = setup
    read = ReadRequest("read:rejected", ReadKind.LODGING,
        check_in=date(2026, 8, 10), check_out=date(2026, 8, 12), adults=2, children=0)
    first = replace(fx._proposal("Vou consultar."), read_requests=(read,),
        facts=(ModelFact("service", "hostel"), ModelFact("adults", 2)))
    port = fx.FakeLodgingReadPort(store)
    model, executor = build([first, invalid_selection(), fx._proposal("Essa opção não foi vinculada.")],
        V2ReadService({ReadKind.LODGING: port}))
    inbox.accept(event("post-read"))
    worker(inbox, executor).run_once(now=fx.NOW)
    assert len(model.calls) == 3 and len(port.calls) == 1
    assert model.calls[-1].observations == model.calls[-2].observations
    assert model.calls[-1].observations
    assert model.calls[-1].action_rejection
    assert_no_effect(store)


@pytest.mark.parametrize("bad", ["selection", "read", "facts"])
def test_invalid_recovery_is_terminal_without_effect_or_endless_retry(setup, bad):
    inbox, store, build = setup
    recovery = invalid_selection() if bad == "selection" else fx._proposal("Tentativa inválida.")
    if bad == "read":
        recovery = replace(recovery, read_requests=(ReadRequest("read:denied", ReadKind.LODGING,
            check_in=date(2026, 8, 10), check_out=date(2026, 8, 12), adults=2, children=0),))
    if bad == "facts":
        recovery = replace(recovery, facts=(ModelFact("adults", 9),))
    model, executor = build([invalid_selection(), recovery, fx._proposal("Nova mensagem.")])
    inbox.accept(event("first"))
    with pytest.raises(Exception):
        worker(inbox, executor).run_once(now=fx.NOW)
    assert len(model.calls) == 2
    assert statuses(inbox) == [("first", "manual_review")]
    assert_no_effect(store)
    assert store._connection.execute("SELECT count(*) FROM boundary_public_outbox").fetchone() == (0,)
    inbox.accept(event("next", at=fx.NOW))
    worker(SQLiteInbox(inbox.path), executor).run_once(now=fx.NOW + timedelta(seconds=6))
    assert statuses(inbox)[-1] == ("next", "processed")




def test_recovery_question_survives_real_adapter(setup):
    from types import SimpleNamespace
    from v2_adapters.hermes_model import HermesModelAdapter
    inbox, store, build = setup
    question = "Quais são as datas?"
    wire_calls = []
    def run(command, **kwargs):
        wire = json.loads(kwargs["input"])
        wire_calls.append(wire)
        payload = json.loads(wire["messages"][-1][1])
        recovering = payload.get("action_rejection") is not None
        output = {
            "intent": "inform", "reply_chunks": [{"text": question, "expects_reply": True}],
            "facts": [], "read_requests": [], "selected_choice_refs": [],
            "selection_requested": not recovering, "pending_action_disposition": None,
            "passengers": [],
        }
        if not recovering:
            output["intent"] = "select"
            output["facts"] = [{"name": "service", "value": "hostel"}]
        return SimpleNamespace(returncode=0, stdout=b"PHASE8_RESULT\0" + json.dumps(output).encode(), stderr=b"")
    adapter = HermesModelAdapter(command=("offline",), system_prompt="Maya", timeout=10,
        transcript_key=b"k" * 32, run=run, environ={})
    initial_model, executor = build([invalid_selection()])
    def complete(request):
        if request.action_rejection is None:
            return initial_model.complete_audited(request)
        return adapter.complete_audited(request)
    executor._model = SimpleNamespace(complete_audited=complete)
    inbox.accept(event("question"))
    result = worker(inbox, executor).run_once(now=fx.NOW)
    assert len(wire_calls) == 1
    assert len(initial_model.calls) == 1
    assert _reply_from_receipt(store.load_turn_receipt(result.batch_id)) == (question,)
    assert_no_effect(store)


@pytest.mark.parametrize("failure", ["confirm", "extra_read"])
def test_post_read_authority_rejection_is_explained_without_repeating_tools(setup, failure):
    inbox, store, build = setup
    read = ReadRequest("read:bounded", ReadKind.LODGING,
        check_in=date(2026, 8, 10), check_out=date(2026, 8, 12), adults=2, children=0)
    first = replace(fx._proposal("Vou consultar."), read_requests=(read,))
    invalid = (replace(fx._proposal("Confirmado."), intent="confirm", confirmed_summary_version=1,
                       confirmed_action_kinds=(CriticalActionKind.RESERVE_LODGING,),
                       approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE) if failure == "confirm"
               else replace(fx._proposal("Nova consulta."), read_requests=(read,)))
    port = fx.FakeLodgingReadPort(store)
    model, executor = build([first, invalid, fx._proposal("Não foi possível concluir essa ação.")],
        V2ReadService({ReadKind.LODGING: port}))
    inbox.accept(event("authority"))
    worker(inbox, executor).run_once(now=fx.NOW)
    assert len(model.calls) == 3 and len(port.calls) == 1
    assert model.calls[-1].action_rejection
    assert_no_effect(store)


def test_legacy_inbox_migration_and_stale_release_preserve_budget(tmp_path):
    # Simulate a pre-change schema, then let the real owner migrate it.
    from v2_application.inbox import _SCHEMA
    path = tmp_path / "legacy.sqlite3"
    old = _SCHEMA.replace(",\n  failure_count INTEGER NOT NULL DEFAULT 0,\n  failure_reason TEXT", "")
    with sqlite3.connect(path) as connection:
        connection.executescript(old)
    inbox = SQLiteInbox(path)
    inbox.accept(event("old"))
    claim = inbox.claim_ready(now=fx.NOW, quiet_window=timedelta(0), lease_for=timedelta(seconds=1))
    reopened = SQLiteInbox(path)
    newer = reopened.claim_ready(now=fx.NOW + timedelta(seconds=2),
        quiet_window=timedelta(0), lease_for=timedelta(seconds=30))
    with pytest.raises(RuntimeError, match="stale"):
        inbox.release_claim(claim, failure_reason="TimeoutError")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT failure_count FROM inbound_events").fetchone() == (0,)
    reopened.release_claim(newer, failure_reason="TimeoutError")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT failure_count,failure_reason FROM inbound_events").fetchone() == (1, "TimeoutError")




def test_commanded_workflow_rejection_does_not_repeat_existing_effect():
    store, model, port, confirm_batch, executor = fx._approval_expiry_fixture(
        approval_ttl=timedelta(minutes=30), confirmation_clock=fx.SequenceClock())
    try:
        executor.execute(confirm_batch)
        before = store.load_state(confirm_batch.lead_id).state.workflow
        counts = tuple(store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            for table in ("boundary_commands", "boundary_command_relays"))
        request = replace(fx.BATCH, batch_id="batch:active-rejection",
            events=(replace(fx.EVENT, event_id="event:active-rejection"),))
        invalid = replace(invalid_selection(), source_event_id=request.batch_id)
        reply = replace(fx._proposal("A operação anterior permanece em acompanhamento."),
            source_event_id=request.batch_id)
        model.proposals[:] = [invalid, reply]
        authority = replace(fx.AUTHORITY, authorization_id="auth:active-rejection",
            allocation_ids=("allocation:active-rejection",), allocation_manifest_hash="9" * 64)
        fx._install_public_authority(store, authority)
        executor._public_authority.values[request.batch_id] = authority
        previous_calls = len(model.calls)
        result = executor.execute(request)
        assert result.reply_chunks == reply.reply_chunks
        assert len(model.calls) == previous_calls + 2
        assert model.calls[-1].action_rejection
        assert store.load_state(request.lead_id).state.workflow == before
        assert tuple(store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            for table in ("boundary_commands", "boundary_command_relays")) == counts
    finally:
        executor._private_customer_facts.close()
        store.close()




def test_committed_reply_survives_three_ack_failures_without_exhausting_budget(setup, monkeypatch):
    inbox, store, build = setup
    model, executor = build([fx._proposal("Resposta já persistida.")])
    inbox.accept(event("ack"))
    complete = SQLiteInbox.complete_claim
    def unavailable(*args, **kwargs):
        raise OSError("synthetic ack failure")
    monkeypatch.setattr(SQLiteInbox, "complete_claim", unavailable)
    for attempt in range(3):
        with pytest.raises(OSError):
            worker(SQLiteInbox(inbox.path), executor).run_once(now=fx.NOW + timedelta(seconds=6 * attempt))
    assert len(model.calls) == 1
    assert statuses(inbox) == [("ack", "pending")]
    assert store._connection.execute("SELECT count(*) FROM boundary_public_outbox").fetchone() == (1,)
    with sqlite3.connect(inbox.path) as connection:
        assert connection.execute("SELECT failure_count FROM inbound_events").fetchone() == (0,)
    monkeypatch.setattr(SQLiteInbox, "complete_claim", complete)
    assert worker(SQLiteInbox(inbox.path), executor).run_once(now=fx.NOW + timedelta(seconds=20)).disposition is InboxWorkerDisposition.REPLAYED
    assert statuses(inbox) == [("ack", "processed")]
    assert_no_effect(store)


def test_transient_model_failure_recovers_before_budget_expires(setup):
    inbox, store, build = setup
    model, executor = build([TimeoutError("offline"), invalid_selection(), fx._proposal("Quais datas?")])
    inbox.accept(event("temporary"))
    with pytest.raises(TimeoutError):
        worker(inbox, executor).run_once(now=fx.NOW)
    result = worker(SQLiteInbox(inbox.path), executor).run_once(now=fx.NOW + timedelta(seconds=6))
    assert result.disposition is InboxWorkerDisposition.COMMITTED
    assert len(model.calls) == 3
    assert statuses(inbox) == [("temporary", "processed")]
    assert_no_effect(store)




def test_rejection_keeps_collected_people_and_holder_facts(setup):
    inbox, store, build = setup
    people = (fx.PassengerInput(1, "adult", "Pessoa Um", date(1990, 1, 2), "f", "BR", is_holder=False),
              fx.PassengerInput(2, "adult", "Pessoa Dois", date(1992, 3, 4), "m", "BR", is_holder=False))
    collected = replace(fx._proposal("Dados registrados."), facts=(
        ModelFact("language", "pt-BR"), ModelFact("service", "agency"),
        ModelFact("product_id", "product:buracao"), ModelFact("activity_date", date(2026, 8, 12)),
        ModelFact("adults", 2), ModelFact("children", 0),
    ), passengers=people)
    model, executor = build([collected, invalid_selection(), fx._proposal("Quais as datas da hospedagem?")])
    inbox.accept(event("people"))
    worker(inbox, executor).run_once(now=fx.NOW)
    inbox.accept(event("lodging"))
    worker(inbox, executor).run_once(now=fx.NOW)
    request = model.calls[-1]
    assert request.action_rejection
    assert request.passengers == people
    values = {fact.name: fact.value for fact in request.state_facts}
    profile = fx.FakeProfile(store).read(event("people").lead_id, now=fx.NOW)
    assert values["full_name"] == profile.full_name
    assert values["email"] == profile.email
    model.steps.append(fx._proposal("Continuo com os dados registrados."))
    inbox.accept(event("following"))
    worker(inbox, executor).run_once(now=fx.NOW)
    assert model.calls[-1].passengers == people
    assert_no_effect(store)


def test_technical_failure_budget_is_durable_and_frees_following_input(setup):
    inbox, store, build = setup
    model, executor = build([TimeoutError("offline") for _ in range(3)] + [fx._proposal("Olá de novo.")])
    inbox.accept(event("first"))
    for attempt in range(3):
        with pytest.raises(TimeoutError):
            worker(SQLiteInbox(inbox.path), executor).run_once(now=fx.NOW + timedelta(seconds=6 * attempt))
    assert statuses(inbox) == [("first", "manual_review")]
    with sqlite3.connect(inbox.path) as connection:
        assert connection.execute("SELECT failure_count,failure_reason FROM inbound_events").fetchone() == (3, "TimeoutError")
    inbox.accept(event("next", at=fx.NOW))
    worker(SQLiteInbox(inbox.path), executor).run_once(now=fx.NOW + timedelta(seconds=20))
    assert len(model.calls) == 4
    assert statuses(inbox)[-1] == ("next", "processed")
    assert_no_effect(store)
