from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_ops_image_and_compose_are_hardened_and_isolated() -> None:
    dockerfile = (ROOT / "Dockerfile.v2-ops").read_text(encoding="utf-8")
    compose = yaml.safe_load(
        (ROOT / "deploy" / "v2-ops" / "compose.ops.yaml").read_text(
            encoding="utf-8"
        )
    )
    service = compose["services"]["v2-ops"]
    rendered = str(service).casefold()

    assert re.search(r"^USER\s+(?!0|root)\S+", dockerfile, re.MULTILINE)
    assert "v2_ops" in dockerfile
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["tmpfs"]
    assert "ports" not in service
    assert "docker.sock" not in rendered
    assert "ga-state" not in rendered
    assert "private-customer" not in rendered
    assert len(service["volumes"]) == 1
    assert service["volumes"][0].endswith(":/data/ops:ro")

    environment = service["environment"]
    assert set(environment) == {
        "V2_OPS_USERNAME",
        "V2_OPS_PASSWORD_HASH",
        "V2_OPS_SESSION_KEY_HEX",
        "V2_OPS_TRACE_KEY_HEX",
        "V2_OPS_TRACE_PATH",
        "V2_OPS_SECURE_COOKIE",
    }
    for forbidden in ("cloudbeds", "bokun", "stripe", "manychat", "provider"):
        assert forbidden not in " ".join(environment).casefold()

    labels = service["labels"]
    label_text = "\n".join(labels)
    assert "Host(`hermes.chapadabackpackers.com`)" in label_text
    assert "Path(`/ops`) || PathPrefix(`/ops/`)" in label_text
    assert "priority=200" in label_text
    assert "traefik.enable=true" in label_text
    assert service["networks"] == ["coolify"]


def test_env_example_contains_only_ops_specific_names() -> None:
    text = (ROOT / "deploy" / "v2-ops" / "env.example").read_text(
        encoding="utf-8"
    )
    names = {
        line.split("=", 1)[0]
        for line in text.splitlines()
        if line and not line.startswith("#")
    }
    assert names == {
        "V2_OPS_USERNAME",
        "V2_OPS_PASSWORD_HASH",
        "V2_OPS_SESSION_KEY_HEX",
        "V2_OPS_TRACE_KEY_HEX",
        "V2_OPS_TRACE_PATH",
        "V2_OPS_SECURE_COOKIE",
        "V2_OPS_DATA_DIR",
        "V2_OPS_IMAGE",
    }
