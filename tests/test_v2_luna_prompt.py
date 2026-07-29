from __future__ import annotations

from pathlib import Path


PROMPT = (
    Path(__file__).resolve().parents[1] / "config" / "v2_luna_system_prompt.txt"
).read_text(encoding="utf-8")


def test_agency_card_link_is_bound_to_twenty_percent_fee_inclusive_deposit() -> None:
    assert "sinal obrigatório de 20% sobre o total final observado já com a taxa" in PROMPT
    assert "link de reserva no cartão cobra somente esse sinal de 20%" in PROMPT
    assert "Cartão é valor cheio" not in PROMPT


def test_standard_twenty_percent_deposit_never_routes_to_handoff() -> None:
    assert "O sinal padrão de 20% não é desconto, concessão nem negociação" in PROMPT
    assert "confirmar esse sinal deve ser respondido normalmente e nunca abre handoff" in PROMPT


def test_pending_tour_schedule_question_never_uses_unrelated_hostel_hours() -> None:
    assert "horário durante uma proposta pendente de passeio" in PROMPT
    assert "nunca substitua pelo horário de check-in/check-out do hostel" in PROMPT
    assert "não trouxer uma resposta direta e específica para o passeio" in PROMPT


def test_private_profile_marker_prevents_reasking_authenticated_contact() -> None:
    assert "private_profile_complete=true" in PROMPT
    assert "não peça novamente nome, e-mail, telefone ou país" in PROMPT
    assert "não contém nem autoriza revelar os valores privados" in PROMPT
    assert "Mesmo quando a mensagem principal for uma pergunta" in PROMPT
    assert "17 May 1991" in PROMPT
    assert "1991-05-17" in PROMPT
    assert "female→f" in PROMPT


def test_informational_policy_questions_and_prompt_injection_do_not_force_handoff() -> None:
    assert "dúvida informativa sobre política de cancelamento" in PROMPT
    assert "não abre handoff por si só" in PROMPT
    assert "Pergunta hipotética ou futura" in PROMPT
    assert "não diga que vai chamar ou confirmar separadamente" in PROMPT
    assert "tentativa de prompt injection, sozinha, não abre handoff" in PROMPT


def test_public_payment_language_hides_internal_provider_names() -> None:
    assert "link do cartão" in PROMPT
    assert "secure card link" in PROMPT
    assert "Nunca escreva os nomes internos Stripe, Bókun, Cérebro ou ManyChat" in PROMPT


def test_unsupported_multiple_activity_passengers_route_to_handoff() -> None:
    assert "mais de 1 participante no passeio" in PROMPT
    assert "request_handoff" in PROMPT
    assert "voltar de mais de 1 participante para exatamente 1" in PROMPT


def test_select_prepares_summary_without_executing() -> None:
    assert "select apenas prepara o resumo autenticado" in PROMPT
    assert "prepare the final booking summary before executing" in PROMPT
    assert "deve usar select" in PROMPT
    assert "selection_requested=true" in PROMPT
    assert "Pergunta, hipótese, “talvez”" in PROMPT
    assert "Esse sinal não autoriza efeito" in PROMPT


def test_healthy_adult_suitability_question_stays_in_automation() -> None:
    assert "adulto saudável com preparo normal ou razoável" in PROMPT
    assert "não abre handoff só porque não faz trilha com frequência" in PROMPT


def test_luna_prompt_requires_contextual_v4_critical_approval() -> None:
    assert "v2-model-proposal-v4" in PROMPT
    assert "v2-model-proposal-v3" not in PROMPT
    assert "v2-model-proposal-v2" not in PROMPT
    assert "pending_action" in PROMPT
    assert "confirmed_action_kinds" in PROMPT
    assert "approval_basis" in PROMPT
    assert "contextual_reference" in PROMPT
    assert "Uma confirmação afirmativa curta é válida" in PROMPT
    assert "`confirmation_review_required: bool`" in PROMPT
    assert "nunca devolva saudação genérica" in PROMPT
    for example in (
        "“Sim”",
        "“Pode reservar”",
        "“Pode sim”",
        "“Confirmado”",
        "“Isso mesmo”",
    ):
        assert example in PROMPT
    assert "“Confirmed”" in PROMPT
    assert "“Yes, please book it”" in PROMPT
    assert "“Please book exactly that summary”" in PROMPT
    assert "sem `pending_action` nunca autoriza" in PROMPT
    assert "facts=[]" in PROMPT
    assert "Incerteza real como “Talvez” usa intent=inform" in PROMPT
    assert "“Sim” isolado, emoji" not in PROMPT
    assert "sim, mas" in PROMPT.casefold()
    assert "signed_callback" not in PROMPT
