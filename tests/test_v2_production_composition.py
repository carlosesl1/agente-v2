from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from reservation_domain import ExecutionCertainty, ReservationOperation, dumps_command
from reservation_execution import DispatchRequest
from v2_application.critical_actions import CriticalActionDisposition
from v2_application.cloudbeds_audit import SQLiteCloudbedsAuditStore
from v2_contracts.critical_actions import CriticalActionKind
from v2_contracts.providers import ReadKind
from reservation_followup.workers import HandoffOutboxWorker
from v2_host.composition import V2Container, V2Role
from v2_host.production import (
    _critical_action_policy,
    ClosedCapabilityWorker,
    ReconciliationStage,
    build_read_service,
    build_worker_set,
)
from v2_host.settings import RuntimeMode, V2Settings
from v2_host.worker_main import WorkerFailureReason, WorkerQueue, _load_worker_factory
from v2_application.payments import PaymentInitiationWorker
from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
from v2_application.outcome_projector import ReservationOutcomeProjector
from v2_application.completion_projector import CompletionProjector
from v2_application.public_delivery import CombinedPublicDeliveryWorker
from v2_application.relay_worker import BoundaryRelayWorker, RelayWorkerDisposition
from v2_application.workers import V2ReservationWorker
import v2_host.production as production
from tests.phase5_helpers import T0, persist_script, workflow_events

REAL_EFFECTS_ACK = "ENABLE_V2_REAL_EFFECTS_FOR_CONTROLLED_TEST"


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
        "cloudbeds_source_id": "source-1",
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


def test_critical_action_policy_is_derived_only_from_effect_gates(
    tmp_path: Path,
) -> None:
    closed = _settings(tmp_path)
    policy = _critical_action_policy(closed)
    assert all(
        policy.classify(kind) is CriticalActionDisposition.DENY
        for kind in CriticalActionKind
    )

    base = replace(
        closed,
        runtime_mode=RuntimeMode.CONTROLLED_WRITE,
        allowed_subscriber_ids=("1873018537",),
        hermes_model="openai-codex/gpt-5.6-luna",
        candidate_git_sha="a" * 40,
        candidate_image_digest="sha256:" + "b" * 64,
        manychat_api_key="manychat-secret",
        hermes_command=("python", "hermes_child.py", "hermes"),
        hermes_system_prompt="closed prompt",
        hermes_transcript_key=b"critical-policy-test-key-00000001",
        knowledge_base_path=(tmp_path / "knowledge.sqlite3").resolve(),
        public_authority_manifest_path=(tmp_path / "authority.json").resolve(),
        public_authority_hmac_key=b"critical-authority-key-000000001",
    )
    common = {
        "real_effects_ack": REAL_EFFECTS_ACK,
        "global_kill_switch_engaged": False,
        "write_window_end": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    lodging = _critical_action_policy(
        replace(base, cloudbeds_writes_enabled=True, **common)
    )
    assert (
        lodging.classify(CriticalActionKind.RESERVE_LODGING, now=datetime.now(timezone.utc))
        is CriticalActionDisposition.ASK
    )
    assert (
        lodging.classify(CriticalActionKind.BOOK_ACTIVITY, now=datetime.now(timezone.utc))
        is CriticalActionDisposition.DENY
    )

    activity = _critical_action_policy(
        replace(base, bokun_writes_enabled=True, **common)
    )
    assert (
        activity.classify(CriticalActionKind.BOOK_ACTIVITY, now=datetime.now(timezone.utc))
        is CriticalActionDisposition.ASK
    )
    assert activity.valid_until is not None
    assert activity.activity_participant_limit == 6
    assert (
        activity.classify(
            CriticalActionKind.BOOK_ACTIVITY,
            now=activity.valid_until,
        )
        is CriticalActionDisposition.DENY
    )

    package = _critical_action_policy(
        replace(
            base,
            cloudbeds_writes_enabled=True,
            bokun_writes_enabled=True,
            **common,
        )
    )
    assert (
        package.classify(CriticalActionKind.BOOK_PACKAGE, now=datetime.now(timezone.utc))
        is CriticalActionDisposition.ASK
    )

    payment = _critical_action_policy(
        replace(
            base,
            stripe_links_enabled=True,
            stripe_hostel_account_profile_id="stripe-account:hostel:test",
            stripe_agency_account_profile_id="stripe-account:agency:test",
            stripe_hostel_secret_key="rk_" + "test_scoped_hostel",
            stripe_agency_secret_key="rk_" + "test_scoped_agency",
            payment_result_store_key=b"p" * 32,
            **common,
        )
    )
    assert (
        payment.classify(
            CriticalActionKind.INITIATE_PAYMENT,
            payment_method="stripe",
            now=datetime.now(timezone.utc),
        )
        is CriticalActionDisposition.ASK
    )
    assert (
        payment.classify(
            CriticalActionKind.INITIATE_PAYMENT,
            payment_method="wise",
            now=datetime.now(timezone.utc),
        )
        is CriticalActionDisposition.DENY
    )
    for unsupported in (
        CriticalActionKind.MODIFY_RESERVATION,
        CriticalActionKind.CANCEL_RESERVATION,
        CriticalActionKind.CHARGE_OR_CAPTURE,
        CriticalActionKind.REFUND,
    ):
        assert payment.classify(unsupported) is CriticalActionDisposition.DENY


def test_dark_read_only_factory_builds_closed_effect_graph_and_truthful_readiness(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    private_customer = container.private_customer
    try:
        workers = build_worker_set(container=container, settings=settings)
        readiness = container.readiness()

        assert type(private_customer) is SQLitePrivateCustomerFactStore
        assert private_customer.path == settings.sqlite_paths["private_customer"]
        assert readiness.owner_counts["private_customer"] == 1
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
        assert type(workers[WorkerQueue.BOUNDARY_RELAY]) is BoundaryRelayWorker
        assert (
            workers[WorkerQueue.BOUNDARY_RELAY]
            .run_once(now=datetime.now(timezone.utc))
            .disposition
            is RelayWorkerDisposition.IDLE
        )
        assert type(workers[WorkerQueue.RECONCILIATION]).__name__ == "ReconciliationStage"
        assert settings.all_real_effect_gates_closed is True
    finally:
        container.close()
    with pytest.raises(RuntimeError, match="closed"):
        private_customer.load("manychat:closed-private-customer")


def test_read_service_is_constructed_from_direct_provider_transports(tmp_path: Path) -> None:
    reads = build_read_service(_settings(tmp_path))

    assert type(reads).__name__ == "V2ReadService"
    assert "CloudbedsHTTPTransport" in repr(reads)
    assert "BokunHTTPTransport" in repr(reads)
    assert reads._ports[ReadKind.ACTIVITY]._transport._quote_checkout_enabled is False


def test_controlled_write_idle_mounts_inbox_and_boundary_relay_with_effects_closed(
    tmp_path: Path,
) -> None:
    key = b"authenticated-authority-key-0000001"
    manifest = tmp_path / "authority-controlled.json"
    authority = {
        "authorization_id": "authority:controlled-idle",
        "subscriber_id": "1873018537",
        "target_binding_hash": "1" * 64,
        "channel_id": "manychat:channel-controlled",
        "channel_scope": "manychat:subscriber-1873018537",
        "generation": 1,
        "capability_policy_digest": "2" * 64,
        "effect_authorization_binding_digest": "3" * 64,
        "contract_digest": "4" * 64,
        "deadline_at": "2099-01-01T00:00:00+00:00",
        "allocations": [
            {"allocation_id": "allocation:controlled-0", "ordinal": 0},
            {"allocation_id": "allocation:controlled-1", "ordinal": 1},
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
    knowledge = tmp_path / "cerebro-controlled.yaml"
    knowledge.write_text(
        "entries:\n  - id: faq-1\n    topic: geral\n    question: Oi?\n    answer: Olá.\n",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        runtime_mode=RuntimeMode.CONTROLLED_WRITE,
        allowed_subscriber_ids=("1873018537",),
        hermes_model="openai-codex/gpt-5.6-luna",
        candidate_git_sha="a" * 40,
        candidate_image_digest="sha256:" + "b" * 64,
        manychat_api_key="manychat-secret",
        hermes_command=("python", "-m", "v2_host.hermes_child", "hermes"),
        hermes_system_prompt="Return the exact V2 proposal contract.",
        hermes_transcript_key=b"transcript-key-for-controlled-test-01",
        public_authority_manifest_path=manifest,
        public_authority_hmac_key=key,
        knowledge_base_path=knowledge,
    )
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        workers = build_worker_set(container=container, settings=settings)

        assert type(workers[WorkerQueue.INBOX]).__name__ == "InboxTurnWorker"
        turn_budget = timedelta(
            seconds=(settings.hermes_timeout_seconds * 6) + 30
        )
        assert workers[WorkerQueue.INBOX]._executor._turn_timeout == turn_budget
        assert workers[WorkerQueue.INBOX]._lease_ttl == turn_budget + timedelta(
            seconds=15
        )
        assert type(workers[WorkerQueue.BOUNDARY_RELAY]) is BoundaryRelayWorker
        assert type(workers[WorkerQueue.RESERVATION]) is ClosedCapabilityWorker
        assert workers[WorkerQueue.RECONCILIATION]._manual_handoff is not None
        assert settings.all_real_effect_gates_closed is True
        assert container.public_turn_capacity(
            now=datetime(2026, 8, 1, tzinfo=timezone.utc)
        ) == 1
        assert (
            container.controlled_public_ingress_reason(
                now=datetime(2026, 8, 1, tzinfo=timezone.utc)
            )
            is None
        )
        readiness = container.readiness()
        assert readiness.status == "ready"
        assert readiness.capabilities["controlled_public_ingress"] == "ready"
        assert readiness.capabilities["manychat_delivery"] == "closed"
    finally:
        container.close()

    enabled = replace(
        settings,
        cloudbeds_writes_enabled=True,
        real_effects_ack=REAL_EFFECTS_ACK,
        global_kill_switch_engaged=False,
        write_window_end=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    enabled_container = V2Container.open(settings=enabled, role=V2Role.WORKER)
    try:
        enabled_workers = build_worker_set(
            container=enabled_container,
            settings=enabled,
        )
        assert type(enabled_workers[WorkerQueue.RESERVATION]) is V2ReservationWorker
        assert (
            enabled_container.readiness().capabilities["reservation_writes"]
            == "ready"
        )
        assert enabled.real_effect_gates == {
            "cloudbeds_writes": True,
            "bokun_writes": False,
            "stripe_links": False,
            "wise_instructions": False,
            "pix_instructions": False,
            "manychat_delivery": False,
            "manychat_handoff": False,
        }
    finally:
        enabled_container.close()

    bokun_enabled = replace(
        settings,
        bokun_writes_enabled=True,
        real_effects_ack=REAL_EFFECTS_ACK,
        global_kill_switch_engaged=False,
        write_window_end=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    bokun_container = V2Container.open(
        settings=bokun_enabled,
        role=V2Role.WORKER,
    )
    try:
        bokun_workers = build_worker_set(
            container=bokun_container,
            settings=bokun_enabled,
        )
        reservation = bokun_workers[WorkerQueue.RESERVATION]
        assert type(reservation) is V2ReservationWorker
        assert set(reservation._worker._adapter._adapters) == {
            ReservationOperation.BOOK_ACTIVITY
        }
        assert bokun_container.readiness().capabilities["reservation_writes"] == "ready"
    finally:
        bokun_container.close()

    both_enabled = replace(bokun_enabled, cloudbeds_writes_enabled=True)
    both_container = V2Container.open(settings=both_enabled, role=V2Role.WORKER)
    try:
        both_workers = build_worker_set(
            container=both_container,
            settings=both_enabled,
        )
        reservation = both_workers[WorkerQueue.RESERVATION]
        assert type(reservation) is V2ReservationWorker
        assert set(reservation._worker._adapter._adapters) == {
            ReservationOperation.RESERVE_LODGING,
            ReservationOperation.BOOK_ACTIVITY,
        }
    finally:
        both_container.close()

    stripe_enabled = replace(
        settings,
        stripe_links_enabled=True,
        real_effects_ack=REAL_EFFECTS_ACK,
        global_kill_switch_engaged=False,
        write_window_end=datetime.now(timezone.utc) + timedelta(hours=1),
        stripe_hostel_account_profile_id="stripe-account:hostel:test",
        stripe_agency_account_profile_id="stripe-account:agency:test",
        stripe_hostel_secret_key="rk_" + "test_scoped_hostel",
        stripe_agency_secret_key="rk_" + "test_scoped_agency",
        payment_result_store_key=b"p" * 32,
    )
    stripe_container = V2Container.open(
        settings=stripe_enabled,
        role=V2Role.WORKER,
    )
    try:
        stripe_workers = build_worker_set(
            container=stripe_container,
            settings=stripe_enabled,
        )
        assert type(
            stripe_workers[WorkerQueue.PAYMENT_INITIATION]
        ) is PaymentInitiationWorker
        assert type(
            stripe_workers[WorkerQueue.OUTCOME_PROJECTOR]
        ) is ReservationOutcomeProjector
        assert type(stripe_workers[WorkerQueue.POST_PAYMENT]) is CompletionProjector
        assert (
            stripe_container.readiness().capabilities["stripe_test_links"]
            == "ready"
        )
    finally:
        stripe_container.close()

    payment_instructions = tmp_path / "payment-instructions.json"
    payment_instructions.write_text(
        json.dumps(
            {
                "schema": "v2-payment-instructions-v1",
                "version": "test-v1",
                "hostel": {
                    "pix": "Pix hostel; aguarde validação.",
                    "wise": "Wise hostel; aguarde validação.",
                },
                "agency": {
                    "pix": "Pix agência; aguarde validação.",
                    "wise": "Wise agência; aguarde validação.",
                },
            }
        ),
        encoding="utf-8",
    )
    instructions_enabled = replace(
        settings,
        wise_instructions_enabled=True,
        pix_instructions_enabled=True,
        payment_result_store_key=b"p" * 32,
        real_effects_ack=REAL_EFFECTS_ACK,
        global_kill_switch_engaged=False,
        write_window_end=datetime.now(timezone.utc) + timedelta(hours=1),
        stripe_hostel_account_profile_id="receiver:hostel",
        stripe_agency_account_profile_id="receiver:agency",
        payment_instruction_path=payment_instructions,
    )
    instructions_container = V2Container.open(
        settings=instructions_enabled,
        role=V2Role.WORKER,
    )
    try:
        instructions_workers = build_worker_set(
            container=instructions_container,
            settings=instructions_enabled,
        )
        assert type(
            instructions_workers[WorkerQueue.PAYMENT_INITIATION]
        ) is PaymentInitiationWorker
        assert type(
            instructions_workers[WorkerQueue.OUTCOME_PROJECTOR]
        ) is ReservationOutcomeProjector
        assert type(
            instructions_workers[WorkerQueue.POST_PAYMENT]
        ) is CompletionProjector
        assert instructions_container.readiness().capabilities[
            "wise_instructions"
        ] == "ready"
        assert instructions_container.readiness().capabilities[
            "pix_instructions"
        ] == "ready"
    finally:
        instructions_container.close()

    manychat_enabled = replace(
        settings,
        manychat_delivery_enabled=True,
        real_effects_ack=REAL_EFFECTS_ACK,
        global_kill_switch_engaged=False,
        write_window_end=datetime.now(timezone.utc) + timedelta(hours=1),
        manychat_reply_field_id=101,
        manychat_reply_flow_ns="flow:reply:v2",
        manychat_payment_link_field_id=201,
        manychat_payment_description_field_id=202,
        manychat_payment_flow_ns="flow:payment:v2",
    )
    manychat_container = V2Container.open(
        settings=manychat_enabled,
        role=V2Role.WORKER,
    )
    try:
        manychat_workers = build_worker_set(
            container=manychat_container,
            settings=manychat_enabled,
        )
        assert type(
            manychat_workers[WorkerQueue.PUBLIC_DELIVERY]
        ) is CombinedPublicDeliveryWorker
        assert (
            manychat_container.readiness().capabilities["manychat_delivery"]
            == "ready"
        )
    finally:
        manychat_container.close()

    handoff_enabled = replace(
        settings,
        manychat_handoff_enabled=True,
        real_effects_ack=REAL_EFFECTS_ACK,
        global_kill_switch_engaged=False,
        write_window_end=datetime.now(timezone.utc) + timedelta(hours=1),
        manychat_handoff_tag_id=301,
        manychat_handoff_flow_ns="flow:handoff:v2",
    )
    handoff_container = V2Container.open(
        settings=handoff_enabled,
        role=V2Role.WORKER,
    )
    try:
        handoff_workers = build_worker_set(
            container=handoff_container,
            settings=handoff_enabled,
        )
        assert type(handoff_workers[WorkerQueue.HANDOFF]) is HandoffOutboxWorker
        assert (
            handoff_container.readiness().capabilities["manychat_handoff"]
            == "ready"
        )
    finally:
        handoff_container.close()


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


def _audit_enabled_settings(tmp_path: Path) -> V2Settings:
    knowledge = (tmp_path / "knowledge.sqlite3").resolve()
    knowledge.write_text(
        "entries:\n  - id: faq-audit\n    topic: geral\n    question: Oi?\n    answer: Olá.\n",
        encoding="utf-8",
    )
    key = b"audit-authority-key-0000000000001"
    manifest = (tmp_path / "authority.json").resolve()
    authority = {
        "authorization_id": "authority:audit-composition-1",
        "subscriber_id": "1873018537",
        "target_binding_hash": "1" * 64,
        "channel_id": "manychat:audit-composition",
        "channel_scope": "manychat:subscriber-1873018537",
        "generation": 1,
        "capability_policy_digest": "2" * 64,
        "effect_authorization_binding_digest": "3" * 64,
        "contract_digest": "4" * 64,
        "deadline_at": "2099-01-01T00:00:00+00:00",
        "allocations": [{"allocation_id": "allocation:audit-0", "ordinal": 0}],
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
    return _settings(
        tmp_path,
        runtime_mode=RuntimeMode.CONTROLLED_WRITE,
        cloudbeds_writes_enabled=True,
        real_effects_ack=REAL_EFFECTS_ACK,
        global_kill_switch_engaged=False,
        write_window_end=datetime.now(timezone.utc) + timedelta(hours=1),
        allowed_subscriber_ids=("1873018537",),
        hermes_model="openai-codex/gpt-5.6-luna",
        candidate_git_sha="a" * 40,
        candidate_image_digest="sha256:" + "b" * 64,
        manychat_api_key="manychat-secret",
        hermes_command=("python", "-m", "v2_host.hermes_child", "hermes"),
        hermes_system_prompt="Return the exact V2 proposal contract.",
        hermes_transcript_key=b"transcript-key-for-audit-test-00001",
        knowledge_base_path=knowledge,
        public_authority_manifest_path=manifest,
        public_authority_hmac_key=key,
    )


def _seed_confirmed_cloudbeds_outcome(container: V2Container, *, suffix: str) -> None:
    execution = container.execution
    assert execution is not None
    workflow_id = f"workflow:composition-audit-{suffix}"
    initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
    execution.create_workflow(initial)
    persist_script(execution, workflow_id, script)
    claim = execution.claim_command(
        worker_id=f"worker:composition-audit-{suffix}",
        now=T0 + timedelta(minutes=2),
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    request = DispatchRequest.from_command(claim.command, dumps_command(claim.command))
    permit = execution.fence_dispatch(
        claim,
        request,
        now=T0 + timedelta(minutes=2),
    )
    execution.record_outcome(
        permit,
        claim.command.outcome(
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
            normalized_status="confirmed",
            provider_reference=f"provider:cloudbeds:reservation-composition-{suffix}",
            evidence=(request.payload_hash,),
        ),
        now=T0 + timedelta(minutes=2, seconds=1),
    )


def _seed_confirmed_bokun_outcome(container: V2Container, *, suffix: str) -> None:
    execution = container.execution
    assert execution is not None
    workflow_id = f"workflow:composition-bokun-audit-{suffix}"
    initial, script = workflow_events("bokun", workflow_id=workflow_id)
    execution.create_workflow(initial)
    persist_script(execution, workflow_id, script)
    claim = execution.claim_command(
        worker_id=f"worker:composition-bokun-audit-{suffix}",
        now=T0 + timedelta(minutes=2),
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    request = DispatchRequest.from_command(claim.command, dumps_command(claim.command))
    permit = execution.fence_dispatch(
        claim,
        request,
        now=T0 + timedelta(minutes=2),
    )
    execution.record_outcome(
        permit,
        claim.command.outcome(
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
            normalized_status="confirmed",
            provider_reference=f"provider:bokun:booking-composition-{suffix}",
            evidence=(request.payload_hash,),
        ),
        now=T0 + timedelta(minutes=2, seconds=1),
    )


class _BokunAuditGETPort:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def get_booking(self, booking_id: str) -> object:
        self.calls.append(booking_id)
        return self.payload


class _AuditGETPort:
    def __init__(self, actions: list[object]) -> None:
        self.actions = list(actions)
        self.calls: list[str] = []

    def get_reservation(self, reservation_id: str) -> object:
        self.calls.append(reservation_id)
        if not self.actions:
            raise AssertionError("unexpected synthetic Cloudbeds audit GET")
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class _StageRunner:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[datetime] = []

    def run_once(self, *, now: datetime) -> object:
        self.calls.append(now)
        return self.result


def test_reconciliation_projects_bokun_get_only_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        _audit_enabled_settings(tmp_path),
        cloudbeds_writes_enabled=False,
        bokun_writes_enabled=True,
    )
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    port = _BokunAuditGETPort(
        {
            "bookingId": "booking-composition-one",
            "status": "CONFIRMED",
            "totalPrice": "1300.00",
            "currency": "BRL",
            "activityBooking": {
                "activityId": "913776",
                "date": "2026-11-11",
                "startTimeId": "3210363",
                "rateId": "RATE1",
                "startTime": "07:30",
                "adults": 1,
                "children": 0,
            },
        }
    )
    monkeypatch.setattr(
        production,
        "BokunGETAuditTransport",
        lambda **_: port,
        raising=False,
    )
    try:
        _seed_confirmed_bokun_outcome(container, suffix="one")
        stage = ReconciliationStage(
            container=container,
            reads=_ProbeReads(),
            settings=settings,
        )

        result = stage.run_once(now=T0 + timedelta(minutes=3))

        assert result["bokun_audit"] == {
            "status": "ok",
            "projection": {"inserted": 1, "replayed": 0, "ignored": 0},
            "observation": {"status": "matched", "attempts": 1},
        }
        assert result["cloudbeds_audit"]["status"] == "closed"
        assert port.calls == ["booking-composition-one"]
        assert settings.sqlite_paths["bokun_audit"].is_file()
        assert not hasattr(port, "post")
    finally:
        container.close()


def test_reconciliation_projects_one_cloudbeds_get_audit_and_owns_store_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _audit_enabled_settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    port = _AuditGETPort([{}])
    transport_kwargs: list[dict[str, object]] = []
    opened_stores: list[SQLiteCloudbedsAuditStore] = []

    def build_transport(**kwargs: object) -> _AuditGETPort:
        transport_kwargs.append(dict(kwargs))
        return port

    def open_store(path: Path) -> SQLiteCloudbedsAuditStore:
        store = SQLiteCloudbedsAuditStore(path)
        opened_stores.append(store)
        return store

    monkeypatch.setattr(
        production,
        "CloudbedsGETAuditTransport",
        build_transport,
        raising=False,
    )
    monkeypatch.setattr(
        production,
        "SQLiteCloudbedsAuditStore",
        open_store,
        raising=False,
    )
    try:
        _seed_confirmed_cloudbeds_outcome(container, suffix="one")
        stage = ReconciliationStage(
            container=container,
            reads=_ProbeReads(),
            settings=settings,
        )

        assert port.calls == []
        assert opened_stores == []

        result = stage.run_once(now=T0 + timedelta(minutes=3))

        assert port.calls == ["reservation-composition-one"]
        assert transport_kwargs == [
            {
                "api_key": "cloudbeds-secret",
                "property_id": "property-1",
                "base_url": "https://api.cloudbeds.com",
            }
        ]
        assert result["cloudbeds_audit"] == {
            "status": "ok",
            "projection": {"inserted": 1, "replayed": 0, "ignored": 0},
            "observation": {"status": "retryable_not_visible", "attempts": 1},
        }
        assert len(opened_stores) == 1
        assert opened_stores[0].path == settings.sqlite_paths["cloudbeds_audit"]
        assert opened_stores[0]._closed is True
        assert stage._cloudbeds_audit_transport is port
        assert all(
            not hasattr(port, name)
            for name in ("source_id", "idempotency_key", "post", "post_reservation")
        )
    finally:
        container.close()


def test_reconciliation_records_transport_failure_as_bounded_retry_without_blocking_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _audit_enabled_settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    port = _AuditGETPort([RuntimeError("synthetic audit failure")])
    monkeypatch.setattr(
        production,
        "CloudbedsGETAuditTransport",
        lambda **_: port,
        raising=False,
    )
    try:
        _seed_confirmed_cloudbeds_outcome(container, suffix="failure")
        stage = ReconciliationStage(
            container=container,
            reads=_ProbeReads(),
            settings=settings,
        )
        reservation = _StageRunner({"reservation": "reconciled"})
        payment = _StageRunner({"payment": "reconciled"})
        manual_handoff = _StageRunner({"manual_handoff": "projected"})
        stage._reservation = reservation
        stage._payment = payment
        stage._manual_handoff = manual_handoff
        now = T0 + timedelta(minutes=3)

        result = stage.run_once(now=now)

        assert reservation.calls == [now]
        assert payment.calls == [now]
        assert manual_handoff.calls == [now]
        assert result["reservation"] == {"reservation": "reconciled"}
        assert result["payment"] == {"payment": "reconciled"}
        assert result["manual_handoff"] == {"manual_handoff": "projected"}
        assert result["status"] == "ok"
        assert result["cloudbeds_audit"] == {
            "status": "ok",
            "projection": {"inserted": 1, "replayed": 0, "ignored": 0},
            "observation": {"status": "retryable_not_visible", "attempts": 1},
        }
        assert port.calls == ["reservation-composition-failure"]
    finally:
        container.close()


def test_cloudbeds_terminal_divergence_degrades_reconciliation_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _audit_enabled_settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    port = _AuditGETPort(
        [{"success": True, "data": {"reservationID": "reservation-wrong"}}]
    )
    monkeypatch.setattr(
        production,
        "CloudbedsGETAuditTransport",
        lambda **_: port,
        raising=False,
    )
    try:
        _seed_confirmed_cloudbeds_outcome(container, suffix="divergent")
        stage = ReconciliationStage(
            container=container,
            reads=_ProbeReads(),
            settings=settings,
        )

        result = stage.run_once(now=T0 + timedelta(minutes=3))

        assert result.reason is WorkerFailureReason.CLOUDBEDS_AUDIT_DIVERGENT
        assert result.result["cloudbeds_audit"]["status"] == "degraded"
        assert result.result["cloudbeds_audit"]["observation"] == {
            "status": "divergent",
            "attempts": 1,
        }
        assert port.calls == ["reservation-composition-divergent"]
    finally:
        container.close()


@pytest.mark.parametrize("owner_name", ("execution", "boundary", "private_customer"))
def test_reconciliation_rejects_hardlinked_audit_store_before_schema_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_name: str,
) -> None:
    settings = _audit_enabled_settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    port = _AuditGETPort([{}])
    monkeypatch.setattr(
        production,
        "CloudbedsGETAuditTransport",
        lambda **_: port,
        raising=False,
    )
    try:
        owner = getattr(container, owner_name)
        assert owner is not None
        audit_path = settings.sqlite_paths["cloudbeds_audit"]
        owner_path = settings.sqlite_paths[owner_name]
        audit_path.hardlink_to(owner_path)
        tables_before = {
            row[0]
            for row in owner._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        stage = ReconciliationStage(
            container=container,
            reads=_ProbeReads(),
            settings=settings,
        )

        result = stage.run_once(now=T0 + timedelta(minutes=3))
        tables_after = {
            row[0]
            for row in owner._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        assert result.reason is WorkerFailureReason.CLOUDBEDS_AUDIT_UNAVAILABLE
        assert result.result["cloudbeds_audit"]["status"] == "degraded"
        assert port.calls == []
        assert owner_path.samefile(audit_path)
        assert tables_after == tables_before
        assert "cloudbeds_audit_tasks" not in tables_after
    finally:
        container.close()


def test_confirmed_lodging_recovery_projects_completion_after_write_gate_closes(
    tmp_path: Path,
) -> None:
    armed = _audit_enabled_settings(tmp_path)
    first_container = V2Container.open(settings=armed, role=V2Role.WORKER)
    try:
        _seed_confirmed_cloudbeds_outcome(first_container, suffix="post-crash")
    finally:
        first_container.close()

    closed = replace(
        armed,
        cloudbeds_writes_enabled=False,
        global_kill_switch_engaged=True,
        write_window_end=None,
    )
    recovered = V2Container.open(settings=closed, role=V2Role.WORKER)
    try:
        workers = build_worker_set(container=recovered, settings=closed)
        completion = workers[WorkerQueue.POST_PAYMENT]

        assert type(completion) is CompletionProjector
        first = completion.run_once(now=T0 + timedelta(minutes=4))
        replay = completion.run_once(now=T0 + timedelta(minutes=5))

        assert first.inserted == 1
        assert replay.inserted == 0
        assert recovered.payment_initiation is None
        assert closed.cloudbeds_writes_enabled is False
        assert closed.enabled_payment_methods == ()
    finally:
        recovered.close()


def test_lodging_completion_readiness_is_ready_without_payment_effects(
    tmp_path: Path,
) -> None:
    settings = _audit_enabled_settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        workers = build_worker_set(container=container, settings=settings)

        assert type(workers[WorkerQueue.POST_PAYMENT]) is CompletionProjector
        assert container.readiness().capabilities["completion_projector"] == "ready"
        assert container.readiness().capabilities["outcome_projector"] == "closed"
        assert container.readiness().capabilities["payment_initiation"] == "closed"
    finally:
        container.close()


def test_reconciliation_audit_is_closed_with_gate_closed_and_queue_catalog_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)

    def forbidden_transport(**_: object) -> object:
        raise AssertionError("closed Cloudbeds audit constructed a transport")

    monkeypatch.setattr(
        production,
        "CloudbedsGETAuditTransport",
        forbidden_transport,
        raising=False,
    )
    try:
        stage = ReconciliationStage(
            container=container,
            reads=_ProbeReads(),
            settings=settings,
        )

        result = stage.run_once(now=T0 + timedelta(minutes=3))

        assert result["cloudbeds_audit"] == {
            "status": "closed",
            "projection": {"inserted": 0, "replayed": 0, "ignored": 0},
            "observation": None,
        }
        assert settings.sqlite_paths["cloudbeds_audit"].exists() is False
        assert tuple(queue.value for queue in WorkerQueue) == (
            "inbox",
            "boundary_relay",
            "reservation",
            "handoff",
            "outcome_projector",
            "payment_initiation",
            "settlement",
            "post_payment",
            "public_delivery",
            "reconciliation",
        )
    finally:
        container.close()
