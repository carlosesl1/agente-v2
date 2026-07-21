# Evidências — Fase 8

Status: **entrada autenticada; design/plano ativos; build e gates operacionais não iniciados**.

## Entrada

`entry-baseline.json` foi produzido mecanicamente a partir de objetos Git,
evidências JSON autenticadas da Fase 7 e fingerprints read-only. Ele fixa:

- base/closeout `93682024...` e tree `b779e35c...`;
- spec `0dbc9cb...` e plano corrigido `49b4930...`;
- candidato funcional, snapshot terminal, revisão 3/3 e CI `29804123764`;
- réplica limpa, runtime observado, imagem/release live de rollback;
- integração pós-merge 762/762 e identidade do output;
- versões Python/SQLite, zero capability executada, rollout `NO-GO` e
  `phase9_started=false`.

O runtime `/home/ubuntu/chapada-leads-hermes` foi somente lido para confirmar
HEAD/tree/status/diff. A Task 1 não executou Docker, provider, ManyChat ou rede e
não alterou o runtime.

## RED da Task 1

`red-results.json` retém somente os envelopes sanitizados dos REDs funcionais:

- entrada: `python3 -B -m unittest tests.test_phase8_entry -v`, exit `1`, 2
  testes (1 failure e 1 error), causado por `entry-baseline.json` ausente e índice
  stale; output 4.815 bytes, SHA-256
  `0903fcef46d98f9f95ac6d61ea540b242803c2fc6aeda0892a86ef9cc2169c51`;
- validator legado: `python3 -B scripts/validate_phase0.py`, exit `1`, causado
  por uma fixture hostil de e-mail da Fase 7 que autoincriminava o scanner global;
  output 282 bytes, SHA-256
  `61e4428d09ce4ce16ce8a1be249e02377822ad8a37cc8257552a3a3969764d63`.
  A fixture continua produzindo o mesmo valor em runtime, sem manter o literal no
  source versionado;
- checksum legado: `python3 -B scripts/validate_phase1.py`, exit `1`, causado pelo
  hash do risk register anterior à Fase 8; o Phase 2 usava o mesmo hash compartilhado.
  Output 839 bytes, SHA-256
  `2a0c0f90ce981f60b0f34534c5099a744c4b7c6eb88dced7e07e5b7b8058ba7a`.
  Somente as duas entradas de checksum correspondentes foram atualizadas.

O output bruto permanece em `/tmp` e não entra no Git. Falhas de comandos do
plano antigo são diagnóstico histórico e não são apresentadas como RED
funcional.

## Estado dos gates

| Gate | Estado na entrada |
|---|---|
| Entry | autenticado |
| Build | não iniciado |
| Dark canary | bloqueado pelo build |
| Ingress | bloqueado pelo dark canary |
| Conversa por Carlos | bloqueado pelo ingress |
| Canary E2E | bloqueado e sem autorização de write |
| Rollout | `NO-GO` |
| Fase 9 | não iniciada |

Nenhum artefato futuro é antecipado nesta Task 1. Manifesto/validator próprios da
Fase 8 pertencem a uma task posterior do plano.

## Verificação desta entrada

```bash
python3 -B -m unittest tests.test_phase8_entry -v
python3 -B -m unittest tests.test_phase7_closeout -v
python3 -B scripts/validate_phase0.py
python3 -B scripts/validate_phase1.py
python3 -B scripts/validate_phase2.py
python3 -B scripts/validate_phase3.py
python3 -B scripts/validate_phase4.py
python3 -B scripts/validate_phase5.py
python3 -B scripts/validate_phase6.py
python3 -B scripts/validate_phase7.py --terminal
```

Os manifests legados afetados por documentos compartilhados devem ser
regenerados na ordem: Fase 3 apenas validator; Fase 4 com outputs explícitos;
Fases 5–7 com `--write` e depois `--check`.

## Sanitização

Este diretório não recebe `.env`, tokens, PII, subscriber IDs, mensagens,
payloads brutos, bancos, logs, screenshots, outputs brutos ou material privado
de canary.
