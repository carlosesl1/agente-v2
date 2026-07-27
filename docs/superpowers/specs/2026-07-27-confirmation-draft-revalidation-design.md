# Confirmação com revalidação estruturada do draft — design

Data: 2026-07-27

## Contexto

No preflight conversacional real, a Luna produziu `intent=confirm` e `confirmed_summary_version=1` para um workflow `awaiting_confirmation` válido. Mesmo assim, não pediu a leitura Cloudbeds no mesmo turno. O reducer exige que `reads` atuais correspondam exatamente ao draft e, corretamente, não emitiu comando. O resultado foi um loop público de “vou atualizar disponibilidade”.

## Objetivo

Permitir que uma confirmação contextual tipada conclua o protocolo sem depender de o modelo lembrar de emitir novamente uma consulta já determinada pelo draft pendente.

## Decisão

O `V2TurnExecutor` derivará leituras de confirmação somente quando todas estas condições forem verdadeiras:

1. o estado do domínio é exatamente `AwaitingConfirmationState`;
2. a primeira proposta validada tem `intent == "confirm"`;
3. a versão confirmada coincide com a versão do draft pendente;
4. a proposta não trouxe consultas próprias;
5. cada componente do draft pode ser convertido sem ambiguidade em uma `ReadRequest` tipada.

As consultas serão reconstruídas a partir dos componentes autenticados do draft, e não do texto do cliente:

- hospedagem: `start_date`, `end_date`, adultos e crianças do componente;
- passeio: `product_id` canônico preservado nos fatos estruturados e data/participantes do componente;
- pacote: uma consulta por componente, respeitando o limite existente de uma por `kind`.

Após as leituras, a segunda rodada do modelo recebe as observations atuais. Para preservar a confirmação inequívoca já fechada na primeira rodada, se a segunda proposta perder `intent=confirm`, o executor manterá a intenção, a versão confirmada e os fatos da primeira proposta, aceitando apenas o texto público normalizado da segunda rodada. O reducer continuará sendo a autoridade final: só emitirá comando se `_reads_bind_draft` provar igualdade exata entre observations atuais e draft.

## Propriedades de segurança

- Nenhuma palavra ou regex da mensagem ativa o comportamento.
- Uma proposta `inform`, `select`, `adjust` ou `request_handoff` não ganha leituras derivadas.
- Versão divergente continua retornando `stale_confirmation` sem consulta/write.
- Mudança de preço, disponibilidade, provider binding ou perfil continua bloqueando o comando.
- Gates, autoridade, idempotência, fencing, worker, read-back e Stripe test-only permanecem inalterados.
- Se não for possível reconstruir a consulta de modo exato, o fluxo permanece fail-closed.

## Testes

1. RED: confirmação de draft de hospedagem sem `read_requests` deve executar uma leitura derivada e emitir exatamente um comando quando a observation coincide.
2. RED: intenção `inform` no mesmo estado não deve gerar leitura derivada.
3. RED: confirmação com versão divergente não deve consultar provider nem emitir comando.
4. GREEN: testes focados do executor e reducer.
5. Regressão do candidato com ambiente limpo e config explícita.
6. Preflight live-prompt do fluxo completo com gates fechados.
7. Somente depois: lote real de uma hospedagem e um passeio, Stripe em test mode, seguido de read-back e preservação dos IDs.
