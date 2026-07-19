# Fase 4 — Resumo e confirmação únicos

## Status

`concluída, publicada e com CI remoto verde`

Aberta em `2026-07-19T02:21:03Z`, a partir do commit-base
`18588cf2d9d771decd4e7c56540fabd79ed4ebcc`.

## Objetivo

Provar deterministicamente uma versão comercial, um resumo persistível e uma
confirmação natural posterior que produz no máximo um `ReservationCommand`, sem
provider, rede ou side effect.

## Design e plano executados

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

Estado local antes do CI remoto:

- [x] renderer PT-BR/EN e `PreparedSummary` determinísticos;
- [x] corpus sintético com 32 casos nas seis categorias;
- [x] trusted binding recompõe hash/locale/IDs antes do evento;
- [x] `awaiting_adjustment` desarma o resumo antigo;
- [x] replays Cloudbeds e Bókun começam em workflow vazio;
- [x] property gate: 50.000 casos, 12.500 autorizações, zero violações;
- [x] mutation gate: 19/19 mortos em cópias temporárias;
- [x] regressões pesadas, validators 0–4, hashes e scans finais;
- [x] commits publicados e CI remoto verificado.

## Implementação

- package puro `reservation_confirmation` com DTOs fechados, renderer,
  apresentação, classifier Protocol/referência, binding e properties;
- domínio ampliado para 16 estados e 192 pares explícitos, sem evento novo;
- `start_time=None` é filtro aberto para opções de atividade; horário explícito
  continua sendo igualdade exata;
- o classificador retorna somente decisão/evidência; o reducer continua único
  owner de autorização e construção de `ReservationCommand`.

## Evidência local principal

- 50.000 casos, seed `20260719`;
- elapsed `105.558s`, max RSS `26584 KB`, exit code `0`;
- 25.000 baselines Cloudbeds e 25.000 Bókun, todos desde `new_workflow`;
- 12.500 comandos para 12.500 aceites autorizados;
- zero comando prematuro, segundo comando, reemissão, stale acceptance,
  falha de desarme, evento em falha de contexto, exceção ou violação;
- 19/19 mutantes mortos;
- legado permaneceu somente leitura no fingerprint canônico.

## Baseline legado somente leitura

- HEAD: `57408d8b2040399bc25ee7957505208079458884`;
- status entries canônicos: `80`;
- comando do fingerprint: `git status --short -z | sha256sum`;
- status SHA-256:
  `77c02eb09d415e01f45515ccacf9bc7b93f34d1d8a66aafc0af905d8734c940b`.

## Closeout remoto

- commit de implementação:
  `2c922d1b88eaf44412c1a808c4786e4729e8ba64`;
- `HEAD == origin/main == remote` verificado após o push;
- cinco workflows do commit concluíram em `success`;
- IDs e URLs estão em `evidence/phase-04/ci-result.json`;
- Fase 5 não iniciada;
- rollout comercial permanece `NO-GO`.

## Rollback

Reverter somente os commits da Fase 4 no repositório novo. Não há ação live.
