# Fase 5 — Comando e execução duráveis

## Status

`concluída, publicada e com CI remoto verde`

Aberta em `2026-07-19T04:50:49Z`, a partir do commit-base
`e51259ea0d19a2d07d3d14ee086b0766776cbeab`.

As Tasks 1–12 foram concluídas e aprovadas por gates read-only independentes,
sem finding Critical ou Important aberto. O commit de implementação foi
publicado, conferido no remoto e validado pelos seis workflows da Fase 5.

## Objetivo

Retirar writes do request/turno da LLM e provar localmente store transacional,
lease, ledger, dispatch único, outcome, reconciliação e outbox desacoplada.

## Design

- [spec da Fase 5](../../superpowers/specs/2026-07-19-phase-5-durable-command-execution-design.md)
- [plano TDD da Fase 5](../../superpowers/plans/2026-07-19-phase-5-durable-command-execution.md)

## Componentes entregues

- package `reservation_execution` sem transport/adapters externos default;
- DTOs operacionais fechados e imutáveis;
- DDL SQLite e PostgreSQL gerado de contrato comum;
- `SQLiteUnitOfWork` com optimistic revision e transações atômicas;
- command/ledger autorizados somente pelo reducer;
- claim, lease, fencing e slot único de dispatch;
- worker one-shot com fronteira conservadora de provider;
- outcome atômico com state, eventos e outbox final;
- reconciler one-shot sem adapter;
- outbox worker com claim/fencing/receipt próprios;
- fault injection, restart, contention multiprocesso, properties e mutations.
- contrato operacional independente dos runners, com schemas e valores exatos;
- validator recursivo para package, mutation targets, overclaims e call graph
  outbox→ledger.

## Invariantes provadas localmente

1. state, eventos, command, ledger e outbox não ficam parcialmente persistidos;
2. mesmo identity material é idempotente; bytes divergentes falham fechados;
3. lease/token stale não fenceiam, gravam outcome nem receipt;
4. um command autorizado consome no máximo um slot de dispatch;
5. somente falha comprovadamente pré-provider pode voltar a retry;
6. pós-fence incerto vira `called_unknown` e revisão manual sem redispatch;
7. falha de delivery não altera ledger nem repete provider;
8. adulteração de state/event/command/outcome/receipt falha antes de uso;
9. properties começam em `new_workflow` e atravessam Cloudbeds/Bókun sintéticos;
10. mutantes rodam em cópias temporárias com baseline verde obrigatório.
11. recovery pós-crash mede setup, baseline pós-child, final e delta; crashes
    pós-dispatch exigem `1/1/0` e não podem esconder redispatch.

## Persistência e limites

- SQLite por arquivo é a prova executável;
- WAL/SHM/DB e logs nunca são evidência versionada;
- PostgreSQL é apenas DDL estático/regenerável e não foi executado;
- nenhuma prova desta fase autoriza migração, produção ou equivalência PostgreSQL;
- fixtures, IDs, receipts e provider effects são sintéticos e sanitizados.

## Evidência de entrada

- [x] Fase 4 concluída e publicada;
- [x] `HEAD == origin/main == remote` no commit-base;
- [x] cinco workflows terminais da Fase 4 em `success`;
- [x] validadores 0–4 em `ok`;
- [x] legado permaneceu somente leitura;
- [x] persistência SQLite autorizada;
- [x] arquitetura, ownership, spec e plano aprovados.

## Gates de saída da Task 12

- [x] implementação Tasks 1–11 aprovada sem finding material aberto;
- [x] suíte fresca capturada sem output bruto versionado;
- [x] 20.000 properties, seed `2026071905`, zero safety failures;
- [x] 17 fault points e 2.000 restart schedules, zero violations;
- [x] 50 contention rounds de command e outbox, zero violations;
- [x] catálogo completo de 20 mutantes mortos com baseline verde;
- [x] schema/package manifests e `SHA256SUMS` regeneráveis;
- [x] validators 0–5 locais verdes;
- [x] workflow CI paralelo da Fase 5 e cinco workflows regressivos verdes;
- [x] commit de implementação publicado e conferido no remoto;
- [x] commit documental terminal preparado para publicação e conferência.

## Closeout remoto

- commit de implementação e documentação local aprovada:
  `9199b2c70fb3a26d9f12949b25d135f625b2317d`;
- `main == origin/main == remote` verificado após o push;
- seis workflows do commit concluíram em `success`;
- o workflow `phase-5-durable-execution` concluiu os jobs
  `static-validation`, `full-suite`, `properties`,
  `fault-restart-contention`, `mutations` e `phase5-gate` em `success`;
- IDs e URLs estão em `evidence/phase-05/ci-result.json`;
- Carlos autorizou abrir a Fase 6 somente após este closeout;
- a Fase 6 ainda não foi iniciada neste commit;
- rollout comercial permanece `NO-GO`.

## Fora do escopo

- Hermes/LLM, runner, plugin ou executor legado;
- ManyChat/WhatsApp/e-mail;
- Cloudbeds/Bókun write real;
- Docker, PostgreSQL, Supabase, Redis ou deploy;
- pagamento/handoff;
- shadow/canary/rollout;
- iniciar a Fase 6.

## Riscos

R04, R08 e R47–R50/R52/R53 possuem mitigação local executável. R51 continua
aberto: SQLite verde não prova PostgreSQL ou locking de produção. Migração e
canary continuam bloqueados.

## Rollback

Reverter somente os commits da Fase 5 no repositório novo. Não há ação live,
banco externo, provider effect, delivery ou deploy para desfazer.

## Decisão de avanço

A Fase 6 não foi iniciada neste closeout. Carlos autorizou sua abertura após a
publicação e conferência deste fechamento. Rollout comercial permanece
**NO-GO**.
