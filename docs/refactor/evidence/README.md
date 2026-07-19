# Evidências da refatoração

Este diretório contém somente evidências técnicas sanitizadas.

## Regras

Permitido:

- commits, branches e URLs públicas de repositório;
- status Git e diff stats sem conteúdo;
- image digest e hashes SHA-256;
- configuração não secreta;
- contagens, métricas e exit codes.

Proibido:

- credenciais, tokens e arquivos `.env`;
- PII e identificadores de contatos;
- mensagens reais;
- payloads brutos de providers;
- bancos, logs e comprovantes;
- respostas completas de endpoints que possam conter dados operacionais.

## Fase 0

`phase-00/baseline-manifest.json` é o manifesto principal. `phase-00/validation-result.json` registra os checks executados. `SHA256SUMS` protege a integridade dos arquivos de evidência da fase.

Para verificar:

```bash
python3 scripts/validate_phase0.py
```

## Fase 1

`phase-01/incident-coverage.json` e `phase-01/source-map.json` são gerados
deterministicamente a partir do corpus. `behavior-baseline.md` separa o
comportamento aceito do não aceito; `classification-method.md` define o sentido
de `reproduced` e `contract_characterized`; `source-readonly-verification.json`
prova que o legado permaneceu somente leitura.

Para verificar:

```bash
python3 -m characterization.harness
python3 -m unittest discover -s tests -v
python3 scripts/validate_phase1.py
```

## Fase 2

`phase-02/property-result.json` registra o gate determinístico de 100 mil
sequências. `domain-manifest.json` protege o package puro; a matriz completa
estado/evento e o contrato canônico ficam em `../domain/`.

Para verificar:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_phase2_properties.py --sequences 100000 --max-events 20 --seed 20260718
python3 scripts/validate_phase2.py
```

## Fase 3

`phase-03/lookup-manifest.json` e `fixture-manifest.json` protegem o package
read-only e oito fixtures sintéticas. `property-result.json` registra 50 mil
casos adapter-backed de seleção/invalidação para ambos providers;
`mutation-result.json` registra o catálogo reproduzível; `source-map.json` documenta as fontes
legadas somente leitura.

Para verificar:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_phase3_properties.py --cases 50000 --seed 20260718
python3 scripts/run_phase3_mutations.py
python3 scripts/validate_phase3.py
```

## Fase 4

`phase-04/entry-baseline.json` registra o commit-base, os gates regressivos e o
boundary autorizado. A fase permanece sem LLM, rede, provider, mensagem ou
write live.
