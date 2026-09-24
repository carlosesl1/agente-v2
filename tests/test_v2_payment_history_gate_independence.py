"""Payment effect gates must not erase authenticated historical observations."""
from datetime import date, timedelta

import pytest

from reservation_domain import ExecutionCertainty
from v2_contracts.execution_context import ProviderReservationStatus
from v2_contracts.model import ModelFact, ModelProposal
from v2_contracts.providers import ReadKind, ReadRequest


from tests.test_v2_execution_context import _queued_state
from tests.test_v2_outcome_projector import NOW, _finish_next, _package_command, _persist
from tests.test_v2_production_composition import _settings
from tests.test_v2_settings import _controlled_env
from v2_application.active_execution import (
    ReservationExecutionStatusResolver,
    component_renewal_allowed,
)
from v2_application.critical_actions import CriticalActionDisposition
from v2_application.reservations import ReservationAllocator
from v2_contracts.critical_actions import CriticalActionKind
from v2_host.composition import V2Container, V2Role
from v2_host.production import ClosedCapabilityWorker, _critical_action_policy, build_worker_set
from v2_host.settings import V2ProcessRole, V2Settings
from v2_host.worker_main import WorkerQueue


def _selection(service="agency"):
    read = ReadRequest("read:renewal", ReadKind.ACTIVITY,
                       product_id="product:four-ps", activity_date=date(2026, 8, 1),
                       adults=2, children=0)
    return ModelProposal("proposal:renewal", "inform", (),
                         (ModelFact("service", service),), (read,), (),
                         selection_requested=True)


@pytest.mark.parametrize("role", [V2ProcessRole.WORKER, V2ProcessRole.COMBINED])
def test_closed_payment_gates_preserve_worker_history_key(tmp_path, role):
    env = _controlled_env(tmp_path)
    env["V2_PAYMENT_RESULT_STORE_KEY_HEX"] = "ab" * 32
    settings = V2Settings.from_env(env, process_role=role)
    assert settings.enabled_payment_methods == ()
    assert settings.all_real_effect_gates_closed
    assert settings.payment_result_store_key == bytes.fromhex("ab" * 32)


def test_api_never_receives_history_key_with_payment_gates_closed(tmp_path):
    env = _controlled_env(tmp_path)
    env["V2_PAYMENT_RESULT_STORE_KEY_HEX"] = "not-even-a-worker-key"
    settings = V2Settings.from_env(env, process_role=V2ProcessRole.API)
    assert settings.payment_result_store_key == b""


@pytest.mark.parametrize("raw", ["ab", "ab" * 31, "ab" * 33])
def test_configured_history_key_must_be_32_bytes_even_when_effects_closed(tmp_path, raw):
    env = _controlled_env(tmp_path)
    env["V2_PAYMENT_RESULT_STORE_KEY_HEX"] = raw
    with pytest.raises(ValueError, match="32-byte"):
        V2Settings.from_env(env, process_role=V2ProcessRole.WORKER)


def test_unconfigured_history_remains_unavailable_without_creating_store(tmp_path):
    settings = _settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        assert container.payment_initiation is None
        assert not settings.sqlite_paths["payment_initiation"].exists()
    finally:
        container.close()


def test_closed_history_owner_does_not_enable_any_effect_worker(tmp_path):
    settings = _settings(tmp_path, payment_result_store_key=b"h" * 32)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        assert container.payment_initiation is not None
        workers = build_worker_set(container=container, settings=settings)
        assert container.owner_counts()["payment_initiation"] == 1
        assert container.readiness().status == "ready"
        assert container.readiness().capabilities["payment_initiation"] == "closed"
        assert isinstance(workers[WorkerQueue.PAYMENT_INITIATION], ClosedCapabilityWorker)
        assert isinstance(workers[WorkerQueue.OUTCOME_PROJECTOR], ClosedCapabilityWorker)
        assert isinstance(workers[WorkerQueue.RESERVATION], ClosedCapabilityWorker)
        assert settings.all_real_effect_gates_closed
        assert all(_critical_action_policy(settings).classify(kind) is CriticalActionDisposition.DENY
                   for kind in CriticalActionKind)
    finally:
        container.close()


class TerminalReservationReader:
    """Controlled provider statuses, not a real-provider or financial test."""
    def read(self, *, service, provider_reference):
        return ProviderReservationStatus(
            "observed", NOW, "TIMEOUT" if service == "activity" else "cancelled",
            "NOT_PAID" if service == "activity" else "PAID",
            "0" if service == "activity" else "450", "730.80", "BRL",
        )


def test_retained_package_context_allows_only_unpaid_component_with_gates_closed(tmp_path):
    settings = _settings(tmp_path, payment_result_store_key=b"h" * 32)
    parent = _package_command()
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        _persist(container.execution, ReservationAllocator().allocate(parent).commands)
        _finish_next(container.execution, now=NOW, certainty=ExecutionCertainty.EFFECT_CONFIRMED)
        _finish_next(container.execution, now=NOW + timedelta(seconds=1),
                     certainty=ExecutionCertainty.EFFECT_CONFIRMED)
    finally:
        container.close()

    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        state = _queued_state(parent)
        before = container.execution.list_outcome_projection_inputs()
        # This is the former productive dependency: omitting the history owner
        # makes the exact same expired/unpaid component unverifiable.
        unavailable = ReservationExecutionStatusResolver(
            container.execution, followup=container.followup,
            reservation_status_reader=TerminalReservationReader(),
        ).context(state)
        assert not component_renewal_allowed(state, _selection(), execution_context=unavailable, now=NOW)
        assert container.payment_initiation is not None
        resolver = ReservationExecutionStatusResolver(
            container.execution, payment_store=container.payment_initiation,
            followup=container.followup, reservation_status_reader=TerminalReservationReader(),
        )
        context = resolver.context(state)
        assert all(c.payment_initiation_status == "not_recorded" for c in context.components)
        assert component_renewal_allowed(state, _selection(), execution_context=context, now=NOW)
        assert not component_renewal_allowed(state, _selection("hostel"), execution_context=context, now=NOW)
        assert resolver.context(state) == context
        assert container.execution.list_outcome_projection_inputs() == before
        for table in ("payment_initiations", "stripe_step_receipts", "stripe_reconciliations"):
            assert container.payment_initiation._connection.execute(
                f"select count(*) from {table}").fetchone()[0] == 0
    finally:
        container.close()
