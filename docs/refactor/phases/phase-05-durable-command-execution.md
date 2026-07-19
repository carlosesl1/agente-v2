# Fase 5 — Comando e execução duráveis

## Status

`design aprovado; especificação aguardando revisão do usuário`

Aberta em `2026-07-19T04:50:49Z`, a partir do commit-base
`e51259ea0d19a2d07d3d14ee086b0766776cbeab`.

## Objetivo

Retirar writes do request/turno da LLM e provar localmente store transacional,
lease, ledger, dispatch único, outcome, reconciliação e outbox desacoplada.

## Design

- [spec da Fase 5](../../superpowers/specs/2026-07-19-phase-5-durable-command-execution-design.md)

## Decisões aprovadas

- SQLite por arquivo como prova executável;
- DDL SQLite/PostgreSQL gerado de contrato comum;
- nenhum Docker, PostgreSQL ou Supabase executado;
- unidade transacional única para state/event/command/outbox;
- ledger e outbox permanecem separados;
- nenhum adapter/transporte default;
- qualquer incerteza pós-dispatch vai para revisão manual;
- Fase 6 não será iniciada automaticamente.

## Gate de entrada

- [x] Fase 4 concluída e publicada;
- [x] `HEAD == origin/main == remote` no commit-base;
- [x] árvore limpa na abertura;
- [x] cinco workflows terminais da Fase 4 em `success`;
- [x] validadores 0–4 em `ok`;
- [x] legado permaneceu somente leitura;
- [x] persistência SQLite autorizada;
- [x] arquitetura/ownership aprovados;
- [x] política de dispatch conservadora aceita por melhor julgamento;
- [x] design completo aprovado;
- [ ] especificação escrita revisada pelo usuário;
- [ ] plano TDD escrito e aprovado para execução.

## Escopo autorizado após aprovação do plano

- package puro/operacional `reservation_execution`;
- SQLite local e temporário;
- contratos/fakes in-memory sem rede;
- DDL PostgreSQL estático e não executado;
- fault injection, restart, multiprocess, properties e mutations;
- evidências sanitizadas e CI.

## Fora do escopo

- Hermes/LLM, runner, plugin ou executor legado;
- ManyChat/WhatsApp/e-mail;
- Cloudbeds/Bókun write real;
- Docker, PostgreSQL, Supabase, Redis ou deploy;
- pagamento/handoff;
- shadow/canary/rollout;
- iniciar a Fase 6.

## Gate de saída resumido

1. persistência atômica de state/event/command/ledger/outbox;
2. optimistic revision e idempotência fail-closed;
3. lease/fencing/restart/race provados;
4. dispatch slot e provider calls no máximo um;
5. unknown sem retry e com manual review;
6. outbox incapaz de repetir provider;
7. fault matrix, properties e mutations verdes;
8. DDL/manifests/checksums regeneráveis;
9. validadores 0–5 e CI verdes;
10. rollout `NO-GO`.

## Rollback

Reverter somente os commits da Fase 5 no repositório novo. Não há ação live.
