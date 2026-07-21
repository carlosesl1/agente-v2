# Fase 7 — Migração das fronteiras

## Estado

- Status: **candidato congelado anterior invalidado; remediação focused em curso**.
- Commit de entrada: `4169c6149f76e8bf4f30a26ee9d0bfbc43a58984`.
- Tree de entrada: `b2ce9d0b35924db2b2a387d0aa7a5ba92490bce4`.
- Spec corrigida: `580b1da3602308c16c8a45af694fe6c804ce7ffb`.
- Plano aprovado: `81204c46ad89e11ce2320ac30a0fcdeb828454d6`.
- Branch: `phase7-boundary-migration`.
- Rollout: **NO-GO**.
- `rollout=NO-GO`.
- `phase8_started=false`.

## Candidato congelado invalidado

- `agente-v2`: `d0ba3f7b062d61a2b95f500e18badd6fdb8772ae`.
- Candidato: `ef5dd46c27ccb72e977b333f526521a5f6b0225c`.
- Tree: `ceced389dfc73ebd41ac499fa20660890dc86035`.
- Réplica runtime corrigida: `e2e1570757c2afe3d0b1d25a1ef94cd2c709d674`.
- Tree da réplica corrigida: `8dc9aed8092661b701104bd89dedf865cd4d94b6`.
- Patch autenticado: SHA-256
  `f06d3bd1a43e986ed66bb9ee3737e055e15986e9ca7522dfac88c6b4b034d5c0`.
- Focused runtime: 421/421 no blast radius sanitizado e 103/103 no pre-freeze.
- Runtime integral local instalado do wheel: 963/963 em 33 s.
- CI remoto: run `29787387850`, 6/6 jobs `completed/success` no SHA candidato.
- Runtime original: o pre-flight registrou 80 entradas; a captura posterior
  observou 86. Desde a captura, HEAD/tree/status/diff permanecem idênticos ao
  manifesto autenticado; nenhuma alteração nessa source foi feita pela Fase 7.
- Revisão terminal `deleg_005406ed`: lane 1 `Needs fixes`; lanes 2/3 timeout e
  portanto zero. O candidato e sua CI permanecem evidência histórica, não
  autorização de fechamento.

## Remediação da revisão

Os REDs vinculados ao evidence commit/tree anterior cobrem:

- snapshot cross-lead antes de genesis;
- payment wire ignorado e estados/timestamps contraditórios;
- identidade embutida trocada com hash coerente;
- `BoundaryState.schema_version` desconhecida;
- contention nominal sem contenders concorrentes por domínio.

As correções focused estão em desenvolvimento. Nenhum novo candidato foi
congelado, publicado ou validado remotamente.

O primeiro candidato pre-freeze (`4eb0495a2296ac76d4b2ab25038b6a822f19ec18`)
foi invalidado por dois erros de collection: a captura removia o package
`qa.maya_test_lab.scenarios` junto com o delta local sensível. Nenhum rerun foi
feito nessa tree. A correção mantém os fixtures seguros rastreados do HEAD e
exclui somente a modificação local de `real_world_v1.json`.

O sucessor `76f56f07d9e2a8a9ee49a12b65b918ac5b4b0591` também foi
invalidado sem rerun: cinco testes mostraram que a captura removia o baseline
seguro de `.env.example` e reescrevia IDs operacionais. A terceira reconstrução
mantém baselines seguros e limita a sanitização a campos explícitos de contato.

O terceiro candidato `d710ff6908b811270c6f60cbf27312a77d3ade1f` foi invalidado
sem rerun por duas relações de oracle ainda não alinhadas: ID de contato inteiro
e hash derivado de `message_id`. A quarta reconstrução usa fronteiras de token e
passou 421 testes focused sobre todas as superfícies sanitizadas e consumidores.

O RED semântico original da Task 6 não registra commit/tree unfixed e não é
retroativamente apresentado como reconstruível. A remediação terminal usa
`review-remediation-tests.patch`, vinculado ao commit/tree anterior e aos quatro
outputs RED autenticados.

## Objetivo

Fazer runner, plugin e executor consumirem os mesmos contratos tipados do
kernel por quatro fronteiras únicas:

1. `LegacyStateImporter`;
2. `TurnCoordinator`;
3. `ToolDispatch`;
4. `DecisionComparator`.

A migração usa dual-read/single-write, wheel offline determinístico e uma réplica
autenticada do runtime. A árvore operacional permanece somente leitura.

## Entrada verificada

O pre-flight econômico executou:

- `tests.test_phase6_closeout`: 14/14 em 9,669 s;
- validator da Fase 6: `passed`;
- manifest da Fase 6: `passed`;
- Python `3.12.13`;
- SQLite `3.46.1`;
- runtime observado no HEAD
  `57408d8b2040399bc25ee7957505208079458884`, com 80 entradas locais no
  pre-flight e 86 no instante da captura isolada;
- zero capability live.

Hashes e comandos estão em `../evidence/phase-07/entry-baseline.json`.

## Regime econômico

Existe **uma janela de validação pesada** por candidato congelado:

- durante Tasks 1–14: RED/GREEN focused e regressão por blast radius;
- estágio local congelado: réplica/runtime, wheel e patch, sem repetir a suíte
  integral do `agente-v2`;
- estágio remoto congelado: um push do branch e um ciclo de `phase7.yml`;
- revisão terminal somente para três riscos não sobrepostos;
- correção estreita repete apenas o gate afetado;
- mudança material cria novo candidato.

## Command ownership

O catálogo v2 ativo possui 13 tools:

- cinco reads tipadas;
- duas writes de reserva → `ReservationCommand`;
- duas writes financeiras → `PaymentSettlementCommand`;
- `chapada_commit_state` → facts/intenção tipada;
- três writes sem command publicado ficam `BLOCKED_UNMIGRATED` + manual review:
  `wise_verificar_pagamento`, `cloudbeds_gerar_link_pagamento_stripe` e
  `bokun_gerar_link_pagamento_stripe`.

Nenhum command genérico será inventado. A lacuna bloqueia rollout.

## Limites da fase

Não executar:

- Hermes/LLM/provider;
- ManyChat, e-mail ou mensagem live;
- Cloudbeds, Bókun, Wise, Stripe ou Pix/banco;
- Supabase, Redis, PostgreSQL ou Docker;
- deploy, shadow live, canary ou rollout;
- migração de registros reais.

SQLite é permitido apenas em `:memory:` ou diretório temporário. PostgreSQL
permanece DDL estático.

## Deliverables

- package `reservation_boundary` puro;
- wheel stdlib byte-reproducible;
- estado legado importável sem inferência;
- store single-write/CAS;
- coordinator e dispatch únicos;
- comparator independente;
- réplica e integration patch autenticados;
- properties/faults/restarts/contention/mutations;
- manifests, validator, workflow e CI remoto;
- runtime original sem drift.

## Gate de fechamento

A fase só fecha com:

- dual-read/single-write provado;
- zero provider write no turno;
- quatro command mappings exatos e três blocks explícitos;
- divergências críticas zero;
- uma janela pesada válida;
- revisão terminal do mesmo candidato;
- branch publicado e CI remoto verde;
- runtime operacional intocado;
- rollout `NO-GO` e `phase8_started=false`.
