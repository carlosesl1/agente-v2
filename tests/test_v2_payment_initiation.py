from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from v2_adapters.pix import PixInstructionAdapter
from v2_adapters.stripe import StripeLinkAdapter
from v2_adapters.wise import WiseInstructionAdapter
from v2_application.payments import (
    PaymentInitiationDisposition,
    PaymentInitiationWorker,
    PaymentService,
    SQLitePaymentInitiationStore,
)
from v2_contracts.payments import (
    BusinessUnit,
    DueKind,
    PaymentMethod,
    PaymentObligation,
    PaymentSelection,
    ReservationPaymentContext,
    StripeLinkRequest,
)


HOSTEL = PaymentObligation(
    payment_id="payment:hostel:001",
    reservation_anchor_id="anchor:hostel:001",
    business_unit=BusinessUnit.HOSTEL,
    amount_minor=30000,
    currency="BRL",
    due_kind=DueKind.PREPAYMENT,
    economic_version=1,
    receiver_profile_id="receiver:hostel",
)
AGENCY = PaymentObligation(
    payment_id="payment:agency:001",
    reservation_anchor_id="anchor:agency:001",
    business_unit=BusinessUnit.AGENCY,
    amount_minor=45000,
    currency="BRL",
    due_kind=DueKind.PREPAYMENT,
    economic_version=1,
    receiver_profile_id="receiver:agency",
)


class StripeTransport:
    def __init__(self) -> None:
        self.requests: list[StripeLinkRequest] = []

    def __call__(self, request: StripeLinkRequest) -> dict[str, str]:
        self.requests.append(request)
        return {
            "link_id": f"plink_{request.account_profile_id}_{request.economic_version}",
            "url": f"https://pay.invalid/{request.payment_id}/{request.economic_version}",
        }


class Knowledge:
    def pix_instruction(self, profile: str) -> str:
        return {
            "receiver:hostel": "Use o Pix oficial do hostel e envie o comprovante.",
            "receiver:agency": "Use o Pix oficial da agência e envie o comprovante.",
        }[profile]


def service() -> tuple[PaymentService, StripeTransport, Knowledge]:
    stripe_transport = StripeTransport()
    knowledge = Knowledge()
    payments = PaymentService(
        stripe=StripeLinkAdapter(
            transport=stripe_transport,
            account_profiles={
                BusinessUnit.HOSTEL: "hostel",
                BusinessUnit.AGENCY: "agency",
            },
            enabled=True,
        ),
        wise=WiseInstructionAdapter(
            instructions={
                "receiver:hostel": "Faça a transferência Wise usando a referência exibida; a confirmação ocorrerá após verificação.",
                "receiver:agency": "Faça a transferência Wise para a agência; a confirmação ocorrerá após verificação.",
            }
        ),
        pix=PixInstructionAdapter(knowledge=knowledge),
    )
    return payments, stripe_transport, knowledge


def test_hostel_stripe_uses_only_hostel_account_and_anchor() -> None:
    payments, transport, _ = service()

    link = payments.initiate(HOSTEL, PaymentMethod.STRIPE)

    assert link.account_profile_id == "hostel"
    assert link.reservation_anchor_id == HOSTEL.reservation_anchor_id
    assert link.settled is False
    assert [request.account_profile_id for request in transport.requests] == ["hostel"]


def test_agency_stripe_uses_only_agency_account_and_anchor() -> None:
    payments, transport, _ = service()

    link = payments.initiate(AGENCY, PaymentMethod.STRIPE)

    assert link.account_profile_id == "agency"
    assert link.reservation_anchor_id == AGENCY.reservation_anchor_id
    assert [request.account_profile_id for request in transport.requests] == ["agency"]


def test_wise_instruction_contains_no_unverified_payment_claim() -> None:
    payments, transport, _ = service()

    instruction = payments.initiate(HOSTEL, PaymentMethod.WISE)

    assert instruction.settled is False
    assert "confirmado" not in instruction.public_text.lower()
    assert "pago" not in instruction.public_text.lower()
    assert transport.requests == []


def test_pix_instruction_comes_from_authorized_knowledge_profile() -> None:
    payments, _, knowledge = service()

    instruction = payments.initiate(AGENCY, PaymentMethod.PIX)

    assert instruction.receiver_profile_id == AGENCY.receiver_profile_id
    assert instruction.public_text == knowledge.pix_instruction("receiver:agency")
    assert instruction.settled is False


def test_foreign_guest_due_at_checkin_has_no_payment_effect() -> None:
    payments, _, _ = service()
    context = ReservationPaymentContext(
        payment_id="payment:foreign:001",
        reservation_anchor_id="anchor:foreign:001",
        business_unit=BusinessUnit.HOSTEL,
        amount_minor=30000,
        currency="BRL",
        receiver_profile_id="receiver:hostel",
        guest_country_code="US",
    )

    plan = payments.plan(context)

    assert plan.obligation.due_kind is DueKind.DUE_AT_CHECKIN
    assert plan.payment_effects == ()


def test_method_change_preserves_reservation_and_economic_version() -> None:
    payments, _, _ = service()
    selected = PaymentSelection(HOSTEL, PaymentMethod.PIX)

    changed = payments.change_method(selected, PaymentMethod.WISE)

    assert changed.obligation.reservation_anchor_id == HOSTEL.reservation_anchor_id
    assert changed.obligation.economic_version == HOSTEL.economic_version
    assert changed.method is PaymentMethod.WISE


def test_economic_change_increments_only_financial_version() -> None:
    payments, _, _ = service()
    selected = PaymentSelection(HOSTEL, PaymentMethod.PIX)

    changed = payments.change_amount(selected, amount_minor=31000)

    assert changed.obligation.reservation_anchor_id == HOSTEL.reservation_anchor_id
    assert changed.obligation.economic_version == HOSTEL.economic_version + 1
    assert changed.obligation.amount_minor == 31000
    assert replace(changed.obligation, amount_minor=HOSTEL.amount_minor).reservation_anchor_id == HOSTEL.reservation_anchor_id


NOW = datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
RESULT_KEY = b"payment-result-test-key-00000001"


def test_stripe_initiation_is_fenced_and_provider_is_called_once(tmp_path: Path) -> None:
    payments, transport, _ = service()
    path = tmp_path / "payment-init.sqlite3"
    store = SQLitePaymentInitiationStore(path, result_encryption_key=RESULT_KEY)
    selection = PaymentSelection(HOSTEL, PaymentMethod.STRIPE)
    assert store.enqueue(selection, now=NOW) is True
    worker = PaymentInitiationWorker(
        store=store,
        payments=payments,
        worker_id="worker:payment-initiation",
        lease_ttl=timedelta(seconds=30),
    )

    first = worker.run_once(now=NOW + timedelta(seconds=1))
    second = worker.run_once(now=NOW + timedelta(seconds=2))

    assert first.disposition is PaymentInitiationDisposition.COMPLETED
    assert second.disposition is PaymentInitiationDisposition.IDLE
    assert len(transport.requests) == 1
    assert store.dispatch_slots(selection) == 1
    persisted = b"".join(
        candidate.read_bytes()
        for candidate in path.parent.glob(path.name + "*")
    )
    assert b"https://pay.invalid" not in persisted


def test_stripe_timeout_after_fence_is_manual_review_without_retry(tmp_path: Path) -> None:
    class TimeoutTransport:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, request):
            self.calls += 1
            raise TimeoutError("after dispatch")

    transport = TimeoutTransport()
    payments = PaymentService(
        stripe=StripeLinkAdapter(
            transport=transport,
            account_profiles={
                BusinessUnit.HOSTEL: "hostel",
                BusinessUnit.AGENCY: "agency",
            },
            enabled=True,
        ),
        wise=WiseInstructionAdapter(instructions={"receiver:hostel": "Transferência Wise pendente de verificação."}),
        pix=PixInstructionAdapter(knowledge=Knowledge()),
    )
    store = SQLitePaymentInitiationStore(
        tmp_path / "payment-timeout.sqlite3",
        result_encryption_key=RESULT_KEY,
    )
    selection = PaymentSelection(HOSTEL, PaymentMethod.STRIPE)
    store.enqueue(selection, now=NOW)
    worker = PaymentInitiationWorker(
        store=store,
        payments=payments,
        worker_id="worker:payment-timeout",
        lease_ttl=timedelta(seconds=30),
    )

    first = worker.run_once(now=NOW + timedelta(seconds=1))
    second = worker.run_once(now=NOW + timedelta(seconds=2))

    assert first.disposition is PaymentInitiationDisposition.MANUAL_REVIEW
    assert second.disposition is PaymentInitiationDisposition.IDLE
    assert transport.calls == 1


def test_closed_effect_guard_leaves_selection_unfenced_and_calls_no_provider(
    tmp_path: Path,
) -> None:
    class ClosedGuard:
        def allows_workflow(self, workflow_id: str) -> bool:
            return False

    payments, transport, _ = service()
    store = SQLitePaymentInitiationStore(
        tmp_path / "payment-closed-window.sqlite3",
        result_encryption_key=RESULT_KEY,
    )
    selection = PaymentSelection(HOSTEL, PaymentMethod.STRIPE)
    store.enqueue(selection, now=NOW)
    worker = PaymentInitiationWorker(
        store=store,
        payments=payments,
        worker_id="worker:payment-closed-window",
        lease_ttl=timedelta(seconds=30),
        effect_guard=ClosedGuard(),
    )

    result = worker.run_once(now=NOW + timedelta(seconds=1))

    assert result.disposition is PaymentInitiationDisposition.IDLE
    assert transport.requests == []
    assert store.dispatch_slots(selection) == 0
