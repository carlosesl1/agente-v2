from __future__ import annotations

from pathlib import Path


PROMPT = (
    Path(__file__).resolve().parents[1] / "config" / "v2_luna_system_prompt.txt"
).read_text(encoding="utf-8")


def test_agency_stripe_link_is_bound_to_twenty_percent_fee_inclusive_deposit() -> None:
    assert "sinal obrigatório de 20% sobre o total final Bókun já com a taxa" in PROMPT
    assert "link Stripe de reserva cobra somente esse sinal de 20%" in PROMPT
    assert "Cartão é valor cheio" not in PROMPT


def test_standard_twenty_percent_deposit_never_routes_to_handoff() -> None:
    assert "O sinal padrão de 20% não é desconto, concessão nem negociação" in PROMPT
    assert "confirmar esse sinal deve ser respondido normalmente e nunca abre handoff" in PROMPT
