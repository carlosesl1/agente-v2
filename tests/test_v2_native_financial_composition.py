from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tests.test_v2_production_composition import _audit_enabled_settings
from v2_host.composition import V2Container, V2Role
from v2_host.production import build_worker_set
from v2_host.worker_main import WorkerQueue


def test_stripe_flag_mounts_real_settlement_and_completion_observers(tmp_path):
    settings = replace(
        _audit_enabled_settings(tmp_path),
        bokun_writes_enabled=True,
        stripe_links_enabled=True,
        stripe_settlement_enabled=True,
        stripe_hostel_account_profile_id="stripe-account:hostel:test",
        stripe_agency_account_profile_id="stripe-account:agency:test",
        stripe_hostel_secret_key="sk_test_hostel_fixture",
        stripe_agency_secret_key="sk_test_agency_fixture",
        payment_result_store_key=b"x" * 32,
        manychat_delivery_enabled=True,
        manychat_handoff_enabled=True,
        manychat_reply_field_id=1,
        manychat_reply_flow_ns="content-fixture",
        manychat_handoff_tag_id=2,
    )
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        workers = build_worker_set(container=container, settings=settings)
        assert (
            workers[WorkerQueue.SETTLEMENT].worker._settlement.settlement_id
            == "v2:stripe-provider-settlement"
        )
        assert workers[WorkerQueue.SETTLEMENT].worker._clock is not None
        assert (
            workers[WorkerQueue.SETTLEMENT]
            .run_once(now=datetime.now(UTC))
            .disposition.value
            == "idle"
        )
        assert (
            workers[WorkerQueue.POST_PAYMENT].effects._delivery.delivery_id
            == "v2:stripe-payment-effect-observer"
        )
    finally:
        container.close()


def test_closed_financial_gates_still_observe_existing_local_receipts(tmp_path):
    settings = _audit_enabled_settings(tmp_path)
    assert settings.stripe_settlement_enabled is False
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        workers = build_worker_set(container=container, settings=settings)
        observer = workers[WorkerQueue.POST_PAYMENT].effects
        assert observer._delivery.observation_only is True
        assert observer._clock is not None
        assert observer.run_once(now=datetime.now(UTC)).disposition.value == "idle"
        assert not hasattr(workers[WorkerQueue.SETTLEMENT], "worker")
    finally:
        container.close()


def test_stripe_settlement_cannot_open_without_provider_and_handoff_gates(tmp_path):
    with pytest.raises(ValueError, match="Stripe settlement requires"):
        replace(_audit_enabled_settings(tmp_path), stripe_settlement_enabled=True)
