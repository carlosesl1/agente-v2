"""Deterministic Phase 7 fault, restart, and contention harness."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import tempfile
from threading import Barrier
from typing import Final

from reservation_domain import (
    ConfirmationDecisionKind,
    ConfirmationReceived,
    CustomerFacts,
    DraftRequested,
    EconomicTerms,
    LookupEvidence,
    LookupRecorded,
    LookupStatus,
    Money,
    OfferChosen,
    OfferSnapshot,
    Party,
    SearchQuery,
    ServiceKind,
    StartSearch,
    SummaryRecorded,
    new_workflow,
    reduce,
)
from reservation_execution import OutboxMessage
from reservation_execution.types import OutboxKind
from reservation_boundary.legacy_state import import_legacy_state
from reservation_boundary.properties import assert_frozen_candidate, synthetic_collecting_snapshot
from reservation_boundary.sqlite_store import (
    ConcurrencyConflict,
    DataCorruption,
    IdentityConflict,
    SQLiteBoundaryStore,
)
from reservation_boundary.types import (
    BoundaryCommit,
    BoundaryState,
    ImportDisposition,
    ImportReason,
    ImportResult,
    LegacyLeadSnapshot,
)


RESTART_SCHEDULES: Final = 2_000
CONTENTION_DOMAINS: Final = ("genesis", "event", "command", "outbox")
CONTENTION_ROUNDS_PER_DOMAIN: Final = 50
MUTANT_COUNT: Final = 12
T0: Final = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class FaultRow:
    name: str
    passed: bool
    detail_hash: str

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise TypeError("fault name must be exact nonempty text")
        if type(self.passed) is not bool:
            raise TypeError("fault passed must be an exact bool")
        if type(self.detail_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", self.detail_hash) is None:
            raise ValueError("detail_hash must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class ContentionRow:
    domain: str
    round_index: int
    contenders: int
    winners: int
    conflicts: int
    state_rows: int
    event_rows: int
    command_rows: int
    outbox_rows: int
    passed: bool
    detail_hash: str

    def __post_init__(self) -> None:
        if self.domain not in CONTENTION_DOMAINS:
            raise ValueError("contention domain is outside the closed catalog")
        for name in (
            "round_index",
            "contenders",
            "winners",
            "conflicts",
            "state_rows",
            "event_rows",
            "command_rows",
            "outbox_rows",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise TypeError(f"{name} must be an exact nonnegative integer")
        if self.contenders < 2 or self.winners + self.conflicts != self.contenders:
            raise ValueError("contention outcomes must reconstruct all contenders")
        if type(self.passed) is not bool:
            raise TypeError("contention passed must be an exact bool")
        if type(self.detail_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", self.detail_hash) is None:
            raise ValueError("contention detail_hash must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class FaultReport:
    faults: tuple[FaultRow, ...]
    passed: bool
    restart_schedules: int
    restarts_passed: bool
    contention_rows: int
    contention_details: tuple[ContentionRow, ...] = ()

    def __post_init__(self) -> None:
        if type(self.faults) is not tuple or any(type(row) is not FaultRow for row in self.faults):
            raise TypeError("faults must contain exact FaultRow values")
        if len({row.name for row in self.faults}) != len(self.faults):
            raise ValueError("fault names must be unique")
        if type(self.restart_schedules) is not int or self.restart_schedules < 1:
            raise TypeError("restart_schedules must be an exact positive integer")
        if type(self.restarts_passed) is not bool:
            raise TypeError("restarts_passed must be an exact bool")
        if (
            type(self.contention_rows) is not int
            or self.contention_rows < len(CONTENTION_DOMAINS)
            or self.contention_rows % len(CONTENTION_DOMAINS) != 0
        ):
            raise ValueError("contention_rows must reconstruct all four domains")
        if (
            type(self.contention_details) is not tuple
            or any(type(row) is not ContentionRow for row in self.contention_details)
            or len(self.contention_details) != self.contention_rows
        ):
            raise ValueError("contention rows must reconstruct from exact details")
        expected_passed = (
            all(row.passed for row in self.faults)
            and self.restarts_passed
            and all(row.passed for row in self.contention_details)
        )
        if type(self.passed) is not bool or self.passed != expected_passed:
            raise ValueError("fault report passed must derive from rows/restarts")

    def to_dict(self) -> dict[str, object]:
        return {
            "contention_domains": list(CONTENTION_DOMAINS),
            "contention_details": [
                {
                    "command_rows": row.command_rows,
                    "conflicts": row.conflicts,
                    "contenders": row.contenders,
                    "detail_hash": row.detail_hash,
                    "domain": row.domain,
                    "event_rows": row.event_rows,
                    "outbox_rows": row.outbox_rows,
                    "passed": row.passed,
                    "round_index": row.round_index,
                    "state_rows": row.state_rows,
                    "winners": row.winners,
                }
                for row in self.contention_details
            ],
            "contention_rows": self.contention_rows,
            "faults": [
                {
                    "detail_hash": row.detail_hash,
                    "name": row.name,
                    "passed": row.passed,
                }
                for row in self.faults
            ],
            "passed": self.passed,
            "restart_schedules": self.restart_schedules,
            "restarts_passed": self.restarts_passed,
        }


def _detail(name: str, passed: bool) -> str:
    return hashlib.sha256(f"phase7:{name}:{int(passed)}".encode()).hexdigest()


def _fixture(store: SQLiteBoundaryStore, index: int = 0):
    source = synthetic_collecting_snapshot(index)
    result = import_legacy_state(source)
    if result.disposition is not ImportDisposition.MIGRATED:
        raise AssertionError("synthetic fixture did not migrate")
    persisted = store.import_genesis(source, result, claimed_at=T0)
    return source, persisted


def _commit_fixture(store: SQLiteBoundaryStore, source, current, token: int, *, hook=None):
    event_id = "event-fault-001"
    state = replace(current.state, version=1, processed_event_ids=(event_id,))
    return store.commit(
        event_id=event_id,
        event_hash="a" * 64,
        expected_version=0,
        fencing_token=token,
        commit=BoundaryCommit(state, (), (), ()),
        committed_at=T0,
        fault_hook=hook,
    )


def _rollback_fault(stage: str) -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        current, token = store.acquire_fence(source.raw_fields["lead_key"])

        def hook(actual: str) -> None:
            if actual == stage:
                raise RuntimeError(stage)

        raised = False
        try:
            _commit_fixture(store, source, current, token, hook=hook)
        except RuntimeError as exc:
            raised = str(exc) == stage
        loaded = store.load_state(source.raw_fields["lead_key"])
        event_count = store._connection.execute(
            "SELECT count(*) FROM boundary_events"
        ).fetchone()[0]
        passed = raised and loaded.version == 0 and event_count == 0
        return FaultRow(stage, passed, _detail(stage, passed))
    finally:
        store.close()


def _stale_fence_fault() -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        current, first = store.acquire_fence(source.raw_fields["lead_key"])
        store.acquire_fence(source.raw_fields["lead_key"])
        raised = False
        try:
            _commit_fixture(store, source, current, first)
        except ConcurrencyConflict:
            raised = True
        passed = raised and store.event_hash(source.raw_fields["lead_key"], "event-fault-001") is None
        return FaultRow("stale_fence", passed, _detail("stale_fence", passed))
    finally:
        store.close()


def _event_conflict_fault() -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        current, token = store.acquire_fence(source.raw_fields["lead_key"])
        persisted = _commit_fixture(store, source, current, token)
        raised = False
        try:
            store.commit(
                event_id="event-fault-001",
                event_hash="b" * 64,
                expected_version=1,
                fencing_token=token,
                commit=BoundaryCommit(persisted.state, (), (), ()),
                committed_at=T0,
            )
        except IdentityConflict:
            raised = True
        passed = raised and store.load_state(source.raw_fields["lead_key"]).version == 1
        return FaultRow("event_hash_conflict", passed, _detail("event_hash_conflict", passed))
    finally:
        store.close()


def _genesis_conflict_fault() -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        divergent = synthetic_collecting_snapshot(1)
        raw = dict(divergent.raw_fields)
        raw["lead_key"] = source.raw_fields["lead_key"]
        raw["metadata"] = dict(source.raw_fields["metadata"])
        import json
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        from reservation_boundary.types import LegacyLeadSnapshot
        candidate = LegacyLeadSnapshot(
            1,
            "chapada-leads-hermes",
            raw,
            canonical,
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
        result = import_legacy_state(candidate)
        raised = False
        try:
            store.import_genesis(candidate, result, claimed_at=T0)
        except IdentityConflict:
            raised = True
        return FaultRow("genesis_conflict", raised, _detail("genesis_conflict", raised))
    finally:
        store.close()


def _state_hash_fault() -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        store._connection.execute(
            "UPDATE boundary_state SET state_hash=? WHERE lead_key=?",
            ("f" * 64, source.raw_fields["lead_key"]),
        )
        raised = False
        try:
            store.load_state(source.raw_fields["lead_key"])
        except DataCorruption:
            raised = True
        return FaultRow("state_hash_tamper", raised, _detail("state_hash_tamper", raised))
    finally:
        store.close()


def _run_restarts(schedules: int) -> bool:
    with tempfile.TemporaryDirectory(prefix="phase7-restarts-") as directory:
        path = Path(directory) / "boundary.db"
        store = SQLiteBoundaryStore.open_path(path)
        source, expected = _fixture(store)
        lead_key = source.raw_fields["lead_key"]
        store.close()
        for _ in range(schedules):
            reopened = SQLiteBoundaryStore.open_path(path)
            try:
                if reopened.load_state(lead_key) != expected:
                    return False
            finally:
                reopened.close()
    return True


def _queued_state_and_command(index: int, lead_key: str):
    workflow = new_workflow(workflow_id=f"workflow-contention-{index:06d}", started_at=T0)
    query = SearchQuery(
        service=ServiceKind.ACTIVITY,
        start_date=date(2026, 7, 21),
        end_date=None,
        start_time="08:00",
        party=Party(adults=1, children=0),
    )
    lookup = LookupEvidence(
        lookup_id=f"lookup-contention-{index:06d}",
        service=ServiceKind.ACTIVITY,
        query_signature=query.signature,
        observed_at=T0 + timedelta(seconds=1),
        expires_at=T0 + timedelta(minutes=5),
        snapshot_hash=hashlib.sha256(f"lookup:{index}".encode()).hexdigest(),
        status=LookupStatus.POSITIVE,
    )
    offer = OfferSnapshot(
        offer_id=f"offer-contention-{index:06d}",
        lookup_id=lookup.lookup_id,
        service=ServiceKind.ACTIVITY,
        provider_ref=f"provider-contention-{index:06d}",
        public_label="Synthetic contention offer",
        start_date=query.start_date,
        end_date=None,
        start_time=query.start_time,
        party=query.party,
        total=Money(amount=Decimal("100.00"), currency="BRL"),
        available=True,
    )
    events = (
        StartSearch(
            event_id=f"event-contention-{index:06d}-1",
            occurred_at=T0 + timedelta(seconds=1),
            query=query,
        ),
        LookupRecorded(
            event_id=f"event-contention-{index:06d}-2",
            occurred_at=T0 + timedelta(seconds=2),
            evidence=lookup,
            offers=(offer,),
        ),
        OfferChosen(
            event_id=f"event-contention-{index:06d}-3",
            occurred_at=T0 + timedelta(seconds=3),
            offer_id=offer.offer_id,
        ),
        DraftRequested(
            event_id=f"event-contention-{index:06d}-4",
            occurred_at=T0 + timedelta(seconds=4),
            draft_id=f"draft-contention-{index:06d}",
            customer=CustomerFacts(
                customer_ref=f"customer-contention-{index:06d}",
                full_name="Synthetic Contention Person",
                email=f"contention.{index}" + chr(64) + "example.invalid",
                phone_e164="+99900000000",
                country_code="ZZ",
            ),
            terms=EconomicTerms(payment_method="pix", add_ons=()),
        ),
    )
    for event in events:
        workflow = reduce(workflow, event).state
    summary = SummaryRecorded(
        event_id=f"event-contention-{index:06d}-5",
        occurred_at=T0 + timedelta(seconds=5),
        summary_event_id=f"summary-contention-{index:06d}",
        draft_version=workflow.draft.version,
        subject_signature=workflow.draft.subject_signature,
        outbox_message_id=f"outbox-summary-contention-{index:06d}",
    )
    workflow = reduce(workflow, summary).state
    accepted = reduce(
        workflow,
        ConfirmationReceived(
            event_id=f"event-contention-{index:06d}-6",
            occurred_at=T0 + timedelta(seconds=6),
            confirmation_event_id=f"confirmation-contention-{index:06d}",
            decision=ConfirmationDecisionKind.ACCEPT,
            target_draft_version=workflow.draft.version,
            subject_signature=workflow.draft.subject_signature,
        ),
    )
    return (
        BoundaryState(7, lead_key, 0, accepted.state, None, (), accepted.state.meta.seen_event_ids),
        accepted.commands[0],
    )


def _genesis_candidate(index: int, lead_key: str) -> tuple[LegacyLeadSnapshot, ImportResult]:
    raw = json.loads(synthetic_collecting_snapshot(index).canonical_json)
    raw["lead_key"] = lead_key
    raw["metadata"]["workflow_id"] = f"workflow-genesis-{index:06d}"
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot = LegacyLeadSnapshot(
        1,
        "chapada-leads-hermes",
        raw,
        canonical,
        hashlib.sha256(canonical.encode()).hexdigest(),
    )
    result = import_legacy_state(snapshot)
    if result.disposition is not ImportDisposition.MIGRATED:
        raise AssertionError("contention genesis candidate did not migrate")
    return snapshot, result


def _outbox(command, index: int) -> OutboxMessage:
    payload = json.dumps(
        {"command_id": command.command_id, "round": index},
        sort_keys=True,
        separators=(",", ":"),
    )
    return OutboxMessage(
        message_id=f"outbox-contention-{index:06d}",
        idempotency_key=f"outbox-idem-contention-{index:06d}",
        workflow_id=command.workflow_id,
        command_id=command.command_id,
        kind=OutboxKind.SUMMARY_PRESENTED,
        template_id=f"template-contention-{index:06d}",
        canonical_payload=payload,
        payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
        created_at=T0,
    )


def _contention_round(domain: str, index: int) -> ContentionRow:
    with tempfile.TemporaryDirectory(prefix=f"phase7-contention-{domain}-") as directory:
        path = Path(directory) / "boundary.db"
        lead_key = f"lead-contention-{domain}-{index:06d}"
        command = None
        if domain == "genesis":
            seed = SQLiteBoundaryStore.open_path(path)
            seed.close()
        else:
            seed = SQLiteBoundaryStore.open_path(path)
            snapshot, imported = _genesis_candidate(index, lead_key)
            if domain in {"command", "outbox"}:
                queued, command = _queued_state_and_command(index, lead_key)
                imported = ImportResult(ImportDisposition.MIGRATED, queued, ImportReason.NONE)
            seed.import_genesis(snapshot, imported, claimed_at=T0)
            seed.close()

        barrier = Barrier(2, timeout=5)

        def contend(contender: int) -> str:
            store = SQLiteBoundaryStore.open_path(path)
            try:
                if domain == "genesis":
                    candidate, result = _genesis_candidate(index * 2 + contender, lead_key)
                    barrier.wait()
                    store.import_genesis(candidate, result, claimed_at=T0)
                else:
                    current, token = store.acquire_fence(lead_key)
                    barrier.wait()
                    event_id = f"event-race-{domain}-{index:06d}-{contender}"
                    state = replace(
                        current.state,
                        version=1,
                        processed_event_ids=(*current.state.processed_event_ids, event_id),
                    )
                    commands = (command,) if domain == "command" else ()
                    outbox = (_outbox(command, index),) if domain == "outbox" else ()
                    store.commit(
                        event_id=event_id,
                        event_hash=hashlib.sha256(event_id.encode()).hexdigest(),
                        expected_version=0,
                        fencing_token=token,
                        commit=BoundaryCommit(state, commands, outbox, ()),
                        committed_at=T0,
                    )
                return "winner"
            except (ConcurrencyConflict, IdentityConflict):
                return "conflict"
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = tuple(pool.map(contend, (0, 1)))
        check = SQLiteBoundaryStore.open_path(path)
        try:
            counts = tuple(
                check._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in (
                    "boundary_state",
                    "boundary_events",
                    "boundary_commands",
                    "boundary_outbox",
                )
            )
        finally:
            check.close()
        winners = outcomes.count("winner")
        conflicts = outcomes.count("conflict")
        expected = (
            1,
            0 if domain == "genesis" else 1,
            1 if domain == "command" else 0,
            1 if domain == "outbox" else 0,
        )
        passed = winners == 1 and conflicts == 1 and counts == expected
        material = json.dumps(
            {
                "conflicts": conflicts,
                "counts": counts,
                "domain": domain,
                "index": index,
                "outcomes": outcomes,
                "passed": passed,
                "winners": winners,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return ContentionRow(
            domain,
            index,
            2,
            winners,
            conflicts,
            counts[0],
            counts[1],
            counts[2],
            counts[3],
            passed,
            hashlib.sha256(material.encode()).hexdigest(),
        )


def _run_contention(rounds_per_domain: int) -> tuple[ContentionRow, ...]:
    return tuple(
        _contention_round(domain, index)
        for domain in CONTENTION_DOMAINS
        for index in range(rounds_per_domain)
    )


def run_fault_matrix(
    *,
    focused: bool,
    frozen_tree: str | None = None,
    current_tree: str | None = None,
) -> FaultReport:
    if type(focused) is not bool:
        raise TypeError("focused must be an exact bool")
    if not focused:
        assert_frozen_candidate(frozen_tree=frozen_tree, current_tree=current_tree)
    rows = [
        _rollback_fault("after_state_update"),
        _rollback_fault("after_event_insert"),
        _stale_fence_fault(),
        _event_conflict_fault(),
    ]
    if not focused:
        rows.extend((_genesis_conflict_fault(), _state_hash_fault()))
    schedules = 10 if focused else RESTART_SCHEDULES
    rounds = 2 if focused else CONTENTION_ROUNDS_PER_DOMAIN
    restarts_passed = _run_restarts(schedules)
    contention_details = _run_contention(rounds)
    faults = tuple(rows)
    return FaultReport(
        faults,
        all(row.passed for row in faults) and restarts_passed,
        schedules,
        restarts_passed,
        len(contention_details),
        contention_details,
    )


__all__ = (
    "CONTENTION_DOMAINS",
    "CONTENTION_ROUNDS_PER_DOMAIN",
    "ContentionRow",
    "MUTANT_COUNT",
    "RESTART_SCHEDULES",
    "FaultReport",
    "FaultRow",
    "run_fault_matrix",
)
