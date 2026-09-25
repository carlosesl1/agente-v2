"""Provider request overrides for the closed Maya V8 response."""

from __future__ import annotations

from copy import deepcopy

from v2_contracts.model_wire import V8_RESPONSE_JSON_SCHEMA, V9_RESPONSE_JSON_SCHEMA


def maya_v8_request_overrides(contract="maya-v8") -> dict[str, object]:
    """Return an isolated Responses API structured-output override."""

    return {
        "extra_body": {
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "maya_v9_conversation" if contract == "maya-v9" else "maya_v8_conversation",
                    "strict": True,
                    "schema": deepcopy(V9_RESPONSE_JSON_SCHEMA if contract == "maya-v9" else V8_RESPONSE_JSON_SCHEMA),
                }
            }
        }
    }


__all__ = ["maya_v8_request_overrides"]
