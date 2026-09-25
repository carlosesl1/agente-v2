from v2_host.structured_output import maya_v8_request_overrides


def test_v9_discriminator_has_explicit_type_for_codex_schema():
    schema = maya_v8_request_overrides("maya-v9")["extra_body"]["text"]["format"][
        "schema"
    ]
    assert schema["properties"]["schema"].get("type") == "string"
    assert schema["properties"]["schema"]["const"] == "v2-model-proposal-v9"

    def check(node):
        if isinstance(node, dict):
            if "enum" in node or "const" in node:
                assert "type" in node
            if "required" in node:
                assert len(node["required"]) == len(set(node["required"]))
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
