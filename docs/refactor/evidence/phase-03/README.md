# Evidências da Fase 3

Fase: `phase-03-lookups-and-offer-snapshots`.

Status: **remediação pós-closeout**, após parecer independente tardio.

Este diretório prova adapters read-only e seleção por identidade opaca. Não
contém payload live, credencial, header, PII, mensagem real ou write de
provider.

## Artefatos

- `entry-baseline.json` — base da fase e fonte legada somente leitura;
- `red-result-*.json` — cinco REDs iniciais e um RED tardio com cinco
  reproduções materiais;
- `source-map.json` — símbolos legados que informaram o contrato, sem import;
- `lookup-manifest.json` — hashes do package `reservation_lookup`;
- `fixture-manifest.json` — hashes das oito fixtures sintéticas/sanitizadas;
- `property-result.json` — 50 mil casos adapter-backed de autorização e
  invalidação;
- `mutation-result.json` — dezenove mutantes críticos mortos;
- `performance-result.json` — duração, RSS e exit code do gate integral;
- `adversarial-review.md` — revisão de boundary, identidade e falso verde;
- `validation-result.json` — resultado consolidado dos gates;
- `SHA256SUMS` — integridade dos artefatos principais.

Documentação associada:

- `../../phases/phase-03-lookups-and-offer-snapshots.md`;
- `../../../superpowers/specs/2026-07-18-phase-3-lookup-adapters-design.md`;
- `../../../superpowers/plans/2026-07-18-phase-3-lookup-adapters.md`.

## Reexecução

```bash
python3 scripts/generate_phase3_manifest.py \
  --lookup-manifest docs/refactor/evidence/phase-03/lookup-manifest.json \
  --fixture-manifest docs/refactor/evidence/phase-03/fixture-manifest.json
python3 -m unittest discover -s tests -v
python3 scripts/run_phase3_properties.py \
  --cases 50000 --seed 20260718 \
  --write docs/refactor/evidence/phase-03/property-result.json
python3 scripts/run_phase3_mutations.py \
  --write docs/refactor/evidence/phase-03/mutation-result.json
python3 scripts/validate_phase3.py
```

## Limite da evidência

Os testes atravessam request builder, transport fake, parsing, normalização,
provenance e seleção. Não são E2E de provider live, auth, Hermes/ManyChat,
catálogo, write, worker, store ou rollout. O `ReadTransport` real permanece
fora desta fase.
