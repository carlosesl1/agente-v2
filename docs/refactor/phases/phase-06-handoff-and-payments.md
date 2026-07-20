# Fase 6 — Handoff e pagamentos separados

## Status

`concluída, publicada e com sete workflows remotos verdes`

Aberta em `2026-07-19T17:48:53Z`, a partir do commit-base
`6c65c2612aefce4b217dcd0308e33dd68e1dc7db`.

## Objetivo

Separar atendimento humano e financeiro do ciclo de reserva concluído, com
workflows, ledgers, claims, workers e outboxes próprios, sem reabrir ou repetir
a reserva.

## Design

- [spec da Fase 6](../../superpowers/specs/2026-07-19-phase-6-handoff-payments-design.md)
- [plano TDD da Fase 6](../../superpowers/plans/2026-07-19-phase-6-handoff-payments.md)

## Decisões de entrada

- `PaymentWorkflow` somente depois de reservation `effect_confirmed`;
- handoff exige fila/estado durável e acknowledgement público;
- e-mail interno de handoff é opcional e desativável;
- Pix, Wise e Stripe têm evidências e claims distintos;
- troca de método sem alteração econômica não reabre a reserva;
- mudança econômica cria versão/confirmação financeira, nunca outro
  `ReservationCommand`;
- arquitetura adotada: workflows irmãos independentes, não FSM de reserva
  ampliada e não motor genérico de side effects.

## Gate de entrada

- [x] Fase 5 concluída e publicada;
- [x] terminal closeout em
  `6c65c2612aefce4b217dcd0308e33dd68e1dc7db`;
- [x] `main == origin/main == remote` no commit-base;
- [x] seis workflows 0–5 do terminal closeout em `success`;
- [x] usuário autorizou seguir para a Fase 6 após concluir a Fase 5;
- [x] `/home/ubuntu/chapada-leads-hermes` permanece somente leitura;
- [x] SQLite local autorizado;
- [x] rollout `NO-GO`;
- [x] spec revisada e aprovada pelo usuário;
- [x] plano TDD aprovado sob a autorização contínua de avanço;
- [x] implementação iniciada somente após worktree limpa e pre-flight sem conflitos.

## Escopo autorizado

- package puro `reservation_followup`;
- `HandoffWorkflow` e `PaymentWorkflow`;
- DTOs/reducers/serialização fechados;
- SQLite file-backed e DDL PostgreSQL estático;
- settlement ledger, evidence claims e outboxes independentes;
- workers one-shot com ports caller-supplied;
- testes sintéticos, properties, fault injection, restart, contention e
  mutations;
- manifests, validator e CI da Fase 6.

## Fora do escopo

- editar ou executar o legado/live;
- runner, plugin, executor, Hermes ou LLM;
- ManyChat/WhatsApp/e-mail/form live;
- Stripe/Wise/Pix/banco ou comprovante real;
- Cloudbeds/Bókun;
- Docker, PostgreSQL, Supabase ou Redis;
- deploy, shadow, canary ou rollout;
- iniciar a Fase 7.

## Gates de saída planejados

1. handoff não depende de e-mail e possui precedência pública terminal;
2. payment só nasce de `effect_confirmed`;
3. zero novo `ReservationCommand` após anchor confirmada;
4. claims globais impedem replay cross-target de Pix/Wise/Stripe;
5. um sujeito financeiro consome no máximo um slot de settlement;
6. partial/unknown nunca retorna a retry automático;
7. falha de outbox nunca repete settlement;
8. 20.000 properties, 2.000 restart schedules e contention multiprocesso;
9. mutation catalog integral;
10. validators 0–6, manifests, hashes e CI verdes;
11. revisão independente sem finding Critical/Important;
12. commit publicado e remoto conferido;
13. rollout permanece `NO-GO`.

## Gate local executado

- [x] suíte completa fresca: 642/642 em 229,843 s;
- [x] properties: 20.000 casos, seed `2026071906`, em 857,092 s para budget de 900 s;
- [x] fault matrix: 27/27;
- [x] restart: 2.000 schedules;
- [x] contention: 50 rounds × quatro domínios, 200/200 single-winner;
- [x] mutation catalog: 12/12 mortos;
- [x] manifests determinísticos e validator fechado independente;
- [x] DDL SQLite/PostgreSQL sem drift, com PostgreSQL apenas estático;
- [x] zero capability live e rollout `NO-GO`;
- [x] commit `8f23a8376f1d226f2ada5d80a45cbb930a79429e` publicado e SHA remoto conferido;
- [x] sete workflows remotos verdes;
- [x] seis jobs do workflow da Fase 6 em `success`;
- [x] commit documental terminal preparado para publicação.

## Closeout remoto

- gate terminal `deleg_a0d93273`: três verdicts `Approved`, sem findings;
- `main == origin/main == remote == 8f23a8376f1d226f2ada5d80a45cbb930a79429e` após o push;
- workflows 0–6 concluídos em `success` para esse SHA;
- jobs `static-validation`, `full-suite`, `properties`,
  `fault-restart-contention`, `mutations` e `phase6-gate` concluídos em
  `success`;
- IDs e URLs estão em `evidence/phase-06/ci-result.json`;
- o commit documental usa skip de CI porque não altera produção e evita repetir
  a validação pesada do candidato já congelado;
- rollout comercial permanece `NO-GO` e `phase7_started=false`.

## Evidência de entrada

- `../evidence/phase-06/entry-baseline.json`.

## Rollback

Reverter somente os commits da Fase 6 no repositório novo. Nenhuma capability
live é executada nesta fase.

## Decisão de avanço

A Fase 7 permanece bloqueada. O fechamento da Fase 6 não a inicia
automaticamente.

Estado vinculante do closeout local:

- implementação funcional e gates adversariais das Tasks 1–13 aprovados;
- Task 14 aprovada terminalmente 3/3;
- publicação e CI remoto autenticados em `ci-result.json`;
- PostgreSQL não foi executado;
- rollout `NO-GO`;
- `phase7_started=false`.
