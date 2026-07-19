# Evidências da Fase 5

Fase: `phase-05-durable-command-execution`.

Status: **implementação aprovada; gates integrais e closeout em execução**.

Design e plano TDD versionados:

- `../../../superpowers/specs/2026-07-19-phase-5-durable-command-execution-design.md`;
- `../../../superpowers/plans/2026-07-19-phase-5-durable-command-execution.md`.

## Entrada

`entry-baseline.json` fixa:

- SHA terminal da Fase 4;
- igualdade local/origin/remoto;
- validadores 0–4 verdes;
- cinco workflows terminais em `success`;
- fingerprint do legado somente leitura;
- autorização de SQLite sem Docker/PostgreSQL/Supabase;
- rollout `NO-GO`.

## Regras

- bancos SQLite, WAL e SHM nunca são versionados;
- somente fixtures sintéticas e sanitizadas;
- nenhum payload bruto, PII real, segredo ou log de worker;
- nenhum provider, delivery, runtime ou rede live;
- claims só são feitos após execução real dos gates correspondentes.

## Implementação local

- `reservation_execution` contém contratos, SQLite UnitOfWork, worker,
  reconciler, outbox e properties sem capability externa default;
- DDL SQLite/PostgreSQL é regenerável do mesmo contrato, mas somente SQLite foi
  executado;
- state/event/command/ledger/outbox são persistidos atomicamente;
- pós-fence incerto nunca retorna a retry automático;
- delivery possui lease/fencing próprios e não altera o ledger comercial.
- `operational-gate-contract.json` é a autoridade independente e versionada
  para 20.000 properties, 17 fault points, nove restart points, 20 mutantes,
  schemas de schedules/rounds, schemas recursivos fechados das métricas e dos
  13 arquivos RED, e o workflow canônico;
- o validator reconstrói schedules, contagens, identidades e pós-condições a
  partir das linhas, sem confiar em `result=passed` ou agregados declarados;
- counters live/network exigem inteiro zero exato, flags negativas exigem
  booleano `false`, e proveniência RED rejeita campos ausentes ou extras.

## Gate de saída em execução

- suíte fresca com output bruto somente em `/tmp`;
- 20.000 properties adapter-backed;
- 17 fault points, 2.000 restart schedules e 50 contention rounds;
- 20 mutantes materiais em cópias temporárias;
- manifests, validator fechado, workflow CI e revisão adversarial;
- rollout `NO-GO`; Fase 6 não iniciada.
