"""Causal service-context regressions; local SQLite, no external effects."""

from dataclasses import replace
from datetime import date, timedelta
import json

import pytest

from reservation_boundary import (
    ConversationProjection,
    ConversationStage,
    StringSlot,
    TypedFact,
)
from reservation_domain import Party
from v2_adapters.hermes_model import _request_wire, _passenger
from v2_application.conversation import resolve_effective_customer
from v2_application.passengers import merge_manifest, attach_projection_manifest
from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
from v2_contracts.model import ModelFact, ModelRequest
from v2_contracts.passengers import PassengerInput
from tests.test_v2_turn_executor import NOW, BATCH, FakeProfile, SQLiteBoundaryStore


def _person(position=1, **kw):
    values = dict(
        full_name="Pessoa Exemplo",
        birth_date=date(1988, 2, 3),
        gender="f",
        country_code="BR",
    )
    values.update(kw)
    return PassengerInput(position, "adult", **values)


def _projection(manifest):
    return attach_projection_manifest(
        ConversationProjection(ConversationStage.RECEPTIONIST, (), "pt-BR", (), None),
        TypedFact("passenger_manifest", StringSlot(manifest), "a" * 64),
    )


def _profile():
    store = SQLiteBoundaryStore.open_memory_v8()
    try:
        return replace(
            FakeProfile(store).read(BATCH.lead_id, now=NOW), phone_e164="+5575999990199"
        )
    finally:
        store.close()


def test_values_are_accepted_in_model_request_and_wire():
    request = ModelRequest(
        "req:values",
        "lead:values",
        "turn:values",
        "Pode usar os dados anteriores",
        "pt-BR",
        1,
        state_facts=(
            ModelFact("full_name", "Pessoa Exemplo"),
            ModelFact("birth_date", date(1988, 2, 3)),
        ),
    )
    wire = _request_wire(request, "System")
    assert b"Pessoa Exemplo" in wire
    assert b"1988-02-03" in wire
    assert b"private_customer_fact_names" not in wire
    assert b"passenger_manifest_status" not in wire


def test_explicit_holder_uses_existing_second_passenger_and_not_same_name():
    profile = _profile()
    # Equal names are deliberately insufficient: only the explicit role links them.
    profile = replace(profile, full_name="Pessoa Exemplo", gender=None)
    party = Party(2, 0)
    original = merge_manifest(
        None, (_person(1), _person(2, birth_date=date(1992, 4, 5))), party
    )
    unresolved = resolve_effective_customer(
        profile, _projection(original), NOW, activity_party=party
    )
    assert "birth_date" in unresolved.missing_fields
    linked = merge_manifest(
        original,
        (
            _person(
                2,
                full_name=None,
                birth_date=None,
                gender=None,
                country_code=None,
                is_holder=True,
            ),
        ),
        party,
    )
    resolved = resolve_effective_customer(
        profile, _projection(linked), NOW, activity_party=party
    )
    assert resolved.customer.birth_date == date(1992, 4, 5)
    assert resolved.customer.passengers[0].birth_date == date(1988, 2, 3)
    corrected = merge_manifest(
        linked,
        (_person(2, birth_date=date(1993, 4, 5)),),
        party,
        allow_replacement=True,
    )
    assert resolve_effective_customer(
        profile, _projection(corrected), NOW, activity_party=party
    ).customer.birth_date == date(1993, 4, 5)


def test_holder_link_survives_store_reopen_without_copying_person(tmp_path):
    store = SQLitePrivateCustomerFactStore(tmp_path / "facts.sqlite3")
    manifest = merge_manifest(
        None, (_person(1, is_holder=True), _person(2)), Party(2, 0)
    )
    store.persist_passenger_manifest(
        lead_id=BATCH.lead_id,
        source_turn_id="turn:holder",
        source_event_hash="b" * 64,
        fact=TypedFact("passenger_manifest", StringSlot(manifest), "a" * 64),
        persisted_at=NOW,
    )
    store.close()
    store = SQLitePrivateCustomerFactStore(tmp_path / "facts.sqlite3")
    loaded = store.load_passenger_manifest(BATCH.lead_id)
    assert loaded.value.value == manifest
    assert store.load(BATCH.lead_id).birth_date is None
    assert resolve_effective_customer(
        _profile(), _projection(loaded.value.value), NOW, activity_party=Party(2, 0)
    ).customer.birth_date == date(1988, 2, 3)
    store.close()


def test_contact_phone_persists_without_changing_channel_identity(tmp_path):
    store = SQLitePrivateCustomerFactStore(tmp_path / "facts.sqlite3")
    store.persist_turn(
        lead_id=BATCH.lead_id,
        source_turn_id="turn:phone",
        source_event_hash="b" * 64,
        facts=(ModelFact("phone_e164", "+351912345678"),),
        persisted_at=NOW,
    )
    store.close()
    store = SQLitePrivateCustomerFactStore(tmp_path / "facts.sqlite3")
    facts = store.load(BATCH.lead_id)
    profile = _profile()
    original_phone = profile.phone_e164
    empty = ConversationProjection(
        ConversationStage.RECEPTIONIST, (), "pt-BR", (), None
    )
    customer = resolve_effective_customer(
        profile, empty, NOW, private_facts=facts
    ).customer
    assert customer.phone_e164 == "+351912345678"
    assert facts.lead_id == BATCH.lead_id
    assert profile.phone_e164 == original_phone
    store.close()


def test_small_dialogues_continue_beyond_four_exchanges(tmp_path):
    store = SQLitePrivateCustomerFactStore(tmp_path / "facts.sqlite3")
    for i in range(12):
        store.record_dialogue_turn(
            lead_id="lead:history",
            source_turn_id=f"turn:{i}",
            source_event_hash=f"{i:064x}",
            customer_message=f"Mensagem {i}",
            assistant_reply_chunks=(f"Resposta {i}",),
            committed_at=NOW + timedelta(seconds=i),
        )
    store.close()
    store = SQLitePrivateCustomerFactStore(tmp_path / "facts.sqlite3")
    history = store.load_recent_dialogue("lead:history")
    assert len(history) == 12
    ModelRequest(
        "req:history",
        "lead:history",
        "turn:now",
        "Continue",
        "pt-BR",
        12,
        recent_dialogue=history,
    )
    store.close()


def test_parser_accepts_role_only_update_without_recollecting_values():
    row = dict(
        position=2,
        participant_type="adult",
        full_name=None,
        birth_date=None,
        gender=None,
        country_code=None,
        is_holder=True,
    )
    assert _passenger(row).is_holder is True
    with pytest.raises(ValueError):
        _passenger(dict(row, is_holder="yes"))


def test_legacy_manifest_keeps_values_without_guessing_holder():
    legacy = json.dumps(
        {
            "schema": "v2-passenger-manifest-v1",
            "adults": 1,
            "children": 0,
            "passengers": [
                dict(
                    position=1,
                    participant_type="adult",
                    full_name="Pessoa Exemplo",
                    birth_date="1988-02-03",
                    gender="f",
                    country_code="BR",
                )
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    upgraded = json.loads(merge_manifest(legacy, (), Party(1, 0)))
    assert upgraded["passengers"][0]["is_holder"] is False
    assert upgraded["passengers"][0]["birth_date"] == "1988-02-03"


def test_holder_reassignment_and_detach_never_copy_person_values():
    from v2_application.conversation import customer_context_values

    party = Party(2, 0)
    original = merge_manifest(
        None,
        (
            _person(1, is_holder=True),
            _person(2, full_name="Outra Pessoa", birth_date=None),
        ),
        party,
    )
    with pytest.raises(ValueError, match="holder"):
        merge_manifest(
            original,
            (
                _person(
                    2,
                    full_name=None,
                    birth_date=None,
                    gender=None,
                    country_code=None,
                    is_holder=True,
                ),
            ),
            party,
        )
    switched = merge_manifest(
        original,
        (
            _person(
                2,
                full_name=None,
                birth_date=None,
                gender=None,
                country_code=None,
                is_holder=True,
            ),
        ),
        party,
        allow_replacement=True,
    )
    rows = json.loads(switched)["passengers"]
    assert [row["is_holder"] for row in rows] == [False, True]
    values, _ = customer_context_values(_profile(), _projection(switched), NOW)
    assert values["full_name"] == "Outra Pessoa"
    assert "birth_date" not in values
    detached = merge_manifest(
        switched,
        (
            _person(
                2,
                full_name=None,
                birth_date=None,
                gender=None,
                country_code=None,
                is_holder=False,
            ),
        ),
        party,
        allow_replacement=True,
    )
    assert not any(row["is_holder"] for row in json.loads(detached)["passengers"])
    assert json.loads(detached)["passengers"][0]["birth_date"] == "1988-02-03"
    with pytest.raises(ValueError, match="multiple holders"):
        merge_manifest(
            original, (_person(1, is_holder=True), _person(2, is_holder=True)), party
        )


def test_dialogue_budget_does_not_delete_persisted_turns(tmp_path):
    store = SQLitePrivateCustomerFactStore(tmp_path / "budget.sqlite3")
    for i in range(6):
        store.record_dialogue_turn(
            lead_id="lead:budget",
            source_turn_id=f"turn:{i}",
            source_event_hash=f"{i:064x}",
            customer_message="a" * 16000,
            assistant_reply_chunks=("b" * 16000,),
            committed_at=NOW + timedelta(seconds=i),
        )
    history = store.load_recent_dialogue("lead:budget")
    assert (
        len(history) == 2
    )  # 64k budget, whole exchanges, not truncating message text.
    assert (
        store._connection.execute(
            "SELECT COUNT(*) FROM private_dialogue_turns"
        ).fetchone()[0]
        == 6
    )
    ModelRequest(
        "req:budget",
        "lead:budget",
        "turn:now",
        "Continue",
        "pt-BR",
        6,
        recent_dialogue=history,
    )
    store.close()


def test_phone_schema_migration_preserves_old_snapshot_and_journal(tmp_path):
    import sqlite3
    from v2_application.private_customer_facts import _EXPECTED_TABLE_SQL

    path = tmp_path / "legacy.sqlite3"
    store = SQLitePrivateCustomerFactStore(path)
    store.persist_turn(
        lead_id=BATCH.lead_id,
        source_turn_id="turn:old",
        source_event_hash="b" * 64,
        facts=(
            ModelFact("full_name", "Pessoa Legada"),
            ModelFact("birth_date", date(1988, 2, 3)),
        ),
        persisted_at=NOW,
    )
    old = store.load(BATCH.lead_id)
    store.close()
    # Recreate the PREVIOUS schema on this synthetic database only.
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE private_customer_facts RENAME TO temp_facts")
        db.execute(
            _EXPECTED_TABLE_SQL["private_customer_facts"].replace(",'phone_e164'", "")
        )
        db.execute("INSERT INTO private_customer_facts SELECT * FROM temp_facts")
        db.execute("DROP TABLE temp_facts")
    store = SQLitePrivateCustomerFactStore(path)
    assert store.load(BATCH.lead_id) == old
    store.persist_turn(
        lead_id=BATCH.lead_id,
        source_turn_id="turn:phone",
        source_event_hash="c" * 64,
        facts=(ModelFact("phone_e164", "+351912345678"),),
        persisted_at=NOW,
    )
    updated = store.load(BATCH.lead_id)
    assert updated.full_name == old.full_name
    assert updated.birth_date == old.birth_date
    assert updated.phone_e164 == "+351912345678"
    store.close()
    store = SQLitePrivateCustomerFactStore(path)
    assert store.load(BATCH.lead_id) == updated
    store.close()


def test_eight_executor_turns_reuse_holder_values_and_original_dialogue(tmp_path):
    from tests.test_v2_turn_executor import (
        EVENT,
        AUTHORITY,
        FakeAuditedModel,
        FixedClock,
        FixedAuthority,
        _enabled_reducer,
        _install_public_authority,
    )
    from v2_application.turn_executor import V2TurnExecutor
    from v2_application.reads import V2ReadService
    from v2_contracts.channel import InboundBatch
    from v2_contracts.model import ModelProposal

    store = SQLiteBoundaryStore.open_memory_v8()
    path = tmp_path / "journey.sqlite3"
    for i in range(8):
        event = replace(
            EVENT,
            event_id=f"event:context:{i}",
            payload_hash=f"{i:064x}",
            text=f"Mensagem original {i}",
        )
        batch = InboundBatch(
            f"batch:context:{i}",
            BATCH.lead_id,
            BATCH.subscriber_id,
            (event,),
            event.text,
        )
        authority = replace(
            AUTHORITY,
            authorization_id=f"auth:context:{i}",
            allocation_ids=(f"allocation:context:{i}",),
            allocation_manifest_hash=f"{i:064x}",
        )
        _install_public_authority(store, authority)
        facts = (
            (
                ModelFact("service", "agency"),
                ModelFact("adults", 2),
                ModelFact("children", 0),
                ModelFact("email", "contato@example.invalid"),
            )
            if i == 0
            else ()
        )
        passengers = (
            (_person(1), _person(2, birth_date=date(1992, 4, 5))) if i == 0 else ()
        )
        if i == 5:
            passengers = (
                _person(
                    2,
                    full_name=None,
                    birth_date=None,
                    gender=None,
                    country_code=None,
                    is_holder=True,
                ),
            )
        proposal = ModelProposal(
            batch.batch_id,
            "inform",
            ("Qual é a data?",),
            facts,
            (),
            (),
            passengers=passengers,
            clarification_question="Qual é a data?",
        )
        model = FakeAuditedModel(store, [proposal])
        private = SQLitePrivateCustomerFactStore(path)

        class Authority(FixedAuthority):
            def resolve(self, *args, **kwargs):
                return authority

        executor = V2TurnExecutor(
            store=store,
            model=model,
            reads=V2ReadService({}),
            profile=FakeProfile(store),
            private_customer_facts=private,
            reducer=_enabled_reducer(),
            public_authority=Authority(),
            clock=FixedClock(),
            locale="pt-BR",
            turn_timeout=timedelta(seconds=30),
            max_commit_attempts=1,
        )
        result = executor.execute(batch)
        assert not result.receipt.command_rows and not result.receipt.relay_rows
        if i == 7:
            request = model.calls[0]
            values = {f.name: f.value for f in request.state_facts}
            assert values["birth_date"] == date(1992, 4, 5)
            assert values["email"] == "contato@example.invalid"
            assert request.passengers[1].is_holder is True
            assert request.passengers[0].birth_date == date(1988, 2, 3)
            assert len(request.recent_dialogue) == 7
            assert request.recent_dialogue[0].customer_message == "Mensagem original 0"
            user = json.loads(
                json.loads(_request_wire(request, "Prompt"))["messages"][-1][1]
            )
            assert user["passengers"][1]["birth_date"] == "1992-04-05"
        private.close()
    store.close()


def test_confirmation_review_uses_same_customer_and_dialogue_context():
    from tests.test_v2_hermes_model_adapter import _confirmation_review_request
    from v2_adapters.hermes_model import _confirmation_review_wire
    from v2_contracts.model import ConversationExchange

    request = replace(
        _confirmation_review_request(),
        state_facts=(ModelFact("email", "contato@example.invalid"),),
        passengers=(_person(1, is_holder=True),),
        recent_dialogue=(ConversationExchange("Sou a titular", ("Entendido",)),),
    )
    wire = json.loads(_confirmation_review_wire(request, "Prompt"))
    user = json.loads(wire["messages"][-1][1])
    assert user["passengers"][0]["is_holder"] is True
    assert user["state_facts"] == [
        {"name": "email", "value": "contato@example.invalid"}
    ]
    assert wire["messages"][0] == ["user", "Sou a titular"]
