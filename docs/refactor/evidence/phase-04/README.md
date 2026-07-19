# Evidências da Fase 4

Fase: `phase-04-single-summary-and-confirmation`.

Status: **concluída, publicada e com CI remoto verde**.

Este diretório prova resumo único determinístico, persistência do artefato,
classificação model-agnostic, binding confiável e no máximo um comando após
confirmação posterior da versão vigente. Não contém mensagens reais, payloads
live, credenciais, rede, LLM, provider write, Hermes/ManyChat ou rollout.

## Artefatos

- `entry-baseline.json` — base limpa e gates de entrada;
- `red-result-*.json` — onze REDs versionados dos ciclos TDD, incluindo as
  revisões semântica, de baseline, identidade e API pública;
- `source-map.json` — símbolos legados lidos apenas como referência;
- `package-manifest.json` — hashes do package `reservation_confirmation`;
- `fixture-manifest.json` — hash e classificação do corpus sintético PT/EN;
- `property-result.json` — 50 mil workflows completos e determinísticos,
  divididos igualmente entre Cloudbeds/Bókun in-memory;
- `mutation-result.json` — catálogo crítico executado em cópias temporárias;
- `performance-result.json` — duração, RSS e exit code do gate integral;
- `adversarial-review.md` — revisão de boundary, falso verde e invariantes;
- `validation-result.json` — resumo local dos gates finais;
- `ci-result.json` — IDs/URLs dos cinco workflows verdes do commit de implementação;
- `SHA256SUMS` — integridade dos artefatos principais.

Documentação associada:

- `../../phases/phase-04-single-summary-and-confirmation.md`;
- `../../../superpowers/specs/2026-07-19-phase-4-summary-confirmation-design.md`;
- `../../../superpowers/plans/2026-07-19-phase-4-summary-confirmation.md`.

## Reexecução

```bash
python3 scripts/generate_phase4_manifest.py \
  --package-manifest docs/refactor/evidence/phase-04/package-manifest.json \
  --fixture-manifest docs/refactor/evidence/phase-04/fixture-manifest.json
python3 -m unittest discover -s tests -v
python3 scripts/run_phase4_properties.py \
  --cases 50000 --seed 20260719 \
  --write docs/refactor/evidence/phase-04/property-result.json
python3 scripts/run_phase4_mutations.py \
  --write docs/refactor/evidence/phase-04/mutation-result.json
python3 scripts/validate_phase4.py
```

## Limite da evidência

Os replays atravessam adapters in-memory das Fases 2–3 e o boundary da Fase 4.
Não são E2E de LLM, Hermes, ManyChat, rede, outbox durável, banco, provider,
write comercial, worker, deploy ou rollout. O rollout permanece **NO-GO**.
