# Evidências — Fase 7

Status: **implementação focused concluída; candidato ainda não congelado**.

## Entrada

`entry-baseline.json` fixa:

- commit/tree terminal publicado da Fase 6;
- baseline focused 14/14;
- validator e manifest da Fase 6 verdes;
- versões Python/SQLite;
- HEAD/tree/status do runtime observado;
- zero capability live;
- rollout `NO-GO` e `phase8_started=false`.

`red-results.json` cobre Tasks 1–15 com envelopes sanitizados: task, comando,
exit, classe causal, SHA-256 e bytes. Raw outputs permanecem em `/tmp`.

## Runtime isolado

- `runtime-source-manifest.json`: fingerprint da source e baseline sanitizado;
- `runtime-contract-manifest.json`: catálogo/schema shape observado;
- `runtime-integration.patch`: patch cumulativo aplicável ao baseline;
- `runtime-integration-manifest.json`: commits/trees/wheel/testes focused;
- `runtime-integration-contract-manifest.json`: contrato após integração.

O patch reproduz a tree `8dc9aed8092661b701104bd89dedf865cd4d94b6`.
O source operacional permaneceu no HEAD `57408d8b2040399bc25ee7957505208079458884`.
O pre-flight registrou 80 entradas locais; o manifesto de captura fixa as 86
entradas observadas posteriormente e seus hashes exatos. A Fase 7 autentica o
estado da captura, sem atribuir a mudança intermediária e sem modificar a source.

## Regime de validação

- desenvolvimento: focused + blast radius;
- candidato congelado: estágio local privado da réplica;
- mesmo candidato: um ciclo remoto pesado do branch;
- nenhum rerun integral sem mudança material.

## Limites

Não entram neste diretório: credenciais, PII, mensagens, payloads brutos,
bancos, logs, comprovantes, screenshots, source snapshot operacional ou raw
diffs da árvore suja.

`ci-result.json` não existe até um run remoto real do candidato congelado.

Também não existem antes do freeze: `candidate.json`, resultados integrais,
`review-result.json` e `local-integration-result.json`.

O candidato `4eb0495a2296ac76d4b2ab25038b6a822f19ec18` foi invalidado
após uma única tentativa local terminar em collection exit 2. A causa e a saída
autenticada serão preservadas no resultado local do candidato sucessor; a tree
inválida não foi reexecutada.

O sucessor `76f56f07d9e2a8a9ee49a12b65b918ac5b4b0591` também teve uma
única tentativa, com cinco falhas de fidelidade da captura. A tree foi
invalidada e não reexecutada.

O terceiro candidato `d710ff6908b811270c6f60cbf27312a77d3ade1f` também foi
executado uma única vez: duas falhas de relacionamento de oracles sanitizados.
Foi invalidado e não reexecutado. A reconstrução seguinte passou 421/421 no
blast radius sanitizado antes do freeze.
