# Fase 6 — Handoff e pagamentos separados

## Status

`design formalizado; implementação não iniciada`

Aberta em `2026-07-19T17:48:53Z`, a partir do commit-base
`6c65c2612aefce4b217dcd0308e33dd68e1dc7db`.

## Objetivo

Separar atendimento humano e financeiro do ciclo de reserva concluído, com
workflows, ledgers, claims, workers e outboxes próprios, sem reabrir ou repetir
a reserva.

## Design

- [spec da Fase 6](../../superpowers/specs/2026-07-19-phase-6-handoff-payments-design.md)

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
- [ ] spec revisada pelo usuário;
- [ ] plano TDD aprovado.

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

## Evidência de entrada

- `../evidence/phase-06/entry-baseline.json`.

## Rollback

Reverter somente os commits da Fase 6 no repositório novo. Nenhuma capability
live é executada nesta fase.

## Decisão de avanço

A Fase 7 permanece bloqueada. O fechamento da Fase 6 não a inicia
automaticamente.
