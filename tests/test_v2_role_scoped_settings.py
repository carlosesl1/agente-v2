from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest
import yaml

from v2_host.api_main import build_api_app
from v2_host.settings import V2ProcessRole, V2Settings


CANDIDATE_SHA = "a" * 40
CANDIDATE_DIGEST = "sha256:" + "b" * 64


def _identity(tmp_path: Path) -> dict[str, str]:
    return {
        "V2_SQLITE_PATH": str((tmp_path / "inbox.sqlite3").resolve()),
        "V2_RUNTIME_MODE": "controlled_write",
        "V2_ALLOWED_SUBSCRIBER_IDS": "1873018537",
        "V2_CANDIDATE_GIT_SHA": CANDIDATE_SHA,
        "V2_CANDIDATE_IMAGE_DIGEST": CANDIDATE_DIGEST,
        "V2_GLOBAL_KILL_SWITCH": "true",
        "V2_STRIPE_ENVIRONMENT": "test",
        "V2_PUBLIC_AUTHORITY_MANIFEST_PATH": str(
            (tmp_path / "authority.json").resolve()
        ),
        "V2_PUBLIC_AUTHORITY_HMAC_KEY_HEX": "22" * 32,
    }


def _worker_env(tmp_path: Path) -> dict[str, str]:
    return _identity(tmp_path) | {
        "V2_CLOUDBEDS_API_KEY": "cloudbeds-read-key",
        "V2_CLOUDBEDS_PROPERTY_ID": "property-1",
        "V2_BOKUN_ACCESS_KEY": "bokun-read-key",
        "V2_BOKUN_SECRET_KEY": "bokun-read-secret",
        "V2_BOKUN_PRODUCT_MAP_JSON": '{"product:buracao":"12345"}',
        "V2_READ_PROBE_CHECK_IN": "2099-08-01",
        "V2_READ_PROBE_CHECK_OUT": "2099-08-02",
        "V2_READ_PROBE_ACTIVITY_DATE": "2099-08-01",
        "V2_READ_PROBE_PRODUCT_ID": "product:buracao",
        "V2_MANYCHAT_API_KEY": "manychat-key",
        "V2_HERMES_COMMAND_JSON": '["python","hermes_child.py"]',
        "V2_HERMES_SYSTEM_PROMPT": "closed prompt",
        "V2_HERMES_TRANSCRIPT_KEY_HEX": "11" * 32,
        "V2_KNOWLEDGE_BASE_PATH": str((tmp_path / "knowledge.sqlite3").resolve()),
        "V2_HERMES_MODEL": "openai-codex/gpt-5.6-luna",
    }


def test_api_role_ignores_worker_only_environment_and_boots_without_financial_capability(
    tmp_path: Path,
) -> None:
    env = _identity(tmp_path) | {
        "V2_MANYCHAT_WEBHOOK_SECRET": "api-ingress-secret",
        "V2_CLOUDBEDS_API_KEY": "FORBIDDEN_WORKER_SECRET",
        "V2_CLOUDBEDS_PROPERTY_ID": "FORBIDDEN_PROPERTY",
        "V2_BOKUN_ACCESS_KEY": "FORBIDDEN_BOKUN_ACCESS",
        "V2_BOKUN_SECRET_KEY": "FORBIDDEN_BOKUN_SECRET",
        "V2_BOKUN_PRODUCT_MAP_JSON": '{"product:buracao":"FORBIDDEN_PRODUCT"}',
        "V2_MANYCHAT_API_KEY": "FORBIDDEN_MANYCHAT_API",
        "V2_HERMES_COMMAND_JSON": '["FORBIDDEN_MODEL_COMMAND"]',
        "V2_HERMES_SYSTEM_PROMPT": "FORBIDDEN_MODEL_PROMPT",
        "V2_HERMES_TRANSCRIPT_KEY_HEX": "33" * 32,
        "V2_STRIPE_HOSTEL_SECRET_KEY": "FORBIDDEN_STRIPE_HOSTEL",
        "V2_STRIPE_AGENCY_SECRET_KEY": "FORBIDDEN_STRIPE_AGENCY",

    }

    settings = V2Settings.from_env(env, process_role=V2ProcessRole.API)

    assert settings.process_role is V2ProcessRole.API
    assert settings.webhook_secret == "api-ingress-secret"
    assert settings.financial_webhooks_configured is False
    assert settings.read_providers_configured is False
    assert settings.cloudbeds_api_key == ""
    assert settings.bokun_access_key == ""
    assert settings.bokun_product_map == {}
    assert settings.manychat_api_key == ""
    assert settings.hermes_command == ()
    assert settings.hermes_system_prompt == ""
    assert settings.hermes_transcript_key == b""
    assert settings.stripe_hostel_secret_key == ""
    assert settings.stripe_agency_secret_key == ""
    assert settings.all_real_effect_gates_closed is True

    with TestClient(build_api_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "alive", "role": "api"}
        assert client.post("/webhook/payments/stripe", content=b"{}").status_code == 503


def test_worker_role_ignores_ingress_secrets_and_retains_worker_capabilities(
    tmp_path: Path,
) -> None:
    env = _worker_env(tmp_path) | {
        "V2_MANYCHAT_WEBHOOK_SECRET": "FORBIDDEN_INGRESS_SECRET",
        "V2_STRIPE_WEBHOOK_SECRET": "FORBIDDEN_FINANCIAL_SECRET",
        "V2_PIX_RECEIVER_PROFILE_ID": "FORBIDDEN_FINANCIAL_PROFILE",
    }

    settings = V2Settings.from_env(env, process_role=V2ProcessRole.WORKER)

    assert settings.process_role is V2ProcessRole.WORKER
    assert settings.webhook_secret == ""
    assert settings.stripe_webhook_secret == ""
    assert settings.pix_receiver_profile_id == ""
    assert settings.financial_webhooks_configured is False
    assert settings.read_providers_configured is True
    assert settings.hermes_model == "openai-codex/gpt-5.6-luna"
    assert settings.manychat_api_key == "manychat-key"


def test_worker_role_still_fails_closed_without_provider_credentials(
    tmp_path: Path,
) -> None:
    env = _worker_env(tmp_path)
    env.pop("V2_BOKUN_SECRET_KEY")

    with pytest.raises(ValueError, match="Bókun read credentials"):
        V2Settings.from_env(env, process_role=V2ProcessRole.WORKER)


def test_worker_role_still_fails_closed_without_model_credentials(
    tmp_path: Path,
) -> None:
    env = _worker_env(tmp_path)
    env.pop("V2_HERMES_COMMAND_JSON")

    with pytest.raises(ValueError, match="Hermes model command"):
        V2Settings.from_env(env, process_role=V2ProcessRole.WORKER)


def test_compose_api_environment_has_no_worker_or_financial_secrets() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "compose.v2.yaml").read_text(encoding="utf-8"))
    api_env = manifest["services"]["api"]["environment"]
    worker_env = manifest["services"]["worker"]["environment"]

    forbidden_api = {
        "V2_CLOUDBEDS_API_KEY",
        "V2_BOKUN_ACCESS_KEY",
        "V2_BOKUN_SECRET_KEY",
        "V2_MANYCHAT_API_KEY",
        "V2_STRIPE_HOSTEL_SECRET_KEY",
        "V2_STRIPE_AGENCY_SECRET_KEY",
        "V2_HERMES_TRANSCRIPT_KEY_HEX",
        "V2_STRIPE_WEBHOOK_SECRET",
        "V2_WISE_WEBHOOK_SECRET",
        "V2_PIX_WEBHOOK_SECRET",
    }
    forbidden_worker = {
        "V2_MANYCHAT_WEBHOOK_SECRET",
        "V2_STRIPE_WEBHOOK_SECRET",
        "V2_WISE_WEBHOOK_SECRET",
        "V2_PIX_WEBHOOK_SECRET",
    }

    assert forbidden_api.isdisjoint(api_env)
    assert forbidden_worker.isdisjoint(worker_env)
    assert api_env["V2_PROCESS_ROLE"] == "api"
    assert worker_env["V2_PROCESS_ROLE"] == "worker"
    assert "V2_MANYCHAT_WEBHOOK_SECRET" in api_env
    assert "V2_PUBLIC_AUTHORITY_HMAC_KEY_HEX" in api_env
    assert "V2_CLOUDBEDS_API_KEY" in worker_env
    assert "V2_HERMES_TRANSCRIPT_KEY_HEX" in worker_env


def test_process_role_environment_must_match_entrypoint_authority(tmp_path: Path) -> None:
    api_env = _identity(tmp_path) | {
        "V2_PROCESS_ROLE": "worker",
        "V2_MANYCHAT_WEBHOOK_SECRET": "api-ingress-secret",
    }

    try:
        V2Settings.from_env(api_env, process_role=V2ProcessRole.API)
    except ValueError as exc:
        assert str(exc) == "V2_PROCESS_ROLE does not match entrypoint process role"
    else:
        raise AssertionError("mismatched process role must fail closed")


def test_dockerfile_uses_canonical_source_repository() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile.v2").read_text(encoding="utf-8")

    assert (
        'org.opencontainers.image.source="https://github.com/carlosesl1/agente-v2"'
        in dockerfile
    )
    assert "github.com/nesquena/agente-v2" not in dockerfile
