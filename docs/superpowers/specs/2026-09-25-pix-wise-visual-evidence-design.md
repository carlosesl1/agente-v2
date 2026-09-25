# V2 — Pix e Wise por comprovante, com revisão humana posterior

## Decisão de produto e estado do documento

Carlos definiu nesta conversa que Pix e Wise precisam de análise do comprovante contra os dados corretos da cobrança. Ao escolher explicitamente a política, autorizou: **aceitar e dar baixa automaticamente quando o comprovante passar nas conferências, deixando revisão humana pendente para depois**.

A política acima está decidida. Este desenho detalhado está proposto para revisão antes do plano de implementação. Não declara implementação, homologação, deploy, autorização de pagamento real nem aprovação para GA. Dashboard visual e comandos humanos ficam fora desta entrega.

## Contexto verificado

A autoridade V2 passou READ → VERIFY antes da análise. Na fonte TEST autenticada pelo manifesto:

- `reservation_followup/payment.py`: `PixVisualEvidence` e `_validate_pix` verificam valor, moeda, perfil destinatário, status concluído, E2E e integridade da evidência.
- No mesmo módulo, `VerifiedWiseCredit` e `_validate_wise` representam crédito verificado com conta, assinatura, referência e janela. Um comprovante visual não pode preencher `signature_verified=True` nem ser apresentado como esse crédito.
- `v2_application/payments.py` distingue aceitação visual de confirmação bancária e aplica identidade global da evidência.
- `v2_application/financial_webhooks.py` recebe envelopes normalizados autenticados. Isso não prova acesso bancário direto, interpretação de imagem nem um E2E visual em funcionamento.

Os valores ativos de imagem/ref/configuração continuam exclusivamente no manifesto canônico. V3, legado, Stripe e mudança de GA/Ops estão fora do escopo.

## Fluxo proposto e responsabilidades

1. ManyChat entrega o comprovante associado à mensagem e ao lead. A análise usa o arquivo efetivamente recebido, não somente nome, legenda ou alegação do cliente.
2. Maya interpreta o documento e a conversa, incluindo pagamento por acompanhante. Ela identifica campos presentes, ausentes, ilegíveis e inconsistentes, sem inventá-los. Não há segundo agente julgando sua resposta.
3. A ferramenta recupera a cobrança canônica e compara os campos extraídos. A decisão de permitir o efeito é determinística e segue a política aprovada; não depende de uma frase de aprovação do modelo.
4. O owner existente registra a evidência, o resultado das conferências e a obrigação exata, com identidade transacional global. O arquivo original e a observação continuam vinculados à análise.
5. Comprovante aceito autoriza uma baixa pelo caminho durável de settlement, sujeito a reserva válida, valor correto, autorização vigente e deduplicação. Não há POST dentro da leitura visual nem edição manual de SQLite.
6. Maya recebe o resultado real. Informa baixa efetuada somente após resultado confirmado. Em fila ou em resultado incerto, não anuncia pagamento registrado.
7. A revisão humana permanece pendente como dimensão separada. Não bloqueia a baixa aprovada, não aciona handoff automaticamente e não pausa o ManyChat.

## Conferências obrigatórias

| Dimensão | Critério |
|---|---|
| Cobrança e reserva | Vínculo inequívoco com obrigação aberta, componente e unidade comercial; não reutilizar captura/evidência antiga por inferência |
| Destinatário | Dados legíveis compatíveis com beneficiário e chave/conta configurados para hostel ou agência; nome isolado não substitui um identificador suficiente |
| Valor/moeda | Exatamente o sinal ou saldo solicitado na moeda esperada; pagamento menor/maior segue exceção explícita, sem ajuste silencioso |
| Wise/câmbio | Distinguir valor de origem, tarifas e valor destinado ao beneficiário; comparar com instrução/cotação vinculada, sem inventar conversão ou presumir crédito recebido |
| Situação | Documento informa execução concluída; agendado, pendente, em processamento ou mera ordem de envio não aprovam a baixa |
| Data/hora | Compatível com emissão e vigência da cobrança; data da transação é distinta da data de recebimento do arquivo |
| Identidade Pix | E2E legível e consistente; formato correto não prova existência da transação |
| Identidade Wise | Identificador de transferência/transação e dados suficientes para vínculo inequívoco |
| Pagador | Maya associa semanticamente titular/terceiro à reserva; divergência de nome sozinha não invalida pagamento por acompanhante |
| Reutilização | Mesma transação na mesma obrigação é NOOP; em outra obrigação exige tratamento explícito, nunca uma segunda baixa |
| Conteúdo insuficiente | Ausência, corte, ilegibilidade ou contradição relevante impede aprovação automática; não preencher dado com valor esperado |

Não prometer detectar toda adulteração. Hash do arquivo e aparência consistente não autenticam a transação. Arquivos recortados/reexportados da mesma transação não podem contornar a identidade financeira.

## Estados e persistência

Preservar três dimensões independentes, sem duplicar o ledger financeiro:

- **Análise do comprovante:** recebido, incompleto/inconclusivo, divergente ou aceito.
- **Efeito financeiro:** estado canônico do settlement (nenhum, em fila, confirmado, falha sem efeito ou efeito incerto).
- **Revisão humana:** pendente após aceitação por comprovante; campos de conclusão e decisão reservados ao fluxo humano futuro.

A evidência visual mantém `bank_settlement_confirmed=False`/confirmação bancária desconhecida. Baixa comercial baseada no comprovante não transforma essa origem em API bancária ou crédito verificado. Pix e Wise visual devem compartilhar os mecanismos existentes de vínculo, deduplicação, settlement e outbox, mantendo tipos de evidência próprios. `VerifiedWiseCredit` continua distinto e compatível com históricos existentes.

O registro para o futuro dashboard inclui: referência do comprovante, lead/reserva/obrigação, método/unidade comercial, dados esperados e encontrados, campos ausentes, motivos do resultado, identidade transacional, horários, referência da baixa e pendência de revisão. Qualquer correção humana posterior será uma nova ação auditada, não reescrita do pagamento histórico. Não implementar agora endpoints de aprovação, estorno ou edição em Maya Ops, que permanece somente leitura.

## Exceções e continuidade

- Comprovante incompleto: Maya pede o dado ou documento necessário e mantém atendimento, sem baixa.
- Comprovante divergente/agendado: Maya explica o fato sem acusar fraude; nenhuma baixa.
- Cobrança expirada, reserva cancelada, conflito de reutilização ou impossibilidade de resolver: encaminhamento humano acionável pelo mecanismo existente; não renovar reserva nem transferir dinheiro automaticamente.
- Falha transitória de leitura: retomada limitada, sem efeitos financeiros.
- Resultado de baixa incerto: reconciliação antes de qualquer tentativa; handoff se impeditivo. Nunca repetir POST para obter prova.
- Revisão posterior pendente, por si só, não é erro de atendimento nem justificativa para handoff.

## Validação e critérios de aceite

1. RED causal antes de código; testes usam portas controladas, nunca simulam sucesso como prova externa.
2. Casos positivos Pix/Wise com formatos diferentes, idiomas relevantes e pagador terceiro explicitamente vinculado.
3. Negativas isoladas: conta/unidade, valor, moeda, data, status agendado/pendente, identificador ausente, ilegibilidade, campos contraditórios e reserva inválida.
4. Mesmo arquivo e mesma transação em arquivo diferente, outro lead/reserva, concorrência e reinício: no máximo uma baixa autorizada e nenhuma confirmação duplicada.
5. Wise visual nunca se torna `VerifiedWiseCredit`; dados ausentes não são completados a partir da cobrança; aceitação visual nunca declara confirmação bancária.
6. Pendência de revisão humana não bloqueia a baixa nem a comunicação; exceção impeditiva produz handoff real, não apenas uma flag.
7. Exercitar o caminho ManyChat → arquivo → Maya → comparação → owner → settlement → retorno → resposta. Separar testes locais, modelo real, transporte real e efeito financeiro real.
8. Documentos sintéticos só provam comportamento de leitura/validação em ambiente isolado. Homologação com comprovante real e baixa externa exige escopo e autorização próprios, sem inventar pagamento nem gerar cobrança automática.
9. Publicação TEST, promoção GA e construção do dashboard são decisões posteriores, separadas deste documento.

## Revisão do desenho

O desenho reutiliza os owners existentes e não pressupõe API Pix/Wise, confirmação de saldo bancário ou UI já disponível. A política aceita o risco residual da análise documental. A revisão humana posterior é visibilidade/auditoria, não um gate prévio. Não há necessidade de outro pagamento Stripe para iniciar esta implementação.
