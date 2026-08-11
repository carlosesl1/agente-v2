from __future__ import annotations

from v2_contracts.model_wire import V8_RESPONSE_JSON_SCHEMA
from v2_host.structured_output import (
    GROUNDING_REVIEW_JSON_SCHEMA,
    grounding_review_request_overrides,
    maya_v8_request_overrides,
)


def test_maya_v8_request_overrides_use_codex_extra_body_text_format() -> None:
    assert maya_v8_request_overrides() == {
        "extra_body": {
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "maya_v8_conversation",
                    "strict": True,
                    "schema": V8_RESPONSE_JSON_SCHEMA,
                }
            }
        }
    }


def test_maya_v8_request_overrides_return_an_isolated_copy() -> None:
    first = maya_v8_request_overrides()
    second = maya_v8_request_overrides()
    assert first == second
    assert first is not second

    first["extra_body"]["text"]["format"]["schema"]["properties"].clear()
    assert maya_v8_request_overrides() == second


def test_grounding_review_schema_is_closed_and_does_not_author_public_text() -> None:
    assert GROUNDING_REVIEW_JSON_SCHEMA == {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "unsupported_chunk_indices"],
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["supported", "unsupported"],
            },
            "unsupported_chunk_indices": {
                "type": "array",
                "maxItems": 2,
                "items": {"type": "integer", "minimum": 0, "maximum": 1},
            },
        },
    }
    assert "reply" not in repr(GROUNDING_REVIEW_JSON_SCHEMA).casefold()
    assert "text" not in repr(GROUNDING_REVIEW_JSON_SCHEMA).casefold()


def test_grounding_review_uses_separate_strict_provider_contract() -> None:
    overrides = grounding_review_request_overrides()
    format_value = overrides["extra_body"]["text"]["format"]
    assert format_value == {
        "type": "json_schema",
        "name": "maya_grounding_review",
        "strict": True,
        "schema": GROUNDING_REVIEW_JSON_SCHEMA,
    }
    assert format_value != maya_v8_request_overrides()["extra_body"]["text"][
        "format"
    ]
