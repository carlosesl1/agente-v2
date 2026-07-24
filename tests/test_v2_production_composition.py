from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from v2_host.composition import V2Container, V2Role
from v2_host.production import (
    ClosedCapabilityWorker,
    ReconciliationStage,
    build_read_service,
    build_worker_set,
)
from v2_host.settings import RuntimeMode, V2Settings
from v2_host.worker_main import WorkerQueue, _load_worker_factory


def _settings(tmp_path: Path, **overrides: object) -> V2Settings:
    values: dict[str, object] = {
        "webhook_secret": "m" * 32,
        "sqlite_path": tmp_path / "state" / "inbox.sqlite3",
        "stripe_webhook_secret": "s" * 32,
        "wise_webhook_secret": "w" * 32,
        "pix_webhook_secret": "p" * 32,
        "pix_receiver_profile_id": "pix-hostel",
        "wise_signer_profile_id": "wise-signer",
        "wise_account_profile_id": "wise-account",
        "stripe_account_profile_id": "stripe-account",
        "runtime_mode": RuntimeMode.DARK_READ_ONLY,
        "cloudbeds_api_key": "cloudbeds-secret",
        "cloudbeds_property_id": "property-1",
        "bokun_access_key": "bokun-access",
        "bokun_secret_key": "bokun-secret",
        "bokun_product_map": {"product:buracao": "913372"},
        "read_probe_check_in": "2026-08-05",
        "read_probe_check_out": "2026-08-06",
        "read_probe_activity_date": "2026-08-05",
        "read_probe_product_id": "product:buracao",
    }
    values.update(overrides)
    return V2Settings(**values)


def test_default_factory_is_productive_not_qualification() -> None:
    factory = _load_worker_factory("")

    assert factory is build_worker_set
    assert factory.__module__ == "v2_host.production"


def test_dark_read_only_factory_builds_closed_effect_graph_and_truthful_readiness(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        workers = build_worker_set(container=container, settings=settings)
        readiness = container.readiness()

        assert set(workers) == set(WorkerQueue)
        assert readiness.status == "ready"
        assert readiness.capabilities["cloudbeds_reads"] == "ready"
        assert readiness.capabilities["bokun_reads"] == "ready"
        assert readiness.capabilities["hermes_model"] == "closed"
        assert readiness.capabilities["manychat_profile"] == "closed"
        for queue in (
            WorkerQueue.INBOX,
            WorkerQueue.RESERVATION,
            WorkerQueue.PAYMENT_INITIATION,
            WorkerQueue.SETTLEMENT,
            WorkerQueue.POST_PAYMENT,
            WorkerQueue.PUBLIC_DELIVERY,
        ):
            assert type(workers[queue]) is ClosedCapabilityWorker
        assert type(workers[WorkerQueue.RECONCILIATION]).__name__ == "ReconciliationStage"
        assert settings.all_real_effect_gates_closed is True
    finally:
        container.close()


def test_read_service_is_constructed_from_direct_provider_transports(tmp_path: Path) -> None:
    reads = build_read_service(_settings(tmp_path))

    assert type(reads).__name__ == "V2ReadService"
    assert "CloudbedsHTTPTransport" in repr(reads)
    assert "BokunHTTPTransport" in repr(reads)


def test_shadow_mode_fails_closed_without_model_profile_and_authority(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="shadow runtime requires"):
        _settings(tmp_path, runtime_mode=RuntimeMode.SHADOW)


def test_worker_readiness_is_not_ready_before_productive_graph_registration(
    tmp_path: Path,
) -> None:
    container = V2Container.open(settings=_settings(tmp_path), role=V2Role.WORKER)
    try:
        snapshot = container.readiness()
        assert snapshot.status == "not_ready"
        assert "productive_graph_not_built" in snapshot.reasons
    finally:
        container.close()


def test_closed_capability_worker_never_claims_or_fences() -> None:
    worker = ClosedCapabilityWorker("stripe_links")

    result = worker.run_once(now=datetime.now(timezone.utc))

    assert result == {"status": "closed", "capability": "stripe_links"}


class _ProbeReads:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("read probe failed")
        return object()

    def accept(self, observation, *, now):
        return observation


def test_read_probe_failure_remains_degraded_until_next_real_probe(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        reads = _ProbeReads(fail=True)
        stage = ReconciliationStage(container=container, reads=reads, settings=settings)
        now = datetime.now(timezone.utc)

        with pytest.raises(RuntimeError, match="read probe failed"):
            stage.run_once(now=now)
        with pytest.raises(RuntimeError, match="degraded"):
            stage.run_once(now=now)
        assert len(reads.requests) == 1
    finally:
        container.close()


def test_shadow_factory_mounts_real_model_profile_reads_and_inbox_worker(
    tmp_path: Path,
) -> None:
    key = b"authenticated-authority-key-0000001"
    manifest = tmp_path / "authority.json"
    authority = {
        "authorization_id": "authority:shadow-1",
        "subscriber_id": "1873018537",
        "target_binding_hash": "1" * 64,
        "channel_id": "manychat:channel-shadow",
        "channel_scope": "manychat:subscriber-1873018537",
        "generation": 1,
        "capability_policy_digest": "2" * 64,
        "effect_authorization_binding_digest": "3" * 64,
        "contract_digest": "4" * 64,
        "deadline_at": "2099-01-01T00:00:00+00:00",
        "allocations": [
            {"allocation_id": "allocation:shadow-0", "ordinal": 0}
        ],
    }
    signed = {
        "schema": "v2-public-authority-manifest-v1",
        "authorities": [authority],
    }
    canonical = json.dumps(
        signed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    signed["hmac_sha256"] = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    manifest.write_text(json.dumps(signed), encoding="utf-8")
    knowledge = tmp_path / "cerebro.yaml"
    knowledge.write_text(
        "entries:\n  - id: faq-1\n    topic: geral\n    question: Oi?\n    answer: Olá.\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        runtime_mode=RuntimeMode.SHADOW,
        manychat_api_key="manychat-secret",
        hermes_command=("python", "-m", "v2_host.hermes_child", "hermes"),
        hermes_system_prompt="Return the exact V2 proposal contract.",
        hermes_transcript_key=b"transcript-key-for-shadow-test-0001",
        knowledge_base_path=knowledge,
        public_authority_manifest_path=manifest,
        public_authority_hmac_key=key,
    )
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        workers = build_worker_set(container=container, settings=settings)

        assert type(workers[WorkerQueue.INBOX]).__name__ == "InboxTurnWorker"
        assert container.readiness().status == "ready"
        assert container.readiness().capabilities["hermes_model"] == "ready"
        assert container.readiness().capabilities["manychat_profile"] == "ready"
        assert container.readiness().capabilities["knowledge_reads"] == "ready"
        assert container.readiness().capabilities["manychat_delivery"] == "closed"
    finally:
        container.close()
