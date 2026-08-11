from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.v2.yaml"
PROMPT = ROOT / "config/v2_luna_system_prompt.txt"
WORKFLOW = ROOT / ".github/workflows/phase8.yml"


def _environment() -> dict[str, object]:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert type(payload) is dict
    services = payload["services"]
    assert set(services) == {"router", "api", "worker"}
    api = services["api"]
    worker = services["worker"]
    api_environment = api["environment"]
    worker_environment = worker["environment"]
    assert api_environment["V2_PROCESS_ROLE"] == "api"
    assert worker_environment["V2_PROCESS_ROLE"] == "worker"
    for name in (
        "V2_RUNTIME_MODE",
        "V2_CANDIDATE_GIT_SHA",
        "V2_CANDIDATE_IMAGE_DIGEST",
        "V2_ALLOWED_SUBSCRIBER_IDS",
        "V2_GLOBAL_KILL_SWITCH",
        "V2_PUBLIC_AUTHORITY_HMAC_KEY_HEX",
    ):
        assert api_environment[name] == worker_environment[name]
    assert "V2_MANYCHAT_WEBHOOK_SECRET" not in worker_environment
    assert "V2_CLOUDBEDS_API_KEY" not in api_environment
    return worker_environment


def test_compose_pins_image_and_runtime_identity() -> None:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for service in payload["services"].values():
        assert service["image"] == (
            "${V2_IMAGE_REF:?set V2_IMAGE_REF to an immutable repository@sha256 digest}"
        )
        assert service["user"] == "${V2_RUNTIME_UID:-1001}:${V2_RUNTIME_GID:-1001}"
    router = payload["services"]["router"]
    api = payload["services"]["api"]
    worker = payload["services"]["worker"]
    assert router["ports"] == ["127.0.0.1:${V2_BIND_PORT:-18090}:8080"]
    assert "ports" not in api
    assert "ports" not in worker
    assert set(router["networks"]) == {"v2-edge", "v2-backend"}
    assert api["networks"] == ["v2-backend"]
    assert worker["networks"] == ["v2-backend"]
    assert set(payload["networks"]) == {"v2-edge", "v2-backend"}
    for service in (api, worker):
        targets = {volume["target"] for volume in service["volumes"]}
        assert {"/data", "/run/v2/public-authority.json"} <= targets
    worker_targets = {volume["target"] for volume in worker["volumes"]}
    api_targets = {volume["target"] for volume in api["volumes"]}
    assert "/hermes" not in api_targets
    assert "/hermes" in worker_targets


def test_router_composition_binds_immutable_identity_before_forwarding() -> None:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    router = payload["services"]["router"]
    assert router["environment"] == {
        "CANARY_WEBHOOK_SECRET": "${V2_MANYCHAT_WEBHOOK_SECRET:?set V2_MANYCHAT_WEBHOOK_SECRET}",
        "CANARY_SUBSCRIBER_ID": "1873018537",
        "CANARY_V2_URL": "http://api:8080/webhook/manychat",
        "CANARY_V2_READY_URL": "http://api:8080/readyz",
        "CANARY_LEGACY_URL": "${V2_LEGACY_URL:?set private legacy webhook URL}",
        "CANARY_CUTOVER_DEADLINE": "${V2_CANARY_CUTOVER_DEADLINE:-}",
        "CANARY_EXPECTED_GIT_SHA": "${V2_CANDIDATE_GIT_SHA:?set frozen candidate git sha}",
        "CANARY_EXPECTED_IMAGE_REF": "${V2_IMAGE_REF:?set V2_IMAGE_REF to an immutable repository@sha256 digest}",
        "CANARY_EXPECTED_IMAGE_DIGEST": "${V2_CANDIDATE_IMAGE_DIGEST:?set immutable image digest}",
        "CANARY_RUNTIME_IDENTITY_METADATA_PATH": "/run/v2/runtime-identity.json",
        "CANARY_MAX_BODY_BYTES": "${V2_MAX_WEBHOOK_BODY_BYTES:-65536}",
    }
    assert router["depends_on"] == {"api": {"condition": "service_healthy"}}
    assert {volume["target"] for volume in router["volumes"]} == {
        "/run/v2/runtime-identity.json"
    }


def test_ci_contract_covers_hermetic_operational_gates() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for literal in (
        "maya-v2-gap-remediation",
        "docker compose --env-file \"$COMPOSE_TMP/compose.env\" -f compose.v2.yaml config --quiet",
        "docker build",
        "--network none",
        "--read-only",
        "TestClient(create_app_from_env())",
        "CANARY_RUNTIME_IDENTITY_METADATA_PATH=/run/v2/runtime-identity.json",
        "chmod 0444 \"$IDENTITY_FILE\"",
        "tests/test_v2_canary_router.py",
        "tests/test_v2_runtime_identity.py",
        "tests/test_v2_worker_main.py",
        "tests/test_v2_stripe_reconciliation.py",
        "tests/test_v2_rollback_harness.py",
    ):
        assert literal in workflow
    assert "/home/ubuntu/workspace" not in workflow
    assert "agente-v2-deploy" not in workflow


def test_compose_pins_luna_tool_free_child_and_signed_authority() -> None:
    env = _environment()
    assert env["HERMES_HOME"] == "/hermes"
    assert env["HOME"] == "/hermes/home"
    assert env["V2_HERMES_MODEL"] == "openai-codex/gpt-5.6-luna"
    assert env["V2_HERMES_SYSTEM_PROMPT_PATH"] == (
        "/app/config/v2_luna_system_prompt.txt"
    )
    command = json.loads(env["V2_HERMES_COMMAND_JSON"])
    assert command == [
        "python",
        "-m",
        "v2_host.hermes_child",
        "hermes",
        "--profile",
        "leads",
        "-m",
        "gpt-5.6-luna",
        "--provider",
        "openai-codex",
        "--safe-mode",
    ]
    assert "chat" not in command
    assert "-Q" not in command
    assert "--source" not in command
    assert env["V2_PUBLIC_AUTHORITY_MANIFEST_PATH"] == (
        "/run/v2/public-authority.json"
    )
    assert env["V2_PUBLIC_AUTHORITY_HMAC_KEY_HEX"].startswith("${")
    assert env["V2_HERMES_TRANSCRIPT_KEY_HEX"].startswith("${")


def test_phase8_child_bootstraps_candidate_root_before_structured_contract_import() -> None:
    source = (ROOT / "scripts/phase8_hermes_child.py").read_text(encoding="utf-8")
    assert source.index("sys.path.insert") < source.index(
        "from v2_host.structured_output import"
    )


def test_versioned_luna_prompt_closes_model_grammar_and_business_effects() -> None:
    prompt = PROMPT.read_text(encoding="utf-8")
    for literal in (
        "oito campos conversacionais",
        "selected_choice_refs",
        "pending_action_disposition",
        "pending_action",
        "passengers",
        "progress_review_required",
        "product:buracao",
        "Nunca exponha choice_ref",
        "Não invente valor",
        "Não execute reserva",
        "age_guidance=null",
    ):
        assert literal in prompt
    for legacy_output_field in (
        "v2-model-proposal-v7",
        "v2-model-proposal-v6",
        "v2-model-proposal-v5",
        "v2-model-proposal-v4",
        "v2-model-proposal-v3",
        "v2-model-proposal-v2",
        "target_offer_id",
        "target_offer_ids",
        "confirmed_summary_version",
        "confirmed_action_kinds",
        "approval_basis",
        "pending_disposition",
        "contextual_reference",
        "clarification_question",
        "effect_proposals",
    ):
        assert legacy_output_field not in prompt
    assert "source_event_id" in prompt
    assert "A mensagem atual e o fato `language` em state_facts vencem" not in prompt
    assert (
        "O locale do request, derivado do telefone ManyChat autenticado, é autoritativo"
        in prompt
    )
    assert "v2-model-proposal-v4" not in prompt
    assert "v2-model-proposal-v3" not in prompt
    assert "v2-model-proposal-v2" not in prompt
    dockerfile = (ROOT / "Dockerfile.v2").read_text(encoding="utf-8")
    assert "COPY config/v2_luna_system_prompt.txt" in dockerfile


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
