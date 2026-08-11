from __future__ import annotations

import copy

from v2_contracts.model_wire import V8_RESPONSE_FIELDS, V8_RESPONSE_JSON_SCHEMA


def test_v8_response_schema_has_exact_minimal_top_level() -> None:
    assert V8_RESPONSE_FIELDS == frozenset(
        {
            "intent",
            "reply_chunks",
            "facts",
            "read_requests",
            "selected_choice_refs",
            "selection_requested",
            "pending_action_disposition",
            "passengers",
        }
    )
    assert set(V8_RESPONSE_JSON_SCHEMA["properties"]) == V8_RESPONSE_FIELDS
    assert set(V8_RESPONSE_JSON_SCHEMA["required"]) == V8_RESPONSE_FIELDS
    assert V8_RESPONSE_JSON_SCHEMA["additionalProperties"] is False


def test_v8_response_schema_bounds_reply_chunks_and_all_arrays() -> None:
    properties = V8_RESPONSE_JSON_SCHEMA["properties"]
    reply = properties["reply_chunks"]
    assert reply["minItems"] == 1
    assert reply["maxItems"] == 2
    assert reply["items"]["additionalProperties"] is False
    assert set(reply["items"]["required"]) == {"text", "expects_reply"}

    for name in (
        "facts",
        "read_requests",
        "selected_choice_refs",
        "passengers",
    ):
        assert type(properties[name]["maxItems"]) is int
        assert properties[name]["maxItems"] >= 1


def test_v8_read_schema_has_exact_semantic_variants_without_mechanical_ids() -> None:
    variants = V8_RESPONSE_JSON_SCHEMA["properties"]["read_requests"]["items"][
        "anyOf"
    ]
    assert len(variants) == 5
    assert {variant["properties"]["kind"]["const"] for variant in variants} == {
        "knowledge",
        "lodging",
        "activity",
        "room_description",
        "activity_description",
    }
    for variant in variants:
        fields = set(variant["properties"])
        assert "request_id" not in fields
        assert "source_event_id" not in fields
        assert "locale" not in fields
        assert variant["additionalProperties"] is False
        assert set(variant["required"]) == fields


def test_v8_schema_has_no_removed_mechanical_or_duplicate_reply_fields() -> None:
    encoded_names = repr(V8_RESPONSE_JSON_SCHEMA)
    for forbidden in (
        "schema",
        "source_event_id",
        "request_id",
        "locale",
        "effect_proposals",
        "target_offer_id",
        "target_offer_ids",
        "confirmed_summary_version",
        "confirmed_action_kinds",
        "approval_basis",
        "clarification_question",
    ):
        assert f"'{forbidden}'" not in encoded_names


def test_v8_schema_constant_is_not_mutated_by_caller_copy() -> None:
    copied = copy.deepcopy(V8_RESPONSE_JSON_SCHEMA)
    copied["properties"].clear()
    assert set(V8_RESPONSE_JSON_SCHEMA["properties"]) == V8_RESPONSE_FIELDS
