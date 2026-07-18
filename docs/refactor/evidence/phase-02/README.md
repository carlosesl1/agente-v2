# Evidências da Fase 2

Fase: `phase-02-typed-domain-and-reducer`.

Este diretório prova o domínio puro; não contém payload, log, mensagem, contato,
provider response ou dado live.

## Artefatos

- `red-test-plan.md` — owners e testes definidos antes da implementação;
- `red-result.json` — falha RED esperada por ausência do package;
- `property-result.json` — 100 mil sequências/2 milhões de transições, com
  obrigações positivas e classes semânticas de cobertura;
- `mutation-result.json` — onze mutantes críticos mortos pelos testes-alvo;
- `performance-result.json` — comando, versão Python, duração, RSS e exit code
  da carga integral local;
- `domain-manifest.json` — hashes SHA-256 do package de domínio;
- `adversarial-review.md` — revisão independente e tratamento dos achados;
- `validation-result.json` — resultado consolidado dos gates;
- `SHA256SUMS` — integridade dos artefatos principais.

Documentação canônica associada:

- `../../domain/phase2-domain-contract.md`;
- `../../domain/phase2-state-event-matrix.md`;
- `../../phases/phase-02-typed-domain-and-reducer.md`.

## Reexecução

```bash
python3 scripts/generate_phase2_matrix.py \
  --write docs/refactor/domain/phase2-state-event-matrix.md \
  --manifest docs/refactor/evidence/phase-02/domain-manifest.json
python3 -m unittest discover -s tests -v
python3 scripts/run_phase2_properties.py \
  --sequences 100000 --max-events 20 --seed 20260718 \
  --write docs/refactor/evidence/phase-02/property-result.json
python3 scripts/validate_phase2.py
```

## Limite da evidência

O relatório property-based exercita o reducer e os value objects sem I/O. Ele
não é E2E de ManyChat/Hermes, não executa provider e não valida adapters,
persistência, worker, outbox ou renderer; esses owners pertencem às fases
seguintes.
