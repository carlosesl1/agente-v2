# Evidências da Fase 1

## Artefatos canônicos

- `incident-coverage.json` — cobertura gerada de F01–F22, cenários,
  classificações, violações e fault points;
- `corpus-manifest.json` — SHA-256 individual de harness, schema, fixtures e
  todos os cenários;
- `source-map.json` — caminhos/símbolos relativos que sustentam cada witness;
- `behavior-baseline.md` — comportamento aceito e não aceito;
- `classification-method.md` — semântica e limites das classificações;
- `source-readonly-verification.json` — HEAD/status/símbolos e zero ação live;
- `validation-result.json` — comandos e exit codes finais;
- `SHA256SUMS` — integridade dos artefatos da fase.

## Regeneração determinística

```bash
python3 scripts/generate_phase1_reports.py --write
```

O comando deve deixar `incident-coverage.json` e `source-map.json` sem diff.

## Validação

```bash
python3 -m characterization.harness
python3 -m unittest discover -s tests -v
python3 scripts/validate_phase1.py
```

Nenhum arquivo deste diretório contém mensagem real, PII, payload bruto de
provider, segredo ou resultado de operação live.
