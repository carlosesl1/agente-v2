"""Signed fake-provider qualification harness for the three mandatory V2 scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient

from reservation_domain import ServiceKind
from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from reservation_followup import (
    BusinessUnit as FollowupBusinessUnit,
    PaymentMethod as FollowupPaymentMethod,
    PaymentOutboxWorker,
    PaymentSettlementWorker,
    PaymentStatus,
    stripe_target_fingerprint,
    to_wire_json,
    wise_target_fingerprint,
)
from tests.phase5_helpers import persist_script, workflow_events
from tests.test_phase6_payment import stripe_event, wise_credit
from tests.test_phase6_payment_claims import (
    alternate_anchor,
    pix_visual_evidence,
    prepare_payment,
)
from tests.test_phase6_payment_outbox import FakePaymentEffectDelivery
from tests.test_phase6_payment_worker import FakeSettlementPort
from v2_adapters.bokun import BokunReservationPort
from v2_adapters.cloudbeds import CloudbedsReservationPort
from v2_adapters.manychat import ManyChatDeliveryAdapter, ManyChatTransportResponse
from v2_adapters.pix import PixInstructionAdapter
from v2_adapters.stripe import StripeLinkAdapter
from v2_adapters.wise import WiseInstructionAdapter
from v2_application.completion import (
    PublicDeliveryWorker,
    PublicReply,
)
from v2_application.inbox import SQLiteInbox
from v2_application.payments import (
    PaymentInitiationWorker,
    PaymentService,
)
from v2_application.relay_worker import BoundaryRelayWorker
from v2_application.reservations import V2ReservationExecutionAdapter
from v2_application.workers import V2ReservationWorker
from v2_contracts.payments import (
    BusinessUnit,
    DueKind,
    PaymentMethod,
    PaymentObligation,
    PaymentSelection,
)
from v2_contracts.providers import ProviderWriteAuthorization
from v2_host.api_main import build_api_app
from v2_host.composition import V2Container, V2Role
from v2_host.qualification_workers import (
    build_worker_set,
    install_qualification_worker_set,
)
from v2_host.settings import V2Settings
from v2_host.worker_main import WorkerQueue, build_worker_cycle

SIGNED_NOW = datetime(2027, 2, 1, 13, 0, tzinfo=timezone.utc)
TEXT_FIXTURE = Path(__file__).parent / "fixtures" / "v2" / "manychat_text.json"
TEXT_PAYLOAD = json.loads(TEXT_FIXTURE.read_text())


class AllowAllEffects:
    def allows_workflow(self, workflow_id: str) -> bool:
        return True


class ReservationTransport:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls: list[tuple[str, str]] = []

    def __call__(self, operation, payload, *, idempotency_key):
        self.calls.append((operation, idempotency_key))
        key = "reservation_id" if self.provider == "cloudbeds" else "booking_id"
        return {"status": "confirmed", key: f"fake-{self.provider}-reference"}


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


class CountingWise:
    def __init__(self, inner: WiseInstructionAdapter) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def instruction(self, obligation):
        self.calls.append(obligation.payment_id)
        return self.inner.instruction(obligation)


class ManyChatTransport:
    def __init__(self) -> None:
        self.calls = []

    def send_text(self, *, subscriber_id: str, text: str, idempotency_key: str):
        self.calls.append((subscriber_id, text, idempotency_key))
        return ManyChatTransportResponse(
            provider_message_id=f"fake-manychat-message:{len(self.calls)}"
        )


class QualificationInboxStage:
    def __init__(self, inbox: SQLiteInbox, setup) -> None:
        self.inbox = inbox
        self.setup = setup

    def run_once(self, *, now: datetime):
        claim = self.inbox.claim_ready(
            now=now,
            quiet_window=timedelta(0),
            lease_for=timedelta(seconds=30),
        )
        if claim is None:
            return "idle"
        self.setup(claim.batch)
        receipt_hash = hashlib.sha256(
            b"v2-signed-qualification-turn\0" + claim.batch_id.encode()
        ).hexdigest()
        self.inbox.complete_claim(claim, turn_receipt_hash=receipt_hash, now=now)
        return "completed"


class QualificationReconciliationStage:
    def __init__(self, runtime: SignedQualificationRuntime) -> None:
        self.runtime = runtime

    def run_once(self, *, now: datetime):
        self.runtime.last_reconciliation_at = now
        self.runtime.reconciled = self.runtime.completed()
        return "idle"


class QualificationIdleStage:
    def run_once(self, *, now: datetime):
        del now
        return "idle"


@dataclass(frozen=True, slots=True)
class ScenarioShape:
    reservation_providers: tuple[str, ...]
    payment_methods: tuple[PaymentMethod, ...]
    business_units: tuple[BusinessUnit, ...]


SHAPES = {
    "lodging_stripe": ScenarioShape(
        ("cloudbeds",), (PaymentMethod.STRIPE,), (BusinessUnit.HOSTEL,)
    ),
    "activity_pix": ScenarioShape(
        ("bokun",), (PaymentMethod.PIX,), (BusinessUnit.AGENCY,)
    ),
    "package_wise": ScenarioShape(
        ("cloudbeds", "bokun"),
        (PaymentMethod.WISE, PaymentMethod.WISE),
        (BusinessUnit.HOSTEL, BusinessUnit.AGENCY),
    ),
}


def qualification_settings(tmp_path: Path) -> V2Settings:
    return V2Settings(
        webhook_secret="qualification-secret",
        stripe_webhook_secret="stripe-qualification-secret",
        wise_webhook_secret="wise-qualification-secret",
        pix_webhook_secret="pix-qualification-secret",
        sqlite_path=tmp_path / "inbox.sqlite3",
        pix_receiver_profile_id="receiver:profile:synthetic:1",
        wise_signer_profile_id="wise-signer:profile:synthetic:1",
        wise_account_profile_id="wise-account:profile:synthetic:1",
        stripe_account_profile_id="stripe-account:profile:synthetic:1",
    )


class SignedQualificationRuntime:
    def __init__(self, tmp_path: Path, scenario: str) -> None:
        if scenario not in SHAPES:
            raise ValueError("unknown signed qualification scenario")
        self.scenario = scenario
        self.shape = SHAPES[scenario]
        self.settings = qualification_settings(tmp_path)
        self.container = V2Container.open(
            settings=self.settings,
            role=V2Role.WORKER,
        )
        self.reservation_transports = {
            provider: ReservationTransport(provider)
            for provider in self.shape.reservation_providers
        }
        reservation_adapters = []
        for provider, transport in self.reservation_transports.items():
            port = (
                CloudbedsReservationPort(transport)
                if provider == "cloudbeds"
                else BokunReservationPort(transport)
            )
            reservation_adapters.append(
                V2ReservationExecutionAdapter(
                    provider=provider,
                    port=port,
                    authorization=ProviderWriteAuthorization(
                        provider=provider,
                        enabled=True,
                        authorization_id=f"authorization:{provider}:signed-e2e",
                    ),
                    require_private_binding=False,
                )
            )
        assert self.container.execution is not None
        self.reservation_worker = V2ReservationWorker(
            store=self.container.execution,
            adapters=tuple(reservation_adapters),
            effect_guard=AllowAllEffects(),
            worker_id="worker:signed-e2e:reservation",
            lease_ttl=timedelta(seconds=30),
        )
        self.stripe = StripeTransport()
        self.knowledge = Knowledge()
        self.wise = CountingWise(
            WiseInstructionAdapter(
                instructions={
                    "receiver:profile:synthetic:1": (
                        "Use a instrução Wise oficial; aguarde verificação."
                    )
                }
            )
        )
        self.payment_service = PaymentService(
            stripe=StripeLinkAdapter(
                transport=self.stripe,
                account_profiles={
                    BusinessUnit.HOSTEL: "fake-hostel-account",
                    BusinessUnit.AGENCY: "fake-agency-account",
                },
                enabled=True,
            ),
            wise=self.wise,
            pix=PixInstructionAdapter(knowledge=self.knowledge),
        )
        assert self.container.payment_initiation is not None
        self.payment_worker = PaymentInitiationWorker(
            store=self.container.payment_initiation,
            payments=self.payment_service,
            worker_id="worker:signed-e2e:payment-initiation",
            lease_ttl=timedelta(seconds=30),
        )
        assert self.container.followup is not None
        self.settlement = FakeSettlementPort("settled")
        self.settlement_worker = PaymentSettlementWorker(
            store=self.container.followup,
            settlement=self.settlement,
            worker_id="worker:signed-e2e:settlement",
            lease_ttl=timedelta(seconds=30),
        )
        self.payment_effect_delivery = FakePaymentEffectDelivery()
        self.post_payment_worker = PaymentOutboxWorker(
            store=self.container.followup,
            delivery=self.payment_effect_delivery,
            worker_id="worker:signed-e2e:post-payment",
            lease_ttl=timedelta(seconds=30),
        )
        assert self.container.public_outbox is not None
        self.manychat = ManyChatTransport()
        self.public_worker = PublicDeliveryWorker(
            store=self.container.public_outbox,
            delivery=ManyChatDeliveryAdapter(self.manychat),
            worker_id="worker:signed-e2e:public",
            lease_ttl=timedelta(seconds=30),
        )
        self.payment_events = []
        self.payment_ids: list[str] = []
        self.setup_calls = 0
        self.reconciled = False
        self.last_reconciliation_at = None
        workers = {
            WorkerQueue.INBOX: QualificationInboxStage(
                self.container.inbox, self._setup_from_inbox
            ),
            WorkerQueue.BOUNDARY_RELAY: BoundaryRelayWorker(
                boundary=SQLiteBoundaryWorkerStore(self.container.boundary),
                reservation_target=self.container.execution,
                handoff_target=self.container.followup,
                worker_id="worker:signed-e2e:boundary-relay",
                lease_ttl=timedelta(seconds=30),
            ),
            WorkerQueue.RESERVATION: self.reservation_worker,
            WorkerQueue.HANDOFF: QualificationIdleStage(),
            WorkerQueue.OUTCOME_PROJECTOR: QualificationIdleStage(),
            WorkerQueue.PAYMENT_INITIATION: self.payment_worker,
            WorkerQueue.SETTLEMENT: self.settlement_worker,
            WorkerQueue.POST_PAYMENT: self.post_payment_worker,
            WorkerQueue.PUBLIC_DELIVERY: self.public_worker,
            WorkerQueue.RECONCILIATION: QualificationReconciliationStage(self),
        }
        install_qualification_worker_set(self.container, workers)
        factory_workers = build_worker_set(
            container=self.container,
            settings=self.settings,
        )
        self.cycle = build_worker_cycle(self.container, factory_workers)

    def close(self) -> None:
        self.container.close()

    def _queue_reservation(self, provider: str, index: int) -> None:
        assert self.container.execution is not None
        workflow_id = f"workflow:signed:{self.scenario}:{provider}:{index}"
        initial, events = workflow_events(provider, workflow_id=workflow_id)
        self.container.execution.create_workflow(initial)
        persist_script(self.container.execution, workflow_id, events)

    def _anchor(self, suffix: str, unit: BusinessUnit):
        if unit is BusinessUnit.HOSTEL:
            return alternate_anchor(suffix)
        return alternate_anchor(
            suffix,
            service=ServiceKind.ACTIVITY,
            business_unit=FollowupBusinessUnit.AGENCY,
        )

    def _payment_evidence(self, suffix: str, method: PaymentMethod, anchor):
        if method is PaymentMethod.STRIPE:
            event_tail = hashlib.sha256(suffix.encode()).hexdigest()[:20]
            return stripe_event(
                event_id=f"evt_{event_tail}",
                amount_minor=anchor.amount_minor,
                currency=anchor.currency,
                payment_intent_fingerprint=stripe_target_fingerprint(
                    anchor.payment_target_id
                ),
            )
        if method is PaymentMethod.WISE:
            fingerprint = hashlib.sha256(f"wise:{suffix}".encode()).hexdigest()
            return wise_credit(
                amount_minor=anchor.amount_minor,
                currency=anchor.currency,
                transaction_fingerprint=fingerprint,
                reference_fingerprint=wise_target_fingerprint(anchor.payment_target_id),
            )
        e2e_tail = hashlib.sha256(suffix.encode()).hexdigest()[:11].upper()
        return pix_visual_evidence(
            proof_amount_minor=anchor.amount_minor,
            proof_currency=anchor.currency,
            proof_receiver_profile_id=anchor.receiver_profile_id,
            normalized_e2e=f"E1234567820270201{e2e_tail}",
        )

    def _setup_from_inbox(self, batch) -> None:
        self.setup_calls += 1
        if self.setup_calls > 1:
            raise RuntimeError("qualification scenario setup replayed")
        for index, provider in enumerate(self.shape.reservation_providers):
            self._queue_reservation(provider, index)
        assert self.container.followup is not None
        assert self.container.payment_initiation is not None
        for index, (method, unit) in enumerate(
            zip(self.shape.payment_methods, self.shape.business_units)
        ):
            suffix = f"signed-{self.scenario}-{index}"
            anchor = self._anchor(suffix, unit)
            evidence = self._payment_evidence(suffix, method, anchor)
            state, event = prepare_payment(
                self.container.followup,
                suffix=suffix,
                method=FollowupPaymentMethod(method.value),
                evidence=evidence,
                anchor=anchor,
            )
            subject = state.subject
            obligation = PaymentObligation(
                payment_id=subject.payment_id,
                reservation_anchor_id=(
                    subject.confirmed_reservation_anchor.reservation_command_id
                ),
                business_unit=BusinessUnit(subject.business_unit.value),
                amount_minor=subject.amount_minor,
                currency=subject.currency,
                due_kind=DueKind.PREPAYMENT,
                economic_version=subject.payment_version,
                receiver_profile_id=subject.receiver_profile_id,
            )
            self.container.payment_initiation.enqueue(
                PaymentSelection(obligation, method),
                now=SIGNED_NOW,
            )
            self.payment_events.append(event)
            self.payment_ids.append(subject.payment_id)
        assert self.container.public_outbox is not None
        self.container.public_outbox.enqueue(
            PublicReply(
                release_id=f"release:signed:{self.scenario}",
                lead_id=batch.lead_id,
                message_id=f"message:signed:{self.scenario}",
                channel="manychat",
                chunks=("Resultado qualificado com providers locais.",),
            ),
            now=SIGNED_NOW,
        )

    @staticmethod
    def _is_idle(value: object) -> bool:
        disposition = getattr(value, "disposition", value)
        normalized = getattr(disposition, "value", disposition)
        return normalized == "idle"

    def run_to_idle(self, *, start: datetime, limit: int = 30) -> None:
        for offset in range(limit):
            report = self.cycle.run_once(now=start + timedelta(seconds=offset))
            if any(item.failed for item in report.items):
                raise AssertionError(f"qualification worker failed: {report!r}")
            if all(self._is_idle(item.result) for item in report.items):
                return
        raise AssertionError("qualification worker cycle did not become idle")

    def _manychat_payload(self) -> dict[str, object]:
        return {
            **TEXT_PAYLOAD,
            "message_id": f"mc-signed-{self.scenario}",
            "subscriber_id": f"subscriber-signed-{self.scenario}",
            "contact_id": f"subscriber-signed-{self.scenario}",
            "conversation_id": f"conversation-signed-{self.scenario}",
            "message": f"Qualificação {self.scenario}",
            "occurred_at": (SIGNED_NOW - timedelta(seconds=1)).isoformat(),
        }

    def _signed_evidence(self, event) -> tuple[str, bytes, dict[str, str]]:
        evidence = event.evidence
        if type(evidence).__name__ == "VerifiedStripeEvent":
            provider = "stripe"
            external_id = evidence.event_id
            secret = self.settings.stripe_webhook_secret
        elif type(evidence).__name__ == "VerifiedWiseCredit":
            provider = "wise"
            external_id = evidence.transaction_fingerprint
            secret = self.settings.wise_webhook_secret
        else:
            provider = "pix"
            external_id = evidence.normalized_e2e
            secret = self.settings.pix_webhook_secret
        body = json.dumps(
            {
                "provider": provider,
                "external_event_id": external_id,
                "payment_id": event.payment_id,
                "expected_revision": 3,
                "event_wire": to_wire_json(event),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        return (
            provider,
            body,
            {f"X-V2-{provider.title()}-Signature": signature},
        )

    def run_signed(self) -> None:
        payload = self._manychat_payload()
        with TestClient(
            build_api_app(self.settings, clock=lambda: SIGNED_NOW)
        ) as client:
            first = client.post(
                "/webhook/manychat",
                headers={"X-V2-Webhook-Secret": self.settings.webhook_secret},
                json=payload,
            )
            duplicate = client.post(
                "/webhook/manychat",
                headers={"X-V2-Webhook-Secret": self.settings.webhook_secret},
                json=payload,
            )
            if (first.status_code, duplicate.status_code) != (202, 200):
                raise AssertionError("signed ManyChat ingress did not accept/replay")
            self.run_to_idle(start=SIGNED_NOW)
            for event in self.payment_events:
                provider, body, headers = self._signed_evidence(event)
                accepted = client.post(
                    f"/webhook/payments/{provider}",
                    content=body,
                    headers=headers,
                )
                replay = client.post(
                    f"/webhook/payments/{provider}",
                    content=body,
                    headers=headers,
                )
                if (accepted.status_code, replay.status_code) != (202, 200):
                    raise AssertionError(
                        "signed financial evidence did not accept/replay"
                    )
            self.run_to_idle(start=SIGNED_NOW + timedelta(minutes=1))

    def completed(self) -> bool:
        if self.setup_calls != 1 or self.container.inbox.processed_count() != 1:
            return False
        if sum(len(item.calls) for item in self.reservation_transports.values()) != len(
            self.shape.reservation_providers
        ):
            return False
        assert self.container.payment_initiation is not None
        completed_initiations = self.container.payment_initiation._connection.execute(
            "SELECT count(*) FROM payment_initiations WHERE status='completed'"
        ).fetchone()[0]
        if completed_initiations != len(self.shape.payment_methods):
            return False
        assert self.container.followup is not None
        for payment_id in self.payment_ids:
            if (
                self.container.followup.load_payment(payment_id).status
                is not PaymentStatus.PAID
            ):
                return False
        pending_effects = self.container.followup._connection.execute(
            "SELECT count(*) FROM payment_outbox WHERE status!='delivered'"
        ).fetchone()[0]
        if pending_effects != 0:
            return False
        assert self.container.public_outbox is not None
        if (
            self.container.public_outbox.delivered_count(
                f"release:signed:{self.scenario}"
            )
            != 1
        ):
            return False
        return True

    @property
    def provider_call_counts(self) -> dict[str, int]:
        return {
            "cloudbeds": len(self.reservation_transports.get("cloudbeds", ()).calls)
            if "cloudbeds" in self.reservation_transports
            else 0,
            "bokun": len(self.reservation_transports.get("bokun", ()).calls)
            if "bokun" in self.reservation_transports
            else 0,
            "stripe": len(self.stripe.calls),
            "wise": len(self.wise.calls),
            "pix": len(self.knowledge.calls),
            "settlement": self.settlement.dispatch_calls,
            "manychat": len(self.manychat.calls),
        }

    @property
    def public_delivery_count(self) -> int:
        return len(self.manychat.calls)

    @property
    def owner_counts(self) -> dict[str, int]:
        return self.container.owner_counts()
