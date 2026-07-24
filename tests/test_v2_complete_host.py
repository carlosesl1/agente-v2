from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient
import yaml

from reservation_followup import PaymentMethod, to_wire_json
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from tests.test_phase6_payment import stripe_event
from tests.test_phase6_payment_claims import prepare_payment
from v2_host.api_main import build_api_app
from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2Settings
from v2_host.worker_main import WorkerQueue, build_worker_cycle

NOW = datetime(2027, 2, 1, 12, 0, 5, tzinfo=timezone.utc)


class ConcreteStage:
    def run_once(self, *, now):
        return ("idle", now)


def _settings(tmp_path: Path) -> V2Settings:
    return V2Settings(
        webhook_secret="manychat-secret",
        stripe_webhook_secret="stripe-secret",
        wise_webhook_secret="wise-secret",
        pix_webhook_secret="pix-secret",
        sqlite_path=tmp_path / "inbox.sqlite3",
        pix_receiver_profile_id="receiver:profile:synthetic:1",
        wise_signer_profile_id="wise-signer:profile:synthetic:1",
        wise_account_profile_id="wise-account:profile:synthetic:1",
        stripe_account_profile_id="stripe-account:profile:synthetic:1",
    )


def _signed_body(event) -> tuple[bytes, str]:
    body = json.dumps(
        {
            "provider": "stripe",
            "external_event_id": event.evidence.event_id,
            "payment_id": event.payment_id,
            "expected_revision": 3,
            "event_wire": to_wire_json(event),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = "sha256=" + hmac.new(b"stripe-secret", body, hashlib.sha256).hexdigest()
    return body, signature


def test_financial_webhook_verifies_before_persist_and_replays(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    followup = SQLiteFollowupUnitOfWork.open(settings.sqlite_paths["followup"])
    state, event = prepare_payment(
        followup,
        suffix="v2-host-stripe",
        method=PaymentMethod.STRIPE,
        evidence=stripe_event(event_id="evt_v2hoststripe001secure"),
    )
    followup.close()
    body, signature = _signed_body(event)

    with TestClient(build_api_app(settings, clock=lambda: NOW)) as client:
        unauthorized = client.post(
            "/webhook/payments/stripe",
            content=body,
            headers={"content-type": "application/json"},
        )
        first = client.post(
            "/webhook/payments/stripe",
            content=body,
            headers={
                "content-type": "application/json",
                "X-V2-Stripe-Signature": signature,
            },
        )
        replay = client.post(
            "/webhook/payments/stripe",
            content=body,
            headers={
                "content-type": "application/json",
                "X-V2-Stripe-Signature": signature,
            },
        )

    assert unauthorized.status_code == 401
    assert first.status_code == 202
    assert first.json()["status"] == "accepted"
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"
    reopened = SQLiteFollowupUnitOfWork.open(settings.sqlite_paths["followup"])
    try:
        assert reopened._connection.execute(
            "SELECT count(*) FROM payment_evidence_claims"
        ).fetchone() == (1,)
        loaded = reopened.load_payment(state.subject.payment_id)
        assert loaded.evidence_record == event
        assert loaded.verified_evidence is not None
    finally:
        reopened.close()


def test_api_and_worker_roles_have_least_privilege_and_concrete_readiness(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    api = V2Container.open(settings=settings, role=V2Role.API)
    worker = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        assert api.owner_counts() == {
            "boundary": 0,
            "execution": 0,
            "followup": 0,
            "inbox": 1,
            "payment_initiation": 0,
            "public_outbox": 0,
        }
        assert worker.owner_counts() == {
            "boundary": 1,
            "execution": 1,
            "followup": 1,
            "inbox": 1,
            "payment_initiation": 1,
            "public_outbox": 1,
        }
        assert api.readiness().status == "ready"
        assert worker.readiness().status == "not_ready"
        assert worker.readiness().reasons == ("productive_graph_not_built",)
        assert settings.financial_webhooks_configured is True
        assert settings.all_real_effect_gates_closed is True
        worker.register_runtime_capabilities({"test_graph": "ready"})
        assert worker.readiness().status == "ready"
        cycle = build_worker_cycle(
            worker,
            {queue: ConcreteStage() for queue in WorkerQueue},
        )
        assert tuple(cycle.workers) == tuple(WorkerQueue)
        assert all(
            type(stage).__name__ not in {"NoopWorker", "FallbackWorker"}
            for stage in cycle.workers.values()
        )
    finally:
        worker.close()
        api.close()


def test_compose_uses_one_hardened_image_for_distinct_api_and_worker_roles() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "compose.v2.yaml").read_text())
    api = manifest["services"]["api"]
    worker = manifest["services"]["worker"]
    assert api["image"] == worker["image"]
    assert "V2_IMAGE_REF" in api["image"]
    assert "sha256" in api["image"]
    assert "build" not in api
    assert worker["entrypoint"] == ["python", "-m", "v2_host.worker_main"]
    assert api["read_only"] is worker["read_only"] is True
    assert api["cap_drop"] == worker["cap_drop"] == ["ALL"]
    assert api["security_opt"] == worker["security_opt"] == ["no-new-privileges:true"]
    assert "V2_WORKER_FACTORY" in worker["environment"]
    assert worker["environment"]["V2_WORKER_FACTORY"] == "v2_host.production:build_worker_set"
    assert manifest["services"].keys() == {"api", "worker"}


def test_runtime_image_excludes_test_tooling_and_carries_oci_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile.v2").read_text()
    ignore = (root / ".dockerignore").read_text().splitlines()

    assert '"hermes-agent==0.19.0"' in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "org.opencontainers.image.created" in dockerfile
    assert "pytest" not in dockerfile.casefold()
    assert "COPY . /app" not in dockerfile
    assert "tests" in ignore
    assert "docs" in ignore
    assert ".git" in ignore
