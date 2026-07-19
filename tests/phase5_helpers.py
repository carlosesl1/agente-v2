"""Synthetic cross-phase fixtures for Phase 5 durable execution tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from reservation_confirmation import (
    ReferenceConfirmationClassifier,
    SummaryLocale,
    classify_and_bind,
    prepare_summary,
)
from reservation_domain import (
    CustomerFacts,
    DraftRequested,
    EconomicTerms,
    Event,
    ExecutionCertainty,
    ExecutionOutcome,
    LookupRecorded,
    OfferChosen,
    Party,
    ReadyToSummarizeState,
    SearchQuery,
    ServiceKind,
    StartSearch,
    State,
    dumps_command,
    new_workflow,
    reduce,
)
from reservation_execution import DispatchRequest, OutboxMessage, PreparationFailure
from reservation_execution.projection import summary_outbox_message
from reservation_execution.sqlite_store import PersistedTransition, SQLiteUnitOfWork
from reservation_lookup import (
    BokunLookupRequest,
    CloudbedsLookupRequest,
    ReadResponse,
)
from reservation_lookup.bokun import BokunReadAdapter
from reservation_lookup.cloudbeds import CloudbedsReadAdapter

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
_FIXTURES = Path(__file__).parent / "fixtures" / "phase3"


def _opaque_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


class FinalFixtureTransport:
    """Final local transport: finite scripted responses and no network fallback."""

    def __init__(self, responses: tuple[ReadResponse, ...]):
        self._responses = list(responses)
        self.requests: list[object] = []

    def send(self, request):
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("unexpected synthetic fixture request")
        return self._responses.pop(0)

    def assert_exhausted(self) -> None:
        if self._responses:
            raise AssertionError("synthetic fixture responses were not consumed")


def _fixture(provider: str, name: str):
    return json.loads(
        (_FIXTURES / provider / name).read_text(encoding="utf-8")
    )


def _lookup(provider: str):
    if provider == "cloudbeds":
        query = SearchQuery(
            service=ServiceKind.LODGING,
            start_date=date(2026, 11, 10),
            end_date=date(2026, 11, 12),
            start_time=None,
            party=Party(adults=2, children=1),
        )
        transport = FinalFixtureTransport(
            (
                ReadResponse(200, _fixture(provider, "available-room-types.json")),
                ReadResponse(200, _fixture(provider, "rate-plans.json")),
            )
        )
        result = CloudbedsReadAdapter(transport).lookup(
            CloudbedsLookupRequest(property_id="property.42", query=query),
            observed_at=T0,
            ttl=timedelta(minutes=5),
        )
    elif provider == "bokun":
        query = SearchQuery(
            service=ServiceKind.ACTIVITY,
            start_date=date(2026, 11, 11),
            end_date=None,
            start_time=None,
            party=Party(adults=2, children=0),
        )
        transport = FinalFixtureTransport(
            (
                ReadResponse(200, _fixture(provider, "activity.json")),
                ReadResponse(200, _fixture(provider, "availabilities.json")),
            )
        )
        result = BokunReadAdapter(transport).lookup(
            BokunLookupRequest(product_id="913776", query=query),
            observed_at=T0,
            ttl=timedelta(minutes=5),
        )
    else:
        raise ValueError(f"unsupported synthetic provider: {provider}")
    transport.assert_exhausted()
    if not result.offers:
        raise AssertionError("positive synthetic lookup must return an offer")
    return result


def _summary_outbox(workflow_id: str, prepared) -> OutboxMessage:
    return summary_outbox_message(workflow_id=workflow_id, prepared=prepared)


def workflow_events(
    provider: str,
    *,
    workflow_id: str,
) -> tuple[State, tuple[tuple[Event, tuple[OutboxMessage, ...]], ...]]:
    """Return revision-0 state and complete events through accepted confirmation."""

    lookup = _lookup(provider)
    initial = new_workflow(
        workflow_id=workflow_id,
        started_at=T0 - timedelta(seconds=1),
    )
    events: list[tuple[Event, tuple[OutboxMessage, ...]]] = []
    state: State = initial

    fixed_events: tuple[Event, ...] = (
        StartSearch(
            event_id=_opaque_id("event", "phase5", provider, "search", workflow_id),
            occurred_at=T0,
            query=lookup.query,
        ),
        LookupRecorded(
            event_id=_opaque_id("event", "phase5", provider, "lookup", workflow_id),
            occurred_at=T0 + timedelta(seconds=1),
            evidence=lookup.evidence,
            offers=lookup.offers,
        ),
        OfferChosen(
            event_id=_opaque_id("event", "phase5", provider, "choice", workflow_id),
            occurred_at=T0 + timedelta(seconds=2),
            offer_id=lookup.offers[0].offer_id,
        ),
        DraftRequested(
            event_id=_opaque_id("event", "phase5", provider, "draft", workflow_id),
            occurred_at=T0 + timedelta(seconds=3),
            draft_id=_opaque_id("draft", "phase5", provider, workflow_id),
            customer=CustomerFacts(
                customer_ref=_opaque_id("customer", "phase5", provider, workflow_id),
                full_name="Synthetic Store Person",
                email=(
                    f"synthetic.store.{provider}."
                    + hashlib.sha256(workflow_id.encode("utf-8")).hexdigest()[:8]
                    + chr(64)
                    + "example.invalid"
                ),
                phone_e164="+999" + "0" * 8,
                country_code="ZZ",
            ),
            terms=EconomicTerms(payment_method="card"),
        ),
    )
    for event in fixed_events:
        transition = reduce(state, event)
        if transition.commands:
            raise AssertionError("pre-summary fixture emitted a command")
        state = transition.state
        events.append((event, ()))

    if not isinstance(state, ReadyToSummarizeState):
        raise AssertionError("fixture did not reach ready_to_summarize")
    locale = SummaryLocale.PT_BR if provider == "cloudbeds" else SummaryLocale.EN
    prepared = prepare_summary(
        state,
        locale=locale,
        presented_at=T0 + timedelta(seconds=4),
    )
    summary_outbox = _summary_outbox(workflow_id, prepared)
    summary_transition = reduce(state, prepared.event)
    state = summary_transition.state
    events.append((prepared.event, (summary_outbox,)))

    bound = classify_and_bind(
        state,
        source_event_id=_opaque_id("source", "phase5", provider, workflow_id),
        received_at=T0 + timedelta(seconds=5),
        text="Pode fazer." if provider == "cloudbeds" else "Go ahead.",
        locale=locale,
        content_hash=prepared.rendered.content_hash,
        classifier=ReferenceConfirmationClassifier(),
    )
    if bound.event is None:
        raise AssertionError("synthetic acceptance did not bind to an event")
    events.append((bound.event, ()))
    return initial, tuple(events)


def persist_script(
    store: SQLiteUnitOfWork,
    workflow_id: str,
    script: tuple[tuple[Event, tuple[OutboxMessage, ...]], ...],
) -> tuple[PersistedTransition, ...]:
    results = []
    for event, outbox in script:
        state = store.load_workflow(workflow_id)
        results.append(
            store.apply_event(
                workflow_id,
                state.meta.revision,
                event,
                outbox=outbox,
            )
        )
    return tuple(results)


def database_counts(path: Path) -> tuple[int, int, int, int, int]:
    connection = sqlite3.connect(path)
    try:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "workflows",
                "domain_events",
                "reservation_commands",
                "execution_ledger",
                "outbox_messages",
            )
        )
    finally:
        connection.close()


def claim_fixture(test_case):
    """Persist one synthetic authorized command and expose a fixed-TTL claimer."""

    temporary = tempfile.TemporaryDirectory(prefix="phase5-claim-")
    test_case.addCleanup(temporary.cleanup)
    path = Path(temporary.name) / "phase5.db"
    store = SQLiteUnitOfWork.open(path)
    test_case.addCleanup(store.close)
    workflow_id = _opaque_id("workflow", "claim", test_case.id())
    initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
    store.create_workflow(initial)
    persist_script(store, workflow_id, script)

    def claim_at(now: datetime, worker: str = "worker:one"):
        return store.claim_command(
            worker_id=worker,
            now=now,
            lease_ttl=timedelta(seconds=30),
        )

    return store, claim_at


def queued_store_fixture(test_case):
    """Persist one queued authorized command in a file-backed local store."""

    temporary = tempfile.TemporaryDirectory(prefix="phase5-reconcile-")
    test_case.addCleanup(temporary.cleanup)
    path = Path(temporary.name) / "phase5.db"
    store = SQLiteUnitOfWork.open(path)
    test_case.addCleanup(store.close)
    workflow_id = _opaque_id("workflow", "reconcile", test_case.id())
    initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
    store.create_workflow(initial)
    persist_script(store, workflow_id, script)
    command_id = store.load_workflow(workflow_id).command.command_id
    return store, path, workflow_id, command_id


def fenced_store_fixture(test_case, now: datetime):
    """Persist one dispatch fence without invoking any execution adapter."""

    store, path, workflow_id, command_id = queued_store_fixture(test_case)
    claim = store.claim_command(
        worker_id="worker:crashed",
        now=now,
        lease_ttl=timedelta(seconds=30),
    )
    request = DispatchRequest.from_command(claim.command, dumps_command(claim.command))
    store.fence_dispatch(claim, request, now=now)
    return store, path, workflow_id, command_id


class ScriptedExecutionAdapter:
    """Finite execution adapter fake with no network or provider fallback."""

    adapter_id = "scripted-execution"
    adapter_version = 1

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.prepare_calls = 0
        self.dispatch_calls = 0
        self._command = None

    def prepare(self, command):
        self.prepare_calls += 1
        self._command = command
        if self.outcomes and type(self.outcomes[0]) is PreparationFailure:
            raise self.outcomes.pop(0)
        payload = dumps_command(command)
        return DispatchRequest.from_command(command, payload)

    def dispatch(self, request, *, idempotency_key):
        self.dispatch_calls += 1
        if not self.outcomes:
            raise AssertionError("unexpected synthetic dispatch")
        action = self.outcomes.pop(0)
        if isinstance(action, BaseException):
            raise action
        if type(action) is ExecutionOutcome:
            return action
        if type(action) is not ExecutionCertainty or self._command is None:
            raise AssertionError("scripted action must be an outcome or certainty")
        provider_reference = (
            "provider:synthetic"
            if action is not ExecutionCertainty.NOT_CALLED
            else None
        )
        status = {
            ExecutionCertainty.EFFECT_CONFIRMED: "synthetic_effect_confirmed",
            ExecutionCertainty.CALLED_NO_EFFECT: "synthetic_no_effect",
            ExecutionCertainty.CALLED_UNKNOWN: "synthetic_unknown",
            ExecutionCertainty.NOT_CALLED: "synthetic_not_called",
        }[action]
        return self._command.outcome(
            certainty=action,
            normalized_status=status,
            provider_reference=provider_reference,
            evidence=(request.payload_hash,),
        )


def worker_fixture(test_case, action):
    """Persist one queued command and build a local one-shot worker fixture."""

    from reservation_execution.worker import CommandWorker

    temporary = tempfile.TemporaryDirectory(prefix="phase5-worker-")
    test_case.addCleanup(temporary.cleanup)
    path = Path(temporary.name) / "phase5.db"
    store = SQLiteUnitOfWork.open(path)
    test_case.addCleanup(store.close)
    workflow_id = _opaque_id("workflow", "worker", test_case.id())
    initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
    store.create_workflow(initial)
    persist_script(store, workflow_id, script)
    command_id = store.load_workflow(workflow_id).command.command_id
    adapter = ScriptedExecutionAdapter([action])
    worker = CommandWorker(
        store=store,
        adapter=adapter,
        worker_id="worker:scripted",
        lease_ttl=timedelta(seconds=30),
    )
    return store, worker, adapter, workflow_id, command_id
