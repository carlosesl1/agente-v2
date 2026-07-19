# Fase 5 — Comando e execução duráveis

## Status

`implementação aprovada; gates integrais e closeout em execução`

Aberta em `2026-07-19T04:50:49Z`, a partir do commit-base
`e51259ea0d19a2d07d3d14ee086b0766776cbeab`.

A implementação funcional das Tasks 1–11 foi concluída e aprovada por gates
read-only independentes. A Task 12 reúne workloads integrais, manifests,
validator, CI e prova terminal; a fase só muda para `concluída` após publicação
e CI verde.

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
- [ ] workflow CI da Fase 5 e cinco workflows regressivos verdes;
- [ ] commit de implementação publicado e conferido no remoto;
- [ ] commit documental terminal publicado e conferido no remoto.

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

A Fase 6 não foi iniciada e não é autorizada automaticamente pelo closeout da
Fase 5. Rollout comercial permanece **NO-GO**.
