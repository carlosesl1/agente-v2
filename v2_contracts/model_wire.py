"""Child process outcomes and closed Maya V8 response shape."""

from __future__ import annotations

from typing import Final

# Child process status is transport evidence, not customer/model semantics.
CHILD_INPUT_REJECTED_EXIT: Final = 64
CHILD_INVALID_RESPONSE_EXIT: Final = 65
CHILD_EXECUTION_FAILED_EXIT: Final = 70


class ChildInputRejected(ValueError):
    """The child rejected the request before model inference."""


class ChildExecutionFailed(RuntimeError):
    """The child failed to execute; no valid model response was obtained."""


V8_RESPONSE_FIELDS: Final = frozenset(
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

_TEXT: Final = {"type": "string", "minLength": 1, "maxLength": 4096}
_ISO_DATE: Final = {
    "type": "string",
    "format": "date",
    "pattern": r"^\d{4}-\d{2}-\d{2}$",
}
_PRODUCT_ID: Final = {
    "type": "string",
    "pattern": r"^product:[a-z0-9][a-z0-9._-]{0,127}$",
}
_CHOICE_REF: Final = {
    "type": "string",
    "pattern": r"^(?:lodging|activity):[1-9][0-9]?$",
}


def _object(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


V8_RESPONSE_JSON_SCHEMA: Final = _object(
    {
        "intent": {
            "type": "string",
            "enum": ["inform", "select", "adjust", "confirm", "request_handoff"],
        },
        "reply_chunks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 2,
            "items": _object(
                {
                    "text": dict(_TEXT),
                    "expects_reply": {"type": "boolean"},
                }
            ),
        },
        "facts": {
            "type": "array",
            "maxItems": 20,
            "items": _object(
                {
                    "name": {
                        "type": "string",
                        "enum": [
                            "language",
                            "service",
                            "product_id",
                            "payment_method",
                            "full_name",
                            "email",
                            "phone_e164",
                            "country_code",
                            "gender",
                            "start_date",
                            "end_date",
                            "activity_date",
                            "birth_date",
                            "adults",
                            "children",
                        ],
                    },
                    "value": {
                        "anyOf": [
                            {"type": "string", "minLength": 1, "maxLength": 1024},
                            {"type": "integer", "minimum": 0},
                        ]
                    },
                }
            ),
        },
        "read_requests": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "anyOf": [
                    _object(
                        {
                            "kind": {"type": "string", "const": "knowledge"},
                            "query": dict(_TEXT),
                        }
                    ),
                    _object(
                        {
                            "kind": {"type": "string", "const": "lodging"},
                            "check_in": dict(_ISO_DATE),
                            "check_out": dict(_ISO_DATE),
                            "adults": {"type": "integer", "minimum": 1},
                            "children": {"type": "integer", "minimum": 0},
                        }
                    ),
                    _object(
                        {
                            "kind": {"type": "string", "const": "activity"},
                            "product_id": dict(_PRODUCT_ID),
                            "activity_date": dict(_ISO_DATE),
                            "adults": {"type": "integer", "minimum": 1},
                            "children": {"type": "integer", "minimum": 0},
                        }
                    ),
                    _object(
                        {
                            "kind": {"type": "string", "const": "room_description"},
                            "choice_ref": dict(_CHOICE_REF),
                        }
                    ),
                    _object(
                        {
                            "kind": {"type": "string", "const": "activity_description"},
                            "product_id": dict(_PRODUCT_ID),
                        }
                    ),
                ]
            },
        },
        "selected_choice_refs": {
            "type": "array",
            "maxItems": 2,
            "items": dict(_CHOICE_REF),
        },
        "selection_requested": {"type": "boolean"},
        "pending_action_disposition": {
            "anyOf": [
                {"type": "null"},
                {"type": "string", "enum": ["preserve", "revoke"]},
            ]
        },
        "passengers": {
            "type": "array",
            "maxItems": 64,
            "items": _object(
                {
                    "is_holder": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
                    "position": {"type": "integer", "minimum": 1},
                    "participant_type": {
                        "type": "string",
                        "enum": ["adult", "child"],
                    },
                    "full_name": {"anyOf": [dict(_TEXT), {"type": "null"}]},
                    "birth_date": {"anyOf": [dict(_ISO_DATE), {"type": "null"}]},
                    "gender": {
                        "anyOf": [
                            {"type": "string", "enum": ["m", "f"]},
                            {"type": "null"},
                        ]
                    },
                    "country_code": {
                        "anyOf": [
                            {"type": "string", "pattern": r"^[A-Z]{2}$"},
                            {"type": "null"},
                        ]
                    },
                }
            ),
        },
    }
)


from copy import deepcopy
from v2_contracts.payment_proof import proof_schema
V9_RESPONSE_JSON_SCHEMA = deepcopy(V8_RESPONSE_JSON_SCHEMA)
V9_RESPONSE_JSON_SCHEMA["properties"].update(schema={"type":"string","const":"v2-model-proposal-v9"}, payment_proof=proof_schema())
V9_RESPONSE_JSON_SCHEMA["required"] = list(V9_RESPONSE_JSON_SCHEMA["properties"])

def validate_image_data_url(value):
    import base64
    if type(value) is not str or len(value) > 6 * 1024 * 1024:
        raise ValueError("image data exceeds bound")
    prefix, data = value.split(",", 1)
    if prefix not in ("data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"):
        raise ValueError("invalid image media type")
    raw = base64.b64decode(data, validate=True)
    if not raw or len(raw) > 4 * 1024 * 1024:
        raise ValueError("invalid image bytes")
    return raw

__all__ = ["V8_RESPONSE_FIELDS", "V8_RESPONSE_JSON_SCHEMA", "V9_RESPONSE_JSON_SCHEMA"]
