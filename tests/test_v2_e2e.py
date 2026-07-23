from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from phase5_helpers import persist_script, workflow_events
from v2_adapters.bokun import BokunReservationPort
from v2_adapters.cloudbeds import CloudbedsReservationPort
from v2_adapters.manychat import ManyChatDeliveryAdapter, ManyChatTransportResponse
from v2_adapters.pix import PixInstructionAdapter
from v2_adapters.stripe import StripeLinkAdapter
from v2_adapters.wise import WiseInstructionAdapter
from v2_application.completion import (
    PublicDeliveryDisposition,
    PublicDeliveryWorker,
    PublicReply,
)
from v2_application.payments import PaymentInitiationWorker, PaymentService
from v2_application.recovery import (
    PackageComponent,
    PackageProgressStatus,
    PackageRecoveryPolicy,
)
from v2_application.reservations import V2ReservationExecutionAdapter
from v2_application.workers import V2ReservationWorker, V2WorkerDisposition
from v2_contracts.payments import (
    BusinessUnit,
    DueKind,
    PaymentMethod,
    PaymentObligation,
    PaymentSelection,
)
from v2_contracts.providers import ProviderWriteAuthorization
from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2Settings


NOW = datetime(2026, 11, 1, 13, 0, tzinfo=timezone.utc)


class AllowAllEffects:
    def allows_workflow(self, workflow_id: str) -> bool:
        return True


class ReservationTransport:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls: list[tuple[str, str]] = []

    def __call__(self, operation, payload, *, idempotency_key):
        self.calls.append((operation, idempotency_key))
        reference_field = "reservation_id" if self.provider == "cloudbeds" else "booking_id"
        return {"status": "confirmed", reference_field: f"fake-{self.provider}-reference"}


class StripeTransport:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        return {
            "link_id": f"fake-link-{request.payment_id}",
            "url": f"https://pay.invalid/{request.payment_id}",
        }


class Knowledge:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def pix_instruction(self, profile: str) -> str:
        self.calls.append(profile)
        return "Use somente a instrução Pix oficial e aguarde a verificação."


class ManyChatTransport:
    def __init__(self) -> None:
        self.calls = []

    def send_text(self, *, subscriber_id: str, text: str, idempotency_key: str):
        self.calls.append((subscriber_id, text, idempotency_key))
        return ManyChatTransportResponse(
            provider_message_id=f"fake-manychat-message:{len(self.calls)}"
        )


def _settings(tmp_path: Path) -> V2Settings:
    return V2Settings(
        webhook_secret="qualification-secret",
        sqlite_path=tmp_path / "inbox.sqlite3",
    )


def _authorization(provider: str) -> ProviderWriteAuthorization:
    return ProviderWriteAuthorization(
        provider=provider,
        enabled=True,
        authorization_id=f"authorization:fake:{provider}",
    )


def _queue(container: V2Container, provider: str, workflow_id: str) -> None:
    assert container.execution is not None
    initial, events = workflow_events(provider, workflow_id=workflow_id)
    container.execution.create_workflow(initial)
    persist_script(container.execution, workflow_id, events)


def _reservation_worker(
    container: V2Container,
    providers: tuple[str, ...],
) -> tuple[V2ReservationWorker, dict[str, ReservationTransport]]:
    assert container.execution is not None
    transports = {provider: ReservationTransport(provider) for provider in providers}
    adapters = []
    for provider in providers:
        port = (
            CloudbedsReservationPort(transports[provider])
            if provider == "cloudbeds"
            else BokunReservationPort(transports[provider])
        )
        adapters.append(
            V2ReservationExecutionAdapter(
                provider=provider,
                port=port,
                authorization=_authorization(provider),
                require_private_binding=False,
            )
        )
    return (
        V2ReservationWorker(
            store=container.execution,
            adapters=tuple(adapters),
            effect_guard=AllowAllEffects(),
            worker_id="worker:qualification:reservation",
            lease_ttl=timedelta(seconds=30),
        ),
        transports,
    )


def _payments() -> tuple[PaymentService, StripeTransport, Knowledge]:
    stripe = StripeTransport()
    knowledge = Knowledge()
    return (
        PaymentService(
            stripe=StripeLinkAdapter(
                transport=stripe,
                account_profiles={
                    BusinessUnit.HOSTEL: "fake-hostel-account",
                    BusinessUnit.AGENCY: "fake-agency-account",
                },
                enabled=True,
            ),
            wise=WiseInstructionAdapter(
                instructions={
                    "receiver:hostel": "Use a instrução Wise oficial do hostel; aguarde verificação.",
                    "receiver:agency": "Use a instrução Wise oficial da agência; aguarde verificação.",
                }
            ),
            pix=PixInstructionAdapter(knowledge=knowledge),
        ),
        stripe,
        knowledge,
    )


def _obligation(unit: BusinessUnit, suffix: str) -> PaymentObligation:
    return PaymentObligation(
        payment_id=f"payment:{suffix}",
        reservation_anchor_id=f"anchor:{suffix}",
        business_unit=unit,
        amount_minor=30_000 if unit is BusinessUnit.HOSTEL else 45_000,
        currency="BRL",
        due_kind=DueKind.PREPAYMENT,
        economic_version=1,
        receiver_profile_id=f"receiver:{unit.value}",
    )


def _run_payment(
    container: V2Container,
    service: PaymentService,
    selections: tuple[PaymentSelection, ...],
):
    assert container.payment_initiation is not None
    for selection in selections:
        assert container.payment_initiation.enqueue(selection, now=NOW) is True
        assert container.payment_initiation.enqueue(selection, now=NOW) is False
    worker = PaymentInitiationWorker(
        store=container.payment_initiation,
        payments=service,
        worker_id="worker:qualification:payment",
        lease_ttl=timedelta(seconds=30),
    )
    return tuple(worker.run_once(now=NOW + timedelta(seconds=index + 1)) for index in range(len(selections) + 1))


def _deliver(container: V2Container, message_id: str) -> ManyChatTransport:
    assert container.public_outbox is not None
    reply = PublicReply(
        release_id=f"release:{message_id}",
        lead_id="manychat:qualification-lead",
        message_id=message_id,
        channel="manychat",
        chunks=("Resultado qualificado com providers locais.",),
    )
    assert container.public_outbox.enqueue(reply, now=NOW) == 1
    assert container.public_outbox.enqueue(reply, now=NOW) == 0
    transport = ManyChatTransport()
    worker = PublicDeliveryWorker(
        store=container.public_outbox,
        delivery=ManyChatDeliveryAdapter(transport),
        worker_id="worker:qualification:delivery",
        lease_ttl=timedelta(seconds=30),
    )
    assert worker.run_once(now=NOW + timedelta(seconds=1)) is PublicDeliveryDisposition.DELIVERED
    assert worker.run_once(now=NOW + timedelta(seconds=2)) is PublicDeliveryDisposition.IDLE
    return transport


def test_lodging_stripe_qualification_has_one_effect_per_idempotency_key(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        _queue(container, "cloudbeds", "workflow:e2e:lodging-stripe")
        worker, transports = _reservation_worker(container, ("cloudbeds",))
        assert worker.run_once(now=NOW).disposition is V2WorkerDisposition.EFFECT_CONFIRMED
        assert worker.run_once(now=NOW + timedelta(seconds=1)).disposition is V2WorkerDisposition.IDLE

        service, stripe, knowledge = _payments()
        obligation = _obligation(BusinessUnit.HOSTEL, "lodging-stripe")
        results = _run_payment(
            container,
            service,
            (PaymentSelection(obligation, PaymentMethod.STRIPE),),
        )
        delivery = _deliver(container, "message:e2e:lodging-stripe")

        assert settings.all_real_effect_gates_closed is True
        assert len(transports["cloudbeds"].calls) == 1
        assert len(stripe.calls) == 1
        assert knowledge.calls == []
        assert [result.disposition.value for result in results] == ["completed", "idle"]
        assert len(delivery.calls) == 1
    finally:
        container.close()


def test_activity_pix_qualification_uses_knowledge_and_no_stripe(
    tmp_path: Path,
) -> None:
    container = V2Container.open(settings=_settings(tmp_path), role=V2Role.WORKER)
    try:
        _queue(container, "bokun", "workflow:e2e:activity-pix")
        worker, transports = _reservation_worker(container, ("bokun",))
        assert worker.run_once(now=NOW).disposition is V2WorkerDisposition.EFFECT_CONFIRMED
        assert worker.run_once(now=NOW + timedelta(seconds=1)).disposition is V2WorkerDisposition.IDLE

        service, stripe, knowledge = _payments()
        obligation = _obligation(BusinessUnit.AGENCY, "activity-pix")
        results = _run_payment(
            container,
            service,
            (PaymentSelection(obligation, PaymentMethod.PIX),),
        )
        delivery = _deliver(container, "message:e2e:activity-pix")

        assert len(transports["bokun"].calls) == 1
        assert stripe.calls == []
        assert knowledge.calls == ["receiver:agency"]
        assert [result.disposition.value for result in results] == ["completed", "idle"]
        assert len(delivery.calls) == 1
    finally:
        container.close()


def test_package_wise_qualification_keeps_components_and_units_separate(
    tmp_path: Path,
) -> None:
    container = V2Container.open(settings=_settings(tmp_path), role=V2Role.WORKER)
    try:
        _queue(container, "cloudbeds", "workflow:e2e:package:hostel")
        _queue(container, "bokun", "workflow:e2e:package:agency")
        worker, transports = _reservation_worker(container, ("cloudbeds", "bokun"))
        reservation_results = (
            worker.run_once(now=NOW),
            worker.run_once(now=NOW + timedelta(seconds=1)),
            worker.run_once(now=NOW + timedelta(seconds=2)),
        )
        confirmed = tuple(
            result.transition.state for result in reservation_results if result.transition is not None
        )

        hostel = _obligation(BusinessUnit.HOSTEL, "package-hostel-wise")
        agency = _obligation(BusinessUnit.AGENCY, "package-agency-wise")
        service, stripe, knowledge = _payments()
        payment_results = _run_payment(
            container,
            service,
            (
                PaymentSelection(hostel, PaymentMethod.WISE),
                PaymentSelection(agency, PaymentMethod.WISE),
            ),
        )
        delivery = _deliver(container, "message:e2e:package-wise")
        progress = PackageRecoveryPolicy().derive(
            components=tuple(
                PackageComponent(
                    command_id=state.command.command_id,
                    service=state.command.payload.components[0].service,
                    business_unit=(
                        BusinessUnit.HOSTEL
                        if state.command.payload.components[0].service.value == "lodging"
                        else BusinessUnit.AGENCY
                    ),
                    certainty=state.outcome.certainty,
                )
                for state in confirmed
            ),
            obligations=(hostel, agency),
            settled_payment_ids=frozenset((hostel.payment_id, agency.payment_id)),
            required_receipts=frozenset(
                ("reservation:hostel", "reservation:agency", "settlement:hostel", "settlement:agency", "public_delivery")
            ),
            observed_receipts=frozenset(
                ("reservation:hostel", "reservation:agency", "settlement:hostel", "settlement:agency", "public_delivery")
            ),
        )

        assert [result.disposition.value for result in reservation_results] == [
            "effect_confirmed",
            "effect_confirmed",
            "idle",
        ]
        assert len(transports["cloudbeds"].calls) == 1
        assert len(transports["bokun"].calls) == 1
        assert [result.disposition.value for result in payment_results] == [
            "completed",
            "completed",
            "idle",
        ]
        assert stripe.calls == []
        assert knowledge.calls == []
        assert progress.status is PackageProgressStatus.COMPLETED
        assert len(progress.payment_claim_namespaces) == 2
        assert len(delivery.calls) == 1
    finally:
        container.close()
