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

`phase-04/package-manifest.json`, `fixture-manifest.json` e `SHA256SUMS`
protegem o boundary puro e o corpus sintético. `property-result.json` registra 50
mil workflows desde `new_workflow`, divididos igualmente entre Cloudbeds/Bókun
in-memory; `mutation-result.json` registra 19/19 mutantes mortos. `ci-result.json`
fixa os cinco workflows verdes do commit de implementação. A fase não executou
LLM, rede, provider write, mensagem live ou rollout.

Para verificar:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_phase4_properties.py --cases 50000 --seed 20260719
python3 scripts/run_phase4_mutations.py
python3 scripts/validate_phase4.py
```

## Fase 5

`phase-05/entry-baseline.json` fixa a entrada. `property-result.json`,
`fault-matrix.json`, `restart-result.json`, `concurrency-result.json` e
`mutation-result.json` são os gates determinísticos de execução durável.
`schema-manifest.json`, `package-manifest.json` e `SHA256SUMS` protegem DDL,
package e evidências; PostgreSQL permanece não executado. A fase não adiciona
capacidade live e mantém rollout `NO-GO` e Fase 6 bloqueada.

Para verificar, após gerar os artefatos integrais:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_phase5_properties.py --cases 20000 --seed 2026071905
python3 scripts/run_phase5_faults.py --seed 2026071905 --restart-schedules 2000 --contention-rounds 50 --write-fault-matrix /tmp/fault.json --write-restart /tmp/restart.json --write-concurrency /tmp/concurrency.json
python3 scripts/run_phase5_mutations.py
python3 scripts/validate_phase5.py
```

## Fase 6

`phase-06/entry-baseline.json` fixa o commit terminal publicado da Fase 5,
os seis workflows remotos verdes, a autorização explícita de avanço e os
limites sem capability live. A spec e a página da fase formalizam dois
workflows irmãos: handoff e pagamento.

Na abertura, nenhuma reserva, settlement, mensagem, e-mail, provider, banco ou
runtime foi executado; rollout permaneceu `NO-GO`.

O candidato da Task 14 foi aprovado 3/3, publicado em
`8f23a8376f1d226f2ada5d80a45cbb930a79429e` e validado pelos sete workflows
remotos. Os gates registram 642 testes, 20.000 properties em 857,092 s, 27
faults, 2.000 restarts, 200 rows de contention e 12/12 mutantes mortos. IDs e
URLs verificáveis estão em `phase-06/ci-result.json`.

Para verificar o closeout publicado:

```bash
python3 scripts/generate_phase6_manifest.py --check
python3 scripts/validate_phase6.py
```

PostgreSQL não foi executado; rollout `NO-GO`; `phase7_started=false`.

## Fase 7

`phase-07/entry-baseline.json` fixa o closeout publicado da Fase 6, o baseline
focused 14/14, os validators verdes, as versões locais e o runtime observado
somente leitura. `phase-07/red-results.json` registra REDs sanitizados sem raw
output.

A Fase 7 está concluída e publicada no closeout
`93682024b4867d3e313324339a7060d5351dcd3d`. O candidato funcional
`2c99be11b1bdc1b66d14bd7a19c510ec50d502d4` foi autenticado pelo snapshot
terminal `73904070dfcb52a3183459bc97abbc87595e1efe`, revisão 3/3 e CI remoto
`29804123764` com seis jobs verdes. Nenhuma capability live foi executada e o
runtime operacional permaneceu somente leitura. O snapshot terminal da Fase 7
fechou com rollout `NO-GO` e `phase8_started=false`; a Fase 8 foi autorizada
separadamente em 2026-07-21 após o closeout.

Para verificar o closeout publicado:

```bash
python3 scripts/generate_phase7_manifest.py --check
python3 scripts/validate_phase7.py --terminal
```

## Fase 8

`phase-08/entry-baseline.json` autentica base/spec/plano, candidato e snapshot
terminal da Fase 7, revisão/CI, réplica limpa, fingerprints read-only do runtime,
imagem/release live de rollback e o output pós-merge 762/762.
`phase-08/red-results.json` registra somente o envelope do RED funcional da Task
1; o output bruto permanece fora do Git.

A Fase 8 está ativa no gate de entrada. Build, dark canary, ingress, teste
conversacional, canary E2E e rollout não foram iniciados. Nenhum provider write,
Docker, ManyChat ou rede foi executado nesta entrada; rollout `NO-GO` e
`phase9_started=false`.
