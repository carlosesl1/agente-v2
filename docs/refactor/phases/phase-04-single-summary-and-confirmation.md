# Fase 4 — Resumo e confirmação únicos

## Status

`plano aprovado; implementação não iniciada`

Aberta em `2026-07-19T02:21:03Z`, a partir do commit-base
`18588cf2d9d771decd4e7c56540fabd79ed4ebcc`.

## Objetivo

Provar deterministicamente uma versão comercial, um resumo persistível e uma
confirmação natural posterior que produz no máximo um `ReservationCommand`, sem
provider, rede ou side effect.

## Design proposto

- [spec da Fase 4](../../superpowers/specs/2026-07-19-phase-4-summary-confirmation-design.md)
- [plano da Fase 4](../../superpowers/plans/2026-07-19-phase-4-summary-confirmation.md)

Boundary:

```text
ReadyToSummarizeState
→ renderer determinístico
→ SummaryRecorded
→ AwaitingConfirmationState
→ classifier model-agnostic
→ trusted binding da versão/signature vigente
→ ConfirmationReceived
→ reducer
→ zero ou um ReservationCommand
```

## Owners

- renderer: projeção pública do draft, sem private fields/claim;
- classifier: decisão semântica, sem target comercial;
- binder: contexto vigente, IDs e evento tipado;
- reducer: versão, estado, autorização e comando;
- persistência/outbox live: fases posteriores.

## Escopo autorizado

- package puro `reservation_confirmation`;
- renderer PT-BR/EN;
- `ConfirmationClassifier` Protocol;
- classifier determinístico de referência;
- trusted binding ao resumo vigente;
- estado `awaiting_adjustment` para desarmar a versão antiga;
- corpus sintético e replays das seis categorias;
- RED, unit, replay, property e mutation tests;
- manifests, hashes, validador e CI da Fase 4.

## Fora do escopo

- editar ou executar o legado;
- LLM/Hermes real;
- ManyChat/WhatsApp/outbox live;
- provider, rede, auth ou credenciais;
- banco, ledger, worker ou execução;
- runner/plugin/executor, deploy ou rollout;
- iniciar a Fase 5.

## Invariantes

1. classifier nunca fornece draft version/signature;
2. resumo omite IDs privados e claim de efeito;
3. confirmação precisa ser posterior ao resumo vigente;
4. apenas `ACCEPT` coerente emite um command;
5. `REJECT`, `AMBIGUOUS` e `ADJUST` emitem zero;
6. `ADJUST` desarma o resumo antigo;
7. alteração semântica incrementa versão; no-op não;
8. nova versão exige resumo e confirmação novos;
9. duplicata não reemite command;
10. nenhuma capacidade externa existe nesta fase.

## Gate de entrada

- [x] Fase 3 concluída e remediada;
- [x] `HEAD == origin/main == remote` no commit-base;
- [x] working tree limpa na abertura;
- [x] validadores 0–3: `ok`, zero failures;
- [x] usuário autorizou seguir para a próxima fase;
- [x] boundary do classifier escolhido: model-agnostic + referência determinística;
- [x] confirmações contextuais exigem contexto tipado vigente;
- [x] spec revisada e aprovada;
- [x] plano executável escrito e autorrevisado.

## Gate de saída

1. renderer determinístico e seguro;
2. replays explícito/coloquial/contextual/negativo/ambíguo/ajuste PT/EN;
3. aceite válido cria exatamente um command;
4. toda outra combinação cria zero;
5. alteração cria versão e resumo novos;
6. 50 mil casos e mutation catalog passam;
7. gate regressivo da Fase 2 passa em 100 mil × 20;
8. validadores 0–4, scans, hashes e CI passam;
9. commits e remoto são verificados;
10. rollout permanece `NO-GO`.

## Baseline legado somente leitura

- HEAD: `57408d8b2040399bc25ee7957505208079458884`;
- status entries canônicos: `80`;
- comando do fingerprint: `git status --short -z | sha256sum`;
- status SHA-256:
  `77c02eb09d415e01f45515ccacf9bc7b93f34d1d8a66aafc0af905d8734c940b`.

## Rollback

Reverter somente os commits da Fase 4 no repositório novo. Não há ação live.
