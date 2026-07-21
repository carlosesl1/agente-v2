# Evidências — Fase 7

Status: **segundo candidato invalidado; terceira remediação em freeze local**.

## Entrada

`entry-baseline.json` fixa:

- commit/tree terminal publicado da Fase 6;
- baseline focused 14/14;
- validator e manifest da Fase 6 verdes;
- versões Python/SQLite;
- HEAD/tree/status do runtime observado;
- zero capability live;
- rollout `NO-GO` e `phase8_started=false`.

`red-results.json` cobre Tasks 1–17 com envelopes sanitizados: task, comando,
exit, classe causal, SHA-256 e bytes. Os outputs RED da segunda revisão estão
retidos em `review2-red-outputs/`; referências históricas anteriores a `/tmp`
continuam explicitamente marcadas como limitações de proveniência.

## Runtime isolado

- `runtime-source-manifest.json`: fingerprint da source e baseline sanitizado;
- `runtime-contract-manifest.json`: catálogo/schema shape observado;
- `runtime-integration.patch`: patch cumulativo aplicável ao baseline;
- `runtime-integration-manifest.json`: commits/trees/wheel/testes focused;
- `runtime-integration-contract-manifest.json`: contrato após integração.

O patch reproduz a tree `e2f7c321654ab0ae4501da8de7ec3c118b62bbe6`.
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

## Candidato e validação terminal

- `candidate.json`: commit/tree/wheel/patch do candidato congelado;
- `local-integration-result.json`: histórico do candidato invalidado;
- `runtime-validation-result.json`: patch apply/reverse, source e runtime;
- `properties-result.json`: job remoto 20.000/20.000;
- `faults-result.json`: seis faults, 2.000 restarts e contention 200;
- `mutation-result.json`: 12/12;
- `ci-result.json`: run real `29787387850`, 6/6 jobs verdes;
- `performance-result.json`: tempos local e remoto.
- `remediation-local-result.json`: wheel, focused/closeout e adapters remediados;
- `remediation-properties-result.json`: 20.000/20.000 com hash do raw report;
- `remediation-faults-result.json`: 200 rows concorrentes integralmente retidas;
- `remediation-mutations-result.json`: 12/12 com rows retidas.

O run remoto invalidado não publicou os JSONs de `/tmp` como artifacts. O
workflow do candidato remediado publica properties, faults e mutations em três
artifacts nomeados com `${{ github.sha }}` e falha se o report estiver ausente.
Esse novo workflow ainda não foi executado remotamente.

`review-attempt-1.json` registra a lane 1 `Needs fixes` e os dois timeouts que
valem zero. `review-attempt-2.json` retém os três pareceres conclusivos
`Needs fixes` do batch `deleg_39e3d235`, com hashes e summaries integrais. Os
REDs da segunda remediação e seus patches test-only também estão retidos.
`review-result.json` permanece ausente até um novo candidato obter três
verdicts terminais válidos.

O RED semântico histórico da Task 6 tinha hash de output, mas não commit/tree
unfixed. Essa limitação é preservada explicitamente; nenhuma proveniência foi
inventada retroativamente.

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
