# Design — aprovação natural de resumo de reserva

Data: 2026-07-27
Status: aprovado em princípio por Carlos Eduardo

## Problema observado

O child da Maya é stateless por turno. Depois que o pai registra um `AwaitingConfirmationState`, a próxima `ModelRequest` contém apenas a mensagem atual e fatos comerciais; não contém a existência, a versão nem a projeção pública do resumo pendente. Uma confirmação natural como “sim, pode reservar exatamente esse passeio e gerar o link” pode então ser classificada novamente como seleção, repetindo o resumo em vez de confirmá-lo.

O resumo determinístico atual também expõe vocabulário técnico (`BRL 334.95`, `stripe`) e omite data, participantes, taxa e sinal.

## Objetivos

1. Permitir que a Maya interprete uma aceitação contextual natural do resumo imediatamente vigente.
2. Manter a autorização inteiramente vinculada a estado estruturado, versão atual, releitura fresca e binding privado.
3. Produzir resumo e acknowledgement naturais para WhatsApp.
4. Não criar reconhecimento por regex, substring, lista de palavras ou frase mágica.
5. Validar com modelo real e providers controlados/reads reais, mas com workers de reserva, pagamento, handoff e entrega mecanicamente bloqueados.

## Não objetivos

- Não alterar a política comercial de sinal ou taxa.
- Não relaxar frescor, versão, idempotência, fencing ou read-back.
- Não alterar reserva Bókun, Stripe ou ManyChat já existentes.
- Não executar novo efeito externo durante a validação desta mudança.
- Não resolver neste trabalho o problema separado de replay de eventos com identidade divergente.

## Arquitetura escolhida

### 1. Contexto público autenticado do resumo pendente

Adicionar ao contrato `ModelRequest` uma projeção fechada opcional do resumo vigente. Ela será derivada exclusivamente pelo pai quando o `BoundaryState.workflow` for exatamente `AwaitingConfirmationState`.

Campos permitidos:

- versão do resumo;
- tipo de serviço;
- componentes públicos com nome, datas, quantidade de pessoas, total e moeda;
- forma de pagamento em enum canônico;
- texto público já apresentado.

A projeção não conterá `offer_id`, `product_id`, IDs de provider, hashes, binding privado, perfil ou dados pessoais.

O adapter serializará esse contexto no payload atual do child. O prompt instruirá a Maya a interpretar a mensagem inteira contra esse resumo: aceitação contextual inequívoca → `intent=confirm` com a versão fornecida; dúvida, recusa ou mudança → `inform`/`adjust`.

### 2. Autorização permanece determinística

A nova projeção ajuda somente na interpretação conversacional. Ela não autoriza a reserva.

O caminho produtivo continuará exigindo:

- `AwaitingConfirmationState` atual;
- `intent=confirm` tipado pelo modelo;
- versão idêntica à versão pendente;
- perfil compatível;
- releitura fresca derivada do draft;
- oferta, preço, pessoas e binding idênticos;
- criação de exatamente um comando durável e workers fenced.

Nenhum texto isolado abre o gate.

### 3. Resumo natural determinístico

O reducer renderizará o resumo a partir do draft autenticado, sem confiar no texto do modelo.

Para o cenário Bókun em português, formato esperado:

> Só para confirmar: Roteiro dos 4Ps em 18/11/2026 para 1 pessoa, total de R$ 334,95 já com a taxa. O sinal no cartão será de R$ 66,99. Posso reservar?

Regras:

- moeda e data no locale do lead;
- `cartão`, nunca `stripe`;
- valor final Bókun, nunca subtotal;
- para agência + Stripe, sinal de 20% calculado com `Decimal` e `ROUND_HALF_UP` sobre o total confirmado;
- singular/plural natural;
- pacote e hospedagem conservam seus próprios componentes e políticas.

### 4. Acknowledgement seguro

Após o commit do comando, mas antes do resultado do provider:

> Perfeito — vou processar sua reserva agora.

O texto não afirma que a reserva já existe. A confirmação definitiva e o link continuam sendo projetados apenas depois dos outcomes `effect_confirmed` e do Payment Link confirmado.

## Tratamento de mudanças e ambiguidades

- Se o lead mudar data, passeio, pessoas, pagamento ou qualquer termo material, o modelo deve retornar `adjust`; o resumo anterior não é confirmado.
- Se a resposta for ambígua, não há comando e a Maya pede esclarecimento curto.
- Se o total mudar na releitura, o binding falha fechado e um novo resumo deve ser apresentado.
- Handoff continua tendo precedência terminal.

## Testes

### RED/GREEN unitário

1. `ModelRequest` transporta somente a projeção pública fechada do resumo.
2. A projeção omite IDs, hashes, binding e PII.
3. O wire do Hermes child contém o resumo pendente e sua versão.
4. O resumo Bókun PT-BR usa `R$ 334,95`, taxa incluída, data, 1 pessoa, `cartão` e sinal `R$ 66,99`; não contém `BRL`, `stripe`, IDs ou hashes.
5. Confirmação válida produz acknowledgement natural e um comando; `adjust`/ambiguidade não produz comando.
6. Resumo alterado/versionado mantém o fail-closed existente.

### Sandbox conversacional

Executar conversa ManyChat-shaped com modelo real, estado novo e os efeitos mecanicamente bloqueados:

1. disponibilidade e total final;
2. dados e cartão/sinal;
3. resumo natural;
4. lead diz naturalmente “sim, pode reservar exatamente esse passeio e gerar o link”;
5. Maya retorna confirmação tipada no primeiro turno de aceitação, sem repetir resumo;
6. runtime prepara no máximo um comando, mas nenhum worker/provider write/Stripe/ManyChat/handoff é executado.

Preservar transcript sanitizado e contadores de efeitos externos iguais a zero.

## Critérios de aceite

- Uma única confirmação natural do resumo inalterado é suficiente.
- Não há frase mágica nem autorização lexical.
- O resumo não expõe termos internos.
- Nenhum efeito é afirmado antes do read-back.
- Testes focados, sandbox real effect-denied e regressão completa passam no mesmo HEAD.
- Git permanece limpo exceto pelo `uv.lock` preexistente e intocado.
