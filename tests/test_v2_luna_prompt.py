from __future__ import annotations

from pathlib import Path

PROMPT = (
    Path(__file__).resolve().parents[1] / "config" / "v2_luna_system_prompt.txt"
).read_text(encoding="utf-8")


def test_prompt_requires_direct_grounded_atendimento_process() -> None:
    assert "Responda primeiro à pergunta direta do lead" in PROMPT
    assert "não peça permissão para fazer uma consulta" in PROMPT
    assert "Nunca deduza privacidade, banheiro, silêncio" in PROMPT
    assert "preserve cada duração, distância, quantidade e etapa" in PROMPT
    assert "não some trechos consecutivos" in PROMPT
    assert "Só prometa verificar depois" in PROMPT
    assert "não acrescente elogios, popularidade, adequação ou benefícios comerciais" in PROMPT
    assert "A classificação canônica acima é a única justificativa disponível" in PROMPT
    assert "nunca pergunte se deve fazer a consulta que já ocorreu" in PROMPT
    assert "rótulo que declara explicitamente o público feminino, masculino ou misto" in PROMPT
    assert "acomodação restrita por gênero" in PROMPT


def test_unambiguous_current_message_language_overrides_phone_locale_fallback() -> None:
    assert "idioma inequívoco da mensagem atual prevalece" in PROMPT
    assert "locale do request funciona apenas como fallback" in PROMPT


def test_agency_card_link_is_bound_to_twenty_percent_fee_inclusive_deposit() -> None:
    assert "sinal obrigatório de 20% sobre o total final observado já com a taxa" in PROMPT
    assert "link de reserva no cartão cobra somente esse sinal de 20%" in PROMPT
    assert "Cartão é valor cheio" not in PROMPT


def test_standard_twenty_percent_deposit_never_routes_to_handoff() -> None:
    assert "O sinal padrão de 20% não é desconto, concessão nem negociação" in PROMPT
    assert "confirmar esse sinal deve ser respondido normalmente e nunca abre handoff" in PROMPT


def test_progressive_handoff_collects_useful_details_before_operational_transfer() -> None:
    assert "HANDOFF IMEDIATO" in PROMPT
    assert "TRIAGEM ANTES DO HANDOFF" in PROMPT
    assert "pedido explícito para falar com uma pessoa" in PROMPT
    assert "reclamação sensível" in PROMPT
    assert "risco ou dúvida real de segurança" in PROMPT
    assert "desconto, cupom ou negociação" in PROMPT
    assert "continue coletando" in PROMPT
    assert "Já temos tudo para realizar sua reserva" in PROMPT


def test_discount_does_not_handoff_after_only_interest_date_and_party() -> None:
    assert "passeio, data e quantidade não encerram a triagem" in PROMPT
    assert "peça o próximo dado faltante da reserva antes de request_handoff" in PROMPT


def test_progressive_handoff_never_overclaims_reservation_or_payment() -> None:
    assert "resultado da reserva for incerto" in PROMPT
    assert "não afirme que ela foi criada nem que não foi criada" in PROMPT
    assert "não repita nem recrie a reserva" in PROMPT


def test_pending_tour_schedule_question_never_uses_unrelated_hostel_hours() -> None:
    assert "horário durante uma proposta pendente de passeio" in PROMPT
    assert "nunca substitua pelo horário de check-in/check-out do hostel" in PROMPT
    assert "não trouxer uma resposta direta e específica para o passeio" in PROMPT


def test_private_profile_marker_prevents_reasking_authenticated_contact() -> None:
    assert "private_profile_complete=true" in PROMPT
    assert "autenticou nome, e-mail e telefone do contato principal" in PROMPT
    assert "validou o país canônico do binding ou de um fato tipado persistido" in PROMPT
    assert "autenticou nome, e-mail, telefone e país do contato principal" not in PROMPT
    assert "país explicitamente informado na mensagem atual" in PROMPT
    assert "Estados Unidos (US)" in PROMPT
    assert "country_code=US" in PROMPT
    assert "mesmo com intent=inform" in PROMPT
    assert "não peça novamente esses dados do contato" in PROMPT
    assert "lista canônica que informa quais campos já existem" in PROMPT
    assert "Mesmo quando a mensagem principal for uma pergunta" in PROMPT
    assert "17 May 1991" in PROMPT
    assert "1991-05-17" in PROMPT
    assert "female→f" in PROMPT


def test_holder_ambiguity_never_blocks_a_complete_read_only_consultation() -> None:
    assert "Ambiguidade sobre qual acompanhante será o titular nunca bloqueia" in PROMPT
    assert "identidade do titular é requisito para selecionar/reservar" in PROMPT
    assert "não para consultar" in PROMPT


def test_adult_count_never_implies_zero_children_for_lodging_read() -> None:
    assert "Uma contagem de adultos nunca informa a quantidade de crianças" in PROMPT
    assert "inclusive “somos 2 adultos” ou “para dois adultos”" in PROMPT
    assert (
        "Somente uma afirmação explícita de que não há crianças autoriza children=0"
        in PROMPT
    )


def test_every_public_missing_data_question_is_written_once_in_reply_chunks() -> None:
    assert "Escreva cada mensagem pública uma única vez" in PROMPT
    assert '`{"text":"...","expects_reply":true|false}`' in PROMPT
    assert "marque `expects_reply=true` somente no último chunk" in PROMPT
    assert "Toda pergunta pública para obter um dado faltante deve aparecer byte a byte" not in PROMPT
    assert "clarification_question" not in PROMPT


def test_explanation_and_question_need_no_duplicate_protocol_field() -> None:
    assert "Se precisar explicar antes de perguntar" in PROMPT
    assert "um chunk explicativo e um último chunk com `expects_reply=true`" in PROMPT
    assert "o segundo deve ser exatamente clarification_question" not in PROMPT


def test_payer_correction_preserves_private_holder_and_commercial_gate() -> None:
    assert "Quem paga não substitui semanticamente quem é o titular" in PROMPT
    assert (
        "continue perguntando apenas o dado comercial faltante antes de qualquer novo read"
        in PROMPT
    )


def test_voluntarily_supplied_customer_data_may_be_repeated_in_public_reply() -> None:
    assert "Dados que o lead enviou voluntariamente" in PROMPT
    assert "podem ser repetidos" in PROMPT
    assert "não repita nomes inteiros, prenomes, sobrenomes" not in PROMPT
    assert "não repita nomes nem e-mail do corpus privado" not in PROMPT


def test_prompt_has_no_privacy_rewrite_pass() -> None:
    assert "CHECAGEM FINAL OBRIGATÓRIA ANTES DE EMITIR O JSON" not in PROMPT
    assert "releia cada reply_chunk e clarification_question" not in PROMPT
    assert "reescreva o chunk usando somente titular, pagador ou acompanhante" not in PROMPT


def test_runtime_markers_keep_execution_and_recap_read_only() -> None:
    assert "`active_execution_status`" in PROMPT
    assert "uma reserva já está em processamento" in PROMPT
    assert "nunca nova escolha, read, seleção, confirmação ou promessa de reenvio" in PROMPT
    assert "Você decide quando uma consulta nova é necessária" in PROMPT
    assert "emita read_requests mesmo que os parâmetros sejam iguais ao histórico" in PROMPT
    assert "Para apenas recapitular, use consultation_history e read_requests=[]" in PROMPT
    assert "histórico não autoriza reservas" in PROMPT


def test_explicit_service_dates_and_party_counts_are_committed_as_facts() -> None:
    assert "Toda data de serviço explícita na mensagem atual" in PROMPT
    assert "toda quantidade explícita de adultos/crianças/participantes" in PROMPT
    assert "não mencione a data ou quantidade em reply_chunks sem também persisti-la" in PROMPT
    assert "Nov 18 2026" in PROMPT
    assert "one person" in PROMPT


def test_profile_completion_or_refresh_never_routes_to_handoff_by_itself() -> None:
    assert "Atualizar, reler ou verificar o perfil privado, por si só" in PROMPT
    assert "sem request_handoff motivado pelo perfil" in PROMPT
    assert "continuam sendo motivos independentes" in PROMPT
    for independent_reason in (
        "Pedido humano explícito",
        "desconto/negociação real",
        "restrição de segurança",
    ):
        assert independent_reason in PROMPT


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


def test_multiple_activity_passengers_use_complete_private_manifest() -> None:
    assert "qualquer composição positiva aceita pela oferta" in PROMPT
    assert "manifesto individual estiver completo" in PROMPT
    assert "Tamanho do grupo não abre handoff por si só" in PROMPT
    assert "passenger_manifest_status" in PROMPT
    assert "posição" in PROMPT
    assert "campos ainda não fornecidos são null" in PROMPT
    assert "`handoff_status`" in PROMPT
    assert "acknowledgement_pending" in PROMPT
    assert "nunca diga que uma pessoa recebeu" in PROMPT
    assert "Não use request_handoff novamente" in PROMPT


def test_select_prepares_summary_without_executing() -> None:
    assert "select apenas prepara o resumo autenticado" in PROMPT
    assert "prepare the final booking summary before executing" in PROMPT
    assert "deve usar select" in PROMPT
    assert "selection_requested=true" in PROMPT
    assert "Pergunta, hipótese, “talvez”" in PROMPT
    assert "Esse sinal não autoriza efeito" in PROMPT


def test_model_owns_dynamic_language_and_atomic_package_selection() -> None:
    assert "única responsável pela interpretação semântica" in PROMPT
    assert "o pai não usa palavras-chave ou regex" in PROMPT
    assert "referências naturais do lead" in PROMPT
    assert "selecione atomicamente as duas `choice_ref`" in PROMPT
    assert "não copie `offer_id`" in PROMPT
    assert "não troque pela primeira opção mostrada" in PROMPT
    assert "adjudicação semântica pós-consulta" in PROMPT
    assert "não crie nem altere fatos" in PROMPT
    assert "repita somente os fatos comerciais exatos exigidos" in PROMPT
    assert "inclusive quando `service=package`" in PROMPT
    assert "segunda interpretação semântica auditada" in PROMPT
    assert "Primeiro extraia todo `birth_date`/`gender`" in PROMPT


def test_healthy_adult_suitability_question_stays_in_automation() -> None:
    assert "adulto saudável com preparo normal ou razoável" in PROMPT
    assert "não abre handoff só porque não faz trilha com frequência" in PROMPT


def test_missing_age_or_suitability_guidance_never_becomes_reassurance() -> None:
    assert "age_guidance=null ou suitability_guidance=null" in PROMPT
    assert "não existe orientação autenticada para essa afirmação" in PROMPT
    assert "não conclua que a idade não impede" in PROMPT.casefold()
    assert "pergunte naturalmente pelo contexto relevante" in PROMPT


def test_activity_availability_semantics_are_not_duplicated_in_the_prompt() -> None:
    assert "available=true significa disponível" not in PROMPT
    assert "available=false significa indisponível" not in PROMPT
    assert "metadados de grupo não podem inverter `available`" not in PROMPT.casefold()
    assert "Nunca deduza disponibilidade por total_amount" not in PROMPT


def test_luna_prompt_requires_minimal_v8_with_parent_owned_authority() -> None:
    assert "oito campos conversacionais" in PROMPT
    assert (
        "intent,reply_chunks,facts,read_requests,selected_choice_refs,"
        "selection_requested,pending_action_disposition,passengers"
    ) in PROMPT
    assert "v2-model-proposal-v7" not in PROMPT
    assert "v2-model-proposal-v6" not in PROMPT
    assert "pending_action" in PROMPT
    assert "Apenas reconheça semanticamente a confirmação com `intent=confirm`" in PROMPT
    assert "O pai vincula a confirmação" in PROMPT
    assert "Não copie versão, tipos de ação, approval ou IDs" in PROMPT
    assert "Uma confirmação afirmativa curta é válida" in PROMPT
    assert "`confirmation_review_required: bool`" in PROMPT
    assert "`selection_review_required: bool`" in PROMPT
    assert "`progress_review_required: bool`" in PROMPT
    assert "pending_action_disposition" in PROMPT
    assert '"preserve"' in PROMPT
    assert '"revoke"' in PROMPT
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
    assert "`intent=confirm` e facts vazios ou somente language (idioma da conversa)" in PROMPT
    assert "use intent=confirm, facts vazios ou somente language, selected_choice_refs=[]" in PROMPT
    assert "Uma confirmação nunca carrega ou corrige dados pessoais" in PROMPT
    assert "Incerteza real como “Talvez” usa intent=inform" in PROMPT
    assert "“Sim” isolado, emoji" not in PROMPT
    assert "sim, mas" in PROMPT.casefold()
    assert "signed_callback" not in PROMPT
