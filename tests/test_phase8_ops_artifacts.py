from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.v2.yaml"


def _environment() -> dict[str, object]:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert type(payload) is dict
    services = payload["services"]
    assert set(services) == {"api", "worker"}
    api = services["api"]
    worker = services["worker"]
    assert worker["environment"] == api["environment"] | {
        "V2_WORKER_FACTORY": "v2_host.production:build_worker_set",
        "V2_WORKER_INTERVAL_SECONDS": "${V2_WORKER_INTERVAL_SECONDS:-0.25}",
    }
    return api["environment"]


def test_compose_pins_image_and_runtime_identity() -> None:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for service in payload["services"].values():
        assert service["image"] == (
            "${V2_IMAGE_REF:?set V2_IMAGE_REF to an immutable repository@sha256 digest}"
        )
        assert service["user"] == "${V2_RUNTIME_UID:-1001}:${V2_RUNTIME_GID:-1001}"
        assert "${V2_STATE_DIR:?set V2_STATE_DIR}:/data" in service["volumes"]
        assert "${V2_HERMES_HOME_PATH:?set V2_HERMES_HOME_PATH}:/hermes" in service[
            "volumes"
        ]
        assert (
            "${V2_PUBLIC_AUTHORITY_MANIFEST_HOST_PATH:?set authority manifest path}:"
            "/run/v2/public-authority.json:ro"
        ) in service["volumes"]


def test_compose_pins_luna_tool_free_child_and_signed_authority() -> None:
    env = _environment()
    assert env["HERMES_HOME"] == "/hermes"
    assert env["HOME"] == "/hermes/home"
    assert env["V2_HERMES_MODEL"] == "openai-codex/gpt-5.6-luna"
    command = env["V2_HERMES_COMMAND_JSON"]
    for literal in (
        '"python"',
        '"-m"',
        '"v2_host.hermes_child"',
        '"hermes"',
        '"chat"',
        '"openai-codex/gpt-5.6-luna"',
        '"--provider"',
        '"openai-codex"',
        '"--safe-mode"',
        '"--source"',
        '"tool"',
    ):
        assert literal in command
    assert env["V2_PUBLIC_AUTHORITY_MANIFEST_PATH"] == (
        "/run/v2/public-authority.json"
    )
    assert env["V2_PUBLIC_AUTHORITY_HMAC_KEY_HEX"].startswith("${")
    assert env["V2_HERMES_TRANSCRIPT_KEY_HEX"].startswith("${")


def test_compose_has_all_independent_effect_gates_closed_by_default() -> None:
    env = _environment()
    assert {
        name: env[name]
        for name in (
            "V2_ENABLE_CLOUDBEDS_WRITES",
            "V2_ENABLE_BOKUN_WRITES",
            "V2_ENABLE_STRIPE_LINKS",
            "V2_ENABLE_MANYCHAT_DELIVERY",
            "V2_ENABLE_MANYCHAT_HANDOFF",
        )
    } == {
        "V2_ENABLE_CLOUDBEDS_WRITES": "${V2_ENABLE_CLOUDBEDS_WRITES:-false}",
        "V2_ENABLE_BOKUN_WRITES": "${V2_ENABLE_BOKUN_WRITES:-false}",
        "V2_ENABLE_STRIPE_LINKS": "${V2_ENABLE_STRIPE_LINKS:-false}",
        "V2_ENABLE_MANYCHAT_DELIVERY": "${V2_ENABLE_MANYCHAT_DELIVERY:-false}",
        "V2_ENABLE_MANYCHAT_HANDOFF": "${V2_ENABLE_MANYCHAT_HANDOFF:-false}",
    }
    assert env["V2_GLOBAL_KILL_SWITCH"] == "${V2_GLOBAL_KILL_SWITCH:-true}"
    assert env["V2_RUNTIME_MODE"] == "${V2_RUNTIME_MODE:-dark_read_only}"
    assert env["V2_ALLOWED_SUBSCRIBER_IDS"] == "1873018537"


def test_compose_exposes_complete_manychat_and_stripe_configuration() -> None:
    env = _environment()
    for name in (
        "V2_MANYCHAT_API_KEY",
        "V2_MANYCHAT_REPLY_FIELD_ID",
        "V2_MANYCHAT_REPLY_FLOW_NS",
        "V2_MANYCHAT_PAYMENT_LINK_FIELD_ID",
        "V2_MANYCHAT_PAYMENT_DESCRIPTION_FIELD_ID",
        "V2_MANYCHAT_PAYMENT_FLOW_NS",
        "V2_MANYCHAT_HANDOFF_TAG_ID",
        "V2_MANYCHAT_HANDOFF_FLOW_NS",
        "V2_STRIPE_HOSTEL_ACCOUNT_PROFILE_ID",
        "V2_STRIPE_AGENCY_ACCOUNT_PROFILE_ID",
        "V2_STRIPE_HOSTEL_SECRET_KEY",
        "V2_STRIPE_AGENCY_SECRET_KEY",
    ):
        assert name in env
        assert env[name].startswith("${")
    assert env["V2_STRIPE_ENVIRONMENT"] == "test"
