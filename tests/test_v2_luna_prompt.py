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


def test_luna_prompt_requires_contextual_v3_critical_approval() -> None:
    assert "v2-model-proposal-v3" in PROMPT
    assert "v2-model-proposal-v2" not in PROMPT
    assert "pending_action" in PROMPT
    assert "confirmed_action_kinds" in PROMPT
    assert "approval_basis" in PROMPT
    assert "contextual_reference" in PROMPT
    assert "Uma confirmação afirmativa curta é válida" in PROMPT
    for example in (
        "“Sim”",
        "“Pode reservar”",
        "“Pode sim”",
        "“Confirmado”",
        "“Isso mesmo”",
    ):
        assert example in PROMPT
    assert "sem `pending_action` nunca autoriza" in PROMPT
    assert "facts=[]" in PROMPT
    assert "Incerteza real como “Talvez” usa intent=inform" in PROMPT
    assert "“Sim” isolado, emoji" not in PROMPT
    assert "sim, mas" in PROMPT.casefold()
    assert "signed_callback" not in PROMPT
