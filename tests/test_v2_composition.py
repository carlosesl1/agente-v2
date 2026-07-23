from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from v2_host.api_main import build_api_app
from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2Settings


ACK = "ENABLE_V2_REAL_EFFECTS_FOR_CONTROLLED_TEST"


def _settings(tmp_path: Path) -> V2Settings:
    return V2Settings(
        webhook_secret="test-webhook-secret",
        sqlite_path=tmp_path / "inbox.sqlite3",
        stripe_webhook_secret="stripe-test-secret",
        wise_webhook_secret="wise-test-secret",
        pix_webhook_secret="pix-test-secret",
        pix_receiver_profile_id="receiver:pix-test",
        wise_signer_profile_id="signer:wise-test",
        wise_account_profile_id="account:wise-test",
        stripe_account_profile_id="account:stripe-test",
    )


def test_settings_default_every_real_effect_gate_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert settings.real_effect_gates == {
        "bokun_writes": False,
        "cloudbeds_writes": False,
        "manychat_delivery": False,
        "stripe_links": False,
    }
    assert settings.all_real_effect_gates_closed is True
    assert len(set(settings.sqlite_paths.values())) == len(settings.sqlite_paths)
    assert settings.sqlite_paths["inbox"] == tmp_path / "inbox.sqlite3"


def test_enabling_real_effect_requires_exact_operational_ack(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="operational acknowledgment"):
        V2Settings(
            webhook_secret="test-webhook-secret",
            sqlite_path=tmp_path / "inbox.sqlite3",
            cloudbeds_writes_enabled=True,
        )

    settings = V2Settings(
        webhook_secret="test-webhook-secret",
        sqlite_path=tmp_path / "inbox.sqlite3",
        cloudbeds_writes_enabled=True,
        real_effects_ack=ACK,
    )
    assert settings.real_effect_gates["cloudbeds_writes"] is True
    assert settings.all_real_effect_gates_closed is False


def test_container_opens_exactly_one_owner_per_store_and_closes_cleanly(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        assert container.owner_counts() == {
            "boundary": 1,
            "execution": 1,
            "followup": 1,
            "inbox": 1,
            "payment_initiation": 1,
            "public_outbox": 1,
        }
        assert container.settings is settings
        assert container.role is V2Role.WORKER
        assert all(path.is_file() for path in settings.sqlite_paths.values())
    finally:
        container.close()

    container.close()


def test_api_role_exposes_health_and_readiness_without_opening_gates(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    with TestClient(build_api_app(settings)) as client:
        health = client.get("/healthz")
        readiness = client.get("/readyz")

        assert health.status_code == 200
        assert health.json() == {"status": "alive", "role": "api"}
        assert readiness.status_code == 200
        assert readiness.json() == {
            "status": "ready",
            "role": "api",
            "owner_counts": client.app.state.v2_container.owner_counts(),
            "real_effect_gates": settings.real_effect_gates,
        }
        assert client.app.state.v2_container.role is V2Role.API
