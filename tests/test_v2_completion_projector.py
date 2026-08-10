from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_v2_outcome_projector import (
    NOW,
    US_PHONE,
    _finish_next,
    _package_command,
    _persist,
    _stores,
)
from reservation_domain import ExecutionCertainty
from v2_adapters.stripe import StripeLinkAdapter
from v2_adapters.wise import WiseInstructionAdapter
from v2_application.completion import PublicOutboxStore
from v2_application.completion_projector import (
    CompletionProjector,
    _confirmation_text,
    _payment_text,
)
from v2_application.payments import PaymentInitiationWorker, PaymentService
from v2_application.reservations import ReservationAllocator
from v2_contracts.localization import CustomerLanguage
from v2_contracts.channel import PublicMessageAuthor
from v2_contracts.payments import BusinessUnit, PaymentMethod


class _ClosedInstruction:
    def instruction(self, obligation):
        raise AssertionError(f"unexpected non-Stripe obligation: {obligation.payment_id}")


class _ClosedStripe:
    def create_link(self, obligation):
        raise AssertionError(f"unexpected Stripe obligation: {obligation.payment_id}")


class _StripeTransport:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if self.fail:
            raise TimeoutError("provider response lost")
        return {
            "link_id": f"plink-test-{request.business_unit.value}",
            "url": (
                "https://buy.stripe.com/test_"
                f"{request.business_unit.value}_completion"
            ),
        }


def _payment_worker(payments, transport: _StripeTransport) -> PaymentInitiationWorker:
    stripe = StripeLinkAdapter(
        transport=transport,
        account_profiles={
            BusinessUnit.HOSTEL: "stripe-account:hostel:test",
            BusinessUnit.AGENCY: "stripe-account:agency:test",
        },
        enabled=True,
        subscriber_id="1873018537",
    )
    closed = _ClosedInstruction()
    return PaymentInitiationWorker(
        store=payments,
        payments=PaymentService(stripe=stripe, wise=closed, pix=closed),
        worker_id="worker:completion-test",
        lease_ttl=timedelta(seconds=30),
    )


def _completion(execution, payments, public: PublicOutboxStore) -> CompletionProjector:
    return CompletionProjector(
        execution=execution,
        payment_store=payments,
        public_store=public,
        subscriber_id="1873018537",
        account_profiles={
            BusinessUnit.HOSTEL: "stripe-account:hostel:test",
            BusinessUnit.AGENCY: "stripe-account:agency:test",
        },
    )


def test_payment_link_text_uses_the_natural_article_for_each_business_unit() -> None:
    assert _payment_text(
        BusinessUnit.HOSTEL,
        "https://buy.stripe.com/test_hostel",
        CustomerLanguage.PT_BR,
    ) == "Link de pagamento da hospedagem: https://buy.stripe.com/test_hostel"
    assert _payment_text(
        BusinessUnit.AGENCY,
        "https://buy.stripe.com/test_tour",
        CustomerLanguage.PT_BR,
    ) == "Link de pagamento do passeio: https://buy.stripe.com/test_tour"
    assert _payment_text(
        BusinessUnit.HOSTEL,
        "https://buy.stripe.com/test_hostel",
        CustomerLanguage.EN,
    ) == "Accommodation payment link: https://buy.stripe.com/test_hostel"
    assert _payment_text(
        BusinessUnit.AGENCY,
        "https://buy.stripe.com/test_tour",
        CustomerLanguage.EN,
    ) == "Tour payment link: https://buy.stripe.com/test_tour"
    with pytest.raises(ValueError, match="customer_language"):
        _payment_text(
            BusinessUnit.AGENCY,
            "https://buy.stripe.com/test_tour",
            None,
        )


def test_single_lodging_confirmation_enters_public_outbox_once(
    tmp_path: Path,
) -> None:
    execution, payments, outcome = _stores(tmp_path)
    public = PublicOutboxStore((tmp_path / "public-lodging.sqlite3").resolve())
    try:
        command = next(
            item
            for item in ReservationAllocator().allocate(_package_command()).commands
            if item.operation.value == "reserve_lodging"
        )
        _persist(execution, (command,))
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        assert outcome.run_once(now=NOW + timedelta(seconds=2)).inserted == 1

        projector = _completion(execution, payments, public)
        first = projector.run_once(now=NOW + timedelta(seconds=3))
        replay = projector.run_once(now=NOW + timedelta(seconds=4))

        assert first.inserted == 1
        assert replay.inserted == 0
        texts = tuple(
            row[0]
            for row in public._connection.execute(
                "SELECT text FROM public_outbox ORDER BY release_id,chunk_index"
            )
        )
        assert texts == ("Sua hospedagem foi confirmada.",)
    finally:
        public.close()
        payments.close()
        execution.close()


def test_single_activity_confirmation_enters_public_outbox_once(
    tmp_path: Path,
) -> None:
    execution, payments, outcome = _stores(tmp_path)
    public = PublicOutboxStore((tmp_path / "public-activity.sqlite3").resolve())
    try:
        command = next(
            item
            for item in ReservationAllocator().allocate(
                _package_command(phone_e164=US_PHONE)
            ).commands
            if item.operation.value == "book_activity"
        )
        _persist(execution, (command,))
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        assert outcome.run_once(now=NOW + timedelta(seconds=2)).inserted == 1

        projector = _completion(execution, payments, public)
        first = projector.run_once(now=NOW + timedelta(seconds=3))
        replay = projector.run_once(now=NOW + timedelta(seconds=4))

        assert first.inserted == 1
        assert replay.inserted == 0
        texts = tuple(
            row[0]
            for row in public._connection.execute(
                "SELECT text FROM public_outbox ORDER BY release_id,chunk_index"
            )
        )
        assert texts == ("Your tour has been confirmed.",)
    finally:
        public.close()
        payments.close()
        execution.close()


def test_package_confirmation_and_two_links_enter_public_outbox_once(
    tmp_path: Path,
) -> None:
    execution, payments, outcome = _stores(tmp_path)
    public = PublicOutboxStore((tmp_path / "public.sqlite3").resolve())
    try:
        commands = ReservationAllocator().allocate(_package_command()).commands
        _persist(execution, commands)
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=2),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        assert outcome.run_once(now=NOW + timedelta(seconds=3)).inserted == 2
        worker = _payment_worker(payments, _StripeTransport())
        worker.run_once(now=NOW + timedelta(seconds=4))
        worker.run_once(now=NOW + timedelta(seconds=5))

        projector = _completion(execution, payments, public)
        first = projector.run_once(now=NOW + timedelta(seconds=6))
        replay = _completion(execution, payments, public).run_once(
            now=NOW + timedelta(seconds=7)
        )

        assert first.inserted == 3
        assert replay.inserted == 0
        rows = public._connection.execute(
            "SELECT release_id,text,author FROM public_outbox "
            "ORDER BY release_id,chunk_index"
        ).fetchall()
        assert len(rows) == 3
        texts = tuple(row[1] for row in rows)
        assert {
            PublicMessageAuthor(row[2]) for row in rows
        } == {PublicMessageAuthor.AUTHENTICATED_SYSTEM}
        assert sum("confirmad" in text.casefold() for text in texts) == 1
        assert sum("https://buy.stripe.com/" in text for text in texts) == 2
        assert all("product:" not in text for text in texts)
        assert rows[0][0].startswith("release:00-reservation:")
    finally:
        public.close()
        payments.close()
        execution.close()


def test_foreign_phone_package_uses_english_confirmation_and_links(
    tmp_path: Path,
) -> None:
    execution, payments, outcome = _stores(tmp_path)
    public = PublicOutboxStore((tmp_path / "public-english-package.sqlite3").resolve())
    try:
        commands = ReservationAllocator().allocate(
            _package_command(phone_e164=US_PHONE)
        ).commands
        _persist(execution, commands)
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=2),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        assert outcome.run_once(now=NOW + timedelta(seconds=3)).inserted == 2
        worker = _payment_worker(payments, _StripeTransport())
        worker.run_once(now=NOW + timedelta(seconds=4))
        worker.run_once(now=NOW + timedelta(seconds=5))

        projected = _completion(execution, payments, public).run_once(
            now=NOW + timedelta(seconds=6)
        )

        assert projected.inserted == 3
        texts = tuple(
            row[0]
            for row in public._connection.execute(
                "SELECT text FROM public_outbox ORDER BY release_id,chunk_index"
            )
        )
        assert "Your accommodation and tour have been confirmed." in texts
        assert any(text.startswith("Accommodation payment link: ") for text in texts)
        assert any(text.startswith("Tour payment link: ") for text in texts)
        assert all("Link de pagamento" not in text for text in texts)
    finally:
        public.close()
        payments.close()
        execution.close()


def test_confirmation_rejects_mixed_phone_languages_in_one_group() -> None:
    brazilian = ReservationAllocator().allocate(_package_command()).commands
    foreign = ReservationAllocator().allocate(
        _package_command(phone_e164=US_PHONE)
    ).commands

    with pytest.raises(RuntimeError, match="customer language"):
        _confirmation_text((brazilian[0], foreign[1]))


def test_unknown_stripe_link_never_enters_public_outbox(tmp_path: Path) -> None:
    execution, payments, outcome = _stores(tmp_path)
    public = PublicOutboxStore((tmp_path / "public.sqlite3").resolve())
    try:
        command = ReservationAllocator().allocate(_package_command()).commands[0]
        _persist(execution, (command,))
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        assert outcome.run_once(now=NOW + timedelta(seconds=2)).inserted == 1
        result = _payment_worker(payments, _StripeTransport(fail=True)).run_once(
            now=NOW + timedelta(seconds=3)
        )
        assert result.disposition.value == "manual_review"

        projected = _completion(execution, payments, public).run_once(
            now=NOW + timedelta(seconds=4)
        )

        assert projected.inserted == 1
        texts = tuple(
            row[0]
            for row in public._connection.execute(
                "SELECT text FROM public_outbox ORDER BY release_id"
            )
        )
        assert len(texts) == 1
        assert "confirmad" in texts[0].casefold()
        assert "http" not in texts[0]
    finally:
        public.close()
        payments.close()
        execution.close()


def test_wise_instruction_enters_public_outbox_after_confirmed_reservation(
    tmp_path: Path,
) -> None:
    execution, payments, outcome = _stores(
        tmp_path,
        enabled_methods=(PaymentMethod.WISE,),
    )
    public = PublicOutboxStore((tmp_path / "public-wise.sqlite3").resolve())
    try:
        command = ReservationAllocator().allocate(
            _package_command(payment_method="wise")
        ).commands[0]
        _persist(execution, (command,))
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        assert outcome.run_once(now=NOW + timedelta(seconds=2)).inserted == 1
        worker = PaymentInitiationWorker(
            store=payments,
            payments=PaymentService(
                stripe=_ClosedStripe(),
                wise=WiseInstructionAdapter(
                    instructions={
                        "stripe-account:hostel:test": "Dados Wise da hospedagem; aguarde validação.",
                        "stripe-account:agency:test": "Dados Wise da agência; aguarde validação.",
                    }
                ),
                pix=_ClosedInstruction(),
            ),
            worker_id="worker:wise-completion-test",
            lease_ttl=timedelta(seconds=30),
        )
        assert worker.run_once(
            now=NOW + timedelta(seconds=3)
        ).disposition.value == "completed"

        projected = _completion(execution, payments, public).run_once(
            now=NOW + timedelta(seconds=4)
        )

        assert projected.inserted == 2
        texts = tuple(
            row[0]
            for row in public._connection.execute(
                "SELECT text FROM public_outbox ORDER BY release_id"
            )
        )
        assert any("Dados Wise da hospedagem" in text for text in texts)
        assert all("https://" not in text for text in texts)
    finally:
        public.close()
        payments.close()
        execution.close()
