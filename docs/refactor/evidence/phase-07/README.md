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

O patch reproduz a tree `0cddb86d86727802931bcf0b28b8a1f2de93d36a`.
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
