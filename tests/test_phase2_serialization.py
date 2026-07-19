from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest

from reservation_domain.serialization import _cached_type_hints

from reservation_domain import (
    AddOn,
    AwaitingAdjustmentState,
    ConfirmationDecisionKind,
    ConfirmationReceived,
    CustomerFacts,
    DraftAdjusted,
    DraftRequested,
    EconomicTerms,
    EVENT_TYPES,
    ExecutionCertainty,
    ExecutionFinished,
    ExecutionStarted,
    FailedBeforeProviderState,
    FailedNoEffectState,
    LookupEvidence,
    LookupRecorded,
    LookupStatus,
    ManualReviewRequested,
    Money,
    OfferChosen,
    OfferSnapshot,
    Party,
    SearchQuery,
    ServiceKind,
    StartSearch,
    STATE_TYPES,
    SucceededState,
    SummaryRecorded,
    UncertainState,
    WorkflowCancelled,
    WorkflowExpired,
    dumps_command,
    dumps_event,
    dumps_state,
    loads_command,
    loads_event,
    loads_state,
    new_workflow,
    reduce,
)

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def complete_flow():
    query = SearchQuery(
        service=ServiceKind.ACTIVITY,
        start_date=date(2026, 10, 10),
        end_date=None,
        start_time="08:00",
        party=Party(adults=2, children=0),
    )
    evidence = LookupEvidence(
        lookup_id="lookup-serializer",
        service=ServiceKind.ACTIVITY,
        query_signature=query.signature,
        observed_at=T0 + timedelta(seconds=1),
        expires_at=T0 + timedelta(minutes=10),
        snapshot_hash="b" * 64,
        status=LookupStatus.POSITIVE,
    )
    selected = OfferSnapshot(
        offer_id="offer-serializer",
        lookup_id=evidence.lookup_id,
        service=ServiceKind.ACTIVITY,
        provider_ref="provider-activity-alpha",
        public_label="Passeio sintético",
        start_date=query.start_date,
        end_date=None,
        start_time=query.start_time,
        party=query.party,
        total=Money(amount=Decimal("500.00"), currency="BRL"),
        available=True,
    )
    terms = EconomicTerms(
        payment_method="pix",
        add_ons=(
            AddOn(
                code="equipment",
                quantity=2,
                unit_price=Money(amount=Decimal("25.00"), currency="BRL"),
            ),
        ),
    )
    customer_facts = CustomerFacts(
        customer_ref="customer-serializer",
        full_name="Synthetic Serializer Person",
        email="serializer.person" + chr(64) + "example.invalid",
        phone_e164="+99900000002",
        country_code="ZZ",
    )
    events = [
        StartSearch(
            event_id="event-serializer-1",
            occurred_at=T0 + timedelta(seconds=1),
            query=query,
        ),
        LookupRecorded(
            event_id="event-serializer-2",
            occurred_at=T0 + timedelta(seconds=2),
            evidence=evidence,
            offers=(selected,),
        ),
        OfferChosen(
            event_id="event-serializer-3",
            occurred_at=T0 + timedelta(seconds=3),
            offer_id=selected.offer_id,
        ),
        DraftRequested(
            event_id="event-serializer-4",
            occurred_at=T0 + timedelta(seconds=4),
            draft_id="draft-serializer",
            customer=customer_facts,
            terms=terms,
        ),
    ]
    states = [new_workflow(workflow_id="workflow-serializer", started_at=T0)]
    for event in events:
        states.append(reduce(states[-1], event).state)
    ready = states[-1]
    summary = SummaryRecorded(
        event_id="event-serializer-5",
        occurred_at=T0 + timedelta(seconds=5),
        summary_event_id="summary-serializer",
        draft_version=ready.draft.version,
        subject_signature=ready.draft.subject_signature,
        outbox_message_id="outbox-serializer",
    )
    states.append(reduce(ready, summary).state)
    awaiting = states[-1]
    confirmation = ConfirmationReceived(
        event_id="event-serializer-6",
        occurred_at=T0 + timedelta(seconds=6),
        confirmation_event_id="confirmation-serializer",
        decision=ConfirmationDecisionKind.ACCEPT,
        target_draft_version=awaiting.draft.version,
        subject_signature=awaiting.draft.subject_signature,
    )
    queued_transition = reduce(awaiting, confirmation)
    states.append(queued_transition.state)
    return states, (*events, summary, confirmation), queued_transition.commands[0]


def all_domain_samples():
    states, events, command = complete_flow()
    queued = states[-1]
    execution_started = ExecutionStarted(
        event_id="event-serializer-execution-started",
        occurred_at=T0 + timedelta(seconds=7),
        command_id=command.command_id,
    )
    executing = reduce(queued, execution_started).state
    states.append(executing)

    confirmed_outcome = command.outcome(
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        normalized_status="reservation_created",
        provider_reference="reservation-serializer",
        evidence=("e" * 64,),
    )
    execution_finished = ExecutionFinished(
        event_id="event-serializer-execution-finished",
        occurred_at=T0 + timedelta(seconds=8),
        command_id=command.command_id,
        outcome=confirmed_outcome,
    )
    succeeded = reduce(executing, execution_finished).state
    assert isinstance(succeeded, SucceededState)
    states.append(succeeded)

    not_called = reduce(
        executing,
        ExecutionFinished(
            event_id="event-serializer-not-called",
            occurred_at=T0 + timedelta(seconds=8),
            command_id=command.command_id,
            outcome=command.outcome(
                certainty=ExecutionCertainty.NOT_CALLED,
                normalized_status="provider_not_called",
            ),
        ),
    ).state
    assert isinstance(not_called, FailedBeforeProviderState)
    states.append(not_called)

    no_effect = reduce(
        executing,
        ExecutionFinished(
            event_id="event-serializer-no-effect",
            occurred_at=T0 + timedelta(seconds=8),
            command_id=command.command_id,
            outcome=command.outcome(
                certainty=ExecutionCertainty.CALLED_NO_EFFECT,
                normalized_status="called_without_effect",
            ),
        ),
    ).state
    assert isinstance(no_effect, FailedNoEffectState)
    states.append(no_effect)

    uncertain = reduce(
        executing,
        ExecutionFinished(
            event_id="event-serializer-uncertain",
            occurred_at=T0 + timedelta(seconds=8),
            command_id=command.command_id,
            outcome=command.outcome(
                certainty=ExecutionCertainty.CALLED_UNKNOWN,
                normalized_status="provider_result_unknown",
            ),
        ),
    ).state
    assert isinstance(uncertain, UncertainState)
    states.append(uncertain)
    manual_review = ManualReviewRequested(
        event_id="event-serializer-manual-review",
        occurred_at=T0 + timedelta(seconds=9),
        reason="reconciliation_required",
    )
    states.append(reduce(uncertain, manual_review).state)

    initial = states[0]
    cancelled = WorkflowCancelled(
        event_id="event-serializer-cancelled",
        occurred_at=T0 + timedelta(seconds=1),
        reason="lead_cancelled",
    )
    expired = WorkflowExpired(
        event_id="event-serializer-expired",
        occurred_at=T0 + timedelta(seconds=1),
        reason="workflow_ttl_expired",
    )
    states.append(reduce(initial, cancelled).state)
    states.append(reduce(initial, expired).state)

    awaiting = states[5]
    adjustment_decision = ConfirmationReceived(
        event_id="event-serializer-adjustment-decision",
        occurred_at=T0 + timedelta(seconds=7),
        confirmation_event_id="confirmation-serializer-adjustment",
        decision=ConfirmationDecisionKind.ADJUST,
        target_draft_version=awaiting.draft.version,
        subject_signature=awaiting.draft.subject_signature,
    )
    adjustment_state = reduce(awaiting, adjustment_decision).state
    assert isinstance(adjustment_state, AwaitingAdjustmentState)
    states.append(adjustment_state)

    ready = states[4]
    adjusted = DraftAdjusted(
        event_id="event-serializer-adjusted",
        occurred_at=T0 + timedelta(seconds=5),
        customer=ready.draft.customer,
        terms=EconomicTerms(payment_method="cash"),
    )
    return (
        states,
        (
            *events,
            adjusted,
            adjustment_decision,
            execution_started,
            execution_finished,
            manual_review,
            cancelled,
            expired,
        ),
        command,
    )


class SerializerContractTests(unittest.TestCase):
    def test_deserialization_reuses_immutable_type_metadata(self) -> None:
        raw = dumps_state(
            new_workflow(workflow_id="workflow-cache-probe", started_at=T0)
        )
        _cached_type_hints.cache_clear()
        self.assertEqual(loads_state(raw), loads_state(raw))
        info = _cached_type_hints.cache_info()
        self.assertGreater(info.misses, 0)
        self.assertGreater(info.hits, 0)
        with self.assertRaises(TypeError):
            _cached_type_hints(type(loads_state(raw)))["injected"] = str

    def test_every_reachable_state_round_trips(self) -> None:
        states, _, _ = all_domain_samples()
        self.assertEqual({type(state) for state in states}, set(STATE_TYPES))
        for state in states:
            with self.subTest(state=type(state).__name__):
                self.assertEqual(loads_state(dumps_state(state)), state)

    def test_every_flow_event_round_trips(self) -> None:
        _, events, _ = all_domain_samples()
        self.assertEqual({type(event) for event in events}, set(EVENT_TYPES))
        for event in events:
            with self.subTest(event=type(event).__name__):
                self.assertEqual(loads_event(dumps_event(event)), event)

    def test_command_round_trips(self) -> None:
        _, _, command = complete_flow()
        self.assertEqual(loads_command(dumps_command(command)), command)

    def test_unknown_schema_version_fails_closed(self) -> None:
        state, _, _ = complete_flow()
        payload = json.loads(dumps_state(state[-1]))
        payload["schema_version"] = 999
        with self.assertRaises(ValueError):
            loads_state(json.dumps(payload))

    def test_schema_version_requires_exact_json_integer(self) -> None:
        states, _, _ = complete_flow()
        original = json.loads(dumps_state(states[-1]))
        for value in (True, False, 1.0, "1"):
            with self.subTest(value=value):
                payload = json.loads(json.dumps(original))
                payload["schema_version"] = value
                with self.assertRaises(ValueError):
                    loads_state(json.dumps(payload))

    def test_duplicate_keys_fail_closed_at_every_depth(self) -> None:
        states, _, _ = complete_flow()
        raw = dumps_state(states[-1])
        duplicate_envelope = raw.replace(
            '"schema_version":1',
            '"schema_version":1,"schema_version":1',
            1,
        )
        with self.assertRaises(ValueError):
            loads_state(duplicate_envelope)
        duplicate_nested = raw.replace(
            '"revision":6',
            '"revision":6,"revision":6',
            1,
        )
        with self.assertRaises(ValueError):
            loads_state(duplicate_nested)

    def test_malformed_missing_and_wrong_shape_payloads_fail_closed(self) -> None:
        states, _, _ = complete_flow()
        payload = json.loads(dumps_state(states[-1]))
        missing = dict(payload)
        missing.pop("type")
        wrong_data = dict(payload)
        wrong_data["data"] = []
        for raw in (
            "{",
            "[]",
            json.dumps(missing),
            json.dumps(wrong_data),
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    loads_state(raw)

    def test_invalid_wire_types_and_noncanonical_scalars_fail_closed(self) -> None:
        event = StartSearch(
            event_id="event-wire-types",
            occurred_at=T0,
            query=SearchQuery(
                service=ServiceKind.ACTIVITY,
                start_date=date(2026, 10, 10),
                end_date=None,
                start_time="08:00",
                party=Party(adults=2, children=0),
            ),
        )
        base = json.loads(dumps_event(event))
        mutations = []
        invalid_enum = json.loads(json.dumps(base))
        invalid_enum["data"]["query"]["service"] = "invented"
        mutations.append(invalid_enum)
        wrong_integer = json.loads(json.dumps(base))
        wrong_integer["data"]["query"]["party"]["adults"] = True
        mutations.append(wrong_integer)
        compact_date = json.loads(json.dumps(base))
        compact_date["data"]["query"]["start_date"] = "20261010"
        mutations.append(compact_date)
        compact_datetime = json.loads(json.dumps(base))
        compact_datetime["data"]["occurred_at"] = "20261001T120000+0000"
        mutations.append(compact_datetime)
        for mutation in mutations:
            with self.subTest(payload=mutation):
                with self.assertRaises(ValueError):
                    loads_event(json.dumps(mutation))

    def test_encoder_rejects_subclasses_outside_closed_universe(self) -> None:
        states, events, command = complete_flow()

        class InventedState(type(states[0])):
            TYPE = "invented_state"

        class InventedEvent(type(events[0])):
            TYPE = "invented_event"

        class InventedCommand(type(command)):
            TYPE = "invented_command"

        invented_state = InventedState(meta=states[0].meta)
        invented_event = InventedEvent(
            event_id=events[0].event_id,
            occurred_at=events[0].occurred_at,
            query=events[0].query,
        )
        invented_command = InventedCommand(
            **{field.name: getattr(command, field.name) for field in fields(command)}
        )
        for encoder, value in (
            (dumps_state, invented_state),
            (dumps_event, invented_event),
            (dumps_command, invented_command),
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    encoder(value)

    def test_unknown_type_tag_fails_closed(self) -> None:
        state, _, _ = complete_flow()
        payload = json.loads(dumps_state(state[-1]))
        payload["type"] = "invented_state"
        with self.assertRaises(ValueError):
            loads_state(json.dumps(payload))

    def test_unknown_field_fails_closed(self) -> None:
        states, _, _ = complete_flow()
        payload = json.loads(dumps_state(states[-1]))
        payload["unexpected"] = True
        with self.assertRaises(ValueError):
            loads_state(json.dumps(payload))

    def test_unknown_nested_field_fails_closed(self) -> None:
        states, _, _ = complete_flow()
        payload = json.loads(dumps_state(states[-1]))
        payload["data"]["draft"]["unexpected_nested"] = True
        with self.assertRaises(ValueError):
            loads_state(json.dumps(payload))

    def test_forged_draft_signature_fails_closed(self) -> None:
        states, _, _ = complete_flow()
        payload = json.loads(dumps_state(states[-1]))
        forged = "0" * 64
        payload["data"]["draft"]["subject_signature"] = forged
        payload["data"]["summary"]["subject_signature"] = forged
        payload["data"]["confirmation"]["subject_signature"] = forged
        payload["data"]["command"]["subject_signature"] = forged
        with self.assertRaises(ValueError):
            loads_state(json.dumps(payload))

    def test_cross_object_draft_summary_mismatch_fails_closed(self) -> None:
        states, _, _ = complete_flow()
        payload = json.loads(dumps_state(states[-1]))
        payload["data"]["summary"]["draft_version"] += 1
        with self.assertRaises(ValueError):
            loads_state(json.dumps(payload))

    def test_forged_command_identity_fails_closed(self) -> None:
        states, _, _ = complete_flow()
        payload = json.loads(dumps_state(states[-1]))
        payload["data"]["command"]["command_id"] = "cmd:forged-command-identity"
        payload["data"]["meta"]["command_ids"] = ["cmd:forged-command-identity"]
        with self.assertRaises(ValueError):
            loads_state(json.dumps(payload))

    def test_command_state_without_meta_command_id_fails_closed(self) -> None:
        states, _, _ = complete_flow()
        payload = json.loads(dumps_state(states[-1]))
        payload["data"]["meta"]["command_ids"] = []
        with self.assertRaises(ValueError):
            loads_state(json.dumps(payload))

    def test_seen_event_id_without_matching_hash_fails_closed(self) -> None:
        states, _, _ = complete_flow()
        payload = json.loads(dumps_state(states[-1]))
        payload["data"]["meta"]["seen_event_hashes"].pop()
        with self.assertRaises(ValueError):
            loads_state(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
