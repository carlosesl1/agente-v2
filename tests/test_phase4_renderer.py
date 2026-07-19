from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from reservation_confirmation import (
    SummaryLocale,
    prepare_summary,
    render_summary,
)
from reservation_domain import (
    AddOn,
    CommercialDraft,
    CustomerFacts,
    EconomicTerms,
    Money,
    OfferSnapshot,
    Party,
    ReadyToSummarizeState,
    ServiceKind,
    StateMeta,
    build_commercial_draft,
)

UTC = timezone.utc
T0 = datetime(2027, 2, 1, 12, 0, tzinfo=UTC)


def lodging_offer(*, label: str = "Quarto sintético", total: str = "300.00") -> OfferSnapshot:
    return OfferSnapshot(
        offer_id="offer:lodging:alpha",
        lookup_id="lookup:lodging:alpha",
        service=ServiceKind.LODGING,
        provider_ref="cloudbeds.property.alpha.room.shared.rate.flex",
        public_label=label,
        start_date=date(2027, 3, 10),
        end_date=date(2027, 3, 13),
        start_time=None,
        party=Party(adults=2, children=1),
        total=Money(amount=Decimal(total), currency="BRL"),
        available=True,
    )


def activity_offer(*, label: str = "Trilha sintética", total: str = "200.00") -> OfferSnapshot:
    return OfferSnapshot(
        offer_id="offer:activity:alpha",
        lookup_id="lookup:activity:alpha",
        service=ServiceKind.ACTIVITY,
        provider_ref="bokun.product.alpha.start.morning.rate.standard",
        public_label=label,
        start_date=date(2027, 3, 11),
        end_date=None,
        start_time="08:00",
        party=Party(adults=2, children=1),
        total=Money(amount=Decimal(total), currency="BRL"),
        available=True,
    )


def package_draft(
    *,
    components: tuple[OfferSnapshot, ...] | None = None,
    customer: CustomerFacts | None = None,
    terms: EconomicTerms | None = None,
    version: int = 1,
) -> CommercialDraft:
    return build_commercial_draft(
        draft_id="draft:package:alpha",
        version=version,
        created_at=T0,
        components=components or (lodging_offer(), activity_offer()),
        customer=customer
        or CustomerFacts(
            customer_ref="customer:alpha",
            full_name="Pessoa Sintética",
            email="synthetic.person@example.invalid",
            phone_e164="+99900000001",
            country_code="ZZ",
        ),
        terms=terms
        or EconomicTerms(
            payment_method="card",
            add_ons=(
                AddOn(
                    code="breakfast",
                    quantity=2,
                    unit_price=Money(amount=Decimal("30.00"), currency="BRL"),
                ),
            ),
        ),
    )


def ready_state(draft: CommercialDraft | None = None) -> ReadyToSummarizeState:
    return ReadyToSummarizeState(
        meta=StateMeta(
            workflow_id="workflow:package:alpha",
            revision=4,
            last_event_at=T0,
            seen_event_ids=("evt:1", "evt:2", "evt:3", "evt:4"),
            seen_event_hashes=("1" * 64, "2" * 64, "3" * 64, "4" * 64),
            command_ids=(),
        ),
        draft=draft or package_draft(),
    )


class RendererTests(unittest.TestCase):
    def test_pt_package_summary_is_exact_and_has_no_effect_claim(self) -> None:
        rendered = render_summary(package_draft(), locale=SummaryLocale.PT_BR)
        self.assertEqual(
            rendered.content,
            "\n".join(
                (
                    "Resumo do pedido",
                    "Versão: 1",
                    "",
                    "Passeio: Trilha sintética",
                    "Data: 2027-03-11",
                    "Horário: 08:00",
                    "Pessoas: 2 adultos, 1 criança",
                    "Valor: BRL 200.00",
                    "",
                    "Hospedagem: Quarto sintético",
                    "Data: 2027-03-10",
                    "Até: 2027-03-13",
                    "Pessoas: 2 adultos, 1 criança",
                    "Valor: BRL 300.00",
                    "",
                    "Cliente",
                    "Nome: Pessoa Sintética",
                    "E-mail: synthetic.person@example.invalid",
                    "Telefone: +99900000001",
                    "País: ZZ",
                    "Pagamento: card",
                    "",
                    "Adicionais",
                    "- breakfast × 2: BRL 30.00 cada = BRL 60.00",
                    "",
                    "Subtotal das opções: BRL 500.00",
                    "Total de adicionais: BRL 60.00",
                    "Total do pedido: BRL 560.00",
                    "",
                    "Nenhuma reserva foi criada. Confirma este resumo ou deseja ajustar algo?",
                )
            ),
        )
        self.assertEqual(rendered.claim_status, "none")
        self.assertEqual(rendered.private_fields, ())
        self.assertNotIn("Total confirmado", rendered.content)
        self.assertNotIn("reserva confirmada", rendered.content.casefold())

    def test_en_summary_uses_versioned_english_template(self) -> None:
        rendered = render_summary(package_draft(), locale=SummaryLocale.EN)
        self.assertTrue(rendered.content.startswith("Order summary\nVersion: 1"))
        self.assertIn("Activity: Trilha sintética", rendered.content)
        self.assertIn("Lodging: Quarto sintético", rendered.content)
        self.assertIn("Payment: card", rendered.content)
        self.assertTrue(
            rendered.content.endswith(
                "No booking has been created. Do you confirm this summary or want to adjust it?"
            )
        )

    def test_summary_is_deterministic_and_order_independent(self) -> None:
        first = render_summary(package_draft(), locale=SummaryLocale.PT_BR)
        second = render_summary(
            package_draft(components=(activity_offer(), lodging_offer())),
            locale=SummaryLocale.PT_BR,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.content_hash, second.content_hash)

    def test_locale_and_each_reviewable_change_change_content_hash(self) -> None:
        base = package_draft()
        baseline = render_summary(base, locale=SummaryLocale.PT_BR)
        variants = (
            (base, SummaryLocale.EN),
            (package_draft(components=(lodging_offer(total="301.00"), activity_offer())), SummaryLocale.PT_BR),
            (package_draft(customer=replace(base.customer, full_name="Outra Pessoa Sintética")), SummaryLocale.PT_BR),
            (package_draft(terms=EconomicTerms(payment_method="cash", add_ons=base.terms.add_ons)), SummaryLocale.PT_BR),
        )
        for draft, locale in variants:
            with self.subTest(locale=locale, signature=draft.subject_signature):
                rendered = render_summary(draft, locale=locale)
                self.assertNotEqual(rendered.content_hash, baseline.content_hash)

    def test_private_domain_identifiers_never_appear(self) -> None:
        draft = package_draft()
        rendered = render_summary(draft, locale=SummaryLocale.PT_BR)
        private = (
            draft.subject_signature,
            draft.draft_id,
            draft.customer.customer_ref,
            *(
                value
                for component in draft.components
                for value in (
                    component.offer_id,
                    component.lookup_id,
                    component.provider_ref,
                )
            ),
        )
        for value in private:
            with self.subTest(value=value):
                self.assertNotIn(value, rendered.content)

    def test_public_text_is_nfkc_whitespace_canonical(self) -> None:
        rendered = render_summary(
            package_draft(
                components=(
                    lodging_offer(label="  Quarto\u00a0  sintético  "),
                    activity_offer(),
                )
            ),
            locale=SummaryLocale.PT_BR,
        )
        self.assertIn("Hospedagem: Quarto sintético", rendered.content)
        self.assertNotIn("\u00a0", rendered.content)

    def test_renderer_rejects_public_label_equal_to_private_identifier(self) -> None:
        offer = lodging_offer(label="cloudbeds.property.alpha.room.shared.rate.flex")
        with self.assertRaisesRegex(ValueError, "private"):
            render_summary(
                package_draft(components=(offer, activity_offer())),
                locale=SummaryLocale.PT_BR,
            )

    def test_prepare_summary_derives_ids_and_exact_domain_event(self) -> None:
        state = ready_state()
        prepared = prepare_summary(
            state,
            locale=SummaryLocale.PT_BR,
            presented_at=T0 + timedelta(seconds=1),
        )
        repeated = prepare_summary(
            state,
            locale=SummaryLocale.PT_BR,
            presented_at=T0 + timedelta(seconds=1),
        )
        self.assertEqual(prepared, repeated)
        self.assertTrue(prepared.summary_event_id.startswith("summary:"))
        self.assertTrue(prepared.outbox_message_id.startswith("outbox:"))
        self.assertEqual(prepared.event.summary_event_id, prepared.summary_event_id)
        self.assertEqual(prepared.event.outbox_message_id, prepared.outbox_message_id)
        self.assertEqual(prepared.event.draft_version, state.draft.version)
        self.assertEqual(prepared.event.subject_signature, state.draft.subject_signature)
        self.assertEqual(prepared.event.occurred_at, T0 + timedelta(seconds=1))

    def test_prepare_summary_rejects_wrong_state_and_time_before_draft(self) -> None:
        state = ready_state()
        with self.assertRaises(ValueError):
            prepare_summary(
                object(),  # type: ignore[arg-type]
                locale=SummaryLocale.PT_BR,
                presented_at=T0,
            )
        with self.assertRaisesRegex(ValueError, "predates"):
            prepare_summary(
                state,
                locale=SummaryLocale.PT_BR,
                presented_at=T0 - timedelta(seconds=1),
            )
        with self.assertRaises(ValueError):
            prepare_summary(
                state,
                locale="pt_BR",  # type: ignore[arg-type]
                presented_at=T0,
            )


if __name__ == "__main__":
    unittest.main()
