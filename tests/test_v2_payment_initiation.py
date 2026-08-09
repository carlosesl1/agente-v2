from __future__ import annotations

from dataclasses import replace
from datetime import date
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from v2_adapters.pix import PixInstructionAdapter
from v2_adapters.stripe import StripeLinkAdapter
from v2_adapters.wise import WiseInstructionAdapter
from v2_application.payments import (
    PaymentInitiationDisposition,
    PaymentInitiationWorker,
    PaymentService,
    SQLitePaymentInitiationStore,
    _offer_bytes,
    _offer_from_bytes,
    _selection_bytes,
    _selection_from_bytes,
)
from v2_contracts.localization import CustomerLanguage
from v2_contracts.payments import (
    BusinessUnit,
    CheckoutService,
    DueKind,
    PaymentDisplayDetails,
    PaymentMethod,
    PaymentObligation,
    PaymentSelection,
    ReservationPaymentContext,
    StripeLinkRequest,
    StripePaymentLink,
)


HOSTEL_DETAILS = PaymentDisplayDetails(
    service=CheckoutService.LODGING,
    public_label="Suíte Casal",
    start_date=date(2026, 12, 2),
    end_date=date(2026, 12, 4),
    start_time=None,
    adults=1,
    children=0,
    reservation_total_minor=30000,
    package_component=False,
    customer_language=CustomerLanguage.PT_BR,
)
AGENCY_DETAILS = PaymentDisplayDetails(
    service=CheckoutService.ACTIVITY,
    public_label="Roteiro dos 4Ps",
    start_date=date(2026, 12, 3),
    end_date=None,
    start_time="08:30",
    adults=1,
    children=0,
    reservation_total_minor=45000,
    package_component=False,
    customer_language=CustomerLanguage.PT_BR,
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
    display_details=HOSTEL_DETAILS,
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
    display_details=AGENCY_DETAILS,
)


class StripeTransport:
    def __init__(self) -> None:
        self.requests: list[StripeLinkRequest] = []

    def __call__(self, request: StripeLinkRequest) -> dict[str, str]:
        self.requests.append(request)
        return {
            "link_id": f"plink_{request.account_profile_id}_{request.economic_version}",
            "url": f"https://buy.stripe.com/{request.payment_id}/{request.economic_version}",
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


def test_agency_stripe_link_charges_only_configured_twenty_percent_signal() -> None:
    transport = StripeTransport()
    adapter = StripeLinkAdapter(
        transport=transport,
        account_profiles={
            BusinessUnit.HOSTEL: "hostel",
            BusinessUnit.AGENCY: "agency",
        },
        enabled=True,
        payment_percentages={
            BusinessUnit.HOSTEL: 100,
            BusinessUnit.AGENCY: 20,
        },
    )

    adapter.create_link(AGENCY)

    assert transport.requests[0].amount_minor == 9000
    assert transport.requests[0].payment_percentage == 20


def test_agency_stripe_signal_uses_twenty_percent_of_fee_inclusive_bokun_total() -> None:
    transport = StripeTransport()
    adapter = StripeLinkAdapter(
        transport=transport,
        account_profiles={
            BusinessUnit.HOSTEL: "hostel",
            BusinessUnit.AGENCY: "agency",
        },
        enabled=True,
        payment_percentages={
            BusinessUnit.HOSTEL: 100,
            BusinessUnit.AGENCY: 20,
        },
    )
    fee_inclusive = replace(
        AGENCY,
        amount_minor=33495,
        display_details=replace(
            AGENCY.display_details,
            reservation_total_minor=33495,
        ),
    )

    adapter.create_link(fee_inclusive)

    assert transport.requests[0].amount_minor == 6699
    assert transport.requests[0].payment_percentage == 20


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
    assert instruction.public_text.endswith(
        knowledge.pix_instruction("receiver:agency")
    )
    assert instruction.public_text.startswith("Valor desta etapa: R$ 450,00.")
    assert instruction.settled is False


def test_agency_pix_and_wise_instructions_render_twenty_percent_signal() -> None:
    knowledge = Knowledge()
    percentages = {"receiver:hostel": 100, "receiver:agency": 20}
    wise = WiseInstructionAdapter(
        instructions={
            "receiver:hostel": "Transferência Wise do hostel; aguarde validação.",
            "receiver:agency": "Transferência Wise da agência; aguarde validação.",
        },
        payment_percentages=percentages,
    )
    pix = PixInstructionAdapter(
        knowledge=knowledge,
        receiver_profiles=("receiver:hostel", "receiver:agency"),
        payment_percentages=percentages,
    )

    assert wise.instruction(AGENCY).public_text.startswith(
        "Valor desta etapa: R$ 90,00."
    )
    assert pix.instruction(AGENCY).public_text.startswith(
        "Valor desta etapa: R$ 90,00."
    )


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
    assert changed.obligation.display_details.reservation_total_minor == 31000
    restored = replace(
        changed.obligation,
        amount_minor=HOSTEL.amount_minor,
        display_details=HOSTEL.display_details,
    )
    assert restored.reservation_anchor_id == HOSTEL.reservation_anchor_id


NOW = datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
RESULT_KEY = b"payment-result-test-key-00000001"

ACTIVITY_DETAILS = PaymentDisplayDetails(
    service=CheckoutService.ACTIVITY,
    public_label="Roteiro dos 4Ps",
    start_date=date(2026, 12, 3),
    end_date=None,
    start_time="08:30",
    adults=1,
    children=0,
    reservation_total_minor=33495,
    package_component=True,
    customer_language=CustomerLanguage.PT_BR,
)


def test_payment_display_details_validate_closed_service_shapes() -> None:
    assert ACTIVITY_DETAILS.public_label == "Roteiro dos 4Ps"
    with pytest.raises(ValueError, match="activity.*end_date"):
        replace(ACTIVITY_DETAILS, end_date=date(2026, 12, 4))
    with pytest.raises(ValueError, match="lodging.*end_date"):
        replace(
            ACTIVITY_DETAILS,
            service=CheckoutService.LODGING,
            end_date=None,
            start_time=None,
        )
    with pytest.raises(ValueError, match="lodging.*start_time"):
        replace(
            ACTIVITY_DETAILS,
            service=CheckoutService.LODGING,
            end_date=date(2026, 12, 4),
        )
    with pytest.raises(ValueError, match="adults"):
        replace(ACTIVITY_DETAILS, adults=0)
    with pytest.raises(TypeError, match="customer_language"):
        replace(ACTIVITY_DETAILS, customer_language="pt-BR")


def test_payment_selection_round_trips_display_details_and_decodes_legacy_rows() -> None:
    selection = PaymentSelection(
        replace(
            AGENCY,
            amount_minor=ACTIVITY_DETAILS.reservation_total_minor,
            display_details=ACTIVITY_DETAILS,
        ),
        PaymentMethod.STRIPE,
    )

    raw = _selection_bytes(selection)
    payload = json.loads(raw)

    assert payload["obligation"]["display_details"] == {
        "service": "activity",
        "public_label": "Roteiro dos 4Ps",
        "start_date": "2026-12-03",
        "end_date": None,
        "start_time": "08:30",
        "adults": 1,
        "children": 0,
        "reservation_total_minor": 33495,
        "package_component": True,
        "customer_language": "pt-BR",
    }
    assert _selection_from_bytes(raw) == selection

    del payload["obligation"]["display_details"]["customer_language"]
    legacy = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert (
        _selection_from_bytes(legacy).obligation.display_details.customer_language
        is None
    )

    del payload["obligation"]["display_details"]
    oldest = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert _selection_from_bytes(oldest).obligation.display_details is None


def test_payment_selection_rejects_unknown_display_fields() -> None:
    selection = PaymentSelection(
        replace(
            AGENCY,
            amount_minor=ACTIVITY_DETAILS.reservation_total_minor,
            display_details=ACTIVITY_DETAILS,
        ),
        PaymentMethod.STRIPE,
    )
    for field in ("customer_name", "provider_reference"):
        payload = json.loads(_selection_bytes(selection))
        payload["obligation"]["display_details"][field] = "forbidden"

        with pytest.raises(RuntimeError, match="selection is corrupt"):
            _selection_from_bytes(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )


def test_payment_service_propagates_closed_stripe_journal_authority() -> None:
    class JournaledStripe:
        def __init__(self) -> None:
            self.calls: list[tuple[PaymentObligation, dict[str, object]]] = []

        def create_link_journaled(
            self,
            obligation: PaymentObligation,
            **authority: object,
        ) -> StripePaymentLink:
            self.calls.append((obligation, authority))
            return StripePaymentLink(
                payment_id=obligation.payment_id,
                reservation_anchor_id=obligation.reservation_anchor_id,
                account_profile_id="stripe-account:hostel:test",
                economic_version=obligation.economic_version,
                public_url="https://buy.stripe.com/test_journal_authority",
                provider_reference_fingerprint="a" * 64,
                receipt_hash="b" * 64,
                customer_language=CustomerLanguage.PT_BR,
            )

        def create_link(self, obligation: PaymentObligation) -> StripePaymentLink:
            raise AssertionError("journaled initiation must not use legacy create")

    stripe = JournaledStripe()
    payments = PaymentService(
        stripe=stripe,
        wise=WiseInstructionAdapter(
            instructions={
                "receiver:hostel": "Transferência Wise pendente de verificação."
            }
        ),
        pix=PixInstructionAdapter(knowledge=Knowledge()),
    )

    offer = payments.initiate(
        HOSTEL,
        PaymentMethod.STRIPE,
        initiation_id="payment-initiation:journal:001",
        journal_worker_id="worker:payment-journal",
        journal_fencing_token=7,
    )

    assert offer.public_url == "https://buy.stripe.com/test_journal_authority"
    assert stripe.calls == [
        (
            HOSTEL,
            {
                "initiation_id": "payment-initiation:journal:001",
                "journal_worker_id": "worker:payment-journal",
                "journal_fencing_token": 7,
            },
        )
    ]


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
    assert store.completed_offers()[0].customer_language is CustomerLanguage.PT_BR
    persisted = b"".join(
        candidate.read_bytes()
        for candidate in path.parent.glob(path.name + "*")
    )
    assert b"https://buy.stripe.com" not in persisted


def test_legacy_stripe_obligation_fails_before_transport() -> None:
    payments, transport, _ = service()

    with pytest.raises(ValueError, match="display_details"):
        payments.initiate(
            replace(HOSTEL, display_details=None),
            PaymentMethod.STRIPE,
        )

    assert transport.requests == []

    with pytest.raises(ValueError, match="customer_language"):
        payments.initiate(
            replace(
                HOSTEL,
                display_details=replace(
                    HOSTEL_DETAILS,
                    customer_language=None,
                ),
            ),
            PaymentMethod.STRIPE,
        )

    assert transport.requests == []


def test_stripe_result_decodes_historical_row_without_customer_language() -> None:
    current = StripePaymentLink(
        payment_id="payment:hostel:legacy-language",
        reservation_anchor_id="anchor:hostel:legacy-language",
        account_profile_id="stripe-account:hostel:test",
        economic_version=1,
        public_url="https://buy.stripe.com/legacy-language",
        provider_reference_fingerprint="a" * 64,
        receipt_hash="b" * 64,
        customer_language=CustomerLanguage.PT_BR,
    )
    payload = json.loads(_offer_bytes(current))
    del payload["customer_language"]

    historical = _offer_from_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )

    assert historical.customer_language is None


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
