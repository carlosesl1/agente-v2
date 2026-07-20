# Evidências — Fase 7

Status: **fase iniciada; somente baseline de entrada e RED da Task 1 existem**.

## Entrada

`entry-baseline.json` fixa:

- commit/tree terminal publicado da Fase 6;
- baseline focused 14/14;
- validator e manifest da Fase 6 verdes;
- versões Python/SQLite;
- HEAD/tree/status do runtime observado;
- zero capability live;
- rollout `NO-GO` e `phase8_started=false`.

`red-results.json` registra somente envelopes sanitizados: task, comando, exit,
classe causal, SHA-256 e bytes. Raw outputs permanecem em `/tmp`.

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
