# Evidências — Fase 8

Status: **design aprovado; plano/quarentena candidatos; Slice 0, build e gates
operacionais bloqueados**.

## Autoridade atual

A spec upstream aprovada está autenticada por:

- commit `2889e9ec08f466bbb16a30e4bb5c9a098daf54d3`;
- tree `ed57032319d2319389412f4407b268e3d7b7a78c`;
- blob `0e599670b4bc585b1665d932a84afcf3c4b57456`;
- SHA-256 `0f7486191e9963b3786a83cc7096c2af12a89905c5d92fcc27edf431367dcf60`;
- `160392` bytes e `2872` linhas;
- revisão técnica 3/3 e aprovação humana em 2026-07-21.

O único plano candidato é
`docs/superpowers/plans/2026-07-21-phase-8-operational-boundary-correction.md`.
Seu hash, tamanho e line count ficam em `quarantine-manifest.json`.

A autorização humana vigente permitiu somente escrever/revisar plano e quarentena.
Até um novo gate, `implementation_authorized=false`, `build_authorized=false`,
rollout `NO-GO` e `phase9_started=false`.

## Quarentena

`quarantine-manifest.json` registra três camadas sem apagar história:

1. as nove identidades rejeitadas fixadas pela spec aprovada;
2. os bytes observados no HEAD imediatamente antes da quarentena;
3. o estado atual preservado ou substituído de cada path.

A spec e o plano anteriores possuem banner `HISTORICAL-NON-EXECUTABLE`. ADR, página,
plano faseado, validation/rollout e risk register foram reconciliados; os JSONs de
entrada/RED antigos permanecem byte-idênticos e são evidência histórica, não owner de
command, build ou gate.

## Evidência histórica de entrada

`entry-baseline.json` e `red-results.json` não foram reescritos. Eles comprovam o gate
de entrada original e seus REDs sanitizados, inclusive limitações que motivaram a
correção upstream. Seus comandos/identidades não são elegíveis para execução da nova
arquitetura.

Raw output antigo ou futuro não entra no Git. A implementação futura usará um
`EvidenceArtifactStore` privado, content-addressed e crash-safe; o repositório receberá
somente envelopes de hash/tamanho/contagens/conclusão.

## Estado dos gates

| Gate | Estado |
|---|---|
| Design upstream | aprovado 3/3 + Carlos |
| Plano/quarentena | candidato em revisão |
| Slice 0 | bloqueado |
| Implementação/wiring | `NO-GO` |
| Wheel 0.8.0 | não construída |
| Release contract | não implementado |
| Build OCI | não autorizado/não iniciado |
| Dark canary | não iniciado |
| Ingress | não iniciado |
| Conversa por Carlos | bloqueada pelo ingress |
| Canary E2E | sem autorização de write |
| Rollout | `NO-GO` |
| Fase 9 | não iniciada |

## Verificação documental

```bash
python3 -B -m unittest tests.test_phase8_entry -v
git diff --check
```

Os validators legados afetados por documentos compartilhados precisam ser executados
na regressão documental conforme blast radius, sem regenerar raw evidence nem alegar
que a implementação da Fase 8 começou.

## Sanitização

Este diretório não recebe `.env`, tokens, PII, subscriber IDs, mensagens, payloads
brutos, bancos, WAL/SHM, logs, screenshots, outputs brutos ou material privado de
canary.
