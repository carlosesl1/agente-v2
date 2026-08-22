"""Provider request overrides for the closed Maya V8 response."""

from __future__ import annotations

from copy import deepcopy

from v2_contracts.model_wire import V8_RESPONSE_JSON_SCHEMA


def maya_v8_request_overrides() -> dict[str, object]:
    """Return an isolated Responses API structured-output override."""

    return {
        "extra_body": {
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "maya_v8_conversation",
                    "strict": True,
                    "schema": deepcopy(V8_RESPONSE_JSON_SCHEMA),
                }
            }
        }
    }


__all__ = ["maya_v8_request_overrides"]
