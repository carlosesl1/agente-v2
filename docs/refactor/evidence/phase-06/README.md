# Evidências da Fase 6

Fase: `phase-06-handoff-and-payments`.

Status: **matriz de regressão de pureza fechada; re-gate, publicação e CI remoto ainda pendentes**.

Design e plano TDD versionados:

- `../../../superpowers/specs/2026-07-19-phase-6-handoff-payments-design.md`;
- `../../../superpowers/plans/2026-07-19-phase-6-handoff-payments.md`.

## Entrada

`entry-baseline.json` fixa o fechamento publicado da Fase 5, a autorização de
avanço e o boundary sem capabilities live.

## Implementação local

- `reservation_followup` mantém handoff e payment como workflows irmãos;
- handoff sempre abre fila/estado e acknowledgement requerido; e-mail interno é
  opcional/desativável;
- payment só nasce de reservation `effect_confirmed` canônica;
- Pix, Wise e Stripe possuem evidências e claims fechados por método;
- settlement usa um slot permanente e pós-fence incerto vai a revisão manual;
- outboxes de handoff/payment não reabrem reservation nem settlement;
- SQLite foi executado apenas em memória/arquivos temporários;
- o DDL PostgreSQL é contrato estático e **PostgreSQL não foi executado**.

## Gates vinculados

- properties: 20.000 casos e 16 modos preservados; o candidato corrigido levou
  857,092 s para budget de 900 s e reproduziu o artefato byte a byte;
- faults: 27/27, sendo 15 rollbacks e 12 crashes reais;
- restart: 2.000 schedules;
- contention: 50 rounds em quatro domínios, 200/200 single-winner;
- mutation focused runner: verde sob `PYTHONHASHSEED=1` e `777`;
- mutation catalog integral: 12/12 mortos em uma execução fechada;
- suíte terminal: 642/642 em 229,843 s de `unittest`;
- validator fechado independente rejeita gate acima de 900 s, imports de
  subprocesso/multiprocessing e chamadas de execução de processo;
- matriz focused cobre 18 formas de execução de processo, os dois imports
  proibidos e o baseline real com todas as listas de capability vazias;
- manifests, checksums e re-gate terminal serão congelados no novo candidato;
- CI remoto permanece pendente e não é alegado por este candidato.

## Regras de evidência

- nenhum banco, WAL, SHM, log, payload real, PII ou segredo é versionado;
- outputs brutos permanecem em `/tmp`;
- nenhuma rede, Hermes/LLM, ManyChat, WhatsApp, e-mail, Pix, Wise, Stripe,
  Cloudbeds, Bókun, Supabase, Redis, Docker ou provider live é executado;
- `schema-manifest.json`, `package-manifest.json` e `SHA256SUMS` são gerados por
  `scripts/generate_phase6_manifest.py`;
- `scripts/validate_phase6.py` usa catálogos independentes fechados, não
  constantes importadas dos runners.

## Estado de avanço

- rollout: `NO-GO`;
- `phase7_started=false`;
- a Fase 7 permanece bloqueada;
- nenhuma alegação de publicação ou CI remoto verde é feita antes da evidência
  correspondente em `ci-result.json`.
