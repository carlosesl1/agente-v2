from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_v2_runtime_image_contains_the_instrumentation_package() -> None:
    dockerfile = (ROOT / "Dockerfile.v2").read_text(encoding="utf-8")

    assert "COPY v2_ops /app/v2_ops" in dockerfile
    assert re.search(r"compileall\s+-q[\s\\\n\S]*\bv2_ops\b", dockerfile)


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
    assert set(service["volumes"]) == {
        "${V2_OPS_DATA_DIR:-./data}:/data/ops:ro",
        "${V2_OPS_RECORDS_DATA_DIR:?required}:/data/records:ro",
    }

    environment = service["environment"]
    assert set(environment) == {
        "V2_OPS_USERNAME",
        "V2_OPS_PASSWORD_HASH",
        "V2_OPS_SESSION_KEY_HEX",
        "V2_OPS_TRACE_KEY_HEX",
        "V2_OPS_TRACE_PATH",
        "V2_OPS_RECORDS_PATH",
        "V2_OPS_SECURE_COOKIE",
        "V2_OPS_RELEASE_SHA",
        "V2_OPS_IMAGE_DIGEST",
        "V2_OPS_CONFIG_FINGERPRINT",
    }
    assert environment["V2_OPS_RECORDS_PATH"] == "/data/records"
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
        "V2_OPS_RECORDS_PATH",
        "V2_OPS_SECURE_COOKIE",
        "V2_OPS_DATA_DIR",
        "V2_OPS_RECORDS_DATA_DIR",
        "V2_OPS_IMAGE",
        "V2_OPS_RELEASE_SHA",
        "V2_OPS_IMAGE_DIGEST",
        "V2_OPS_CONFIG_FINGERPRINT",
    }


def test_ops_image_keeps_runtime_modules_outside_the_minimal_copy() -> None:
    dockerfile = (ROOT / "Dockerfile.v2-ops").read_text(encoding="utf-8")

    assert "COPY v2_ops /app/v2_ops" in dockerfile
    for forbidden_copy in (
        "reservation_boundary",
        "reservation_domain",
        "reservation_execution",
        "v2_application",
        "v2_host",
    ):
        assert f"COPY {forbidden_copy}" not in dockerfile


def test_env_example_quotes_scrypt_hash_against_compose_interpolation() -> None:
    text = (ROOT / "deploy" / "v2-ops" / "env.example").read_text(
        encoding="utf-8"
    )
    password_line = next(
        line for line in text.splitlines() if line.startswith("V2_OPS_PASSWORD_HASH=")
    )

    assert password_line.startswith("V2_OPS_PASSWORD_HASH='scrypt$")
    assert password_line.endswith("'")
