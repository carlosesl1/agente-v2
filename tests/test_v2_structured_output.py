from __future__ import annotations

from v2_contracts.model_wire import V8_RESPONSE_JSON_SCHEMA
from v2_host.structured_output import maya_v8_request_overrides


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
