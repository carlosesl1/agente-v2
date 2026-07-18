# Fase 2 — Domínio tipado e reducer puro

## Status

`concluída`

Aberta em `2026-07-18T21:05:49Z`, a partir do commit-base
`d949564c9a38474e0d634a4be8ac923680dc7e48`.

Encerrada em `2026-07-18T22:38:27Z`. Implementação publicada em
`212f2cf62541af0622fb16244bafcfb873d7832b`; os workflows
`phase-0-validation`, `phase-1-characterization` e `phase-2-domain` passaram
nesse SHA. Run da Fase 2: `29663723713`.

## Objetivo

Implementar a máquina de estados comercial como domínio puro, imutável e
tipado. O reducer deve transformar:

```text
estado + evento → próximo estado + comandos de domínio
```

sem FastAPI, Hermes, ManyChat, banco, filesystem, relógio global, rede ou
provider.

## Owners nesta fase

- tipos de domínio: tornam estados e eventos explícitos;
- assinatura canônica: cobre toda identidade e economia executável;
- reducer: único owner das transições e criação do comando;
- serializer: único contrato de persistência versionado;
- property runner: prova invariantes sobre sequências duplicadas, inválidas e
  fora de ordem.

## Escopo autorizado

- criar pacote Python puro no `agente-v2`;
- criar estados discriminados, eventos, value objects e outcomes fechados;
- criar `CommercialDraft`, `SummaryPresented`, `ConfirmationDecision` e
  `ReservationCommand` imutáveis;
- criar assinatura canônica determinística;
- criar reducer total com resultado `applied`, `ignored` ou `rejected`;
- criar serializer estrito com `schema_version`;
- criar tabela completa estado/evento;
- criar testes unitários, metamórficos, de serialização e property-based;
- executar no mínimo 100 mil sequências determinísticas;
- registrar relatórios, hashes, riscos, comandos e exit codes.

## Fora do escopo

- editar ou importar `/home/ubuntu/chapada-leads-hermes`;
- integrar FastAPI, Hermes, ManyChat, plugin, executor ou providers;
- implementar adapters Cloudbeds/Bókun — Fase 3;
- implementar renderer/classificador LLM de confirmação — Fase 4;
- implementar store, worker, ledger ou outbox — Fase 5;
- chamar provider, enviar mensagem, alterar contato, container, profile ou env;
- fazer deploy ou iniciar a Fase 3.

## Contrato central

Para uma versão comercial imutável:

```text
oferta técnica selecionada
→ draft canônico assinado
→ SummaryPresented da mesma versão/assinatura
→ ConfirmationDecision posterior aceitando a mesma versão/assinatura
→ no máximo um ReservationCommand
```

O reducer nunca chama provider. Um comando é autorização persistível, não um
efeito externo.

## Invariantes obrigatórios

1. Estado inicial não contém seleção, resumo, confirmação ou comando.
2. Oferta é escolhida somente por `offer_id`; label pública não é identidade.
3. Evidência vencida ou não positiva não permite seleção.
4. Assinatura muda com oferta, provider ref, datas, horário, pessoas, preço,
   moeda, customer facts, pagamento ou adicionais.
5. Alterar apenas label pública, ordem de componentes ou ordem de adicionais
   não muda a assinatura.
6. Resumo sozinho produz zero comandos.
7. Confirmação sem resumo, anterior ao resumo, ambígua, rejeitada, de outra
   versão ou assinatura produz zero comandos.
8. Aceite válido produz exatamente um comando imutável e idempotente.
9. Evento duplicado idêntico não muda estado nem reemite comando; reutilização
   do mesmo ID com payload divergente é rejeitada.
10. Evento fora de ordem é rejeitado sem exception, comando ou mutação
    comercial; somente metadata auditável pode avançar.
11. Nenhuma sequência produz segundo comando para o mesmo workflow.
12. `called_unknown` nunca é reduzido a `not_called` e conduz a estado incerto.
13. Serializer rejeita versão/tag/campo inválido, tipos JSON equivalentes porém
    incorretos, chaves duplicadas, subclasses e escalares não canônicos.
14. Todos os pares estado/evento possuem política declarada independentemente
    dos handlers; tipo novo sem decisão explícita falha fechado.

## Entregáveis

- [x] package de domínio puro;
- [x] estados discriminados e value objects fechados;
- [x] eventos discriminados;
- [x] reducer total;
- [x] assinatura canônica;
- [x] comando e outcome tipados;
- [x] serializer versionado e estrito;
- [x] tabela completa estado/evento;
- [x] testes RED registrados antes da implementação;
- [x] testes unitários, metamórficos e de serialização;
- [x] 100 mil sequências property-based;
- [x] validador local e CI da Fase 2;
- [x] evidências e hashes SHA-256;
- [x] revisão adversarial independente;
- [x] revisão de riscos;
- [x] commit de entrada, implementação e closeout enviados e verificados.

## Gate de aceite

1. Reducer é total para todos os tipos públicos de estado e evento.
2. A matriz completa estado/evento está documentada e testada.
3. Cem mil sequências passam com zero exception, write prematuro, comando
   obrigatório ausente, segundo comando ou violação de duplicidade/ordem, e
   com cobertura positiva das classes semânticas obrigatórias.
4. Testes provam que toda mutação econômica altera assinatura e mutações de
   apresentação não alteram.
5. Round-trip do serializer preserva todos os estados/eventos/comandos e
   entradas desconhecidas falham fechadas.
6. O pacote de domínio não importa capabilities externas.
7. Fases 0 e 1 continuam válidas.
8. Secret/PII scan, compileall, diff check, hashes e CI passam após a última
   alteração.
9. `HEAD == origin/main` após push/fetch e CI do SHA final conclui com sucesso.

## Rollback

A fase adiciona somente domínio puro, testes, documentação e evidências no novo
repositório. Rollback é a reversão dos commits da Fase 2; não há ação live.

## Decisão de avanço

A Fase 2 está concluída. A Fase 3 torna-se elegível, mas permanece **não
iniciada** até direção explícita. Rollout comercial continua **NO-GO**.